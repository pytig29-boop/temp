"""진입 · 돌파 블록. 채널은 직전 봉까지로 계산(shift(1))하고 현재 종가로 돌파를 판정."""
from __future__ import annotations

import pandas as pd

from .base import REGISTRY, Context
from .momentum import as_signal, bars_per, ema


def true_range(bars: pd.DataFrame) -> pd.Series:
    pc = bars["close"].shift(1)
    return pd.concat([bars["high"] - bars["low"], (bars["high"] - pc).abs(), (bars["low"] - pc).abs()],
                     axis=1).max(axis=1)


@REGISTRY.register(
    "entry",
    grid={"lookback": [24, 48, 96, 144, 288]},
    description="종가가 직전 lookback봉(현재 봉 제외)의 최고가를 넘음: close_t > max(high_{t-lookback..t-1}).",
)
def donchian_breakout(bars: pd.DataFrame, ctx: Context, lookback: int = 48) -> pd.Series:
    upper = bars["high"].rolling(lookback, min_periods=lookback).max().shift(1)
    return as_signal(bars["close"] > upper, bars.index)


@REGISTRY.register(
    "entry",
    grid={"span": [12, 24, 48], "mult": [1.5, 2.0, 3.0, 4.0, 5.0]},
    description="켈트너형 채널 돌파: close_t > EMA_{t-1}(close, span) + mult·ATR_{t-1}(span) (ATR=TR의 EMA).",
)
def atr_channel_breakout(bars: pd.DataFrame, ctx: Context, span: int = 24, mult: float = 3.0) -> pd.Series:
    upper = (ema(bars["close"], span) + mult * ema(true_range(bars), span)).shift(1)
    return as_signal(bars["close"] > upper, bars.index, warmup=span + 1)


@REGISTRY.register(
    "entry",
    grid={"lookback": [12, 24, 48], "squeeze_pct": [0.1, 0.2, 0.3, 0.4]},
    description=("수축 후 돌파: 직전 lookback봉 범위폭 (max high-min low)/close 의 1일(5분봉 288봉/1분봉 1440봉) "
                 "백분위 ≤ squeeze_pct "
                 "이고 close_t > 직전 lookback봉 최고가."),
)
def range_compression_break(bars: pd.DataFrame, ctx: Context, lookback: int = 24,
                            squeeze_pct: float = 0.2) -> pd.Series:
    hi = bars["high"].rolling(lookback, min_periods=lookback).max()
    lo = bars["low"].rolling(lookback, min_periods=lookback).min()
    width = (hi - lo) / bars["close"]
    day = bars_per(bars.index, "1D", ctx)
    rank = width.rolling(day, min_periods=day).rank(pct=True)
    squeezed_before = (rank <= squeeze_pct).shift(1, fill_value=False)
    return as_signal(squeezed_before & (bars["close"] > hi.shift(1)), bars.index)
