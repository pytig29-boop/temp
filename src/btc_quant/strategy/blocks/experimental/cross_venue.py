"""실험 · 교차 거래소 블록 (업비트 보조 데이터, PLAN §10b-6).

ctx.aux["upbit"] = 업비트 KRW-BTC OHLCV, 대상 거래소(코인원) bars 와 같은 봉 길이·index.
업비트 WebSocket 은 이 환경에서 실시간 수신 가능 → live_ready=True.

메커니즘 가설: 업비트는 국내 KRW 유동성의 대부분을 차지하고 코인원(~2%)은 가격 발견을 뒤따른다.
Phase 0 에서 바이낸스→업비트 약 1분 선행이 관측됐고, 같은 논리로 업비트→코인원 지연이 예상된다.
(검증 전 가설. 이 블록들은 AI 작성 실험 블록이며 사람 승격 전까지 status="experimental".)

가격 차(gap)는 로그 가격 차 g = log(close_target) - log(close_upbit) 로 계산하고, 거래소 프리미엄이 천천히
표류하므로 **직전 window봉(현재 봉 제외)의 평균·표준편차로 de-meaning** 한 z 만 사용한다.
주의: 봉 종가는 각 거래소의 '마지막 체결' 가격이라 체결이 드문 코인원은 종가가 stale 할 수 있다 —
그 자체가 따라잡기(catch-up) 메커니즘의 일부지만, 실제 체결 가능 가격(호가)과 다를 수 있다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..base import REGISTRY, Context
from ..momentum import as_signal, aux_frame, log_ret

AUTHOR = "claude (AI)"
CREATED = "2026-11-09"
GAP_WINDOWS = [60, 120, 240, 480]


def venue_gap_z(bars: pd.DataFrame, ctx: Context, window: int) -> pd.Series:
    """z_t = (g_t - mean(g_{t-W..t-1})) / std(g_{t-W..t-1}), g = log(close) - log(upbit close).
    업비트 결측 봉은 NaN(창에서 제외, 최소 W/2 개 필요)."""
    up = aux_frame(bars, ctx, "upbit")["close"]
    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.log(bars["close"].where(bars["close"] > 0)) - np.log(up.where(up > 0))
    mp = max(2, int(window) // 2)
    m = g.rolling(int(window), min_periods=mp).mean().shift(1)
    s = g.rolling(int(window), min_periods=mp).std().shift(1)
    return (g - m) / s.where(s > 0)


@REGISTRY.register_experimental(
    "entry",
    grid={"k": [1, 2, 3], "thr_bp": [5, 10, 20, 40]},
    description=("업비트 선행 충격: 업비트 k봉 로그수익률 ru ≥ thr_bp 이고 ru - 대상 k봉 수익률 ≥ thr_bp/2 "
                 "(대상 거래소가 아직 덜 따라옴)."),
    rationale=("업비트가 KRW 가격 발견을 주도하고 코인원은 수 초~1분 지연 추종한다는 가설(Phase 0 바이낸스→업비트 "
               "1분 선행의 국내판). 업비트가 움직였는데 대상이 덜 움직였다면 다음 봉들에서 따라잡기 수익 기대. "
               "0% 수수료라 작은 thr_bp 도 의미가 있다."),
    author=AUTHOR, created=CREATED, requires=("upbit",), live_ready=True,
    lookback_bars=lambda p: int(p.get("k", 1)) + 1,
)
def upbit_lead_impulse(bars: pd.DataFrame, ctx: Context, k: int = 1, thr_bp: float = 10) -> pd.Series:
    ru = log_ret(aux_frame(bars, ctx, "upbit")["close"], k) * 1e4
    rl = log_ret(bars["close"], k) * 1e4
    return as_signal((ru >= thr_bp) & (ru - rl >= thr_bp / 2), bars.index)


@REGISTRY.register_experimental(
    "entry",
    grid={"window": GAP_WINDOWS, "z": [1.5, 2.0, 2.5, 3.0]},
    description=("거래소 가격차 되돌림: g=log(close)-log(upbit close), z=(g_t-mean_W)/std_W "
                 "(직전 window봉, 현재 봉 제외, 최소 W/2) ≤ -z — 대상이 업비트보다 이례적으로 쌈."),
    rationale=("두 KRW 시장은 차익거래로 묶여 있어 프리미엄은 천천히 표류하지만 단기 이탈은 되돌아간다. "
               "대상(소형 거래소)이 업비트 대비 이례적으로 싸면 대상 쪽이 따라 오를 것으로 기대(대상 쪽 매수만 "
               "가능한 현물 롱 전용 구조). 표류하는 프리미엄은 직전 창 평균으로 제거."),
    author=AUTHOR, created=CREATED, requires=("upbit",), live_ready=True,
    lookback_bars=lambda p: int(p.get("window", 240)) + 1,
)
def venue_gap_revert(bars: pd.DataFrame, ctx: Context, window: int = 240, z: float = 2.5) -> pd.Series:
    return as_signal(venue_gap_z(bars, ctx, window) <= -z, bars.index)


@REGISTRY.register_experimental(
    "exit_signal",
    grid={"window": GAP_WINDOWS, "exit_z": [-0.5, 0.0, 0.5, 1.0]},
    description="거래소 가격차 해소: venue_gap_revert 와 같은 z (같은 window) ≥ exit_z 이면 청산.",
    rationale=("venue_gap_revert 의 짝: 따라잡기가 끝나(가격차가 평균 근처로 복귀) 기대 엣지가 소멸하면 청산. "
               "시간 기반 최대 보유보다 메커니즘에 맞는 청산."),
    author=AUTHOR, created=CREATED, requires=("upbit",), live_ready=True,
    lookback_bars=lambda p: int(p.get("window", 240)) + 1,
)
def venue_gap_closed(bars: pd.DataFrame, ctx: Context, window: int = 240, exit_z: float = 0.0) -> pd.Series:
    return as_signal(venue_gap_z(bars, ctx, window) >= exit_z, bars.index)


@REGISTRY.register_experimental(
    "filter",
    grid={"k": [1, 3, 6], "max_drop_bp": [20, 40, 60, 100]},
    description="업비트 급락 아님: 업비트 k봉 로그수익률 > -max_drop_bp (bp). 업비트 결측 봉은 False.",
    rationale=("업비트가 급락 중이면 대상 거래소도 곧 따라 내릴 가능성이 높으므로(선행-추종 가설) 롱 진입을 "
               "보류. binance_not_dumping 의 실시간 가능 대체."),
    author=AUTHOR, created=CREATED, requires=("upbit",), live_ready=True,
    lookback_bars=lambda p: int(p.get("k", 3)) + 1,
)
def upbit_not_dumping(bars: pd.DataFrame, ctx: Context, k: int = 3, max_drop_bp: float = 40) -> pd.Series:
    ru = log_ret(aux_frame(bars, ctx, "upbit")["close"], k) * 1e4
    return as_signal(ru > -max_drop_bp, bars.index)
