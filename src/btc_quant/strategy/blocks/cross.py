"""진입 · 교차시장 블록 (바이낸스 선행). 실시간 바이낸스 피드 확보 전에는 live_ready=False."""
from __future__ import annotations

import pandas as pd

from .base import REGISTRY, Context
from .momentum import aux_frame, as_signal, log_ret


@REGISTRY.register(
    "entry",
    grid={"k": [1, 2, 3], "thr_bp": [10, 20, 30, 50]},
    description=("바이낸스 선행 충격: 바이낸스 k봉 로그수익률 rb ≥ thr_bp 이고 rb - 로컬 k봉 수익률 ≥ thr_bp/2 "
                 "(로컬이 아직 덜 따라옴)."),
    requires=("binance",), live_ready=False,
)
def binance_lead_impulse(bars: pd.DataFrame, ctx: Context, k: int = 2, thr_bp: float = 20) -> pd.Series:
    bn = aux_frame(bars, ctx, "binance")
    rb = log_ret(bn["close"], k) * 1e4
    rl = log_ret(bars["close"], k) * 1e4
    return as_signal((rb >= thr_bp) & (rb - rl >= thr_bp / 2), bars.index)
