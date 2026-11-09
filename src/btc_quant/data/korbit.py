"""코빗(디지털엑스) 공개 시세 수집기.

API 문서: https://docs.digitalx.miraeasset.com/  (2026-11-09 확인)
- REST base: https://api.digitalx.miraeasset.com   (구 api.korbit.co.kr)
- 응답은 {"success": true, "data": ...} 로 래핑
- GET /v2/candles?symbol=btc_krw&interval=1&start=&end=&limit=1..200  (timestamp = 캔들 시작, ms)
- GET /v2/orderbook?symbol=btc_krw   bids 내림차순 / asks 오름차순, price·qty 문자열
- GET /v2/tickSizePolicy?symbol=btc_krw
- Public API 요청 제한: IP 기준 초당 50회
- WebSocket: wss://ws-api.digitalx.miraeasset.com/v2/public
주의: 문서 예시 캔들에 high=9,999,990,000 / low=300,000 같은 비정상 체결이 있다 → quality.flag_wick_outliers 로 처리.
주의: 이 클라우드 IP는 Cloudflare에 차단됨. 사용자 PC 또는 VPS에서 실행한다.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd
import requests

BASE_URL = "https://api.digitalx.miraeasset.com"
PAGE = 200


def to_symbol(market: str) -> str:
    """KRW-BTC / BTC-KRW → btc_krw"""
    a, b = market.lower().split("-")
    return f"{b}_{a}" if a == "krw" else f"{a}_{b}"


def _unwrap(payload):
    if isinstance(payload, dict):
        if payload.get("success") is False:
            raise RuntimeError(f"korbit error: {payload}")
        if "data" in payload:
            return payload["data"]
    return payload


def parse_candles(payload) -> pd.DataFrame:
    rows = _unwrap(payload)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "ts": pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True),
        "open": df["open"].astype(float), "high": df["high"].astype(float),
        "low": df["low"].astype(float), "close": df["close"].astype(float),
        "volume": df["volume"].astype(float),
    })
    out["quote_volume"] = float("nan")  # API 미제공
    return out.set_index("ts").sort_index()


def _get(s: requests.Session, path: str, params: dict):
    for attempt in range(6):
        r = s.get(BASE_URL + path, params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 1)) * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"rate-limited repeatedly: {path} {params}")


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def fetch_candles(market: str, unit: int, start: datetime, end: datetime, max_rps: float = 20.0,
                  session: requests.Session | None = None, progress: bool = True) -> pd.DataFrame:
    """[start, end) 구간을 과거→현재 방향으로 200개씩 페이지네이션."""
    s = session or requests.Session()
    sym = to_symbol(market)
    step = pd.Timedelta(minutes=unit * PAGE)
    t0, stop = _utc(start), _utc(end)
    cur = t0
    frames, n = [], 0
    while cur < stop:
        nxt = min(cur + step, stop)
        page = parse_candles(_get(s, "/v2/candles", {
            "symbol": sym, "interval": str(unit), "limit": PAGE,
            "start": int(cur.timestamp() * 1000), "end": int(nxt.timestamp() * 1000) - 1}))
        if not page.empty:
            frames.append(page)
        cur, n = nxt, n + 1
        if progress and n % 200 == 0:
            print(f"  korbit {sym} {unit}m: {n} req, reached {cur:%Y-%m-%d %H:%M}Z")
        time.sleep(1.0 / max_rps)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[(df.index >= t0) & (df.index < stop)]


def parse_orderbook(payload) -> dict:
    """공통 호가 형식(upbit 스타일 orderbook_units)으로 변환."""
    d = _unwrap(payload)
    units = [{"ask_price": float(a["price"]), "ask_size": float(a["qty"]),
              "bid_price": float(b["price"]), "bid_size": float(b["qty"])}
             for a, b in zip(d["asks"], d["bids"])]
    return {"timestamp": int(d["timestamp"]), "orderbook_units": units}


def fetch_orderbook(market: str, session: requests.Session | None = None) -> dict:
    s = session or requests.Session()
    return parse_orderbook(_get(s, "/v2/orderbook", {"symbol": to_symbol(market)}))


def fetch_tick_size_policy(market: str, session: requests.Session | None = None):
    s = session or requests.Session()
    return _unwrap(_get(s, "/v2/tickSizePolicy", {"symbol": to_symbol(market)}))
