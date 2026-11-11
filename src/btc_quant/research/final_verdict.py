"""결선 — 잠금 구간 최종 판정 F1~F5 (PLAN D27, autonomous_research_plan §5.2·§5.3).

    python -m btc_quant.research.final_verdict pool             # 결선 후보 풀(군집 대표·순위) — 잠금 미사용
    python -m btc_quant.research.final_verdict run --round 1    # phase = final 에서만: 한 묶음으로 열어 판정

판정식은 합성 인증과 **같은 함수**(`certify.final_flags`)를 쓴다. 잠금 구간 수치는 사람용 파일
(`research/reports/human/final_round<N>.json`)에만 남고, 공개 사본(`research/final/round<N>_public.json`)과 원장 결과
코드에는 통과/탈락만 남는다.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from btc_quant.evaluation import gates as G
from btc_quant.evaluation import inference
from btc_quant.evaluation import metrics as M

from . import certify as K
from . import universe as U

LOCKBOX_WARMUP_DAYS = 14
ANN = 365.0


class FinalVerdictError(RuntimeError):
    pass


def conservative_sharpe(daily: pd.Series, sr0_obs: float = 0.0) -> float:
    """보수적 Sharpe (연율) = SR̂ − 1.645·SE(SR̂) − SR₀ (관측 단위 SE 는 왜도·첨도 보정 — Mertens)."""
    x = np.asarray(pd.Series(daily).dropna(), float)
    n = len(x)
    if n < 30 or x.std() == 0:
        return float("-inf")
    sr = x.mean() / x.std()
    from scipy import stats
    sk, ku = float(stats.skew(x)), float(stats.kurtosis(x, fisher=False))
    se = math.sqrt(max(1 - sk * sr + (ku - 1) / 4 * sr * sr, 1e-12) / (n - 1))
    return float((sr - 1.645 * se - sr0_obs) * math.sqrt(ANN))


def _ctx(env, sid: str, version: int) -> dict:
    from .forward import _context
    return _context(env, sid, version)


def candidate_id_of(sid: str, version: int) -> str:
    from btc_quant.registry.strategies import key_of
    return key_of(sid, version)


def dev_val_record(env, sid: str, version: int, L_dev: int = 0) -> tuple[dict, dict]:
    """개발·검증 구간 몫(잠금 미사용): 후보 레코드 c(certify 형식 일부)와 맥락."""
    from .campaign import load_campaign
    from .pipeline import Pipeline
    ctx = _ctx(env, sid, version)
    cr = load_campaign(ctx["campaign_id"], env=env)
    spec = env.registry.load(sid, version)
    lh = spec.logic_hash()
    p = Pipeline(cr, env, register=False, lessons=False, val_budget=False, robustness=False, reference_check=False)
    stats = p.campaign_stats()
    g5 = p.g5(lh, stats)
    dd = cr.returns[lh]
    m1v, vs, ve = p.load_val()
    g6, vres, _vm, _mc = p.g6(spec, m1v, vs, ve, cr.template.costs["primary"], cr.template.costs["stress"],
                              record=False, budget=False, dev_g5=g5)
    dv = vres.daily_returns
    mh = p.mh_cfg is not None
    if mh:                               # managed_hold 프로필: 보유 대비 능동 수익으로 같은 식
        dd = dd - p.dev_benchmark(dd.index).reindex(dd.index).fillna(0.0)
        dv = dv - p.val_benchmark(m1v, dv.index, vs).reindex(dv.index).fillna(0.0)
    bt = inference.bootstrap_mean_test(dv.to_numpy(float), n_boot=2000, seed=0)
    c = {"logic_hash": lh, "val_sharpe": float(M.sharpe(dv)), "val_p": float(bt["p_value"]),
         "dev_max_dd": float(M.max_drawdown(cr.returns[lh])[0]), "val_mdd": float(M.max_drawdown(vres.daily_returns)[0]),
         "dev_worst_month": K._monthly_worst(cr.returns[lh]), "val_worst_month": K._monthly_worst(vres.daily_returns)}
    dres = p.dev_result(spec, cr.template.costs["primary"])
    bh_dev = p.dev_benchmark(cr.returns[lh].index).reindex(cr.returns[lh].index).fillna(0.0)
    bh_val = p.val_benchmark(m1v, vres.daily_returns.index, vs).reindex(vres.daily_returns.index).fillna(0.0)
    meta = {"pipeline": p, "cr": cr, "spec": spec, "g5": dict(g5), "z_dev": G.dsr_z(g5), "dev_daily": dd,
            "dev_raw_daily": cr.returns[lh], "val_raw_daily": vres.daily_returns, "bh_dev": bh_dev, "bh_val": bh_val,
            "trade_returns": [U.portfolio_trade_returns(dres), U.portfolio_trade_returns(vres)],
            "val_daily": dv, "profile": "managed_hold" if mh else "default", "campaign_id": ctx["campaign_id"],
            "dev_conservative_sharpe": conservative_sharpe(dd, float(g5.get("sr0") or 0.0))}
    return c, meta


def pool(env, max_n: int) -> list[dict]:
    """결선 후보 풀: 서류 판정 3개 통과 + validated 이상, 개발 구간 상관 군집(절단 0.5)별 보수적 Sharpe 1위, 순위순 max_n."""
    from btc_quant.evaluation.selection import correlation_clusters
    from btc_quant.registry.authorize import ELIGIBLE_STATES, dossier_verdicts
    rows = []
    for st in ELIGIBLE_STATES:
        for x in env.registry.list(st):
            cid = candidate_id_of(x["id"], x["version"])
            v = dossier_verdicts(cid, env.root)
            if (v.get("skeptic"), v.get("judge"), v.get("paper")) != ("SURVIVES", "PASS", "PASS"):
                continue
            c, meta = dev_val_record(env, x["id"], x["version"])
            rows.append({"candidate_id": cid, "id": x["id"], "version": x["version"], "c": c, "meta": meta})
    if not rows:
        return []
    R = pd.concat([r["meta"]["dev_daily"].rename(r["candidate_id"]) for r in rows], axis=1).fillna(0.0)
    labels = correlation_clusters(R.to_numpy(), 0.5) if len(rows) > 1 else np.array([1])
    best: dict = {}
    for r, lab in zip(rows, labels):
        r["cluster"] = int(lab)
        cur = best.get(lab)
        if cur is None or r["meta"]["dev_conservative_sharpe"] > cur["meta"]["dev_conservative_sharpe"]:
            best[lab] = r
    out = sorted(best.values(), key=lambda r: -r["meta"]["dev_conservative_sharpe"])
    return out[:max_n]


def lockbox_daily(env, meta: dict, key) -> pd.Series:
    from btc_quant.backtest import fast
    from .campaign import get_sleeve_cost, prepare_m1
    cr, spec = meta["cr"], meta["spec"]
    tpl = cr.template
    ls, le = env.data.segment_bounds("lockbox")
    w0 = ls - pd.Timedelta(days=LOCKBOX_WARMUP_DAYS)
    syms = list(tpl.universe)
    sl = {}
    closes = {}
    for sym in syms:
        raw = env.data.read_range(w0, le, tpl.exchange, sym, tpl.timeframe, key=key)
        m1 = prepare_m1(raw, tpl.exchange)
        closes[sym] = m1["close"]
        sl[sym] = fast.run_fast(spec, m1, get_sleeve_cost(tpl.costs["primary"], sym), ls, le,
                                accounting=meta["pipeline"].accounting)
    res = U.portfolio_result(sl)
    d = res.daily_returns
    meta["lb_raw_daily"] = d
    meta["lb_trades"] = len(res.trades)
    meta["trade_returns"] = list(meta["trade_returns"]) + [U.portfolio_trade_returns(res)]
    first = {}
    for sym in syms:                    # 기준가 = 잠금 시작 직전 종가 (Val B&H 와 같은 규칙)
        c0 = closes[sym]
        before = c0[c0.index < ls]
        first[sym] = float(before.iloc[-1]) if len(before) else None
    meta["bh_lb"] = U.buy_hold_universe(closes, d.index, first).reindex(d.index).fillna(0.0)
    if meta["profile"] == "managed_hold":
        d = d - meta["bh_lb"]
    return d


def complete_record(c: dict, meta: dict, dl: pd.Series) -> dict:
    from btc_quant.evaluation import montecarlo
    dv, dd = meta["val_daily"], meta["dev_daily"]
    raw_full = pd.concat([meta["dev_raw_daily"], meta["val_raw_daily"], meta["lb_raw_daily"]])
    bh_full = pd.concat([meta["bh_dev"], meta["bh_val"], meta["bh_lb"]])
    bt = inference.bootstrap_mean_test(dl.to_numpy(float), n_boot=2000, seed=0)
    bo = inference.bootstrap_mean_test(pd.concat([dv, dl]).to_numpy(float), n_boot=2000, seed=0)
    tr = np.concatenate([np.asarray(t, float) for t in meta["trade_returns"]])
    mc = montecarlo.worst_case_mdd95(tr, n_sims=5000) if len(tr) > 1 else 0.0
    c = dict(c)
    c.update(lb_sharpe=float(M.sharpe(dl)), lb_p=float(bt["p_value"]), oos_p=float(bo["p_value"]),
             lb_mdd=float(M.max_drawdown(meta["lb_raw_daily"])[0]), lb_worst_month=K._monthly_worst(meta["lb_raw_daily"]),
             full_mc_mdd95=float(mc), full_sharpe=float(M.sharpe(raw_full)), full_calmar=K._calmar(raw_full),
             full_bh_sharpe=float(M.sharpe(bh_full)), full_bh_calmar=K._calmar(bh_full))
    return c


def run(env, round_no: int, fin: dict | None = None, L: int | None = None) -> dict:
    from btc_quant.params.ruleset import autonomy_cfg, require_locked_ruleset
    from btc_quant.registry.authorize import auto_openings, lockbox_authorization
    ac = autonomy_cfg(env.root)
    if ac.get("phase") != "final":
        raise FinalVerdictError("final round only in phase 'final'")
    rs = require_locked_ruleset(env.root, "final round")
    cfg = G.load_gate_config(Path(env.root) / "config" / "gates.yaml")
    fin = fin or {**K.FINAL_DEFAULT, **(cfg.get("final") or {})}
    if L is None:
        L = env.ledger.loosenings(since_ts=ac.get("program_start"))
    budget = int((ac.get("verdict_budget") or {}).get("lockbox_openings", 5))
    left = budget - auto_openings(env.root)
    if left <= 0:
        raise FinalVerdictError("lockbox budget used")
    cands = pool(env, left)
    out = {"round": round_no, "ruleset": rs, "L": L, "final_rules": fin, "candidates": []}
    for r in cands:
        cid = r["candidate_id"]
        lockbox_authorization(cid, r["c"]["logic_hash"], r["id"], r["version"], env.root)
        key = env.data.open_lockbox(cid, r["c"]["logic_hash"], note=f"final round {round_no}")
        dl = lockbox_daily(env, r["meta"], key)
        c = complete_record(r["c"], r["meta"], dl)
        flags = K.final_flags(c, r["meta"]["z_dev"], fin, L)
        env.ledger.record_lockbox_result(key.opening_id, {"passed": bool(flags["final"]), "round": round_no,
                                                         "ruleset": rs})
        out["candidates"].append({"candidate_id": cid, "cluster": r["cluster"], "flags": flags, "record": c,
                                  "profile": r["meta"]["profile"],
                                  "dev_conservative_sharpe": r["meta"]["dev_conservative_sharpe"]})
    human = Path(env.root) / "research" / "reports" / "human" / f"final_round{round_no}.json"
    human.parent.mkdir(parents=True, exist_ok=True)
    human.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    pub = {"round": round_no, "ruleset": rs, "candidates": [{"candidate_id": x["candidate_id"],
                                                             "final": bool(x["flags"]["final"])} for x in out["candidates"]]}
    pp = Path(env.root) / "research" / "final" / f"round{round_no}_public.json"
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(json.dumps(pub, ensure_ascii=False, indent=2), encoding="utf-8")
    return pub


def main(argv=None) -> int:
    from .campaign import ResearchEnv
    ap = argparse.ArgumentParser(prog="btc_quant.research.final_verdict")
    ap.add_argument("--root", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pool")
    r = sub.add_parser("run")
    r.add_argument("--round", type=int, required=True)
    a = ap.parse_args(argv)
    env = ResearchEnv.at(a.root)
    if a.cmd == "pool":
        print(json.dumps([{"candidate_id": r["candidate_id"], "cluster": r["cluster"],
                           "dev_conservative_sharpe": r["meta"]["dev_conservative_sharpe"]} for r in pool(env, 5)],
                         indent=2, default=float))
        return 0
    print(json.dumps(run(env, a.round), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
