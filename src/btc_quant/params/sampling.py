"""REST 호가 샘플러 — 수집기(`ws_collector._book_row`)와 같은 행 스키마(가격·수량 리스트)로 저장한다.

`data.orderbook.sample` 은 매수 쪽 요약만 남기므로, 비용 보정(class ①)에는 양쪽 호가 전체를
보존하는 이 샘플러를 쓴다. 저장 위치는 WS 파티션(date=…/HH-part.parquet)과 겹치지 않게
    data/raw/exchange=<ex>/symbol=BTC-KRW/orderbook/rest_<UTC>.parquet
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SYMBOL = "BTC-KRW"


def to_book_row(ob: dict, ts_recv: float | None = None) -> dict:
    """`orderbook.fetch_any` 결과(upbit 형식) → 수집기 행 스키마."""
    units = ob["orderbook_units"]
    bids = sorted(((float(u["bid_price"]), float(u["bid_size"])) for u in units), key=lambda x: -x[0])
    asks = sorted(((float(u["ask_price"]), float(u["ask_size"])) for u in units), key=lambda x: x[0])
    bb, ba = bids[0][0], asks[0][0]
    mid = (bb + ba) / 2
    return {
        "ts": int(ob["timestamp"]), "ts_recv": float(ts_recv if ts_recv is not None else time.time()),
        "best_bid": bb, "best_ask": ba, "mid": mid, "spread_bps": (ba - bb) / mid * 1e4,
        "bid_px": [p for p, _ in bids], "bid_qty": [q for _, q in bids],
        "ask_px": [p for p, _ in asks], "ask_qty": [q for _, q in asks],
    }


def sample_books(exchange: str, seconds: float, interval: float = 2.0, market: str = "KRW-BTC",
                 root: Path = Path("data/raw"), fetch=None) -> Path | None:
    """`seconds` 동안 `interval` 초마다 REST 호가를 받아 한 파일로 저장. 반환: 파일 경로(행 없으면 None)."""
    if fetch is None:
        from btc_quant.data.orderbook import fetch_any
        fetch = fetch_any
    rows, t_end = [], time.time() + seconds
    while time.time() < t_end:
        try:
            rows.append(to_book_row(fetch(exchange, market)))
        except Exception as e:  # 개별 실패는 기록만
            print(f"  [{exchange}] snapshot failed {datetime.now(timezone.utc):%H:%M:%S}: {e}")
        time.sleep(interval)
    if not rows:
        return None
    out = Path(root) / f"exchange={exchange}" / f"symbol={SYMBOL}" / "orderbook"
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"rest_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.parquet"
    pd.DataFrame(rows).to_parquet(f, index=False)
    return f


def sample_many(exchanges: list[str], seconds: float, interval: float = 2.0,
                root: Path = Path("data/raw")) -> dict[str, Path | None]:
    """여러 거래소를 동시에(스레드) 샘플링."""
    res: dict[str, Path | None] = {}

    def run(ex):
        res[ex] = sample_books(ex, seconds, interval, root=root)

    ts = [threading.Thread(target=run, args=(ex,)) for ex in exchanges]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return res
