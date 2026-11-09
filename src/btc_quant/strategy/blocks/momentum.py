"""진입 · 모멘텀 블록 (+ 블록 모듈 공용 헬퍼).

모든 지표는 현재 봉까지의 값만 사용한다 (ewm(adjust=False), rolling, shift(+k)).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import REGISTRY, Context

BARS_PER_DAY = 288  # 5분봉


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
