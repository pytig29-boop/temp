"""1분봉 → 5분봉 변환 (계약 모듈)."""
from __future__ import annotations

import pandas as pd


def to_5m(m1: pd.DataFrame) -> pd.DataFrame:
    """index = 5분봉 시작 시각. 1분봉 5개가 모두 있어야(filled 아님) 완전한 봉(`complete`)."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "quote_volume" in m1:
        agg["quote_volume"] = "sum"
    b = m1.resample("5min", label="left", closed="left").agg(agg)
    if "filled" in m1:
        b["complete"] = ~m1["filled"].resample("5min", label="left", closed="left").max().astype(bool)
    else:
        b["complete"] = m1["close"].resample("5min", label="left", closed="left").count() == 5
    return b.dropna(subset=["close"])
