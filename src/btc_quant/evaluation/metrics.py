"""성과 지표 (순수 함수, I/O 없음).

규약
- 일간 수익률 Series: UTC 일자 index, 무거래일 0 포함 (backtest.types.BacktestResult.daily_returns).
- Sharpe 는 일간 평균/표준편차(ddof=1) × √365 (코인은 365일 거래).
- 낙폭(drawdown)은 **양수 크기**로 표기한다 (0.15 = 15% 낙폭). 손실 한도는 음수 수익률로 표기.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

ANN = 365
ROLLING_WINDOW_DAYS = 182
ROLLING_STEP_DAYS = 30
RECENT_DAYS = 365


def _arr(x) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    return a[np.isfinite(a)]


def sharpe(daily, ann: int = ANN) -> float:
    """연율화 Sharpe = mean/std(ddof=1)·√ann. 표본 < 2 또는 std=0 이면 0.0."""
    a = _arr(daily)
    if a.size < 2:
        return 0.0
    sd = a.std(ddof=1)
    if not np.isfinite(sd) or sd <= 0:
        return 0.0
    return float(a.mean() / sd * np.sqrt(ann))


@dataclass(frozen=True)
class WeightedSharpe:
    sharpe: float        # 연율화
    mean: float          # 가중 일간 평균
    std: float           # 가중 일간 표준편차 (reliability-weight 불편 보정)
    n_eff: float         # Kish 유효 표본 = (Σw)² / Σw²
    n: int


def _day_age(index, anchor=None) -> np.ndarray:
    """각 관측의 anchor(기본: 마지막 관측) 대비 경과 일수."""
    if isinstance(index, pd.DatetimeIndex):
        anchor = index.max() if anchor is None else pd.Timestamp(anchor)
        if anchor.tzinfo is None and index.tz is not None:
            anchor = anchor.tz_localize(index.tz)
        return ((anchor - index) / pd.Timedelta(days=1)).to_numpy(dtype=float)
    n = len(index)
    return np.arange(n - 1, -1, -1, dtype=float)


def exp_weights(index, half_life_days: float = 365, anchor=None) -> np.ndarray:
    """w = 0.5 ** (age_days / half_life). 마지막 날(anchor) 가중치 = 1."""
    age = _day_age(index, anchor)
    return np.power(0.5, age / float(half_life_days))


def weighted_sharpe(daily: pd.Series, half_life_days: float = 365, anchor=None,
                    ann: int = ANN) -> WeightedSharpe:
    """지수 가중 Sharpe (PLAN D1/§2.2: 반감기 12개월, 시계열 끝 기준).

    mean_w = Σw x / Σw,  var_w = Σw (x-mean_w)² / (V1 − V2/V1)  (V1=Σw, V2=Σw²; 빈도 아닌
    신뢰도 가중의 불편 분산). 균등 가중이면 일반 Sharpe 와 정확히 일치한다.
    """
    s = pd.Series(daily).dropna()
    x = s.to_numpy(dtype=float)
    n = x.size
    if n < 2:
        return WeightedSharpe(0.0, float(x.mean()) if n else 0.0, 0.0, float(n), n)
    w = exp_weights(s.index, half_life_days, anchor)
    v1, v2 = w.sum(), (w ** 2).sum()
    m = float((w * x).sum() / v1)
    denom = v1 - v2 / v1
    var = float((w * (x - m) ** 2).sum() / denom) if denom > 0 else 0.0
    sd = np.sqrt(max(var, 0.0))
    sr = float(m / sd * np.sqrt(ann)) if sd > 0 else 0.0
    return WeightedSharpe(sr, m, float(sd), float(v1 ** 2 / v2), n)


def equity_curve(daily) -> np.ndarray:
    return np.cumprod(1.0 + _arr(daily))


def max_drawdown(daily) -> tuple[float, int]:
    """(최대 낙폭 크기 ≥ 0, 최장 수중 기간[관측 수=일]).

    낙폭은 시작 자본 1.0 을 초기 고점으로 포함해 계산한다. 수중 기간 = 직전 고점 미만에
    연속으로 머문 최장 일수 (회복 못 한 마지막 구간 포함).
    """
    eq = equity_curve(daily)
    if eq.size == 0:
        return 0.0, 0
    eq = np.concatenate([[1.0], eq])
    peak = np.maximum.accumulate(eq)
    dd = 1.0 - eq / peak
    under = dd > 1e-12
    longest = cur = 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    return float(dd.max()), int(longest)


def trade_stats(trades: pd.DataFrame, n_days: int) -> dict:
    """거래 통계. trades 는 TRADE_COLUMNS (net_ret, hold_min 사용)."""
    n = 0 if trades is None else len(trades)
    if n == 0:
        return {"n_trades": 0, "expectancy": 0.0, "win_rate": 0.0, "profit_factor": 0.0,
                "avg_hold_min": 0.0, "trades_per_day": 0.0}
    r = trades["net_ret"].to_numpy(dtype=float)
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    pf = float(gains / losses) if losses > 0 else (float("inf") if gains > 0 else 0.0)
    return {
        "n_trades": int(n),
        "expectancy": float(r.mean()),
        "win_rate": float((r > 0).mean()),
        "profit_factor": pf,
        "avg_hold_min": float(trades["hold_min"].astype(float).mean()),
        "trades_per_day": float(n / n_days) if n_days > 0 else 0.0,
    }


def yearly_table(daily: pd.Series, trades: pd.DataFrame | None = None) -> pd.DataFrame:
    """달력 연도별 성과표 (index=연도): n_days, total_return, sharpe, max_dd, n_trades.

    거래 연도는 exit_time 기준 (daily_returns 의 청산일 기준 귀속과 일치).
    """
    s = pd.Series(daily).dropna()
    rows = {}
    years = s.index.year
    tr_years = None
    if trades is not None and len(trades):
        tr_years = pd.DatetimeIndex(pd.to_datetime(trades["exit_time"])).year
    for y in sorted(set(years)):
        part = s[years == y]
        mdd, _ = max_drawdown(part)
        rows[int(y)] = {
            "n_days": int(part.size),
            "total_return": float(np.prod(1 + part.to_numpy()) - 1),
            "sharpe": sharpe(part),
            "max_dd": mdd,
            "n_trades": int((tr_years == y).sum()) if tr_years is not None else 0,
        }
    return pd.DataFrame.from_dict(rows, orient="index")


def rolling_sharpe(daily, window: int = ROLLING_WINDOW_DAYS,
                   step: int = ROLLING_STEP_DAYS) -> pd.Series:
    """겹치는 고정 길이 창의 Sharpe. 창은 **시계열 끝에 정렬**(마지막 창이 마지막 날에서 끝남)
    하고 step 일씩 과거로 이동; 결과 index 는 창의 마지막 관측 라벨, 시간순 정렬.
    길이 < window 이면 빈 Series."""
    s = pd.Series(daily).dropna()
    n = s.size
    ends = list(range(n, window - 1, -step))[::-1]
    vals = [sharpe(s.iloc[e - window:e]) for e in ends]
    return pd.Series(vals, index=[s.index[e - 1] for e in ends], dtype=float)


def rolling_positive_fraction(daily, window: int = ROLLING_WINDOW_DAYS,
                              step: int = ROLLING_STEP_DAYS) -> tuple[float, int]:
    """(Sharpe > 0 인 롤링 창 비율, 창 개수). 창이 없으면 (0.0, 0)."""
    r = rolling_sharpe(daily, window, step)
    if r.empty:
        return 0.0, 0
    return float((r > 0).mean()), int(r.size)


def recent(daily: pd.Series, days: int = RECENT_DAYS) -> pd.Series:
    """마지막 `days` 일 (DatetimeIndex 면 달력 기준 (end-days, end], 아니면 마지막 days 개)."""
    s = pd.Series(daily).dropna()
    if isinstance(s.index, pd.DatetimeIndex) and s.size:
        return s[s.index > s.index.max() - pd.Timedelta(days=days)]
    return s.iloc[-days:]


def summarize(result, half_life_days: float = 365) -> dict:
    """BacktestResult → 평탄 dict (관문 입력). 낙폭은 양수 크기.

    키: n_days, sharpe, sharpe_w, mean_w, std_w, n_eff, mean_daily, std_daily, skew,
    kurtosis(정규=3), total_return, cagr, max_dd, max_dd_days, n_trades, expectancy, win_rate,
    profit_factor, avg_hold_min, trades_per_day, recent_sharpe, recent_n_trades,
    rolling_frac_positive, rolling_n_windows, sharpe_year_YYYY.
    """
    s = pd.Series(result.daily_returns).dropna().astype(float)
    x = s.to_numpy()
    n = x.size
    ws = weighted_sharpe(s, half_life_days)
    mdd, mdd_days = max_drawdown(s)
    total = float(np.prod(1 + x) - 1) if n else 0.0
    out = {
        "spec_id": result.spec_id,
        "cost": result.cost.name,
        "n_days": int(n),
        "sharpe": sharpe(s),
        "sharpe_w": ws.sharpe,
        "mean_w": ws.mean,
        "std_w": ws.std,
        "n_eff": ws.n_eff,
        "mean_daily": float(x.mean()) if n else 0.0,
        "std_daily": float(x.std(ddof=1)) if n > 1 else 0.0,
        "skew": float(stats.skew(x)) if n > 2 and x.std() > 0 else 0.0,
        "kurtosis": float(stats.kurtosis(x, fisher=False)) if n > 3 and x.std() > 0 else 3.0,
        "total_return": total,
        "cagr": float((1 + total) ** (ANN / n) - 1) if n and total > -1 else -1.0,
        "max_dd": mdd,
        "max_dd_days": mdd_days,
    }
    trades = result.trades
    out.update(trade_stats(trades, n))
    rec = recent(s)
    out["recent_sharpe"] = sharpe(rec)
    if trades is not None and len(trades) and rec.size:
        et = pd.to_datetime(trades["exit_time"])
        lo = rec.index.min()
        if isinstance(lo, pd.Timestamp):
            et_idx = pd.DatetimeIndex(et)
            if et_idx.tz is None and lo.tzinfo is not None:
                et_idx = et_idx.tz_localize(lo.tzinfo)
            elif et_idx.tz is not None and lo.tzinfo is None:
                lo = lo.tz_localize(et_idx.tz)
            # 일자 index 는 자정 라벨 → 해당 일자 전체 포함
            out["recent_n_trades"] = int((et_idx.normalize() >= lo.normalize()).sum())
        else:
            out["recent_n_trades"] = 0
    else:
        out["recent_n_trades"] = 0
    out["rolling_frac_positive"], out["rolling_n_windows"] = rolling_positive_fraction(s)
    if isinstance(s.index, pd.DatetimeIndex) and n:
        for y, row in yearly_table(s, trades).iterrows():
            out[f"sharpe_year_{y}"] = float(row["sharpe"])
    return out
