"""비용 바닥(cost floor) 분석.

질문: "5분봉 기준으로 진입할 때, 거래 비용을 넘는 가격 움직임이 얼마나 자주 나오는가?
      그리고 손익분기를 하려면 무작위 진입 대비 승률을 몇 %p 올려야 하는가?"

규칙
- 결정 시점: 5분봉 종가. 진입: 다음 1분봉 시가(=다음 5분봉 시가). 같은 종가 체결 가정 금지.
- 경로는 1분봉 high/low로 추적. 같은 1분봉에서 TP·SL 모두 닿으면 손절로 처리(보수적).
- 1분봉 누락(filled) 구간을 포함하는 윈도는 제외.
- 비용 c는 왕복 기준(수수료×2 + 스프레드 + 슬리피지×2), 수익률에서 그대로 차감.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

KST = "Asia/Seoul"


@dataclass(frozen=True)
class CostScenario:
    name: str
    fee: float          # 편도 수수료 (비율)
    spread_bps: float   # 왕복에서 1회 부담하는 스프레드
    slip_bps: float     # 편도 슬리피지

    @property
    def round_trip(self) -> float:
        return 2 * self.fee + self.spread_bps / 1e4 + 2 * self.slip_bps / 1e4


def default_scenarios(spread_bps: float = 1.0) -> list[CostScenario]:
    """수수료 값은 config/exchanges.yaml과 동일(미검증)."""
    return [
        CostScenario("bithumb_coupon(0.04%)", 0.0004, spread_bps, 1.0),
        CostScenario("upbit_base(0.05%)", 0.0005, spread_bps, 1.0),
        CostScenario("upbit_conservative", 0.0005, spread_bps, 3.0),
        CostScenario("upbit_stress", 0.0005, spread_bps * 2, 8.0),
        CostScenario("binance_retail(0.10%)", 0.0010, spread_bps, 1.0),
        CostScenario("bithumb_standard(0.25%)", 0.0025, spread_bps, 1.0),
    ]


def _entries(m1: pd.DataFrame, horizon: int, warmup: int = 5) -> np.ndarray:
    """5분 경계의 1분봉 인덱스 중, [i-warmup, i+horizon) 구간에 누락이 없는 것."""
    n = len(m1)
    minute = m1.index.minute.to_numpy()
    cand = np.flatnonzero(minute % 5 == 0)
    cand = cand[(cand >= warmup) & (cand + horizon <= n)]
    filled = m1["filled"].to_numpy() if "filled" in m1 else np.zeros(n, bool)
    cf = np.concatenate([[0], np.cumsum(filled)])
    bad = cf[cand + horizon] - cf[cand - warmup]
    return cand[bad == 0]


def forward_returns(m1: pd.DataFrame, horizon: int) -> pd.Series:
    idx = _entries(m1, horizon)
    o = m1["open"].to_numpy()
    c = m1["close"].to_numpy()
    r = c[idx + horizon - 1] / o[idx] - 1
    return pd.Series(r, index=m1.index[idx], name=f"r_{horizon}m")


def barrier_outcomes(m1: pd.DataFrame, horizon: int, x: float) -> pd.DataFrame:
    """대칭 TP/SL(±x) 첫 도달 시뮬레이션. outcome: 1=TP, -1=SL, 0=타임아웃."""
    idx = _entries(m1, horizon)
    o = m1["open"].to_numpy()
    h = m1["high"].to_numpy()
    l = m1["low"].to_numpy()
    c = m1["close"].to_numpy()
    hw = sliding_window_view(h, horizon)[idx]
    lw = sliding_window_view(l, horizon)[idx]
    entry = o[idx]
    up_hit = hw >= (entry * (1 + x))[:, None]
    dn_hit = lw <= (entry * (1 - x))[:, None]
    big = horizon + 1
    first_up = np.where(up_hit.any(1), up_hit.argmax(1), big)
    first_dn = np.where(dn_hit.any(1), dn_hit.argmax(1), big)
    outcome = np.zeros(len(idx), dtype=np.int8)
    outcome[first_up < first_dn] = 1
    outcome[first_dn <= first_up] = -1          # 같은 봉 동시 도달 → 손절
    outcome[(first_up == big) & (first_dn == big)] = 0
    timeout_ret = c[idx + horizon - 1] / entry - 1
    gross = np.where(outcome == 1, x, np.where(outcome == -1, -x, timeout_ret))
    hold = np.minimum(np.minimum(first_up, first_dn) + 1, horizon)
    return pd.DataFrame({"outcome": outcome, "gross": gross, "hold_min": hold},
                        index=m1.index[idx])


def horizon_table(m1: pd.DataFrame, horizons, scenarios) -> pd.DataFrame:
    rows = []
    for H in horizons:
        r = forward_returns(m1, H)
        a = r.abs()
        row = {"horizon": f"{H}m", "n": len(r),
               "|r| p50 (bps)": a.median() * 1e4,
               "|r| p75 (bps)": a.quantile(.75) * 1e4,
               "|r| p90 (bps)": a.quantile(.90) * 1e4}
        for s in scenarios:
            row[f"P(r>c) {s.name}"] = (r > s.round_trip).mean()
        rows.append(row)
    return pd.DataFrame(rows).set_index("horizon")


def barrier_table(m1: pd.DataFrame, horizons, xs, scenarios) -> pd.DataFrame:
    rows = []
    for H in horizons:
        for x in xs:
            b = barrier_outcomes(m1, H, x)
            g = b["gross"].mean()
            row = {"horizon": f"{H}m", "TP=SL (bps)": x * 1e4, "n": len(b),
                   "P(TP)": (b.outcome == 1).mean(), "P(SL)": (b.outcome == -1).mean(),
                   "P(timeout)": (b.outcome == 0).mean(),
                   "avg hold (min)": b["hold_min"].mean(),
                   "gross/trade (bps)": g * 1e4}
            for s in scenarios:
                # SL→TP로 확률 Δ를 옮기면 기대값이 2xΔ 증가 → 손익분기 Δ = (c - g) / 2x
                row[f"필요 승률개선 %p {s.name}"] = max(0.0, (s.round_trip - g) / (2 * x)) * 100
            rows.append(row)
    return pd.DataFrame(rows).set_index(["horizon", "TP=SL (bps)"])


def hourly_profile(m1: pd.DataFrame, scenario: CostScenario, horizon: int = 30) -> pd.DataFrame:
    r = forward_returns(m1, horizon)
    hours = r.index.tz_convert(KST).hour
    vol = m1.get("quote_volume")
    g = r.abs().groupby(hours)
    out = pd.DataFrame({
        f"|r_{horizon}m| p50 (bps)": g.median() * 1e4,
        f"P(r>c) {scenario.name}": (r > scenario.round_trip).groupby(hours).mean(),
        f"비용/중앙값 변동": scenario.round_trip / g.median(),
    })
    if vol is not None and vol.notna().any():
        vq = vol.groupby(m1.index.tz_convert(KST).hour).mean()
        out["평균 1분 거래대금"] = vq
    out.index.name = "hour(KST)"
    return out


def regime_table(m1: pd.DataFrame, scenario: CostScenario, horizon: int = 30,
                 lookback_5m: int = 288) -> pd.DataFrame:
    """직전 1일 실현변동성(룩어헤드 없음) 3분위별 성과. 분위 경계는 전체 표본 기준(기술통계용)."""
    c5 = m1["close"].resample("5min").last()
    rv = np.log(c5).diff().rolling(lookback_5m).std().shift(1)
    r = forward_returns(m1, horizon)
    reg = rv.reindex(r.index)
    ok = reg.notna()
    r, reg = r[ok], reg[ok]
    bucket = pd.qcut(reg, 3, labels=["low vol", "mid vol", "high vol"])
    g = r.groupby(bucket, observed=True)
    return pd.DataFrame({
        "n": g.size(),
        "5m rv p50 (bps)": reg.groupby(bucket, observed=True).median() * 1e4,
        f"|r_{horizon}m| p50 (bps)": g.apply(lambda s: s.abs().median() * 1e4),
        f"P(r>c) {scenario.name}": g.apply(lambda s: (s > scenario.round_trip).mean()),
    })


def build_report(m1: pd.DataFrame, title: str, scenarios=None,
                 horizons=(5, 15, 30, 60, 120), xs=(0.002, 0.003, 0.005, 0.008, 0.012),
                 quality_md: str = "") -> str:
    scenarios = scenarios or default_scenarios()
    main = next(s for s in scenarios if s.name.startswith("upbit_base"))
    fmt = dict(floatfmt=".3f")
    parts = [
        f"# 비용 바닥 분석 — {title}",
        "",
        "## 데이터 품질",
        quality_md or "(생략)",
        "",
        "## 비용 시나리오 (왕복)",
        pd.DataFrame([{"scenario": s.name, "fee/side": f"{s.fee:.4%}",
                       "spread (bps)": s.spread_bps, "slip/side (bps)": s.slip_bps,
                       "round trip (bps)": s.round_trip * 1e4} for s in scenarios]
                     ).to_markdown(index=False, floatfmt=".1f"),
        "",
        "## 1. 보유 시간별 수익률 분포 (무조건 진입 = 무작위 진입 기준선)",
        "P(r>c): 롱 진입 후 해당 시간 뒤 청산 시 비용을 넘는 이익이 날 확률.",
        "",
        horizon_table(m1, horizons, scenarios).to_markdown(**fmt),
        "",
        "## 2. 대칭 TP/SL 첫 도달 (1분봉 경로, 동시 도달=손절)",
        "필요 승률개선 %p: 무작위 진입 대비 TP 도달 확률을 몇 %p 올려야 비용 차감 후 본전인지.",
        "",
        barrier_table(m1, horizons, xs, scenarios).to_markdown(**fmt),
        "",
        f"## 3. 시간대별 (KST, 30분 보유, {main.name})",
        "",
        hourly_profile(m1, main).to_markdown(**fmt),
        "",
        f"## 4. 변동성 국면별 (30분 보유, {main.name})",
        "",
        regime_table(m1, main).to_markdown(**fmt),
        "",
    ]
    return "\n".join(parts)
