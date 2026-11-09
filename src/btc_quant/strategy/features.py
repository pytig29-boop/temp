"""1분봉 → 결정 주기 봉 변환 (계약 모듈)."""
from __future__ import annotations

import numpy as np
import pandas as pd

# 긴 봉(B ≥ LONG_BAR_MIN, 15m/1h/4h)의 complete 규칙 (docs/ENGINE_SPEC.md "결정 주기 B"):
# 봉 안에 '데이터 공백'(연속 filled ≥ OUTAGE_MIN 분 — data.quality.GAP_WARMUP_MIN_GAP 와 같은 30분)이 없고
# 1분봉이 B개 모두 있으면 complete. 거래량 0 인 조용한 분(filled 평탄 봉) 몇 개는 봉 OHLC 를 왜곡하지 않으므로
# 허용한다 — '전부 실제 봉' 규칙을 그대로 쓰면 코인원 Dev 에서 4h 봉의 25%(2025년 3%)만 complete 라 진입이
# 고활동 구간으로 편향되고 표본이 사라진다. B ≤ 5 는 기존 규칙(B개 모두 실제 봉) 그대로.
LONG_BAR_MIN = 15
OUTAGE_MIN = 30


def filled_run_length(filled) -> np.ndarray:
    """각 1분봉에서 끝나는 연속 filled 구간 길이 (실제 봉이면 0). 과거만 사용 (인과)."""
    f = np.asarray(filled, dtype=bool)
    pos = np.arange(f.size)
    last_real = np.maximum.accumulate(np.where(~f, pos, -1)) if f.size else pos
    return pos - last_real


def to_tf(m1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """index = 봉 시작 시각 (UTC epoch 기준 `minutes` 분 정렬; 1440 의 약수이므로 UTC 자정 정렬과 같다).

    B ≤ 5: 봉을 구성하는 1분봉 `minutes`개가 모두 있어야(filled 아님) 완전한 봉(`complete`).
    B ≥ 15: 1분봉 B개가 모두 있고 봉 안 어느 분에서도 연속 filled 길이(이전 봉에서 이어진 것 포함)가
    OUTAGE_MIN(30)분 미만이면 complete (데이터 공백만 배제, 조용한 분은 허용).
    `minutes == 1` 이면 1분봉 그대로 (complete = ~filled).
    """
    minutes = int(minutes)
    if minutes < 1:
        raise ValueError("minutes must be >= 1")
    cols = ["open", "high", "low", "close", "volume"] + (["quote_volume"] if "quote_volume" in m1 else [])
    if minutes == 1:
        # resample("1min") 과 같은 결과를 빠르게 (3M 봉에서 resample 생략)
        b = m1[cols].copy()
        if "filled" in m1:
            b["complete"] = ~m1["filled"].fillna(False).astype(bool)
        else:
            b["complete"] = m1["close"].notna()
        return b.dropna(subset=["close"])
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "quote_volume" in m1:
        agg["quote_volume"] = "sum"
    rule = f"{minutes}min"
    b = m1.resample(rule, label="left", closed="left").agg(agg)
    if "filled" in m1 and minutes >= LONG_BAR_MIN:
        run = pd.Series(filled_run_length(m1["filled"].fillna(False).to_numpy(bool)), index=m1.index)
        mx = run.resample(rule, label="left", closed="left").max()
        cnt = m1["close"].resample(rule, label="left", closed="left").count()
        b["complete"] = ((mx < OUTAGE_MIN) & (cnt == minutes)).to_numpy(bool)
    elif "filled" in m1:
        b["complete"] = ~m1["filled"].resample(rule, label="left", closed="left").max().astype(bool)
    else:
        b["complete"] = m1["close"].resample(rule, label="left", closed="left").count() == minutes
    return b.dropna(subset=["close"])


def to_5m(m1: pd.DataFrame) -> pd.DataFrame:
    """index = 5분봉 시작 시각. 1분봉 5개가 모두 있어야(filled 아님) 완전한 봉(`complete`)."""
    return to_tf(m1, 5)
