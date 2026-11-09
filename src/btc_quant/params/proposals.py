"""파라미터 변경 제안·승인 (research/param_proposals/<id>.yaml → research/param_approvals/<id>.json).

- AI(보정·기준 도구)는 제안 파일만 쓴다. 승인은 사람 전용 명령 `python -m btc_quant.params approve <id>`
  (대화형 터미널에서 id 를 직접 타이핑; 연구 모드 hook 이 AI 의 실행을 차단).
- 승인 시 제안의 `from` 값이 지금 값과 같은지 확인한다(그 사이 다른 변경이 있었으면 낡은 제안 → 거부).
- 종류(kind)
  cost_models : class ① 느슨한 비용 변경 (config/costs.yaml 새 버전)
  criterion   : class ② 증거 기준 변경 (gates.yaml 값 + params.yaml 이력, applies_from = 승인 시점의 다음 캠페인)
  preference  : class ③ 선택지 표 (사람이 --choice 로 하나를 고름)
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

import yaml

from ._io import read_yaml, repo_root, utcnow, write_yaml

PROPOSALS_REL = Path("research") / "param_proposals"
APPROVALS_REL = Path("research") / "param_approvals"
KINDS = ("cost_models", "criterion", "preference")
_STATUS_KEYS = ("status", "approval")


class ProposalError(ValueError):
    pass


class ConfirmationError(RuntimeError):
    pass


def proposals_dir(root=None) -> Path:
    return repo_root(root) / PROPOSALS_REL


def approvals_dir(root=None) -> Path:
    return repo_root(root) / APPROVALS_REL


def content_hash(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k not in _STATUS_KEYS + ("content_hash",)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "x"


def write_proposal(doc: dict, root=None) -> str:
    """doc: {class, kind, title, changes|options, evidence, rationale, ...}. 반환 = 제안 id."""
    if doc.get("kind") not in KINDS:
        raise ProposalError(f"kind must be one of {KINDS}")
    if int(doc.get("class", 0)) not in (1, 2, 3):
        raise ProposalError("class must be 1/2/3")
    if doc["kind"] == "preference":
        if not doc.get("options"):
            raise ProposalError("preference proposal needs options")
    elif not doc.get("changes"):
        raise ProposalError("proposal needs changes")
    doc = {"id": None, "created": utcnow(), "created_by": doc.get("created_by", "agent"), **doc,
           "status": "pending"}
    stamp = doc["created"][:10].replace("-", "")
    h = content_hash({**doc, "id": None})
    pid = f"pp-{stamp}-{_slug(doc.get('title') or doc['kind'])}-{h[:6]}"
    doc["id"] = pid
    doc["content_hash"] = content_hash(doc)
    d = proposals_dir(root)
    write_yaml(d / f"{pid}.yaml", doc,
               "# 파라미터 변경 제안 — 사람 승인 전에는 아무 효과 없음. 승인: python -m btc_quant.params approve <id>\n")
    return pid


def load_proposal(pid: str, root=None) -> dict:
    p = proposals_dir(root) / f"{pid}.yaml"
    if not p.exists():
        raise ProposalError(f"proposal {pid} not found in {p.parent}")
    return read_yaml(p)


def approval_record(pid: str, root=None) -> dict | None:
    p = approvals_dir(root) / f"{pid}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def list_proposals(root=None, status: str | None = None) -> list[dict]:
    d = proposals_dir(root)
    out = []
    for p in sorted(d.glob("pp-*.yaml")) if d.exists() else []:
        doc = read_yaml(p)
        st = "approved" if approval_record(doc["id"], root) else doc.get("status", "pending")
        if doc.get("status") == "rejected":
            st = "rejected"
        doc["status"] = st
        if status is None or st == status:
            out.append(doc)
    return out


def describe(doc: dict) -> str:
    """승인 화면에 보여 줄 요약."""
    lines = [f"proposal {doc['id']}  (class {doc['class']}, {doc['kind']})", f"  title: {doc.get('title')}",
             f"  evidence: {doc.get('evidence')}"]
    if doc.get("provisional"):
        lines.append("  ** PROVISIONAL: sample coverage below the 'final' threshold **")
    if doc.get("applies_from"):
        lines.append(f"  applies_from: {doc['applies_from']}")
    for c in doc.get("changes") or []:
        lines.append(f"  - {c['target']}: {json.dumps(c.get('from'), ensure_ascii=False)} → "
                     f"{json.dumps(c.get('to'), ensure_ascii=False)}  [{c.get('direction', '')}]")
    for o in doc.get("options") or []:
        lines.append(f"  option {o['label']}: {json.dumps(o['changes'], ensure_ascii=False)}")
    return "\n".join(lines)


def tty_confirm(expected: str, what: str) -> None:
    import sys
    if not sys.stdin.isatty():
        raise ConfirmationError("refused: this command must be run from an interactive terminal by a human")
    print(f"[HUMAN-ONLY] {what}")
    typed = input(f"Type '{expected}' to confirm: ").strip()
    if typed != expected:
        raise ConfirmationError("confirmation did not match; aborted")


def approve(pid: str, by: str, root=None, choice: str | None = None,
            confirm: Callable[[str, str], None] = tty_confirm, note: str = "") -> dict:
    """사람 승인. confirm(expected, what) 이 예외 없이 끝나야 진행 (기본: 대화형 타이핑)."""
    root = repo_root(root)
    doc = load_proposal(pid, root)
    if approval_record(pid, root):
        raise ProposalError(f"{pid} already approved")
    if doc.get("status") == "rejected":
        raise ProposalError(f"{pid} was rejected")
    if doc.get("content_hash") and content_hash(doc) != doc["content_hash"]:
        raise ProposalError(f"{pid}: proposal file was modified after creation (hash mismatch); regenerate it")
    changes = _changes_for(doc, choice)
    _check_not_stale(doc, changes, root)
    confirm(pid, describe(doc) + (f"\n  choice: {choice}" if choice else ""))
    applied = _apply(doc, changes, by, root)
    rec = {"id": pid, "approved_by": by, "ts": utcnow(), "choice": choice, "note": note,
           "content_hash": doc.get("content_hash"), "applied": applied}
    d = approvals_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return rec


def reject(pid: str, by: str, reason: str, root=None,
           confirm: Callable[[str, str], None] = tty_confirm) -> dict:
    root = repo_root(root)
    doc = load_proposal(pid, root)
    if approval_record(pid, root):
        raise ProposalError(f"{pid} already approved")
    confirm(pid, f"Reject {pid}: {reason}")
    rec = {"id": pid, "rejected_by": by, "ts": utcnow(), "reason": reason, "content_hash": doc.get("content_hash")}
    d = approvals_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.rejected.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    doc["status"] = "rejected"
    write_yaml(proposals_dir(root) / f"{pid}.yaml", doc)
    return rec


# ------------------------------------------------------------------ 내부
def _changes_for(doc: dict, choice: str | None) -> list[dict]:
    if doc["kind"] == "preference":
        if not choice:
            raise ProposalError("preference proposal: pass --choice <label> (one of "
                                f"{[o['label'] for o in doc['options']]})")
        for o in doc["options"]:
            if str(o["label"]) == str(choice):
                return [{"target": f"param:{k}", "key": k, "to": v} for k, v in o["changes"].items()]
        raise ProposalError(f"unknown choice {choice!r}")
    if choice:
        raise ProposalError("--choice only applies to preference proposals")
    return list(doc["changes"])


def _check_not_stale(doc: dict, changes: list[dict], root: Path) -> None:
    from .costs import cost_model
    from .registry import load_registry
    if doc["kind"] == "cost_models":
        for c in changes:
            cur = cost_model(c["name"], root=root)
            f = c["from"]
            if (abs(cur.fee_rate - f["fee_rate"]) > 1e-12 or abs(cur.spread_bps - f["spread_bps"]) > 1e-9
                    or abs(cur.slip_bps - f["slip_bps"]) > 1e-9):
                raise ProposalError(f"stale proposal: {c['name']} changed since the proposal was made "
                                    f"(now {cur}); re-run calibrate")
    else:
        reg = load_registry(root, validate_=False)
        for c in changes:
            p = reg.get(c["key"])
            if "from" in c and _eq(reg.resolve(p), c["from"]) is False:
                raise ProposalError(f"stale proposal: {c['key']} is now {reg.resolve(p)!r}, "
                                    f"proposal assumed {c['from']!r}")


def _eq(a: Any, b: Any) -> bool:
    return yaml.safe_dump(a) == yaml.safe_dump(b)


def _apply(doc: dict, changes: list[dict], by: str, root: Path) -> list[dict]:
    from btc_quant.backtest.types import CostModel

    from .costs import add_versions
    from .registry import next_campaign_seq, record_change
    out = []
    if doc["kind"] == "cost_models":
        items = []
        for c in changes:
            t = c["to"]
            items.append({"name": c["name"], "model": CostModel(c["name"], t["fee_rate"], t["spread_bps"],
                                                                 t["slip_bps"], t.get("tick", 0.0)),
                          "direction": c.get("direction", "looser"), "applied_by": f"human:{by}",
                          "source": doc.get("title", ""), "evidence": doc.get("evidence"), "proposal_id": doc["id"],
                          "extra": {"provisional": bool(doc.get("provisional"))}})
        vs = add_versions(items, root=root)
        out = [{"name": n, "version": v} for n, v in vs.items()]
    elif doc["kind"] == "criterion":
        seq = next_campaign_seq(root)
        for c in changes:
            p = record_change(c["key"], c["to"], by=f"human:{by}", proposal_id=doc["id"],
                              reason=doc.get("title", ""), applies_from_campaign=seq, evidence=doc.get("evidence"),
                              root=root)
            out.append({"key": c["key"], "version": p.version, "applies_from_campaign": seq})
    else:  # preference
        for c in changes:
            p = record_change(c["key"], c["to"], by=f"human:{by}", proposal_id=doc["id"],
                              reason=doc.get("title", ""), evidence=doc.get("evidence"), root=root)
            out.append({"key": c["key"], "version": p.version})
    return out
