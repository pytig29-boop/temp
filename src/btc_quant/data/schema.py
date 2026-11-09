"""공통 OHLCV 스키마.

모든 시계열은 UTC tz-aware DatetimeIndex(`ts`, 봉 시작 시각)와 아래 컬럼을 가진다.
"""
from __future__ import annotations

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume", "quote_volume"]


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """정렬·중복 제거·dtype 정리. 중복 timestamp는 마지막 수집값을 남긴다."""
    out = df.copy()
    if out.index.name != "ts":
        out = out.set_index("ts")
    out.index = pd.to_datetime(out.index, utc=True)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for c in OHLCV_COLUMNS:
        if c not in out.columns:
            out[c] = float("nan")
        out[c] = out[c].astype("float64")
    return out[OHLCV_COLUMNS + [c for c in out.columns if c not in OHLCV_COLUMNS]]
