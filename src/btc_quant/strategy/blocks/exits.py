"""신호 청산 블록 (exit_signal). 포지션 보유 중 True가 되면 청산."""
from __future__ import annotations

import pandas as pd

from .base import REGISTRY, Context
from .momentum import as_signal, ema


@REGISTRY.register(
    "exit_signal",
    grid={"fast": [5, 8, 12], "slow": [26, 48, 96]},
    description="EMA(fast)가 EMA(slow)를 이번 봉에 하향 돌파 (직전 fast≥slow, 현재 fast<slow).",
)
def ema_cross_down(bars: pd.DataFrame, ctx: Context, fast: int = 8, slow: int = 48) -> pd.Series:
    d = ema(bars["close"], fast) - ema(bars["close"], slow)
    return as_signal((d < 0) & (d.shift(1) >= 0), bars.index, warmup=slow)
