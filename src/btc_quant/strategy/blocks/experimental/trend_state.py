"""실험 · 장기 추세 상태 블록 (위험 관리 보유 "managed_hold", 사용자 결정 "B" 준비, 2026-11-10).

docs/MANAGED_HOLD.md 의 "기본 롱 / 필터 청산" 구조를 기존 엔진 의미론으로 표현하기 위한 한 쌍:

    진입(상태형)   trend_ema_4h(span, band_bp)  = close_t > EMA(close, span)_t · (1 + band_bp/1e4)
    신호 청산      trend_ema_off(span, band_bp) = close_t < EMA(close, span)_t · (1 − band_bp/1e4)

- 진입 블록은 추세가 살아 있는 **동안 계속 True** 다(돌파 '이벤트'가 아니라 상태). 엔진은 무포지션이면 다음 결정 경계에서
  진입하고, 보유 중에는 신호 청산이 True 가 될 때까지(그리고 아주 넓은 SL/TP·긴 max_hold 로) 보유한다.
  청산 뒤에는 쿨다운 봉 1개 후 추세가 다시 켜져 있으면 재진입한다.
- band_bp > 0 은 히스테리시스(진입은 EMA 위 band, 청산은 EMA 아래 band) — EMA 근처에서의 잦은 왕복(비용)을 줄인다.
  band_bp = 0 이면 진입 = ¬청산 (EMA 와 정확히 같은 종가만 둘 다 False).
- 캠페인 템플릿에서는 `signal_exit: {block: trend_ema_off, match_entry: [span, band_bp]}` 로 진입과 같은 값에 묶는다
  (research/campaign.py "신호 청산").
- 창은 봉 개수다(다른 블록과 같은 규약). 4h 봉 기준 span 42/90/180/360 = 7/15/30/60일. 다른 결정 주기에서도 계산은
  되지만(인과·수용 검사는 5m·1m 에서) 설계 의도는 4h 장기 추세 필터다.
- EMA 는 재귀식(adjust=False)이라 후행 창(페이퍼·전진 워밍업)에서 마지막 값을 정확히 재현하려면 초기값 가중치
  (1−α)^n ≤ 1e-12 인 n 봉이 필요하다(`lookback_bars`). 4h span 180 이면 ≈ 2,490봉(415일) — 4h 결정 주기의 페이퍼·전진
  워밍업 상한은 420일(`paper.bars.max_warmup_days_for`, Phase 5 에서 120일 → 420일)이라 정확히 재현된다(잔존 ≈ 7e-13).
  span 360 은 상한에서 잔존 ≈ 8e-7 (EMA 근처 비교만 극히 드물게 다를 수 있음). 백테스트(Dev 전체 이력)에는 영향 없음.
"""
from __future__ import annotations

import math

import pandas as pd

from ..base import REGISTRY, Context
from ..momentum import as_signal, ema

AUTHOR = "claude (AI)"
CREATED = "2026-11-10"
SPANS = [42, 90, 180, 360]          # 4h 봉: 7 / 15 / 30 / 60 일. 사전 선언, 결과를 보고 바꾸지 않는다.
BANDS_BP = [0.0, 50.0, 100.0, 200.0]
EMA_TOL = 1e-12                     # paper.bars.EMA_TOL 과 같은 값 (초기값 잔존 가중치 상한)

_TF_NOTE = ("designed for 4h decision bars (span in bars: 42/90/180/360 = 7/15/30/60 days at 4h); computable at "
            "every decision interval (bar counts), but on short bars it is a short-horizon trend state")
_RATIONALE = ("위험 관리 보유(managed_hold, 사용자 결정 B, docs/MANAGED_HOLD.md): 동일 비중 3코인 바구니를 기본 보유하고 "
              "장기 추세가 꺼지면 현금으로. 메커니즘 M3 시계열 모멘텀(research/knowledge/mechanisms.md)의 장기판 — "
              "추세 추종 자금·레버리지 청산 연쇄가 하락 국면을 길게 만든다는 가정(Moskowitz·Ooi·Pedersen 2012 의 "
              "time-series momentum, 이동평균 타이밍 문헌). 목적은 엣지(초과 Sharpe)보다 낙폭 축소다. 상대방: 하락 "
              "추세 초기에 계속 사는 매수자·추세 반전에 늦게 파는 보유자. 동기 증거: 없음(이 블록은 설계 준비용; Dev "
              "실행·선별 없음, 잠금·Val 노출 없음). 파라미터는 span × band_bp 뿐(≤ 16), 결과를 보고 고르지 않는다.")


def _ema_lookback(p: dict) -> int:
    """마지막 값을 전체 이력과 같게 재현하는 봉 수: EMA 초기값 가중치 (1−α)^n ≤ EMA_TOL, + 워밍업 span."""
    span = float(p.get("span", 180))
    a = 2.0 / (span + 1.0)
    return math.ceil(math.log(EMA_TOL) / math.log(1.0 - a)) + int(span) + 2


def _ratio(bars: pd.DataFrame, span: int) -> pd.Series:
    c = bars["close"].where(bars["close"] > 0)
    return c / ema(c, int(span))


@REGISTRY.register_experimental(
    "entry",
    grid={"span": SPANS, "band_bp": BANDS_BP},
    description=("장기 추세 상태(상태형 진입, 추세 중 계속 True): close_t > EMA(close, span)_t·(1 + band_bp/1e4). "
                 "앞 span 봉 False. trend_ema_off 와 짝(signal_exit, match_entry)으로 '추세 동안 보유'."),
    rationale=_RATIONALE, author=AUTHOR, created=CREATED,
    lookback_bars=_ema_lookback, tf_note=_TF_NOTE,
)
def trend_ema_4h(bars: pd.DataFrame, ctx: Context, span: int = 180, band_bp: float = 0.0) -> pd.Series:
    return as_signal(_ratio(bars, span) > 1.0 + float(band_bp) / 1e4, bars.index, warmup=int(span))


@REGISTRY.register_experimental(
    "exit_signal",
    grid={"span": SPANS, "band_bp": BANDS_BP},
    description=("장기 추세 꺼짐(신호 청산): close_t < EMA(close, span)_t·(1 − band_bp/1e4). 앞 span 봉 False. "
                 "trend_ema_4h 와 같은 span·band_bp 로 묶어 히스테리시스 띠를 만든다."),
    rationale=_RATIONALE, author=AUTHOR, created=CREATED,
    lookback_bars=_ema_lookback, tf_note=_TF_NOTE,
)
def trend_ema_off(bars: pd.DataFrame, ctx: Context, span: int = 180, band_bp: float = 0.0) -> pd.Series:
    return as_signal(_ratio(bars, span) < 1.0 - float(band_bp) / 1e4, bars.index, warmup=int(span))


_FILTER_RATIONALE = ("트랙 D(2026-11-12, 자율 연구 R2): 상승 추세 안의 다일 눌림목 매수. 4h 결정 봉에서는 공식 필터 "
                     "trend_regime_1h 가 의미 없다(1h 재표본이 4시간에 한 번뿐 → 항상 False, 캠페인 검사가 거부). "
                     "같은 자산의 결정 봉 종가 > EMA(span)·(1+band_bp/1e4) 를 필터로 쓴다 — trend_ema_4h 진입 블록과 "
                     "같은 계산을 필터 자리에 둔 것. 메커니즘 M4(추세 속 되돌림은 디레버리징·유동성 주도라 추세 자금이 "
                     "다시 들어오면 회복). 파라미터는 span·band_bp 의 사전 선언 격자뿐, 결과를 보고 고르지 않는다.")


@REGISTRY.register_experimental(
    "filter",
    grid={"span": SPANS[:3], "band_bp": BANDS_BP[:3]},
    description=("자기 자산 장기 추세 켜짐(필터): close_t > EMA(close, span)_t·(1 + band_bp/1e4). 앞 span 봉 False. "
                 "4h 결정 봉용 (trend_regime_1h 는 1h 이하 전용)."),
    rationale=_FILTER_RATIONALE, author=AUTHOR, created="2026-11-12",
    lookback_bars=_ema_lookback, tf_note=_TF_NOTE,
)
def trend_ema_filter(bars: pd.DataFrame, ctx: Context, span: int = 42, band_bp: float = 0.0) -> pd.Series:
    return as_signal(_ratio(bars, span) > 1.0 + float(band_bp) / 1e4, bars.index, warmup=int(span))
