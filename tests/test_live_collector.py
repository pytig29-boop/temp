"""WS 수집기: 파싱·저장·재연결 로직 (네트워크 없음, 가짜 WebSocket)."""
import asyncio
import json
import random
import time

import pandas as pd
import pytest

from btc_quant.live import ws_collector as wc

CO_TRADE = {"response_type": "DATA", "channel": "TRADE",
            "data": {"quote_currency": "KRW", "target_currency": "BTC", "id": "1794225921124001",
                     "timestamp": 1794225921124, "price": "101020000", "qty": "0.0212",
                     "is_seller_maker": True}}
CO_BOOK = {"response_type": "DATA", "channel": "ORDERBOOK",
           "data": {"quote_currency": "KRW", "target_currency": "BTC", "timestamp": 1794225900964,
                    "id": "1",
                    "asks": [{"price": str(101_200_000 - i * 10_000), "qty": "0.1"} for i in range(16)],
                    "bids": [{"price": str(101_000_000 - i * 10_000), "qty": "0.2"} for i in range(16)]}}
UP_TRADE = {"type": "trade", "code": "KRW-BTC", "timestamp": 1794225908561,
            "trade_timestamp": 1794225908517, "trade_price": 101000000.0, "trade_volume": 0.00092408,
            "ask_bid": "ASK", "sequential_id": 17942259085170000, "stream_type": "REALTIME"}
UP_BOOK = {"type": "orderbook", "code": "KRW-BTC", "timestamp": 1794225908632,
           "orderbook_units": [{"ask_price": 101_000_000 + i * 1000, "bid_price": 100_999_000 - i * 1000,
                                "ask_size": 1.0, "bid_size": 2.0} for i in range(30)]}


def test_coinone_parse_trade_and_book():
    ad = wc.CoinoneAdapter()
    [(k, r)] = ad.parse(json.dumps(CO_TRADE), 1794225921.3)
    assert k == "trades" and r["side"] == "buy" and r["price"] == 101_020_000 and r["qty"] == 0.0212
    [(k, r)] = ad.parse(json.dumps(CO_BOOK), 1794225921.3)
    assert k == "orderbook" and len(r["ask_px"]) == 15 and len(r["bid_px"]) == 15
    assert r["ask_px"] == sorted(r["ask_px"]) and r["best_ask"] == 101_050_000  # 내림차순 입력을 정렬
    assert r["bid_px"] == sorted(r["bid_px"], reverse=True) and r["best_bid"] == 101_000_000
    assert r["spread_bps"] == pytest.approx(50_000 / 101_025_000 * 1e4)
    assert ad.parse('{"response_type":"PONG"}', 0) == []
    with pytest.raises(RuntimeError):
        ad.parse('{"response_type":"ERROR","error_code":1}', 0)
    assert json.loads(ad.subscribe_messages()[0])["channel"] == "TRADE"


def test_upbit_parse_trade_and_book():
    ad = wc.UpbitAdapter()
    [(k, r)] = ad.parse(json.dumps(UP_TRADE).encode(), 1.0)
    assert k == "trades" and r["side"] == "sell" and r["ts_exchange"] == 1794225908517
    [(k, r)] = ad.parse(json.dumps(UP_BOOK).encode(), 1.0)
    assert len(r["bid_px"]) == 15 and r["best_bid"] == 100_999_000 and r["best_ask"] == 101_000_000
    assert ad.parse(b'{"status":"UP"}', 1.0) == []
    sub = json.loads(ad.subscribe_messages()[0])
    assert {d.get("type") for d in sub} >= {"trade", "orderbook"}


def _trade_row(ts_recv, i=0):
    return {"ts_exchange": int(ts_recv * 1000), "ts_recv": ts_recv, "price": 1.0 + i, "qty": 0.1,
            "side": "buy", "trade_id": str(i)}


def test_write_rows_hour_partitions_and_part_numbers(tmp_path):
    t0 = pd.Timestamp("2026-11-09 23:59:59", tz="UTC").timestamp()
    rows = [_trade_row(t0, 0), _trade_row(t0 + 2, 1)]  # 자정을 넘어 이틀/두 시각에 걸침
    files = wc.write_rows(tmp_path, "upbit", "trades", rows)
    rel = sorted(str(f.relative_to(tmp_path)) for f in files)
    assert rel == ["exchange=upbit/symbol=BTC-KRW/trades/date=2026-11-09/23-0000.parquet",
                   "exchange=upbit/symbol=BTC-KRW/trades/date=2026-11-10/00-0000.parquet"]
    again = wc.write_rows(tmp_path, "upbit", "trades", rows[:1])
    assert again[0].name == "23-0001.parquet"
    df = pd.read_parquet(files[0])
    assert str(df["ts_recv"].dt.tz) == "UTC" and df["price"].iloc[0] == 1.0
    assert not list(tmp_path.rglob("*.tmp"))


def test_sink_requests_flush_on_hour_change(tmp_path):
    s = wc.ParquetSink(tmp_path, flush_rows=10)
    t = 3600 * 1000 + 3599.5
    assert s.add("x", "trades", _trade_row(t)) is False
    assert s.add("x", "trades", _trade_row(t + 1)) is True   # 다음 시각
    s2 = wc.ParquetSink(tmp_path, flush_rows=2)
    s2.add("x", "trades", _trade_row(10))
    assert s2.add("x", "trades", _trade_row(11)) is True     # 행 수 한도


def test_backoff_bounds():
    rng = random.Random(0)
    for a in range(10):
        d = wc.backoff_delay(a, base=1, cap=60, rng=rng)
        assert 0.5 * min(60, 2 ** a) <= d <= min(60, 2 ** a)


def test_parse_exchanges():
    assert wc.parse_exchanges("coinone, upbit") == ("coinone", "upbit")
    with pytest.raises(ValueError):
        wc.parse_exchanges("korbit")


# ---- 가짜 WebSocket ------------------------------------------------------------------------

class FakeWS:
    def __init__(self, messages, hang_after=True):
        self.messages = list(messages)
        self.sent = []
        self.hang_after = hang_after

    async def send(self, m):
        self.sent.append(m)

    async def recv(self):
        if self.messages:
            await asyncio.sleep(0.01)
            return self.messages.pop(0)
        if self.hang_after:
            await asyncio.Event().wait()  # 아무것도 안 옴 → 정체
        raise ConnectionError("closed")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def make_connect(sessions):
    calls = []

    def connect(url, **kw):
        calls.append(url)
        if not sessions:
            return FakeWS([], hang_after=True)
        s = sessions.pop(0)
        if isinstance(s, Exception):
            raise s
        return s
    connect.calls = calls
    return connect


def _cfg(tmp_path, **kw):
    base = dict(exchanges=("upbit",), root=tmp_path / "raw", stale_seconds=0.3, flush_seconds=999,
                heartbeat_path=tmp_path / "runtime" / "hb", gaps_path=tmp_path / "runtime" / "gaps.jsonl",
                backoff_base=0.01, backoff_cap=0.02, ob_interval=1.0)
    base.update(kw)
    return wc.CollectorConfig(**base)


def test_collector_stale_reconnect_throttle_and_flush(tmp_path):
    books = [json.dumps(UP_BOOK).encode() for _ in range(5)]  # 0.01s 간격 → 샘플링 후 1개
    trades = [json.dumps(UP_TRADE).encode() for _ in range(3)]
    ws1 = FakeWS(books + trades)
    connect = make_connect([OSError("refused"), ws1, FakeWS(trades)])
    c = wc.Collector(_cfg(tmp_path), ws_connect=connect)
    seen = []
    c.add_listener(lambda ex, kind, row: seen.append(kind))
    summary = asyncio.run(c.run(duration=1.5, install_signals=False))
    st = c.stats["upbit"]
    assert st.errors >= 1 and st.stale_reconnects >= 1 and st.connects >= 2
    assert st.counts["orderbook"] == 5 and st.saved["orderbook"] == 1
    assert st.counts["trades"] == 6 and seen.count("orderbook") == 5
    files = [p for p in summary["files"]]
    kinds = {p.split("/symbol=BTC-KRW/")[1].split("/")[0] for p in files}
    assert kinds == {"trades", "orderbook"}
    n_tr = sum(len(pd.read_parquet(p)) for p in files if "/trades/" in p)
    assert n_tr == 6
    hb = json.loads((tmp_path / "runtime" / "hb").read_text())
    assert hb["exchanges"]["upbit"]["counts"]["trades"] == 6
    gaps = (tmp_path / "runtime" / "gaps.jsonl").read_text().strip().splitlines()
    assert gaps and json.loads(gaps[0])["exchange"] == "upbit"
    assert ws1.sent and "orderbook" in ws1.sent[0]  # 구독 메시지 전송됨
    assert connect.calls[0] == wc.UpbitAdapter.url


def test_last_message_age(tmp_path):
    c = wc.Collector(_cfg(tmp_path, exchanges=("coinone", "upbit")), ws_connect=make_connect([]))
    now = time.time()
    c._started = now - 50
    c.stats["upbit"].last_any = now - 3
    c.stats["upbit"].last_recv["trades"] = now - 7
    assert c.last_message_age("upbit", now=now) == pytest.approx(3)
    assert c.last_message_age("upbit", "trades", now=now) == pytest.approx(7)
    assert c.last_message_age(now=now) == pytest.approx(50)   # coinone 미수신 → 시작 이후
    assert c.last_message_age("upbit", "orderbook", now=now) == pytest.approx(50)


def test_check_health(tmp_path):
    p = tmp_path / "hb"
    assert wc.check_health(p)[0] is False
    now = 1_000_000.0
    p.write_text(json.dumps({"ts": now - 10, "exchanges": {"upbit": {"age": 5}}}))
    assert wc.check_health(p, now=now)[0] is True
    assert wc.check_health(p, max_age=5, now=now)[0] is False
    p.write_text(json.dumps({"ts": now, "exchanges": {"upbit": {"age": 999}}}))
    ok, msg = wc.check_health(p, max_data_age=300, now=now)
    assert not ok and "upbit" in msg


def test_cli_healthcheck_exit_code(tmp_path):
    from btc_quant.live.__main__ import main
    assert main(["healthcheck", "--heartbeat", str(tmp_path / "missing")]) == 1


def test_cli_kill_resume(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    from btc_quant.live.__main__ import main
    assert main(["kill", "--runtime", str(tmp_path), "--flatten"]) == 0
    assert (tmp_path / "FLATTEN").exists()
    assert main(["resume", "--runtime", str(tmp_path)]) == 0
    assert not (tmp_path / "FLATTEN").exists()
