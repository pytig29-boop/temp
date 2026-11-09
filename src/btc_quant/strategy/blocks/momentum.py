"""진입 · 모멘텀 블록 (+ 블록 모듈 공용 헬퍼).

모든 지표는 현재 봉까지의 값만 사용한다 (ewm(adjust=False), rolling, shift(+k)).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import REGISTRY, Context

BARS_PER_DAY = 288  # 5분봉 기준 상수 (하위 호환용). 벽시계 창은 bars_per(index, "1D", ctx) 사용
DEFAULT_BAR = pd.Timedelta("5min")
_FREQ_PREFIX = 10_000


# ---------------------------------------------------------------- 공용 헬퍼
def ema(s: pd.Series, span: int) -> pd.Series:
    """인과 EMA (adjust=False: 재귀식, 과거만 사용)."""
    return s.ewm(span=span, adjust=False).mean()


def warm(index: pd.Index, n: int) -> np.ndarray:
    """앞쪽 n개 봉(워밍업)은 False, 이후 True."""
    return np.arange(len(index)) >= n


def log_ret(close: pd.Series, k: int = 1) -> pd.Series:
    """k봉 로그수익률 (현재 봉 종가 기준)."""
    return np.log(close / close.shift(k))


def aux_frame(bars: pd.DataFrame, ctx: Context, key: str) -> pd.DataFrame:
    """ctx.aux[key]를 bars.index에 맞춰 정렬 (없는 시각은 NaN → 조건 False)."""
    if key not in ctx.aux:
        raise KeyError(f"block requires ctx.aux[{key!r}]")
    return ctx.aux[key].reindex(bars.index)


def bar_delta(index: pd.Index, ctx: Context | None = None) -> pd.Timedelta:
    """봉 길이. 우선순위: ctx.bar > index 인접 간격의 최빈값 > 5분.

    최빈값은 결측 봉(간격 2배 등)이 조금 있어도 안정적이다. 속도를 위해 앞쪽 _FREQ_PREFIX 개 간격만 본다
    (뒤를 잘라내도 결과 불변 → 절단 인과성 유지). 봉 1개 이하이면 ctx.bar 또는 5분.
    """
    if ctx is not None and ctx.bar is not None:
        return pd.Timedelta(ctx.bar)
    if len(index) < 2:
        return DEFAULT_BAR
    d = np.diff(pd.DatetimeIndex(index[:_FREQ_PREFIX + 1]).as_unit("ns").asi8)
    d = d[d > 0]
    if not len(d):
        return DEFAULT_BAR
    vals, counts = np.unique(d, return_counts=True)
    return pd.Timedelta(int(vals[np.argmax(counts)]), unit="ns")


def bars_per(index: pd.Index, period: str | pd.Timedelta, ctx: Context | None = None) -> int:
    """벽시계 기간(예: "1D", "7D", "1h")에 해당하는 봉 수 (최소 1). 5분봉 "1D" → 288, 1분봉 → 1440."""
    return max(1, int(pd.Timedelta(period) // bar_delta(index, ctx)))


def as_signal(cond, index: pd.Index, warmup: int = 0) -> pd.Series:
    """bool Series로 정리: NaN→False, 워밍업→False."""
    out = pd.Series(cond, index=index).fillna(False).astype(bool)
    if warmup:
        out &= warm(index, warmup)
    return out


# ---------------------------------------------------------------- 블록
@REGISTRY.register(
    "entry",
    grid={"span": [12, 24, 48, 96], "min_slope_bp": [1.0, 2.0, 3.0, 5.0]},
    description="EMA(close, span)의 봉당 기울기 (ema_t/ema_{t-1}-1)·1e4 ≥ min_slope_bp — 상승 추세 진행 중.",
)
def ema_slope(bars: pd.DataFrame, ctx: Context, span: int = 24, min_slope_bp: float = 2.0) -> pd.Series:
    e = ema(bars["close"], span)
    slope_bp = (e / e.shift(1) - 1) * 1e4
    return as_signal(slope_bp >= min_slope_bp, bars.index, warmup=span)


@REGISTRY.register(
    "entry",
    grid={"lookback": [3, 6, 12, 24], "thr_bp": [20, 40, 60, 100]},
    description="lookback봉 수익률 close_t/close_{t-lookback}-1 ≥ thr_bp(bp) — 단기 모멘텀 추종.",
)
def roc_threshold(bars: pd.DataFrame, ctx: Context, lookback: int = 12, thr_bp: float = 60) -> pd.Series:
    roc_bp = (bars["close"] / bars["close"].shift(lookback) - 1) * 1e4
    return as_signal(roc_bp >= thr_bp, bars.index, warmup=lookback)


@REGISTRY.register(
    "entry",
    grid={"fast": [5, 8, 12], "slow": [26, 48, 96]},
    description="EMA(fast)가 EMA(slow)를 이번 봉에 상향 돌파 (직전 fast≤slow, 현재 fast>slow).",
)
def ema_cross_up(bars: pd.DataFrame, ctx: Context, fast: int = 8, slow: int = 48) -> pd.Series:
    d = ema(bars["close"], fast) - ema(bars["close"], slow)
    return as_signal((d > 0) & (d.shift(1) <= 0), bars.index, warmup=slow)
