"""R4 트랙 F-1 보고용 계산 (docs/prompts/opus_r4_plan.md 단계 4). 원장에 쓰지 않는 순수 계산, 개발 구간만.

기존 T1 후보(camp-000034 r1b_vol_breakout_donch_4h_u3_half_0dce65e919) Dev MTM 일간 수익 r_d 에
w_d = min(1, σ*/σ_{d-1}) 를 곱한다. σ_{d-1} = 전일까지 30일 동일 비중 3코인 보유 일간 수익 표준편차,
σ* = 개발 구간 첫 365일의 σ 중앙값(고정). 첫 365일은 비교에서 제외. 대조: 평균 w 와 같은 고정 배수.
사전 규칙: Calmar(w) > Calmar(원래) AND > Calmar(고정) AND Sharpe(w) ≥ 0.95 × Sharpe(원래) → F-2.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from btc_quant.evaluation import metrics as M
from btc_quant.registry.splits import DataAccess
from btc_quant.research.campaign import ResearchEnv, load_campaign

CAMP, LH = "camp-000034", None
SID = "r1b_vol_breakout_donch_4h_u3_half_0dce65e919"


def stats(r: pd.Series) -> dict:
    eq = (1 + r).cumprod()
    mdd = float((eq / eq.cummax() - 1).min())
    yrs = len(r) / 365.0
    cagr = float(eq.iloc[-1] ** (1 / yrs) - 1)
    mon = (1 + r).groupby(r.index.to_period("M")).prod() - 1
    return {"sharpe": float(M.sharpe(r)), "cagr": cagr, "max_dd": mdd, "calmar": cagr / abs(mdd) if mdd < 0 else None,
            "worst_month": float(mon.min()), "days": int(len(r))}


def main() -> dict:
    env = ResearchEnv.at()
    cr = load_campaign(CAMP, env=env)
    spec = env.registry.load(SID, 1)
    r = cr.returns[spec.logic_hash()].copy()
    r.index = pd.DatetimeIndex(r.index)
    da = DataAccess()
    closes = {}
    for sym in ("BTC-KRW", "XRP-KRW", "ETH-KRW"):
        m1 = da.read("dev", "coinone", sym, "1m")
        c = m1["close"].where(m1["close"] > 0).ffill()
        closes[sym] = c.resample("1D").last()
    bh = pd.DataFrame(closes).pct_change().mean(axis=1)
    bh.index = bh.index.tz_convert(r.index.tz) if bh.index.tz is not None and r.index.tz is not None else bh.index
    bh = bh.reindex(r.index)
    sig = bh.rolling(30, min_periods=30).std().shift(1)          # 전일까지
    first = r.index[0] + pd.Timedelta(days=365)
    s_star = float(sig[r.index < first].median())
    w = np.minimum(1.0, s_star / sig).fillna(1.0)
    keep = r.index >= first
    r0, rw = r[keep], (w * r)[keep]
    wbar = float(w[keep].mean())
    rc = wbar * r0
    out = {"sigma_star": s_star, "mean_w": wbar, "original": stats(r0), "vol_managed": stats(rw), "fixed_scale": stats(rc)}
    o, v, f = out["original"], out["vol_managed"], out["fixed_scale"]
    out["rule"] = {"calmar_gt_original": v["calmar"] > o["calmar"], "calmar_gt_fixed": v["calmar"] > f["calmar"],
                   "sharpe_ge_95pct": v["sharpe"] >= 0.95 * o["sharpe"]}
    out["go_F2"] = all(out["rule"].values())
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=1, default=float))
    sys.exit(0)
