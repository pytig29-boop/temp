"""호가 스냅샷 → 스프레드·시장가 체결 비용 추정."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd

from . import coinone, korbit, upbit


def fetch_any(exchange: str, market: str) -> dict:
    if exchange == "korbit":
        return korbit.fetch_orderbook(market)
    if exchange == "coinone":
        return coinone.fetch_orderbook(market)
    return upbit.fetch_orderbook(exchange, market)


def market_buy_cost(units: list[dict], notional: float) -> float:
    """notional(호가 통화)만큼 시장가 매수 시 mid 대비 평균 체결가 괴리(비율)."""
    best_ask, best_bid = units[0]["ask_price"], units[0]["bid_price"]
    mid = (best_ask + best_bid) / 2
    remaining, qty = notional, 0.0
    for u in units:
        px, sz = u["ask_price"], u["ask_size"]
        take = min(remaining, px * sz)
        qty += take / px
        remaining -= take
        if remaining <= 1e-9:
            break
    if remaining > 1e-9:
        return float("nan")  # 표시된 호가 깊이로는 부족
    return (notional / qty) / mid - 1


def summarize_snapshot(ob: dict, notionals=(300_000, 3_000_000, 30_000_000)) -> dict:
    units = ob["orderbook_units"]
    ask, bid = units[0]["ask_price"], units[0]["bid_price"]
    mid = (ask + bid) / 2
    row = {
        "ts": pd.Timestamp(ob["timestamp"], unit="ms", tz="UTC"),
        "best_bid": bid, "best_ask": ask,
        "spread_bps": (ask - bid) / mid * 1e4,
        "bid_depth_top": sum(u["bid_size"] for u in units),
        "ask_depth_top": sum(u["ask_size"] for u in units),
    }
    for n in notionals:
        row[f"buy_impact_bps_{n}"] = market_buy_cost(units, n) * 1e4
    return row


def sample(exchange: str, market: str, seconds: int, interval: float = 2.0) -> pd.DataFrame:
    rows, t_end = [], time.time() + seconds
    while time.time() < t_end:
        try:
            rows.append(summarize_snapshot(fetch_any(exchange, market)))
        except Exception as e:  # 개별 실패는 기록만 하고 계속
            print(f"  snapshot failed at {datetime.now(timezone.utc):%H:%M:%S}: {e}")
        time.sleep(interval)
    return pd.DataFrame(rows).set_index("ts") if rows else pd.DataFrame()
