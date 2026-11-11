"""잠금 구간 자율 개봉 승인 (PLAN D31, autonomous_research_plan §5.3-3).

사람 승인 파일 대신, 코드가 조건을 검사해 `research/lockbox_approvals/<후보>.yaml` 에 `auto: true` 승인 기록을 만든다.
`DataAccess.open_lockbox` 는 auto 승인이면 **같은 검사를 다시 실행**해(verify_auto) 통과할 때만 연다.

조건: phase = final, 규칙 묶음 잠금 유효(require_locked_ruleset), 후보 서류 `research/dossiers/<후보>/verdicts.json` 의
skeptic SURVIVES·judge PASS·paper PASS, 레지스트리 상태 validated 이상, 자율 개봉 예산(autonomy.yaml verdict_budget.
lockbox_openings, 바닥 상한 이하) 남음, 같은 후보 미개봉.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

ELIGIBLE_STATES = ("validated", "challenger", "review", "active")


class AuthorizationError(PermissionError):
    pass


def _root(root=None) -> Path:
    if root is not None:
        return Path(root)
    from .ledger import repo_root
    return repo_root()


def dossier_verdicts(candidate_id: str, root=None) -> dict:
    p = _root(root) / "research" / "dossiers" / candidate_id / "verdicts.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def auto_openings(root=None) -> int:
    from .ledger import Ledger
    led = Ledger(_root(root) / "research" / "ledger")
    return sum(1 for r in led.records("lockbox") if r.get("event") == "open"
               and (r.get("approval") or {}).get("auto"))


def check_conditions(candidate_id: str, logic_hash: str, strategy_id: str, version: int, root=None) -> dict:
    from btc_quant.params.floor import load_floor
    from btc_quant.params.ruleset import autonomy_cfg, require_locked_ruleset
    from .strategies import StrategyRegistry
    r = _root(root)
    ac = autonomy_cfg(r)
    if ac.get("phase") != "final":
        raise AuthorizationError(f"autonomous lockbox opening only in phase 'final' (now {ac.get('phase')})")
    rs = require_locked_ruleset(r, "lockbox opening")
    if not rs:
        raise AuthorizationError("ruleset lock required for autonomous lockbox opening")
    v = dossier_verdicts(candidate_id, r)
    need = {"skeptic": "SURVIVES", "judge": "PASS", "paper": "PASS"}
    bad = {k: v.get(k) for k, want in need.items() if v.get(k) != want}
    if bad:
        raise AuthorizationError(f"dossier verdicts not all passing: {bad}")
    if v.get("logic_hash") not in (None, logic_hash):
        raise AuthorizationError("dossier logic_hash mismatch")
    st = StrategyRegistry(r).state_of(strategy_id, version)
    if st not in ELIGIBLE_STATES:
        raise AuthorizationError(f"{strategy_id} v{version} is {st}, not validated+")
    fl = load_floor(r)
    cap = min(int((ac.get("verdict_budget") or {}).get("lockbox_openings", 5)),
              int(fl["verdict_budget_max"]["lockbox_openings"]))
    n = auto_openings(r)
    if n >= cap:
        raise AuthorizationError(f"autonomous lockbox budget used ({n}/{cap})")
    return {"ruleset": rs, "verdicts": v, "state": st, "budget": [n, cap]}


def lockbox_authorization(candidate_id: str, logic_hash: str, strategy_id: str, version: int, root=None) -> Path:
    """검사 통과 시 자율 승인 기록 작성 (경로 반환)."""
    from .ledger import utcnow
    chk = check_conditions(candidate_id, logic_hash, strategy_id, version, root)
    d = _root(root) / "research" / "lockbox_approvals"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{candidate_id}.yaml"
    p.write_text(yaml.safe_dump({"candidate_id": candidate_id, "logic_hash": logic_hash, "auto": True,
                                 "strategy_id": strategy_id, "version": int(version),
                                 "approved_by": f"ai:autonomy ({chk['ruleset']})", "approved_at": utcnow(),
                                 "checks": json.loads(json.dumps(chk, default=str))},
                                allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def verify_auto(approval: dict, root=None) -> None:
    """open_lockbox 직전 재검사 (기록이 위조·변조돼도 조건이 실제로 성립해야 연다)."""
    check_conditions(approval["candidate_id"], approval["logic_hash"], approval["strategy_id"],
                     int(approval["version"]), root)
