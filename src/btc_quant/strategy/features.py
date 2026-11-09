"""1분봉 → 결정 주기 봉 변환 (계약 모듈)."""
from __future__ import annotations

import pandas as pd


def to_tf(m1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """index = 봉 시작 시각 (UTC epoch 기준 `minutes` 분 정렬).

    봉을 구성하는 1분봉 `minutes`개가 모두 있어야(filled 아님) 완전한 봉(`complete`).
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
    if "filled" in m1:
        b["complete"] = ~m1["filled"].resample(rule, label="left", closed="left").max().astype(bool)
    else:
        b["complete"] = m1["close"].resample(rule, label="left", closed="left").count() == minutes
    return b.dropna(subset=["close"])


def to_5m(m1: pd.DataFrame) -> pd.DataFrame:
    """index = 5분봉 시작 시각. 1분봉 5개가 모두 있어야(filled 아님) 완전한 봉(`complete`)."""
    return to_tf(m1, 5)
