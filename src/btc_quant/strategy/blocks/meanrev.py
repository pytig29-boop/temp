"""진입 · 평균회귀 블록."""
from __future__ import annotations

import pandas as pd

from .base import REGISTRY, Context
from .momentum import as_signal


def rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder RSI (alpha=1/period, 인과 재귀)."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


@REGISTRY.register(
    "entry",
    grid={"window": [24, 48, 96], "z": [1.5, 2.0, 2.5, 3.0]},
    description="볼린저 z = (close-SMA)/STD (window봉, 현재 봉 포함) ≤ -z — 하단 이탈 매수(되돌림 기대).",
)
def bollinger_z_revert(bars: pd.DataFrame, ctx: Context, window: int = 48, z: float = 2.5) -> pd.Series:
    c = bars["close"]
    zz = (c - c.rolling(window, min_periods=window).mean()) / c.rolling(window, min_periods=window).std()
    return as_signal(zz <= -z, bars.index)


@REGISTRY.register(
    "entry",
    grid={"period": [7, 14, 21], "level": [20, 25, 30, 35]},
    description="RSI(period)가 과매도 level을 아래→위로 회복 (직전 RSI<level, 현재 ≥level).",
)
def rsi_oversold_bounce(bars: pd.DataFrame, ctx: Context, period: int = 14, level: float = 30) -> pd.Series:
    r = rsi(bars["close"], period)
    return as_signal((r.shift(1) < level) & (r >= level), bars.index, warmup=3 * period)
