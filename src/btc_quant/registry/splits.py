"""데이터 분할·잠금 구간 접근 통제 (PLAN §2.1, §5.3).

- config/splits.yaml 의 segment(dev/val/lockbox/forward)만 읽을 수 있다.
- lockbox 와 겹치는 범위를 요청하면 LockboxAccessError (LockboxKey 없을 때).
- LockboxKey 는 open_lockbox(candidate_id) 만 발급: 사람 승인 파일
  research/lockbox_approvals/<candidate_id>.yaml 이 있어야 하고(관리자 CLI가 생성),
  해당 후보가 현재 잠금 구간에서 개봉된 적이 없어야 하며, 개봉 상한(기본 10) 이내여야 한다.
  개봉은 원장에 기록되고 승인 파일은 consumed/ 로 옮겨져 소비된다.
"""
from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from .ledger import Ledger, LedgerError, repo_root, utcnow

SEGMENTS = ("dev", "val", "lockbox", "forward")
_CANDIDATE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")

# 이 프로세스에서 open_lockbox 가 발급한 토큰 (위조 키 차단)
_ISSUED_TOKENS: set[str] = set()


class LockboxAccessError(PermissionError):
    pass


class LockboxApprovalError(PermissionError):
    pass


class LockboxExhaustedError(RuntimeError):
    pass


@dataclass(frozen=True)
class LockboxKey:
    candidate_id: str
    lockbox_id: str
    opening_id: str
    _token: str = field(repr=False, default="")


def default_config_path() -> Path:
    return repo_root() / "config" / "splits.yaml"


def load_splits(path: str | Path | None = None) -> dict:
    p = Path(path) if path is not None else default_config_path()
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    segs = cfg.get("segments") or {}
    missing = [s for s in SEGMENTS if s not in segs]
    if missing:
        raise ValueError(f"splits config missing segments {missing}")
    cfg.setdefault("lockbox_cap", 10)
    if not cfg.get("lockbox_id"):
        raise ValueError("splits config needs lockbox_id")
    return cfg


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def segment_bounds(segment: str, config: dict | str | Path | None = None,
                   now: pd.Timestamp | None = None) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) UTC. forward 의 end 가 null 이면 now(기본 현재 시각)."""
    cfg = config if isinstance(config, dict) else load_splits(config)
    if segment not in SEGMENTS:
        raise KeyError(f"unknown segment {segment!r}; one of {SEGMENTS}")
    s = cfg["segments"][segment]
    start = _ts(s["start"])
    end = s.get("end")
    end = _ts(end) if end is not None else (_ts(now) if now is not None else pd.Timestamp.now(tz="UTC"))
    if end <= start and segment != "forward":
        raise ValueError(f"segment {segment} has end <= start")
    return start, end


def overlaps_lockbox(start, end, config: dict | str | Path | None = None) -> bool:
    """[start, end) 가 잠금 구간과 겹치는가. None 은 무한대."""
    lb_s, lb_e = segment_bounds("lockbox", config)
    s = _ts(start) if start is not None else pd.Timestamp.min.tz_localize("UTC")
    e = _ts(end) if end is not None else pd.Timestamp.max.tz_localize("UTC")
    return s < lb_e and e > lb_s


class DataAccess:
    """분할을 강제하는 데이터 접근 계층. 연구 코드는 store.read_ohlcv 대신 이것을 쓴다."""

    def __init__(self, root: str | Path | None = None, data_root: str | Path | None = None,
                 config_path: str | Path | None = None, ledger: Ledger | None = None):
        self.root = Path(root) if root is not None else repo_root()
        self.data_root = Path(data_root) if data_root is not None else self.root / "data" / "raw"
        self.config_path = Path(config_path) if config_path is not None else self.root / "config" / "splits.yaml"
        self.ledger = ledger if ledger is not None else Ledger(self.root / "research" / "ledger")
        self.approvals_dir = self.root / "research" / "lockbox_approvals"

    @property
    def config(self) -> dict:
        return load_splits(self.config_path)  # 매번 읽음(롤링 반영)

    def segment_bounds(self, segment: str, now=None) -> tuple[pd.Timestamp, pd.Timestamp]:
        return segment_bounds(segment, self.config, now=now)

    # ---------------------------------------------------------------- 키 검증
    def _check_key(self, key: LockboxKey | None) -> None:
        if key is None:
            raise LockboxAccessError(
                "requested range overlaps the LOCKBOX segment. Lockbox access needs a human-approved "
                "opening (admin CLI: approve-lockbox <candidate_id>, then open_lockbox()).")
        if not isinstance(key, LockboxKey) or key._token not in _ISSUED_TOKENS:
            raise LockboxAccessError("invalid LockboxKey (not issued by open_lockbox in this process)")
        cfg = self.config
        if key.lockbox_id != cfg["lockbox_id"]:
            raise LockboxAccessError(f"LockboxKey is for {key.lockbox_id}, current lockbox is {cfg['lockbox_id']}")
        op = self.ledger.get("lockbox", key.opening_id)
        if op is None or op.get("candidate_id") != key.candidate_id:
            raise LockboxAccessError("LockboxKey has no matching opening in the ledger")

    # ---------------------------------------------------------------- 읽기
    def check_range(self, start, end, key: LockboxKey | None = None) -> None:
        if overlaps_lockbox(start, end, self.config):
            self._check_key(key)

    def read(self, segment: str, exchange: str, symbol: str, timeframe: str,
             key: LockboxKey | None = None) -> pd.DataFrame:
        start, end = self.segment_bounds(segment)
        return self.read_range(start, end, exchange, symbol, timeframe, key=key)

    def read_range(self, start, end, exchange: str, symbol: str, timeframe: str,
                   key: LockboxKey | None = None) -> pd.DataFrame:
        from btc_quant.data.store import read_ohlcv

        if start is None or end is None:
            raise ValueError("explicit start and end required")
        start, end = _ts(start), _ts(end)
        self.check_range(start, end, key)
        df = read_ohlcv(exchange, symbol, timeframe, start=start.isoformat(), end=end.isoformat(),
                        root=self.data_root)
        # 이중 안전장치: 경계 밖 행 제거
        idx = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.DatetimeIndex(df["ts"])
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        return df[(idx >= start) & (idx < end)]

    # ---------------------------------------------------------------- 개봉
    def approval_path(self, candidate_id: str) -> Path:
        if not _CANDIDATE_RE.match(candidate_id):
            raise ValueError(f"bad candidate id {candidate_id!r}")
        return self.approvals_dir / f"{candidate_id}.yaml"

    def openings(self) -> list[dict]:
        return self.ledger.lockbox_history(self.config["lockbox_id"])

    def open_lockbox(self, candidate_id: str, logic_hash: str | None = None,
                     note: str = "") -> LockboxKey:
        cfg = self.config
        lb_id, cap = cfg["lockbox_id"], int(cfg["lockbox_cap"])
        ap = self.approval_path(candidate_id)
        if not ap.exists():
            raise LockboxApprovalError(
                f"no human approval at {ap}. Request it; a human runs "
                f"`python -m btc_quant.registry.admin_cli approve-lockbox {candidate_id}`.")
        approval = yaml.safe_load(ap.read_text(encoding="utf-8")) or {}
        if approval.get("candidate_id") != candidate_id:
            raise LockboxApprovalError(f"approval file candidate_id mismatch in {ap}")
        if approval.get("lockbox_id") not in (None, lb_id):
            raise LockboxApprovalError(
                f"approval is for lockbox {approval.get('lockbox_id')}, current is {lb_id}")
        if logic_hash and approval.get("logic_hash") not in (None, logic_hash):
            raise LockboxApprovalError("approval logic_hash does not match candidate")
        with self.ledger.lock():
            hist = self.ledger.lockbox_history(lb_id)
            if any(h["candidate_id"] == candidate_id for h in hist):
                raise LockboxApprovalError(
                    f"candidate {candidate_id} already opened lockbox {lb_id} (one opening per candidate)")
            if len(hist) >= cap:
                raise LockboxExhaustedError(
                    f"lockbox {lb_id} reached its cap of {cap} openings. Roll the lockbox (human): "
                    "merge it into dev/val, make accumulated forward data the new lockbox, and set a "
                    "new lockbox_id in config/splits.yaml.")
            rec = self.ledger._append_locked("lockbox", {
                "event": "open", "candidate_id": candidate_id, "lockbox_id": lb_id,
                "logic_hash": logic_hash or approval.get("logic_hash"),
                "approval": approval, "note": note})
            consumed = self.approvals_dir / "consumed"
            consumed.mkdir(parents=True, exist_ok=True)
            os.replace(ap, consumed / f"{candidate_id}__{rec['id']}.yaml")
        token = secrets.token_hex(16)
        _ISSUED_TOKENS.add(token)
        return LockboxKey(candidate_id, lb_id, rec["id"], token)

    def lockbox_status(self) -> dict:
        cfg = self.config
        hist = self.openings()
        return {"lockbox_id": cfg["lockbox_id"], "cap": int(cfg["lockbox_cap"]),
                "openings": len(hist), "remaining": int(cfg["lockbox_cap"]) - len(hist),
                "pending_approvals": sorted(p.stem for p in self.approvals_dir.glob("*.yaml"))
                if self.approvals_dir.exists() else []}


def write_lockbox_approval(root: str | Path | None, candidate_id: str, approved_by: str,
                           lockbox_id: str | None = None, logic_hash: str | None = None,
                           note: str = "") -> Path:
    """사람 승인 파일 생성 (admin_cli 전용)."""
    da = DataAccess(root=root)
    p = da.approval_path(candidate_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"candidate_id": candidate_id, "lockbox_id": lockbox_id or da.config["lockbox_id"],
            "approved_by": approved_by, "approved_at": utcnow(), "note": note}
    if logic_hash:
        body["logic_hash"] = logic_hash
    p.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


__all__ = ["DataAccess", "LockboxKey", "LockboxAccessError", "LockboxApprovalError",
           "LockboxExhaustedError", "segment_bounds", "overlaps_lockbox", "load_splits",
           "write_lockbox_approval", "SEGMENTS", "LedgerError"]
