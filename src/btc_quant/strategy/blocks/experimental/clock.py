"""실험 · 벽시계 진입 블록 (R4 트랙 E, docs/prompts/opus_r4_plan.md 단계 1.1, 2026-11-13).

    clock_entry_utc(hour, minute): True@t ⇔ 봉 t 의 종료 시각(t + 봉 길이)이 UTC hour:minute 과 같다.

- 봉 길이는 `bar_delta(bars.index, ctx)` (session_kst 와 같은 방식). 워밍업 없음, 과거·미래 값을 보지 않는다(시각만).
- 결측 봉이 있어도 시각 기준이다(봉 개수가 아니라 인덱스의 시각으로 판단).
- 4시간 봉은 종료 시각이 0/4/8/12/16/20시뿐이라 거부한다(max_tf_min=60).
- 이번 주기에 쓰는 값은 (21, 0) 과 (0, 5) 두 개뿐. 격자의 나머지(12시·30분)는 수용 검사 격자 요건을 채우는 자리이며,
  쓰면 새 시도다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..base import REGISTRY, Context
from ..momentum import bar_delta

AUTHOR = "claude (AI)"
CREATED = "2026-11-13"

_RATIONALE = (
    "R4 트랙 E (research/knowledge/mechanism_survey_20261112.md C1·C4). 메커니즘 두 개: "
    "(C1) 주요 주식 시장이 모두 닫힌 시간(21~23시 UTC)에 위험을 드는 대가 — 반대편은 장 마감에 맞춰 위험을 줄이는 "
    "참여자 (출처: Quantpedia 2023-09-13, 2024-11-12; 외부 문헌, 비용 미반영·다른 거래소). "
    "(C4) 한국 09시(00:00 UTC) 일봉 경계에서 국내 변동성 돌파 봇이 가격과 무관하게 일괄 시장가 청산하는 압력의 되돌림 — "
    "반대편은 09시에 파는 봇 사용자 (근거: 국내 관행 서술, 수익 통계 없음). "
    "노출 표기: 외부 문헌, 잠금·Val 노출 없음. 단 '09시에 변동이 몰린다'는 관찰(K-P0-3)은 잠금·Val 노출 관찰이라 동기로만 씀. "
    "파라미터는 hour·minute 둘, 쓰는 값은 (21,0)·(0,5) 로 사전 고정."
)


@REGISTRY.register_experimental(
    "entry",
    grid={"hour": [0, 12, 21], "minute": [0, 5, 30]},
    description="벽시계 진입: 봉 종료 시각(index + 봉 길이)이 UTC hour:minute 과 같으면 True (하루 1번).",
    rationale=_RATIONALE, author=AUTHOR, created=CREATED,
    lookback_bars=lambda p: 1, max_tf_min=60,
    tf_note="벽시계 시각 블록 — 4시간 봉은 종료 시각이 0/4/8/12/16/20시뿐",
)
def clock_entry_utc(bars: pd.DataFrame, ctx: Context, hour: int = 21, minute: int = 0) -> pd.Series:
    end = bars.index + bar_delta(bars.index, ctx)
    end = end.tz_convert("UTC") if end.tz is not None else end
    hit = (np.asarray(end.hour) == int(hour)) & (np.asarray(end.minute) == int(minute))
    return pd.Series(hit, index=bars.index)
