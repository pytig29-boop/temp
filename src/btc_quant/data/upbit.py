"""Upbit(및 Upbit 호환 v1 API: Bithumb) 공개 시세 수집기.

- 캔들: GET /v1/candles/minutes/{unit}?market=KRW-BTC&to=<UTC ISO>&count=200
  최신→과거 순으로 최대 200개, `to` 이전 캔들 반환. 거래 없는 분은 캔들이 생성되지 않는다.
- 호가: GET /v1/orderbook?markets=KRW-BTC
Bithumb v1 응답 형식은 Upbit과 동일(실측). 단 `to` 파라미터 시간대 해석이 다름 → format_to().
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

BASE_URLS = {"upbit": "https://api.upbit.com", "bithumb": "https://api.bithumb.com"}


KST = timezone(timedelta(hours=9))


def format_to(exchange: str, to: datetime) -> str:
    """Upbit은 UTC ISO('Z')를 받는다. Bithumb은 'Z'/오프셋을 거부하고 무표기 시각을 KST로 해석한다
    (2026-11-09 실측)."""
    if exchange == "bithumb":
        return to.astimezone(KST).strftime("%Y-%m-%dT%H:%M:%S")
    return to.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_candles(payload: list[dict]) -> pd.DataFrame:
    if not payload:
        return pd.DataFrame()
    df = pd.DataFrame(payload)
    out = pd.DataFrame({
        "ts": pd.to_datetime(df["candle_date_time_utc"], utc=True),
        "open": df["opening_price"],
        "high": df["high_price"],
        "low": df["low_price"],
        "close": df["trade_price"],
        "volume": df["candle_acc_trade_volume"],
        "quote_volume": df["candle_acc_trade_price"],
    })
    return out.set_index("ts").sort_index()


def fetch_candles(exchange: str, market: str, unit: int, start: datetime, end: datetime,
                  max_rps: float = 8.0, session: requests.Session | None = None,
                  progress: bool = True) -> pd.DataFrame:
    """[start, end) 구간의 분봉을 end부터 과거로 페이지네이션하며 수집."""
    s = session or requests.Session()
    url = f"{BASE_URLS[exchange]}/v1/candles/minutes/{unit}"
    to = end.astimezone(timezone.utc)
    start = start.astimezone(timezone.utc)
    frames, n_req = [], 0
    while to > start:
        params = {"market": market, "count": 200, "to": format_to(exchange, to)}
        for attempt in range(6):
            r = s.get(url, params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(1.0 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        else:
            raise RuntimeError(f"rate-limited repeatedly at to={to}")
        page = parse_candles(r.json())
        n_req += 1
        if page.empty:
            break
        frames.append(page)
        oldest = page.index.min().to_pydatetime()
        if oldest >= to:  # 진행이 없으면 무한루프 방지
            break
        to = oldest
        if progress and n_req % 100 == 0:
            print(f"  {exchange} {market} {unit}m: {n_req} req, reached {to:%Y-%m-%d %H:%M}Z")
        time.sleep(1.0 / max_rps)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[(df.index >= start) & (df.index < end)]


def fetch_orderbook(exchange: str, market: str, session: requests.Session | None = None) -> dict:
    s = session or requests.Session()
    r = s.get(f"{BASE_URLS[exchange]}/v1/orderbook", params={"markets": market}, timeout=10)
    r.raise_for_status()
    return r.json()[0]
