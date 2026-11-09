"""파일 기반 전략 레지스트리 (PLAN §6).

strategies/<state>/<id>_v<version>.yaml  (내용 = StrategySpec.to_yaml())
상태 전이는 원장 transitions.jsonl 에 기록.

  draft → candidate → validated → challenger → review → active → retired
  (rejected 를 제외한) 모든 상태 → rejected (사유 필수, 영구 보존)

- review → active 는 사람 승인 파일 research/activation_approvals/<id>_v<ver>.yaml 필요
  (admin_cli activate 가 생성·소비). 활성화 시 기존 active 는 자동으로 retired.
- active 는 항상 최대 1개, challenger 는 최대 3개.
- rollback(): 직전 active 버전(retired 상태)을 복원하고 현재 active 를 retired 로.
"""
from __future__ import annotations

import fcntl
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import yaml

from btc_quant.strategy.spec import StrategySpec

from .ledger import Ledger, repo_root, utcnow

STATES = ("draft", "candidate", "validated", "challenger", "review", "active", "retired", "rejected")
FORWARD = {
    "draft": "candidate",
    "candidate": "validated",
    "validated": "challenger",
    "challenger": "review",
    "active": "retired",
}
MAX_CHALLENGERS = 3
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


class RegistryError(RuntimeError):
    pass


class TransitionError(RegistryError):
    pass


class ActivationApprovalError(PermissionError):
    pass


def key_of(strategy_id: str, version: int) -> str:
    return f"{strategy_id}_v{int(version)}"


class StrategyRegistry:
    def __init__(self, root: str | Path | None = None, ledger: Ledger | None = None):
        self.root = Path(root) if root is not None else repo_root()
        self.dir = self.root / "strategies"
        self.approvals_dir = self.root / "research" / "activation_approvals"
        self.ledger = ledger if ledger is not None else Ledger(self.root / "research" / "ledger")
        for s in STATES:
            (self.dir / s).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 내부
    @contextmanager
    def _lock(self) -> Iterator[None]:
        with open(self.dir / ".lock", "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _path(self, state: str, strategy_id: str, version: int) -> Path:
        return self.dir / state / f"{key_of(strategy_id, version)}.yaml"

    def _move(self, strategy_id: str, version: int, src: str, dst: str) -> None:
        a, b = self._path(src, strategy_id, version), self._path(dst, strategy_id, version)
        if b.exists():
            raise RegistryError(f"{b} already exists")
        os.replace(a, b)

    # ------------------------------------------------------------ 조회
    def state_of(self, strategy_id: str, version: int) -> str | None:
        found = [s for s in STATES if self._path(s, strategy_id, version).exists()]
        if len(found) > 1:
            raise RegistryError(f"{key_of(strategy_id, version)} found in multiple states {found}")
        return found[0] if found else None

    def list(self, state: str | None = None) -> list[dict]:
        out = []
        for s in ([state] if state else STATES):
            for p in sorted((self.dir / s).glob("*.yaml")):
                m = re.match(r"^(.*)_v(\d+)$", p.stem)
                if m:
                    out.append({"id": m.group(1), "version": int(m.group(2)), "state": s, "path": p})
        return out

    def load(self, strategy_id: str, version: int) -> StrategySpec:
        st = self.state_of(strategy_id, version)
        if st is None:
            raise RegistryError(f"unknown strategy {key_of(strategy_id, version)}")
        return StrategySpec.from_yaml(self._path(st, strategy_id, version))

    def active(self) -> dict | None:
        act = self.list("active")
        if len(act) > 1:
            raise RegistryError(f"invariant broken: {len(act)} active strategies")
        return act[0] if act else None

    # ------------------------------------------------------------ 등록·전이
    def register(self, spec: StrategySpec, actor: str = "ai", reason: str = "registered") -> Path:
        """새 버전을 draft 로 등록. 같은 id·version 은 어떤 상태든 재등록 불가(파라미터 변경은 새 버전)."""
        if not _ID_RE.match(spec.id):
            raise ValueError(f"bad strategy id {spec.id!r}")
        spec.validate()
        with self._lock():
            if self.state_of(spec.id, spec.version) is not None:
                raise RegistryError(f"{key_of(spec.id, spec.version)} already registered")
            p = self._path("draft", spec.id, spec.version)
            p.write_text(spec.to_yaml(), encoding="utf-8")
            self.ledger.record_transition(spec.id, spec.version, None, "draft", reason, actor,
                                          logic_hash=spec.logic_hash())
        return p

    def transition(self, strategy_id: str, version: int, to_state: str, reason: str,
                   actor: str = "gate", evidence: dict | None = None) -> None:
        """관문 코드용 전진 전이. active 로는 activate(), rejected 로는 reject() 사용."""
        if to_state == "active":
            raise TransitionError("activation is human-only: use admin_cli activate")
        if to_state == "rejected":
            return self.reject(strategy_id, version, reason, actor=actor)
        if to_state not in STATES:
            raise TransitionError(f"unknown state {to_state}")
        with self._lock():
            cur = self.state_of(strategy_id, version)
            if cur is None:
                raise RegistryError(f"unknown strategy {key_of(strategy_id, version)}")
            if FORWARD.get(cur) != to_state:
                raise TransitionError(f"illegal transition {cur} → {to_state}")
            if to_state == "challenger" and len(self.list("challenger")) >= MAX_CHALLENGERS:
                raise TransitionError(f"challenger slots full ({MAX_CHALLENGERS})")
            spec = StrategySpec.from_yaml(self._path(cur, strategy_id, version))
            self._move(strategy_id, version, cur, to_state)
            self.ledger.record_transition(strategy_id, version, cur, to_state, reason, actor,
                                          logic_hash=spec.logic_hash(), evidence=evidence or {})

    def reject(self, strategy_id: str, version: int, reason: str, actor: str = "gate") -> None:
        if not reason or not reason.strip():
            raise ValueError("reject requires a reason")
        with self._lock():
            cur = self.state_of(strategy_id, version)
            if cur is None:
                raise RegistryError(f"unknown strategy {key_of(strategy_id, version)}")
            if cur == "rejected":
                raise TransitionError("already rejected")
            self._move(strategy_id, version, cur, "rejected")
            self.ledger.record_transition(strategy_id, version, cur, "rejected", reason, actor)

    # ------------------------------------------------------------ 사람 전용
    def approval_path(self, strategy_id: str, version: int) -> Path:
        return self.approvals_dir / f"{key_of(strategy_id, version)}.yaml"

    def write_activation_approval(self, strategy_id: str, version: int, approved_by: str,
                                  note: str = "") -> Path:
        """admin_cli 전용."""
        spec = self.load(strategy_id, version)
        p = self.approval_path(strategy_id, version)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump({
            "strategy_id": strategy_id, "version": int(version), "logic_hash": spec.logic_hash(),
            "approved_by": approved_by, "approved_at": utcnow(), "note": note,
        }, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return p

    def activate(self, strategy_id: str, version: int, actor: str = "human") -> dict | None:
        """review → active. 사람 승인 파일 필요(소비됨). 이전 active 는 retired. 이전 active 반환."""
        ap = self.approval_path(strategy_id, version)
        with self._lock():
            cur = self.state_of(strategy_id, version)
            if cur != "review":
                raise TransitionError(f"only 'review' can be activated ({key_of(strategy_id, version)} is {cur})")
            if not ap.exists():
                raise ActivationApprovalError(
                    f"no human approval at {ap}; activation is human-only (admin_cli activate)")
            approval = yaml.safe_load(ap.read_text(encoding="utf-8")) or {}
            spec = StrategySpec.from_yaml(self._path(cur, strategy_id, version))
            if (approval.get("strategy_id") != strategy_id or int(approval.get("version", -1)) != int(version)
                    or approval.get("logic_hash") != spec.logic_hash()):
                raise ActivationApprovalError("approval file does not match strategy id/version/logic_hash")
            prev = self.active()
            if prev is not None:
                self._move(prev["id"], prev["version"], "active", "retired")
                self.ledger.record_transition(prev["id"], prev["version"], "active", "retired",
                                              f"replaced by {key_of(strategy_id, version)}", actor)
            self._move(strategy_id, version, "review", "active")
            self.ledger.record_transition(
                strategy_id, version, "review", "active", "human approval", actor,
                logic_hash=spec.logic_hash(), approval=approval,
                replaced=key_of(prev["id"], prev["version"]) if prev else None)
            consumed = self.approvals_dir / "consumed"
            consumed.mkdir(parents=True, exist_ok=True)
            os.replace(ap, consumed / f"{key_of(strategy_id, version)}__{utcnow().replace(':', '')}.yaml")
            return prev

    def rollback(self, actor: str = "human", reason: str = "rollback") -> tuple[str, str]:
        """현재 active 를 retired 로, 직전 active(retired 상태)를 active 로. (from_key, to_key) 반환."""
        with self._lock():
            cur = self.active()
            if cur is None:
                raise RegistryError("no active strategy to roll back")
            cur_key = key_of(cur["id"], cur["version"])
            last = None
            for t in reversed(self.ledger.transitions()):
                if t["to_state"] == "active" and key_of(t["strategy_id"], t["version"]) == cur_key:
                    last = t
                    break
            prev_key = last.get("replaced") if last else None
            if not prev_key:
                raise RegistryError(f"no previous active version recorded for {cur_key}")
            m = re.match(r"^(.*)_v(\d+)$", prev_key)
            pid, pver = m.group(1), int(m.group(2))
            if self.state_of(pid, pver) != "retired":
                raise RegistryError(f"previous active {prev_key} is not in 'retired' (is {self.state_of(pid, pver)})")
            self._move(cur["id"], cur["version"], "active", "retired")
            self.ledger.record_transition(cur["id"], cur["version"], "active", "retired",
                                          f"{reason}: rolled back to {prev_key}", actor)
            self._move(pid, pver, "retired", "active")
            self.ledger.record_transition(pid, pver, "retired", "active", reason, actor,
                                          replaced=cur_key, rollback=True)
            return cur_key, prev_key

    def status(self) -> dict:
        return {s: [key_of(x["id"], x["version"]) for x in self.list(s)] for s in STATES}
