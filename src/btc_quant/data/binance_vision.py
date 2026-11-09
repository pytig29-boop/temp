"""Binance 공개 데이터(data.binance.vision) 월별 kline 다운로더.

연구용 데이터 소스. 2025-01-01 이후 spot 파일은 timestamp가 마이크로초 단위이므로
값의 크기로 단위를 판별한다.
"""
from __future__ import annotations

import io
import zipfile

import pandas as pd
import requests

BASE = "https://data.binance.vision/data/spot"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]


def to_utc(ts: pd.Series) -> pd.DatetimeIndex:
    ts = ts.astype("int64")
    unit = "us" if ts.iloc[0] > 10**14 else "ms"
    return pd.to_datetime(ts, unit=unit, utc=True)


def parse_kline_csv(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), header=None)
    if isinstance(df.iloc[0, 0], str):  # 일부 파일은 헤더 포함
        df = pd.read_csv(io.BytesIO(raw))
    df.columns = COLS
    out = df[["open", "high", "low", "close", "volume", "quote_volume", "trades",
              "taker_buy_base"]].astype("float64")
    out.index = to_utc(df["open_time"])
    out.index.name = "ts"
    return out


def month_range(start: str, end: str) -> list[str]:
    return [p.strftime("%Y-%m") for p in pd.period_range(start, end, freq="M")]


def fetch_monthly(symbol: str, interval: str, month: str,
                  session: requests.Session | None = None) -> pd.DataFrame:
    s = session or requests.Session()
    url = f"{BASE}/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{month}.zip"
    r = s.get(url, timeout=60)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        return parse_kline_csv(z.read(z.namelist()[0]))
