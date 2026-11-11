"""판정 규칙 묶음(ruleset) 해시·사전 잠금·구간 소진 (autonomous_research_plan §4-C).

규칙 묶음 = 판정 설정(gates.yaml·floor.yaml·autonomy.yaml 의 판정 절·현재 비용 모델 값·splits.yaml) + 판정 코드 파일들.
잠금 = `research/rule_locks/<id>.json` (해시·커밋·시각·인증 id) 를 커밋한 것. Val·잠금 구간·페이퍼 판정·캠페인 등록은
`require_locked_ruleset()` 을 통과해야 한다(autonomy.yaml `ruleset_lock_required: true` 일 때).

    python -m btc_quant.params ruleset hash | status | lock --id rs-001 --cert <cert id>
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import yaml

CODE_GLOBS = ("src/btc_quant/backtest/**/*.py", "src/btc_quant/evaluation/**/*.py")
CODE_FILES = ("src/btc_quant/research/pipeline.py", "src/btc_quant/research/robustness.py",
              "src/btc_quant/research/managed_hold_gates.py", "src/btc_quant/research/final_verdict.py",
              "src/btc_quant/research/paper_verdict.py", "src/btc_quant/research/nulls.py",
              "src/btc_quant/research/campaign.py", "src/btc_quant/registry/ledger.py",
              "src/btc_quant/registry/splits.py", "src/btc_quant/params/floor.py")
CONFIG_FILES = ("config/gates.yaml", "config/floor.yaml", "config/splits.yaml")
AUTONOMY_VERDICT_KEYS = ("verdict_budget", "program")     # autonomy.yaml 중 판정에 영향을 주는 절 (phase 등은 제외)


class RulesetError(RuntimeError):
    pass


def _root(root=None) -> Path:
    if root is not None:
        return Path(root)
    from btc_quant.registry.ledger import repo_root
    return repo_root()


def ruleset_files(root=None) -> list[Path]:
    r = _root(root)
    out = [r / f for f in CONFIG_FILES if (r / f).exists()]
    for g in CODE_GLOBS:
        out += sorted(p for p in r.glob(g) if "__pycache__" not in p.parts)
    out += [r / f for f in CODE_FILES if (r / f).exists()]
    return out


def ruleset_components(root=None) -> dict:
    r = _root(root)
    comp = {str(p.relative_to(r)): hashlib.sha256(p.read_bytes()).hexdigest() for p in ruleset_files(root)}
    ap = r / "config" / "autonomy.yaml"
    if ap.exists():
        doc = yaml.safe_load(ap.read_text(encoding="utf-8")) or {}
        sub = {k: doc.get(k) for k in AUTONOMY_VERDICT_KEYS}
        comp["config/autonomy.yaml#verdict"] = hashlib.sha256(json.dumps(sub, sort_keys=True).encode()).hexdigest()
    try:
        from .costs import all_cost_models, load_costs
        doc = load_costs(r)
        models = {n: [m.fee_rate, m.spread_bps, m.slip_bps] for n, m in sorted(all_cost_models(doc=doc).items())}
        comp["config/costs.yaml#current"] = hashlib.sha256(json.dumps(models, sort_keys=True).encode()).hexdigest()
    except Exception:  # noqa: BLE001 — 비용 파일이 없는 임시 저장소
        pass
    return comp


def ruleset_hash(root=None) -> str:
    return hashlib.sha256(json.dumps(ruleset_components(root), sort_keys=True).encode()).hexdigest()


def locks_dir(root=None) -> Path:
    return _root(root) / "research" / "rule_locks"


def list_locks(root=None) -> list[dict]:
    d = locks_dir(root)
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("rs-*.json"))] if d.exists() else []


def _git(root, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=_root(root), capture_output=True, text=True, timeout=30)


def lock(lock_id: str, cert_id: str | None, root=None, note: str = "") -> Path:
    """현재 규칙 묶음을 잠근다(파일 작성 — 호출 측이 커밋·푸시). 판정 파일에 커밋 안 된 변경이 있으면 거부."""
    if not lock_id.startswith("rs-"):
        raise RulesetError("lock id must look like rs-001")
    p = locks_dir(root) / f"{lock_id}.json"
    if p.exists():
        raise RulesetError(f"{lock_id} already exists")
    dirty = uncommitted_ruleset_changes(root)
    if dirty:
        raise RulesetError(f"commit ruleset files first: {dirty[:5]}")
    from .floor import check_floor
    from btc_quant.evaluation.gates import load_gate_config
    check_floor(load_gate_config(_root(root) / "config" / "gates.yaml"), root)
    head = _git(root, "rev-parse", "HEAD").stdout.strip() or None
    rec = {"id": lock_id, "hash": ruleset_hash(root), "components": ruleset_components(root), "cert_id": cert_id,
           "code_commit": head, "note": note}
    from btc_quant.registry.ledger import utcnow
    rec["ts"] = utcnow()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def uncommitted_ruleset_changes(root=None) -> list[str]:
    r = _root(root)
    rels = [str(p.relative_to(r)) for p in ruleset_files(root)] + ["config/autonomy.yaml", "config/costs.yaml"]
    out = _git(root, "status", "--porcelain", "--", *rels)
    if out.returncode != 0:
        return []
    return [ln[3:] for ln in out.stdout.splitlines() if ln.strip()]


def autonomy_cfg(root=None) -> dict:
    p = _root(root) / "config" / "autonomy.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def maintenance_open(root=None) -> bool:
    p = _root(root) / "research" / "program" / "state.json"
    if not p.exists():
        return False
    return bool((json.loads(p.read_text(encoding="utf-8")).get("maintenance") or {}).get("open"))


def require_locked_ruleset(root=None, purpose: str = "") -> str | None:
    """유효한 잠금 id 반환. autonomy.yaml ruleset_lock_required 가 아니면 None(검사 생략 — 이전 동작).
    확인: 정비 창구 닫힘, 최신 잠금의 해시 = 현재 해시, 잠금 파일이 커밋됨, 판정 파일에 미커밋 변경 없음, 인증 존재."""
    if not autonomy_cfg(root).get("ruleset_lock_required"):
        return None
    if maintenance_open(root):
        raise RulesetError(f"maintenance window open — {purpose} refused")
    locks = list_locks(root)
    if not locks:
        raise RulesetError(f"no ruleset lock — {purpose} refused")
    cur = locks[-1]
    h = ruleset_hash(root)
    if h != cur["hash"]:
        raise RulesetError(f"ruleset hash {h[:12]} != lock {cur['id']} {cur['hash'][:12]} — {purpose} refused "
                           "(open a maintenance window, certify, lock a new ruleset)")
    rel = f"research/rule_locks/{cur['id']}.json"
    if not _git(root, "log", "-1", "--format=%H", "--", rel).stdout.strip():
        raise RulesetError(f"{rel} is not committed")
    dirty = uncommitted_ruleset_changes(root)
    if dirty:
        raise RulesetError(f"uncommitted ruleset changes {dirty[:5]} — {purpose} refused")
    if cur.get("cert_id"):
        cp = _root(root) / "research" / "certifications" / cur["cert_id"] / "cert.json"
        if not cp.exists() or not json.loads(cp.read_text(encoding="utf-8")).get("passed"):
            raise RulesetError(f"certification {cur['cert_id']} missing or not passed")
    return cur["id"]


def segment_consumed(ledger, segment: str, since_ts: str | None = None) -> bool:
    """구간 소진(§4-C): 그 구간을 처음 연 뒤에 그 구간을 읽는 규칙(scope=segment)을 느슨하게 바꿨으면 True."""
    if segment == "val":
        opens = [r["ts"] for r in ledger.records("val_evaluations") if r.get("event") == "open"
                 and (since_ts is None or str(r["ts"]) >= str(since_ts))]
    elif segment == "lockbox":
        opens = [r["ts"] for r in ledger.records("lockbox") if r.get("event") == "open"]
    else:
        return False
    if not opens:
        return False
    first = min(str(t) for t in opens)
    # 같은 초(ts 해상도)는 보수적으로 '연 뒤'로 본다
    return any(r["kind"] in ("looser", "profile_new") and r.get("scope") == segment and str(r["ts"]) >= first
               for r in ledger.criteria_revisions())


def status(root=None) -> dict:
    locks = list_locks(root)
    h = ruleset_hash(root)
    return {"current_hash": h, "latest_lock": locks[-1]["id"] if locks else None,
            "matches_latest": bool(locks and locks[-1]["hash"] == h),
            "lock_required": bool(autonomy_cfg(root).get("ruleset_lock_required")),
            "maintenance_open": maintenance_open(root), "uncommitted": uncommitted_ruleset_changes(root)}
