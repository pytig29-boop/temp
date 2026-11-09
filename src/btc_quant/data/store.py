"""Parquet 저장소 (월 단위 파티션) + DuckDB 조회.

data/raw/exchange=<ex>/symbol=<SYM>/timeframe=<tf>/year=YYYY/month=MM/part.parquet
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from .schema import normalize_ohlcv

DATA_ROOT = Path("data/raw")


def series_dir(exchange: str, symbol: str, timeframe: str, root: Path = DATA_ROOT) -> Path:
    return root / f"exchange={exchange}" / f"symbol={symbol}" / f"timeframe={timeframe}"


def write_ohlcv(df: pd.DataFrame, exchange: str, symbol: str, timeframe: str,
                root: Path = DATA_ROOT) -> list[Path]:
    """월별로 기존 파일과 병합해 저장한다(같은 ts는 새 값이 우선)."""
    df = normalize_ohlcv(df)
    written = []
    for (y, m), part in df.groupby([df.index.year, df.index.month]):
        d = series_dir(exchange, symbol, timeframe, root) / f"year={y:04d}" / f"month={m:02d}"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "part.parquet"
        if f.exists():
            old = pd.read_parquet(f)
            part = normalize_ohlcv(pd.concat([old, part]))
        part.to_parquet(f)
        written.append(f)
    return written


def read_ohlcv(exchange: str, symbol: str, timeframe: str, start: str | None = None,
               end: str | None = None, root: Path = DATA_ROOT) -> pd.DataFrame:
    glob = str(series_dir(exchange, symbol, timeframe, root) / "**" / "*.parquet")
    q = f"SELECT * FROM read_parquet('{glob}', hive_partitioning=false)"
    conds = []
    if start:
        conds.append(f"ts >= TIMESTAMPTZ '{start}'")
    if end:
        conds.append(f"ts < TIMESTAMPTZ '{end}'")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    df = duckdb.sql(q + " ORDER BY ts").df()
    if df.empty:
        raise FileNotFoundError(f"no data under {glob}")
    return normalize_ohlcv(df)
