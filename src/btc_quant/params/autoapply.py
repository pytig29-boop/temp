"""기준 변경 자동 적용 (autonomous_research_plan §4-B, PLAN D30) — class ② '사람 승인'을 대체.

    python -m btc_quant.params auto-apply <제안 id> --cert <cert id> [--weekly-review]

조건 (하나라도 어긋나면 거부, 아무것도 쓰지 않음):
1. 인증 기록 `research/certifications/<cert>/cert.json` 이 passed, 그 인증의 변경 목록 = 제안의 변경 목록,
   인증 기준(base_ruleset) = 지금의 규칙 묶음 해시 (인증 뒤 다른 것이 바뀌지 않았음).
2. 바닥 규칙(floor): 적용 뒤 설정이 바닥보다 느슨하지 않음.
3. 느슨화(cert.kind ∈ looser·profile_new): 주간 점검(`--weekly-review`) 때만, 프로그램 누적 3회(바닥 max_loosenings) 이하,
   이미 연 구간(검증·잠금)을 읽는 규칙이면 거부(구간 소진 방지 — 실행 계획 §8-4).
적용 = 기존 `proposals._apply`(applies_from_campaign = 다음 캠페인) + 승인 기록(`by = ai:autonomy (<cert>)`) + 원장 criteria_revisions.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .proposals import ProposalError, _changes_for, _check_not_stale, approvals_dir, content_hash, load_proposal

SCOPE_PREFIXES = (("gates.G6", "val"), ("gates.final", "lockbox"), ("gates.G7", "lockbox"), ("gates.G8", "paper"))


class AutoApplyError(RuntimeError):
    pass


def change_scope(key: str) -> str:
    for pre, sc in SCOPE_PREFIXES:
        if key.startswith(pre):
            return sc
    if key.startswith("gates.profiles.") and ".g6_" in key:
        return "val"
    return "dev"


def _root(root=None) -> Path:
    if root is not None:
        return Path(root)
    from btc_quant.registry.ledger import repo_root
    return repo_root()


def load_cert(cert_id: str, root=None) -> dict:
    p = _root(root) / "research" / "certifications" / cert_id / "cert.json"
    if not p.exists():
        raise AutoApplyError(f"certification {cert_id} not found")
    return json.loads(p.read_text(encoding="utf-8"))


def _norm(changes) -> list:
    return sorted([[c["key"], c.get("to")] for c in changes], key=lambda x: x[0])


def _simulated_gate_cfg(changes, root) -> dict:
    from btc_quant.evaluation.gates import load_gate_config
    from .registry import get_path
    cfg = copy.deepcopy(load_gate_config(_root(root) / "config" / "gates.yaml"))
    for c in changes:
        k = c["key"]
        if not k.startswith("gates.") and not k.startswith("robustness."):
            continue
        dotted = k[len("gates."):] if k.startswith("gates.") else k
        try:
            get_path(cfg, dotted)
        except KeyError:
            continue
        d = cfg
        parts = dotted.split(".")
        for p in parts[:-1]:
            d = d[p]
        d[parts[-1]] = c.get("to")
    return cfg


def auto_apply(pid: str, cert_id: str, root=None, weekly_review: bool = False) -> dict:
    from btc_quant.registry.ledger import Ledger
    from .floor import check_gate_floor, load_floor
    from .proposals import _apply
    from .ruleset import autonomy_cfg, ruleset_hash
    r = _root(root)
    doc = load_proposal(pid, r)
    if doc.get("kind") != "criterion":
        raise AutoApplyError("auto-apply handles criterion proposals only (costs: calibrate; preferences: user)")
    if doc.get("content_hash") and content_hash(doc) != doc["content_hash"]:
        raise AutoApplyError("proposal file modified after creation")
    if (approvals_dir(r) / f"{pid}.json").exists():
        raise AutoApplyError(f"{pid} already applied")
    cert = load_cert(cert_id, r)
    if not cert.get("passed"):
        raise AutoApplyError(f"certification {cert_id} did not pass")
    changes = _changes_for(doc, None)
    if _norm(changes) != _norm(cert.get("changes") or []):
        raise AutoApplyError("certification covers different changes than the proposal")
    before = ruleset_hash(r)
    if cert.get("base_ruleset") != before:
        raise AutoApplyError("ruleset changed since certification — re-certify")
    kind = cert.get("kind")
    if kind not in ("stricter", "neutral", "looser", "profile_new", "initial"):
        raise AutoApplyError(f"certification kind {kind!r} unknown")
    ac0 = __import__("btc_quant.params.ruleset", fromlist=["autonomy_cfg"]).autonomy_cfg(r)
    if kind == "initial" and ac0.get("program_start"):
        raise AutoApplyError("'initial' ruleset (pre-program, not counted in L) only before the program starts")
    fl = load_floor(r)
    check_gate_floor(_simulated_gate_cfg(changes, r), fl)
    led = Ledger(r / "research" / "ledger")
    ac = autonomy_cfg(r)
    scopes = sorted({change_scope(c["key"]) for c in changes})
    if kind in ("looser", "profile_new"):
        if not weekly_review:
            raise AutoApplyError("loosenings are applied only at the weekly review (--weekly-review)")
        n = led.loosenings(since_ts=ac.get("program_start"))
        cap = min(int(fl.get("max_loosenings", 3)), int((ac.get("program") or {}).get("max_loosenings", 3)))
        if n >= cap:
            raise AutoApplyError(f"loosening budget used ({n}/{cap})")
        for sc in scopes:
            if sc in ("val", "lockbox"):
                opened = (led.val_uses_since(ac.get("program_start") or "0") if sc == "val"
                          else sum(1 for x in led.records("lockbox") if x.get("event") == "open"))
                if opened:
                    raise AutoApplyError(f"refusing to loosen a {sc}-reading rule after {sc} was opened "
                                         "(would consume the segment)")
    _check_not_stale(doc, changes, r)
    by = f"ai:autonomy ({cert_id})"
    applied = _apply(doc, changes, by, r)
    after = ruleset_hash(r)
    rid = led.record_criteria_revision(kind, [c["key"] for c in changes], {c["key"]: c.get("from") for c in changes},
                                       {c["key"]: c.get("to") for c in changes}, cert_id=cert_id,
                                       ruleset_before=before, ruleset_after=after, reason=doc.get("title", ""),
                                       rescue_table=cert.get("rescue_table"),
                                       scope=scopes[0] if len(scopes) == 1 else "+".join(scopes))
    from btc_quant.registry.ledger import utcnow
    rec = {"id": pid, "approved_by": by, "ts": utcnow(), "choice": None, "note": f"auto-apply ({kind})",
           "content_hash": doc.get("content_hash"), "applied": applied, "criteria_revision": rid}
    d = approvals_dir(r)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return rec


__all__ = ["AutoApplyError", "auto_apply", "change_scope", "load_cert", "ProposalError"]
