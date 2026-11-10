"""managed_hold 관문 프로필 합성 보정 (Phase 5, 사람 승인 대기).

시장: 3코인(BTC/XRP/ETH, 공통 요인 상관 ≈ 0.6) GARCH·t 1분봉 위에 **공통 국면 표류**를 얹는다. 국면은 상승(+μ/일)·하락(−μ/일)이
번갈아 오고 길이는 기하분포(평균 L일).
  - 귀무(추세 필터가 무의미): L = 1일 (같은 크기의 표류가 매일 무작위로 바뀜 — 15~30일 EMA 가 타이밍할 수 없음), μ ∈ {0, 0.3%}
  - 심은 추세 지속: L ∈ {45, 90}일, μ = 0.3%/일 (하락 국면을 피하면 B&H 보다 낫다)
전략 격자 = 준비된 템플릿 `research/campaign_templates/managed_hold_4h_u3.yaml` (span 90/180 × band 0/100, 4 스펙) 그대로.
각 시장: Dev/Val 실행(빠른 엔진, MTM, 자산별 비용) → Dev 순 능동 Sharpe 최고 스펙 선택 → `managed_hold_gates.evaluate`.

    python -m btc_quant.research.managed_hold_study run --raw DIR --jobs 3 [--n 40]
    python -m btc_quant.research.managed_hold_study analyze --raw DIR --out summary.json --tables tables.md
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from btc_quant.evaluation import metrics as M
from btc_quant.evaluation import montecarlo

from . import calibration_study as C
from . import gate_review_study as GR
from . import managed_hold_gates as MG

TEMPLATE = Path(__file__).resolve().parents[3] / "research" / "campaign_templates" / "managed_hold_4h_u3.yaml"
SCENARIOS = {  # 이름: (종류, 평균 국면 길이 L일, 일 표류 μ)
    "null_flat": ("null", 1, 0.0),
    "null_switch": ("null", 1, 0.003),
    "trend_L45": ("planted", 45, 0.003),
    "trend_L90": ("planted", 90, 0.003),
}
SEED0 = {"null_flat": 81_000, "null_switch": 82_000, "trend_L45": 83_000, "trend_L90": 84_000}


def regime_drift(days: pd.DatetimeIndex, mean_len: float, mu: float, seed: int) -> pd.Series:
    """UTC 일별 표류 (+μ / −μ 국면, 길이 ~ 기하(평균 mean_len), 첫 국면 무작위)."""
    rng = np.random.default_rng(seed)
    out = np.empty(len(days))
    sign = 1.0 if rng.random() < 0.5 else -1.0
    i = 0
    while i < len(days):
        n = int(rng.geometric(1.0 / max(mean_len, 1.0)))
        out[i:i + n] = sign * mu
        sign = -sign
        i += n
    return pd.Series(out, index=days)


def gen_markets(lay: C.Layout, seed: int, mean_len: float, mu: float) -> dict[str, pd.DataFrame]:
    base = GR.gen_universe_markets(lay.data_start, lay.val_end, seed, 0.0)
    idx = next(iter(base.values())).index
    days = pd.date_range(idx[0].floor("D"), idx[-1].floor("D"), freq="D")
    dr = regime_drift(days, mean_len, mu, seed + 7)
    per_min = dr.reindex(idx.floor("D")).to_numpy(float) / 1440.0
    mult = np.exp(np.cumsum(per_min))
    out = {}
    for s, df in base.items():
        d = df.copy()
        for c in ("open", "high", "low", "close"):
            d[c] = d[c].to_numpy(float) * mult
        out[s] = d
    return out


def _specs():
    from .campaign import CampaignTemplate
    tpl = CampaignTemplate.from_dict(yaml.safe_load(TEMPLATE.read_text(encoding="utf-8")))
    specs = tpl.enumerate()
    keys = {}
    for sp in specs:
        p = sp.entry.params
        keys[sp.logic_hash()] = (int(p["span"]), float(p["band_bp"]))
    return specs, keys


def _neighbors(keys: dict, lh: str) -> list[str]:
    a = keys[lh]
    return [k for k, b in keys.items() if k != lh and sum(x != y for x, y in zip(a, b)) == 1]


def _segment(spec, m1s, costs, s, e, closes):
    from btc_quant.backtest import fast

    from . import universe as U
    sl = {sym: fast.run_fast(spec, m1s[sym], costs[sym], s, e, accounting="mtm") for sym in m1s}
    res = U.portfolio_result(sl)
    days = res.daily_returns.index
    bh = U.buy_hold_universe(closes, days).reindex(days).fillna(0.0)
    a = (res.daily_returns - bh).astype(float)
    expo = {sym: MG.daily_exposure(r.trades, days).to_numpy(float) for sym, r in sl.items()}
    aret = {}
    for sym in m1s:
        dc = closes[sym].resample("1D").last().reindex(days).ffill()
        prev = closes[sym].resample("1D").last().shift(1).reindex(days).ffill()
        aret[sym] = (dc / prev - 1).fillna(0.0).to_numpy(float)
    tr = U.portfolio_trade_returns(res)
    mc = montecarlo.worst_case_mdd95(tr, n_sims=2000) if len(tr) > 1 else 0.0
    return {"active": a, "expo": expo, "asset_ret": aret, "bh": bh.to_numpy(float),
            "n_switches": int(sum(len(r.trades) for r in sl.values())),
            "mdd": float(M.max_drawdown(res.daily_returns)[0]), "bh_mdd": float(M.max_drawdown(bh)[0]),
            "mc_mdd95": float(mc), "total": float((1 + res.daily_returns).prod() - 1),
            "bh_total": float((1 + bh).prod() - 1)}


def run_task(task: dict) -> dict:
    from .campaign import get_sleeve_cost
    lay = C.Layout(**task["layout"])
    kind, L, mu = SCENARIOS[task["scenario"]]
    mk = gen_markets(lay, task["seed"], L, mu)
    m1s = {s: C._engine_m1(df) for s, df in mk.items()}
    closes = {s: m["close"] for s, m in m1s.items()}
    costs = {s: get_sleeve_cost("coinone_conservative", s) for s in m1s}
    specs, keys = _specs()
    ds, de = pd.Timestamp(lay.dev_start, tz="UTC"), pd.Timestamp(lay.val_start, tz="UTC")
    vs, ve = de, pd.Timestamp(lay.val_end, tz="UTC")
    dev = {sp.logic_hash(): _segment(sp, m1s, costs, ds, de, closes) for sp in specs}
    srs = {k: MG.active_sharpe(v["active"]) for k, v in dev.items()}
    sr_obs = {k: float(np.mean(v["active"]) / np.std(v["active"])) if np.std(v["active"]) > 0 else 0.0
              for k, v in dev.items()}
    sel = max(srs, key=srs.get)
    spec = next(sp for sp in specs if sp.logic_hash() == sel)
    val = _segment(spec, m1s, costs, vs, ve, closes)
    ev = MG.evaluate(dev[sel], val, srs, _neighbors(keys, sel), sel, float(np.var(list(sr_obs.values()))),
                     len(specs), seed=task["seed"])
    return {"id": task["id"], "scenario": task["scenario"], "kind": kind, "seed": task["seed"], "L": L, "mu": mu,
            "selected": list(keys[sel]), "dev_active_sharpe": srs[sel],
            "dev_mdd": dev[sel]["mdd"], "dev_bh_mdd": dev[sel]["bh_mdd"], "dev_total": dev[sel]["total"],
            "dev_bh_total": dev[sel]["bh_total"], "val_mdd": val["mdd"], "val_bh_mdd": val["bh_mdd"],
            "result": json.loads(json.dumps(ev, default=float))}


def _run_save(task, raw):
    fp = Path(raw) / f"{task['id']}.json"
    if fp.exists():
        return task["id"], 0.0, None
    t0 = time.time()
    try:
        fp.write_text(json.dumps(run_task(task), ensure_ascii=False))
        return task["id"], time.time() - t0, None
    except Exception as e:  # noqa: BLE001
        import traceback
        (Path(raw) / "_errors").mkdir(exist_ok=True)
        (Path(raw) / "_errors" / f"{task['id']}.txt").write_text(traceback.format_exc())
        return task["id"], time.time() - t0, f"{type(e).__name__}: {e}"


def analyze(recs: list[dict]) -> dict:
    out = {}
    for sc in SCENARIOS:
        rs = [r for r in recs if r["scenario"] == sc]
        n = len(rs)
        ff: dict = {}
        for r in rs:
            k = r["result"]["first_failed"] or "validated"
            ff[k] = ff.get(k, 0) + 1
        out[sc] = {"n": n, "validated": sum(r["result"]["validated"] for r in rs),
                   "dev_passed": sum(r["result"]["dev_passed"] for r in rs), "first_failed": ff,
                   "median_dev_active_sharpe": float(np.median([r["dev_active_sharpe"] for r in rs])) if rs else None,
                   "median_val_mdd_ratio": float(np.median([abs(r["val_mdd"]) / abs(r["val_bh_mdd"]) for r in rs
                                                            if r["val_bh_mdd"]])) if rs else None}
    nulls = [r for r in recs if r["kind"] == "null"]
    out["fpr_all_nulls"] = {"x": sum(r["result"]["validated"] for r in nulls), "n": len(nulls)}
    return out


def tables_markdown(res: dict) -> str:
    lines = ["| 시나리오 | 시장 | Dev 통과 | 검증 통과 | 첫 실패 분포 | Dev 능동 Sharpe 중앙값 | Val MDD / B&H MDD 중앙값 |",
             "|---|---|---|---|---|---|---|"]
    for sc in SCENARIOS:
        r = res[sc]
        lines.append(f"| {sc} | {r['n']} | {r['dev_passed']} | {r['validated']} | "
                     + ", ".join(f"{k} {v}" for k, v in sorted(r["first_failed"].items()))
                     + f" | {r['median_dev_active_sharpe']:.2f} | {r['median_val_mdd_ratio']:.2f} |")
    f = res["fpr_all_nulls"]
    lines.append(f"\n전체 귀무 오탐: {f['x']}/{f['n']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--raw", required=True)
    r.add_argument("--jobs", type=int, default=3)
    r.add_argument("--n", type=int, default=40)
    a2 = sub.add_parser("analyze")
    a2.add_argument("--raw", required=True)
    a2.add_argument("--out", required=True)
    a2.add_argument("--tables", default="")
    a = ap.parse_args(argv)
    raw = Path(a.raw)
    if a.cmd == "run":
        raw.mkdir(parents=True, exist_ok=True)
        lay = asdict(C.FULL_LAYOUT)
        tasks = [{"id": f"mh_{sc}_{SEED0[sc] + i}", "scenario": sc, "seed": SEED0[sc] + i, "layout": lay}
                 for i in range(a.n) for sc in SCENARIOS]
        todo = [t for t in tasks if not (raw / f"{t['id']}.json").exists()]
        print(f"[mh] {len(tasks)} tasks, {len(todo)} to run", flush=True)
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=a.jobs, mp_context=get_context("spawn")) as pool:
            for i, f in enumerate(as_completed([pool.submit(_run_save, t, str(raw)) for t in todo]), 1):
                tid, sec, err = f.result()
                print(f"[mh] {i}/{len(todo)} {tid} {sec:.0f}s elapsed {time.time() - t0:.0f}s"
                      + (f" ERROR {err}" if err else ""), flush=True)
        print("[mh] finished", flush=True)
        return 0
    recs = [json.loads(p.read_text()) for p in sorted(raw.glob("mh_*.json"))]
    res = analyze(recs)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    t = tables_markdown(res)
    if a.tables:
        Path(a.tables).write_text(t)
    print(t)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
