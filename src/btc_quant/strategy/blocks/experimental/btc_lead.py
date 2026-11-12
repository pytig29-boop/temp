"""실험 · BTC 추세 필터 (자율 프로그램 R2 트랙 C, 2026-11-12).

    btc_trend_filter(span, band_bp) = BTC_close_t > EMA(BTC_close, span)_t · (1 + band_bp/1e4)

XRP·ETH 슬리브가 **코인원 BTC-KRW 의 4시간 추세가 켜져 있을 때만** 진입하도록 하는 필터. 보조 데이터 키 `coinone_btc`
(`research.campaign.load_aux_m1` 이 어떤 슬리브든 코인원 BTC-KRW 1분봉을 읽어 같은 결정 봉으로 만든다). BTC 슬리브에서는
자기 자신의 추세 필터와 같다.

메커니즘: 알트코인은 BTC 의 국면을 뒤따른다(크립토 수익의 공통 요인 — BTC 가 시장 베타를 결정하고, 알트 돌파는 BTC 상승
국면에서 지속성이 크다). 상대방: BTC 하락 국면에서 알트 돌파를 사는 늦은 추세 추종자. 동기 증거(Dev 만): camp-000034 심사에서
XRP·ETH 슬리브만의 Sharpe 가 1.06 으로 약했다(XRP 중앙 거래 −5bp) — BTC 국면이 알트 돌파의 질을 가른다는 가설.
잠금·Val 노출 없음. 파라미터는 span × band_bp(≤ 9), 사전 선언.
live_ready=True: 페이퍼 서비스가 BTC 슬리브의 체결 1분봉 이력을 알트 슬리브의 coinone_btc 로 공유한다(paper.service).
"""
from __future__ import annotations

import pandas as pd

from ..base import REGISTRY, Context
from ..momentum import as_signal, aux_frame, ema
from .trend_state import _ema_lookback

AUTHOR = "claude (AI)"
CREATED = "2026-11-12"
SPANS = [42, 90, 180]       # 4h 봉 7 / 15 / 30 일 (사전 선언)
BANDS_BP = [0.0, 50.0, 100.0]

_RATIONALE = ("알트코인은 BTC 국면을 뒤따른다(공통 요인). XRP·ETH 돌파 진입을 BTC 4h 추세가 켜져 있을 때로 제한. "
              "상대방: BTC 하락 국면에서 알트 돌파를 사는 늦은 추세 추종자. 동기 증거: camp-000034 심사(Dev)에서 XRP+ETH "
              "슬리브만의 Sharpe 1.06, XRP 중앙 거래 −5bp. 잠금·Val 노출 없음.")


@REGISTRY.register_experimental(
    "filter",
    grid={"span": SPANS, "band_bp": BANDS_BP},
    description="코인원 BTC-KRW 결정 봉 종가 > EMA(span)·(1+band_bp/1e4) (BTC 추세 켜짐). 보조 데이터 coinone_btc 필요.",
    rationale=_RATIONALE, author=AUTHOR, created=CREATED, requires=("coinone_btc",), live_ready=True,
    lookback_bars=_ema_lookback, tf_note="designed for 4h decision bars",
)
def btc_trend_filter(bars: pd.DataFrame, ctx: Context, span: int = 42, band_bp: float = 0.0) -> pd.Series:
    b = aux_frame(bars, ctx, "coinone_btc")["close"]
    c = b.where(b > 0)
    return as_signal(c / ema(c, int(span)) > 1.0 + float(band_bp) / 1e4, bars.index, warmup=int(span))
