"""코인원 공개 시세 수집기.

- GET /public/v2/chart/KRW/BTC?interval=1m&size<=500&timestamp=<ms>
  `timestamp` 이하(포함) 캔들을 최신→과거 순으로 반환. 2020년 이전까지 이력 존재 (2026-11-09 실측).
- GET /public/v2/orderbook/KRW/BTC?size=15   (size 30은 거부됨, 15는 허용 — 실측)
- 요청 제한: public 버킷 1,200, 초당 20 회복 (응답 헤더 public-ratelimit-*)
- 수수료: 2026-08-26부터 별도 공지 시까지 0% (30일 바우처 재발급, Open API 포함)
"""
from __future__ import annotations

import time

import pandas as pd
import requests

BASE_URL = "https://api.coinone.co.kr"
PAGE = 500


def _pair(market: str) -> tuple[str, str]:
    """KRW-BTC / BTC-KRW → ("KRW", "BTC")"""
    a, b = market.upper().split("-")
    return (a, b) if a == "KRW" else (b, a)


def _check(d: dict) -> dict:
    if d.get("result") != "success":
        raise RuntimeError(f"coinone error: {d}")
    return d


def parse_chart(payload: dict) -> pd.DataFrame:
    rows = _check(payload).get("chart", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "ts": pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True),
        "open": df["open"].astype(float), "high": df["high"].astype(float),
        "low": df["low"].astype(float), "close": df["close"].astype(float),
        "volume": df["target_volume"].astype(float),
        "quote_volume": df["quote_volume"].astype(float),
    })
    return out.set_index("ts").sort_index()


def _get(s: requests.Session, path: str, params: dict) -> dict:
    for attempt in range(6):
        r = s.get(BASE_URL + path, params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(1.0 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"rate-limited repeatedly: {path} {params}")


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def fetch_candles(market: str, unit: int, start, end, max_rps: float = 15.0,
                  session: requests.Session | None = None, progress: bool = True) -> pd.DataFrame:
    """[start, end) 구간을 end에서 과거로 500개씩 페이지네이션."""
    s = session or requests.Session()
    quote, target = _pair(market)
    t0, stop = _utc(start), _utc(end)
    cursor = int(stop.timestamp() * 1000) - 1
    frames, n = [], 0
    while cursor >= t0.timestamp() * 1000:
        page = parse_chart(_get(s, f"/public/v2/chart/{quote}/{target}",
                                {"interval": f"{unit}m", "size": PAGE, "timestamp": cursor}))
        n += 1
        if page.empty:
            break
        frames.append(page)
        oldest = int(page.index.min().timestamp() * 1000)
        if oldest > cursor:
            break
        cursor = oldest - 1
        if progress and n % 200 == 0:
            print(f"  coinone {target}/{quote} {unit}m: {n} req, reached {page.index.min():%Y-%m-%d %H:%M}Z")
        time.sleep(1.0 / max_rps)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[(df.index >= t0) & (df.index < stop)]


def parse_orderbook(payload: dict) -> dict:
    d = _check(payload)
    units = [{"ask_price": float(a["price"]), "ask_size": float(a["qty"]),
              "bid_price": float(b["price"]), "bid_size": float(b["qty"])}
             for a, b in zip(d["asks"], d["bids"])]
    return {"timestamp": int(d["timestamp"]), "orderbook_units": units}


def fetch_orderbook(market: str, session: requests.Session | None = None) -> dict:
    s = session or requests.Session()
    quote, target = _pair(market)
    return parse_orderbook(_get(s, f"/public/v2/orderbook/{quote}/{target}", {"size": 15}))
