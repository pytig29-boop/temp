"""위험 관리 보유(managed_hold) 보조 계산 — 정기 적립(DCA) 오버레이 (docs/MANAGED_HOLD.md §3, 준비 단계).

엔진·관문은 비율 수익(시간가중, TWR) 위에서 돈다: 슬리브 자산 대비 일간 수익 r_t. 정기 적립(고정 KRW 를 주기마다
추가 투입)은 **현금 흐름**이라 TWR 지표(Sharpe·MDD)를 바꾸지 않고 금액가중 결과(최종 평가액·IRR)만 바꾼다. 그래서
엔진 기능이 아니라 일간 수익 위의 오버레이로 계산한다:

    V_0 = 0;  하루 d 시작에 적립일이면 V ← V + c (적립 = 그날 시작 시점 전략 상태 그대로 — 보유 중이면 같은 비중으로
    매수, 현금 상태면 현금으로 대기; 전략 일간 수익 r_d 가 적립분에도 똑같이 적용된다), 그 뒤 V ← V·(1 + r_d).

이 가정은 "적립금이 전략 슬리브 자산의 일부가 된다"는 뜻이다. 적립 시점의 추가 매수 비용(진입 비용)은 r_d 에 들어
있지 않으므로 `buy_cost` (편도 비율, 예: 비용 모델 side_cost + fee_rate)로 적립금에서 따로 뺀다(보유 중인 날만,
보수적으로 모든 적립일에 빼려면 always_charge=True).

비교 기준: 같은 일정의 DCA-매수보유(같은 c, 같은 날, r_d = 동일 비중 B&H 일간 수익). 금액가중 수익률(IRR)은 일 단위
현금 흐름의 내부수익률(연율, 365일). 이 모듈은 결과를 계산만 한다 — 관문·원장과 무관하다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import optimize

ANN = 365


def contribution_days(days: pd.DatetimeIndex, period: str = "W") -> pd.DatetimeIndex:
    """적립일 = 각 기간(pandas 기간 코드: "W" 주, "M" 월, "D" 매일)의 첫 거래일 (UTC 일자)."""
    days = pd.DatetimeIndex(days)
    if len(days) == 0:
        return days
    naive = days.tz_convert(None) if days.tz is not None else days
    per = naive.to_period(period)
    first = ~pd.Series(per).duplicated().to_numpy()
    return days[first]


def irr_daily(flows: np.ndarray, final_value: float, ann: int = ANN) -> float | None:
    """일별 투입액 flows[d] (≥ 0, 날 d 시작) 과 마지막 날 끝의 평가액 → 연율 IRR. 투입이 없으면 None."""
    flows = np.asarray(flows, float)
    n = len(flows)
    if n == 0 or flows.sum() <= 0:
        return None
    t_left = n - np.arange(n)            # 투입일 시작 ~ 마지막 날 끝까지의 일수

    def npv(r):
        return float(np.sum(flows * (1.0 + r) ** t_left) - final_value)

    lo, hi = -0.999, 0.05
    while npv(hi) < 0 and hi < 10:
        hi *= 2
    if npv(lo) > 0 or npv(hi) < 0:
        return None
    rd = optimize.brentq(npv, lo, hi, xtol=1e-14)
    return float((1.0 + rd) ** ann - 1.0)


def dca_overlay(daily_returns: pd.Series, amount: float, period: str = "W", *,
                exposure: pd.Series | None = None, buy_cost: float = 0.0,
                always_charge: bool = False) -> dict:
    """전략 일간 수익(TWR) 위의 정기 적립 결과.

    exposure: 날 d 시작의 보유 여부/비중(0~1, 없으면 1) — buy_cost 를 보유 비중만큼 적립금에서 뺄 때 쓴다.
    반환: invested, final_value, profit, multiple(final/invested), irr_ann, n_contributions, max_dd_value
    (평가액 − 누적 투입액 기준이 아니라 평가액 곡선 자체의 최대 낙폭 — 적립이 낙폭을 가리므로 참고용)."""
    r = pd.Series(daily_returns, dtype=float).fillna(0.0)
    days = pd.DatetimeIndex(r.index)
    cdays = set(contribution_days(days, period))
    expo = (pd.Series(1.0, index=days) if exposure is None
            else pd.Series(exposure, dtype=float).reindex(days).fillna(0.0).clip(0.0, 1.0))
    v = 0.0
    flows = np.zeros(len(days))
    values = np.zeros(len(days))
    for i, d in enumerate(days):
        if d in cdays:
            flows[i] = float(amount)
            charge = 1.0 if always_charge else float(expo.iloc[i])
            v += float(amount) * (1.0 - float(buy_cost) * charge)
        v *= 1.0 + float(r.iloc[i])
        values[i] = v
    invested = float(flows.sum())
    peak = np.maximum.accumulate(values) if len(values) else values
    dd = float(np.max(1.0 - values / np.where(peak > 0, peak, np.nan), initial=0.0)) if len(values) else 0.0
    return {"period": period, "amount": float(amount), "n_contributions": int((flows > 0).sum()),
            "invested": invested, "final_value": float(v), "profit": float(v - invested),
            "multiple": float(v / invested) if invested > 0 else None,
            "irr_ann": irr_daily(flows, v), "max_dd_value": dd}


def dca_compare(strategy_daily: pd.Series, bh_daily: pd.Series, amount: float, period: str = "W", **kw) -> dict:
    """같은 적립 일정의 전략 vs DCA-매수보유 (B&H 는 항상 보유: buy_cost 를 모든 적립일에)."""
    s = dca_overlay(strategy_daily, amount, period, **kw)
    bkw = {"buy_cost": kw.get("buy_cost", 0.0), "always_charge": True}
    b = dca_overlay(pd.Series(bh_daily, dtype=float).reindex(pd.Series(strategy_daily).index).fillna(0.0),
                    amount, period, **bkw)
    return {"strategy": s, "buy_hold": b,
            "final_value_ratio": (s["final_value"] / b["final_value"]) if b["final_value"] else None,
            "irr_diff": (None if s["irr_ann"] is None or b["irr_ann"] is None else s["irr_ann"] - b["irr_ann"])}


__all__ = ["ANN", "contribution_days", "dca_compare", "dca_overlay", "irr_daily"]
