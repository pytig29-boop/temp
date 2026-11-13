"""실험 · 변동성 상한 필터 (R4 트랙 F-2, docs/prompts/opus_r4_plan.md 단계 4, 2026-11-13).

    vol_cap(window, max_pct): 공식 vol_regime 과 같은 실현 변동성 rv = std(1봉 로그수익률, window봉) 의
    직전 7일 롤링 백분위가 max_pct 이하일 때 True (= vol_regime 의 반대쪽 꼬리: 고변동 국면 진입 제외).
"""
from __future__ import annotations

import pandas as pd

from ..base import REGISTRY, Context
from ..filters import VOL_RANK_DAYS
from ..momentum import as_signal, bars_per, log_ret

AUTHOR = "claude (AI)"
CREATED = "2026-11-13"
_RATIONALE = (
    "R4 트랙 F (research/knowledge/mechanism_survey_20261112.md C2 변동성 조절 — Moreira·Muir 2017 'Volatility-Managed "
    "Portfolios' 계열, 암호화폐 적용 문헌). 메커니즘: 변동성 급등 국면의 돌파는 기대 수익 대비 위험이 커서 위험 조정 "
    "수익을 깎는다(변동성은 지속되나 기대 수익은 비례해 오르지 않음). 반대편: 고변동 국면에 레버리지 청산·공황 매도로 "
    "가격 무관하게 거래하는 참여자. 동기: R4 F-1 보고용 계산(개발 구간, log_exploration camp-000039) — 효과 매우 작음. "
    "외부 문헌 기반, 잠금·Val 노출 없음. 파라미터 window·max_pct 둘, 쓰는 값 (48, 0.67) 사전 고정."
)


@REGISTRY.register_experimental(
    "filter",
    grid={"window": [12, 24, 48], "max_pct": [0.5, 0.67, 0.8]},
    description=("변동성 상한: rv = std(1봉 로그수익률, window봉)의 직전 7일 롤링 백분위 ≤ max_pct "
                 "(vol_regime 과 같은 rv·순위 창, 반대 방향)."),
    rationale=_RATIONALE, author=AUTHOR, created=CREATED,
    tf_note=("coarse at 4h: the 7-day rank window is 42 bars (min 6) and window=48 bars (8 days) exceeds it "
             "— a slow vol-trend measure there"),
)
def vol_cap(bars: pd.DataFrame, ctx: Context, window: int = 48, max_pct: float = 0.67) -> pd.Series:
    rv = log_ret(bars["close"]).rolling(window, min_periods=window).std()
    day = bars_per(bars.index, "1D", ctx)
    rank = rv.rolling(VOL_RANK_DAYS * day, min_periods=day).rank(pct=True)
    return as_signal(rank <= max_pct, bars.index)
