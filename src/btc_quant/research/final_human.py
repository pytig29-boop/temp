"""결선 — 사람 승인 경로 (사용자 결정 2026-11-12: 페이퍼 판정 전에 잠금 구간을 먼저 연다).

    python -m btc_quant.research.final_human run --candidate <id>_v<n> --round 1

`final_verdict.run` 은 자율 개봉(서류 판정 skeptic·judge·paper 모두 통과, phase final)만 다룬다. 이 경로는
사람이 `admin_cli approve-lockbox` 로 쓴 승인 파일로 연다(`DataAccess.open_lockbox` 가 승인 파일을 검사).
판정식·수치 계산은 `final_verdict` 와 **같은 함수**(dev_val_record, lockbox_daily, complete_record,
certify.final_flags)를 그대로 쓴다. 결과 기록 규칙도 같다: 수치는 사람용 파일
(`research/reports/human/final_round<N>.json`)에만, 공개 사본·원장에는 통과/탈락만.
페이퍼 판정은 최종 승인 조건으로 그대로 남는다(공개 사본의 `paper_required`).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from btc_quant.evaluation import gates as G

from . import certify as K
from . import final_verdict as FV


def run(env, candidate_id: str, round_no: int) -> dict:
    from btc_quant.params.ruleset import autonomy_cfg, require_locked_ruleset
    from btc_quant.registry.authorize import ELIGIBLE_STATES, dossier_verdicts
    sid, _, ver = candidate_id.rpartition("_v")
    version = int(ver)
    if env.registry.state_of(sid, version) not in ELIGIBLE_STATES:
        raise FV.FinalVerdictError(f"{candidate_id} is not validated+")
    v = dossier_verdicts(candidate_id, env.root)
    if (v.get("skeptic"), v.get("judge")) != ("SURVIVES", "PASS"):
        raise FV.FinalVerdictError(f"dossier needs skeptic SURVIVES and judge PASS: {v}")
    ac = autonomy_cfg(env.root)
    rs = require_locked_ruleset(env.root, "final round (human approval)")
    cfg = G.load_gate_config(Path(env.root) / "config" / "gates.yaml")
    fin = {**K.FINAL_DEFAULT, **(cfg.get("final") or {})}
    L = env.ledger.loosenings(since_ts=ac.get("program_start"))
    c, meta = FV.dev_val_record(env, sid, version)
    key = env.data.open_lockbox(candidate_id, c["logic_hash"], note=f"final round {round_no} (human approval)")
    dl = FV.lockbox_daily(env, meta, key)
    c = FV.complete_record(c, meta, dl)
    flags = K.final_flags(c, meta["z_dev"], fin, L)
    env.ledger.record_lockbox_result(key.opening_id, {"passed": bool(flags["final"]), "round": round_no,
                                                     "ruleset": rs, "approval": "human"})
    out = {"round": round_no, "ruleset": rs, "L": L, "final_rules": fin, "approval": "human",
           "candidates": [{"candidate_id": candidate_id, "cluster": 1, "flags": flags, "record": c,
                           "profile": meta["profile"], "dev_conservative_sharpe": meta["dev_conservative_sharpe"]}]}
    human = Path(env.root) / "research" / "reports" / "human" / f"final_round{round_no}.json"
    human.parent.mkdir(parents=True, exist_ok=True)
    human.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    pub = {"round": round_no, "ruleset": rs, "approval": "human", "paper_required": True,
           "candidates": [{"candidate_id": candidate_id, "final": bool(flags["final"])}]}
    pp = Path(env.root) / "research" / "final" / f"round{round_no}_public.json"
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(json.dumps(pub, ensure_ascii=False, indent=2), encoding="utf-8")
    return pub


def main(argv=None) -> int:
    from .campaign import ResearchEnv
    ap = argparse.ArgumentParser(prog="btc_quant.research.final_human")
    ap.add_argument("--root", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--candidate", required=True)
    r.add_argument("--round", type=int, required=True)
    a = ap.parse_args(argv)
    env = ResearchEnv.at(a.root)
    print(json.dumps(run(env, a.candidate, a.round), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
