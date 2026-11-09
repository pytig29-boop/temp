"""필터 블록. 진입 신호와 AND 결합된다 (전략당 최대 2개)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import REGISTRY, Context
from .momentum import BARS_PER_DAY, as_signal, aux_frame, ema, log_ret, warm

BAR = pd.Timedelta("5min")

# KST 시(hour) 집합. 판정 시각 = 봉 종료 시각(index+5분) = 진입 결정 시각.
SESSIONS: dict[str, tuple[int, ...]] = {
    "us_open": (22, 23, 0),
    "night": (22, 23, 0, 1),                 # Phase 0: KST 22~01시 고변동
    "asia_open": (9, 10),
    "active": (22, 23, 0, 9),
    "all_but_quiet": tuple(h for h in range(24) if h not in (3, 4, 5, 6, 7, 8)),
}

VOL_RANK_LOOKBACK = 7 * BARS_PER_DAY        # 실현변동성 백분위 비교 구간: 직전 7일


@REGISTRY.register(
    "filter",
    grid={"session": list(SESSIONS)},
    description=("KST 시간대 필터: 봉 종료 시각(index+5분)의 KST 시가 SESSIONS[session]에 속함. "
                 + "; ".join(f"{k}={list(v)}" for k, v in SESSIONS.items())),
)
def session_kst(bars: pd.DataFrame, ctx: Context, session: str = "active") -> pd.Series:
    hours = (bars.index + BAR).tz_convert("Asia/Seoul").hour
    return pd.Series(np.isin(hours, SESSIONS[session]), index=bars.index)


@REGISTRY.register(
    "filter",
    grid={"window": [12, 24, 48], "min_pct": [0.2, 0.33, 0.5, 0.67, 0.8]},
    description=("고변동 국면: 실현변동성 rv = std(1봉 로그수익률, window봉)의 직전 7일(2016봉, 최소 1일) "
                 "롤링 백분위 ≥ min_pct."),
)
def vol_regime(bars: pd.DataFrame, ctx: Context, window: int = 24, min_pct: float = 0.5) -> pd.Series:
    rv = log_ret(bars["close"]).rolling(window, min_periods=window).std()
    rank = rv.rolling(VOL_RANK_LOOKBACK, min_periods=BARS_PER_DAY).rank(pct=True)
    return as_signal(rank >= min_pct, bars.index)


@REGISTRY.register(
    "filter",
    grid={"window": [48, 96, 288], "min_z": [0.0, 0.5, 1.0, 1.5, 2.0]},
    description=("거래량 급증: z = (log1p(vol_t) - mean)/std, 평균·표준편차는 직전 window봉(현재 봉 제외). "
                 "z ≥ min_z."),
)
def volume_z(bars: pd.DataFrame, ctx: Context, window: int = 96, min_z: float = 1.0) -> pd.Series:
    x = np.log1p(bars["volume"])
    m = x.rolling(window, min_periods=window).mean().shift(1)
    s = x.rolling(window, min_periods=window).std().shift(1)
    return as_signal((x - m) / s >= min_z, bars.index)


@REGISTRY.register(
    "filter",
    grid={"span": [12, 24, 48, 168]},
    description=("1시간 추세: 마지막 '완료된' 1시간봉 종가 > EMA(1시간 종가, span시간). "
                 "봉 t의 판정 시각 t+5분 기준 floor(t+5분,'h')-1h 시간봉을 사용 (미완성 시간봉 미사용)."),
)
def trend_regime_1h(bars: pd.DataFrame, ctx: Context, span: int = 24) -> pd.Series:
    hc = bars["close"].resample("1h", label="left", closed="left").last().dropna()
    above = (hc > ema(hc, span)) & warm(hc.index, span)
    last_done_hour = (bars.index + BAR).floor("h") - pd.Timedelta("1h")
    vals = above.reindex(last_done_hour).to_numpy()
    return as_signal(vals, bars.index)


@REGISTRY.register(
    "filter",
    grid={"k": [1, 3, 6], "max_drop_bp": [20, 40, 60, 100]},
    description="바이낸스 급락 아님: 바이낸스 k봉 로그수익률 > -max_drop_bp (bp).",
    requires=("binance",), live_ready=False,
)
def binance_not_dumping(bars: pd.DataFrame, ctx: Context, k: int = 3, max_drop_bp: float = 40) -> pd.Series:
    rb = log_ret(aux_frame(bars, ctx, "binance")["close"], k) * 1e4
    return as_signal(rb > -max_drop_bp, bars.index)
