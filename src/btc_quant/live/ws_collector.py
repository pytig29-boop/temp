"""공개 WebSocket 실시간 수집기 — 코인원 / 업비트 KRW-BTC 체결·호가 → Parquet.

공식 문서 (2026-11-09 확인, 메시지 형식은 같은 날 실측으로 검증)
- 코인원 Public WebSocket v2: https://docs.coinone.co.kr/reference/public-websocket-1
  (개요 https://docs.coinone.co.kr/docs/websocket-overview)
  endpoint ``wss://stream.coinone.co.kr``
  구독  {"request_type":"SUBSCRIBE","channel":"TRADE"|"ORDERBOOK",
         "topic":{"quote_currency":"KRW","target_currency":"BTC"}}
  응답  CONNECTED → SUBSCRIBED → DATA{channel, data}.  PING 요청 {"request_type":"PING"} → PONG.
        문서상 유휴 연결은 일정 시간(30분) 후 종료되므로 주기적 PING 필요.
  TRADE  data: id, timestamp(ms), price(str), qty(str), is_seller_maker(bool)
         is_seller_maker=True → 매수자가 테이커 → side="buy"
  ORDERBOOK data: timestamp(ms), id, asks/bids [{price, qty}] (asks 는 내림차순으로 옴 → 정렬 필요)
- 업비트 WebSocket: https://docs.upbit.com/kr/reference/websocket-guide
  (영문 https://global-docs.upbit.com/reference/websocket-guide,
   체결 https://docs.upbit.com/kr/reference/websocket-trade,
   호가 https://docs.upbit.com/kr/reference/websocket-orderbook)
  endpoint ``wss://api.upbit.com/websocket/v1``
  구독  [{"ticket":"..."},{"type":"trade","codes":["KRW-BTC"]},
         {"type":"orderbook","codes":["KRW-BTC"]},{"format":"DEFAULT"}]
  메시지는 바이너리 프레임의 JSON. 유휴 120초 시 서버가 연결 종료 → "PING" 텍스트 또는 WS ping.
  trade: trade_timestamp(ms), trade_price, trade_volume, ask_bid("BID"=매수 테이커), sequential_id
  orderbook: timestamp(ms), orderbook_units[{ask_price,bid_price,ask_size,bid_size}] (최대 30단계)

저장 레이아웃 (UTC 기준 날짜/시각 파티션; 기존 store.py 와 같은 UTC 관례)
  data/raw/exchange=<ex>/symbol=BTC-KRW/trades/date=YYYY-MM-DD/HH-<part>.parquet
  data/raw/exchange=<ex>/symbol=BTC-KRW/orderbook/date=YYYY-MM-DD/HH-<part>.parquet
  <part> 는 4자리 일련번호. 버퍼는 flush 주기마다(기본 300초), 행 수 한도 초과 시, 그리고
  UTC 시각(hour)이 바뀔 때 기록된다. 한 파일 안의 행은 모두 같은 UTC 시각(ts_recv 기준)에 속한다.

행 스키마
  trades:    ts_exchange, ts_recv (datetime64[ms, UTC]), price, qty (float64), side ("buy"/"sell"),
             trade_id (str)
  orderbook: ts, ts_recv, best_bid, best_ask, mid, spread_bps,
             bid_px, bid_qty, ask_px, ask_qty (list<float64>, 최우선→15단계)
  호가는 디스크 보호를 위해 기본 1초당 최대 1개로 샘플링한다(콜백에는 모든 갱신이 전달됨).

견고성: WS ping + 거래소 앱 레벨 PING, 지수 백오프+지터 재연결, 무수신 N초(stale_seconds) 시
재연결, 종료 시 버퍼 flush, 하트비트 파일(runtime/collector.heartbeat), 끊김 구간 기록
(runtime/collector_gaps.jsonl — 추후 REST 백필용).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

log = logging.getLogger("btc_quant.live.collector")

SYMBOL = "BTC-KRW"
DEPTH = 15


# --------------------------------------------------------------------------------------------
# 정규화
# --------------------------------------------------------------------------------------------

def _book_row(ts_ms: int, ts_recv: float, bids: list[tuple[float, float]],
              asks: list[tuple[float, float]], depth: int = DEPTH) -> dict | None:
    bids = sorted(bids, key=lambda x: -x[0])[:depth]
    asks = sorted(asks, key=lambda x: x[0])[:depth]
    if not bids or not asks:
        return None
    bb, ba = bids[0][0], asks[0][0]
    mid = (bb + ba) / 2
    return {
        "ts": int(ts_ms), "ts_recv": ts_recv,
        "best_bid": bb, "best_ask": ba, "mid": mid,
        "spread_bps": (ba - bb) / mid * 1e4 if mid > 0 else float("nan"),
        "bid_px": [p for p, _ in bids], "bid_qty": [q for _, q in bids],
        "ask_px": [p for p, _ in asks], "ask_qty": [q for _, q in asks],
    }


class Adapter:
    """거래소별 연결·구독·파싱 규칙. parse() 는 [(kind, row)] 를 돌려준다 (kind: trades|orderbook)."""

    name: str = ""
    url: str = ""
    app_ping_interval: float = 20.0

    def subscribe_messages(self) -> list[str]:
        raise NotImplementedError

    def app_ping(self) -> str | None:
        return None

    def parse(self, raw: str | bytes, ts_recv: float) -> list[tuple[str, dict]]:
        raise NotImplementedError


class CoinoneAdapter(Adapter):
    name = "coinone"
    url = "wss://stream.coinone.co.kr"

    def __init__(self, quote: str = "KRW", target: str = "BTC"):
        self.topic = {"quote_currency": quote, "target_currency": target}

    def subscribe_messages(self) -> list[str]:
        return [json.dumps({"request_type": "SUBSCRIBE", "channel": ch, "topic": self.topic})
                for ch in ("TRADE", "ORDERBOOK")]

    def app_ping(self) -> str | None:
        return json.dumps({"request_type": "PING"})

    def parse(self, raw, ts_recv):
        m = json.loads(raw)
        rt = m.get("response_type")
        if rt == "ERROR":
            raise RuntimeError(f"coinone ws error: {m}")
        if rt != "DATA":
            return []
        d = m.get("data") or {}
        ch = m.get("channel")
        if ch == "TRADE":
            return [("trades", {
                "ts_exchange": int(d["timestamp"]), "ts_recv": ts_recv,
                "price": float(d["price"]), "qty": float(d["qty"]),
                "side": "buy" if d.get("is_seller_maker") else "sell",
                "trade_id": str(d.get("id", "")),
            })]
        if ch == "ORDERBOOK":
            row = _book_row(int(d["timestamp"]), ts_recv,
                            [(float(x["price"]), float(x["qty"])) for x in d.get("bids", [])],
                            [(float(x["price"]), float(x["qty"])) for x in d.get("asks", [])])
            return [("orderbook", row)] if row else []
        return []


class UpbitAdapter(Adapter):
    name = "upbit"
    url = "wss://api.upbit.com/websocket/v1"
    app_ping_interval = 60.0  # 서버 유휴 타임아웃 120초보다 짧게

    def __init__(self, market: str = "KRW-BTC"):
        self.market = market

    def subscribe_messages(self) -> list[str]:
        return [json.dumps([{"ticket": f"btc-quant-{uuid.uuid4().hex[:12]}"},
                            {"type": "trade", "codes": [self.market]},
                            {"type": "orderbook", "codes": [self.market]},
                            {"format": "DEFAULT"}])]

    def app_ping(self) -> str | None:
        return "PING"

    def parse(self, raw, ts_recv):
        m = json.loads(raw)
        t = m.get("type")
        if "error" in m:
            raise RuntimeError(f"upbit ws error: {m}")
        if t == "trade":
            return [("trades", {
                "ts_exchange": int(m["trade_timestamp"]), "ts_recv": ts_recv,
                "price": float(m["trade_price"]), "qty": float(m["trade_volume"]),
                "side": "buy" if m.get("ask_bid") == "BID" else "sell",
                "trade_id": str(m.get("sequential_id", "")),
            })]
        if t == "orderbook":
            units = m.get("orderbook_units") or []
            row = _book_row(int(m["timestamp"]), ts_recv,
                            [(float(u["bid_price"]), float(u["bid_size"])) for u in units],
                            [(float(u["ask_price"]), float(u["ask_size"])) for u in units])
            return [("orderbook", row)] if row else []
        return []  # {"status":"UP"} 등


ADAPTERS: dict[str, Callable[[], Adapter]] = {"coinone": CoinoneAdapter, "upbit": UpbitAdapter}


# --------------------------------------------------------------------------------------------
# Parquet 저장
# --------------------------------------------------------------------------------------------

def _to_frame(kind: str, rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["ts_recv"] = pd.to_datetime((df["ts_recv"] * 1000).round().astype("int64"), unit="ms", utc=True)
    tcol = "ts_exchange" if kind == "trades" else "ts"
    df[tcol] = pd.to_datetime(df[tcol].astype("int64"), unit="ms", utc=True)
    return df


def partition_dir(root: Path, exchange: str, kind: str, hour: pd.Timestamp,
                  symbol: str = SYMBOL) -> Path:
    return (Path(root) / f"exchange={exchange}" / f"symbol={symbol}" / kind
            / f"date={hour:%Y-%m-%d}")


def _next_part(d: Path, hh: str) -> int:
    nums = []
    for p in d.glob(f"{hh}-*.parquet"):
        with contextlib.suppress(ValueError):
            nums.append(int(p.stem.split("-", 1)[1]))
    return max(nums, default=-1) + 1


def write_rows(root: Path, exchange: str, kind: str, rows: list[dict],
               symbol: str = SYMBOL) -> list[Path]:
    """행을 ts_recv 의 UTC 시각별로 나눠 새 part 파일로 기록(원자적 rename). 동기 함수."""
    if not rows:
        return []
    df = _to_frame(kind, rows)
    out = []
    hours = df["ts_recv"].dt.floor("h")
    for hour, part in df.groupby(hours, sort=True):
        d = partition_dir(root, exchange, kind, hour, symbol)
        d.mkdir(parents=True, exist_ok=True)
        hh = f"{hour:%H}"
        f = d / f"{hh}-{_next_part(d, hh):04d}.parquet"
        tmp = f.with_suffix(".parquet.tmp")
        part.reset_index(drop=True).to_parquet(tmp, index=False)
        os.replace(tmp, f)
        out.append(f)
    return out


class ParquetSink:
    """(exchange, kind) 별 버퍼. 시각이 바뀌거나 행 수/주기 한도에 걸리면 flush."""

    def __init__(self, root: Path, flush_seconds: float = 300.0, flush_rows: int = 20_000,
                 symbol: str = SYMBOL):
        self.root, self.flush_seconds, self.flush_rows, self.symbol = (
            Path(root), flush_seconds, flush_rows, symbol)
        self.buf: dict[tuple[str, str], list[dict]] = {}
        self.buf_hour: dict[tuple[str, str], int] = {}
        self.last_flush = time.monotonic()
        self.files: list[Path] = []
        self.rows_written: dict[tuple[str, str], int] = {}
        self._lock = asyncio.Lock()

    def add(self, exchange: str, kind: str, row: dict) -> bool:
        """행 추가. 즉시 flush 가 필요하면 True (시각 변경 또는 행 수 초과)."""
        key = (exchange, kind)
        hour = int(row["ts_recv"] // 3600)
        b = self.buf.setdefault(key, [])
        b.append(row)
        prev = self.buf_hour.setdefault(key, hour)
        if hour != prev:
            self.buf_hour[key] = hour
            return True
        return len(b) >= self.flush_rows

    def due(self) -> bool:
        return time.monotonic() - self.last_flush >= self.flush_seconds

    async def flush(self) -> list[Path]:
        async with self._lock:
            bufs, self.buf = self.buf, {}
            self.last_flush = time.monotonic()
            written: list[Path] = []
            for (ex, kind), rows in bufs.items():
                if not rows:
                    continue
                try:
                    files = await asyncio.to_thread(write_rows, self.root, ex, kind, rows, self.symbol)
                except Exception:
                    log.exception("parquet write failed (%s/%s, %d rows) — rows re-queued",
                                  ex, kind, len(rows))
                    self.buf.setdefault((ex, kind), [])[:0] = rows
                    continue
                written += files
                self.rows_written[(ex, kind)] = self.rows_written.get((ex, kind), 0) + len(rows)
            self.files += written
            return written


# --------------------------------------------------------------------------------------------
# 연결 루프
# --------------------------------------------------------------------------------------------

def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0,
                  rng: random.Random | None = None) -> float:
    """지수 백오프 + full jitter: U(0.5, 1.0) × min(cap, base·2^attempt)."""
    r = rng or random
    return min(cap, base * (2 ** max(0, attempt))) * (0.5 + 0.5 * r.random())


@dataclass
class StreamStats:
    connects: int = 0
    reconnects: int = 0
    stale_reconnects: int = 0
    errors: int = 0
    messages: int = 0
    counts: dict[str, int] = field(default_factory=dict)   # 정규화된 이벤트 수 (kind 별)
    saved: dict[str, int] = field(default_factory=dict)    # 버퍼에 들어간 행 수 (호가 샘플링 후)
    last_recv: dict[str, float] = field(default_factory=dict)  # kind → wall clock
    last_any: float | None = None


@dataclass
class CollectorConfig:
    exchanges: tuple[str, ...] = ("coinone", "upbit")
    root: Path = Path("data/raw")
    symbol: str = SYMBOL
    ob_interval: float = 1.0          # 디스크 기록용 호가 샘플링 간격(초)
    stale_seconds: float = 30.0       # 이 시간 동안 아무 메시지 없으면 재연결
    flush_seconds: float = 300.0
    flush_rows: int = 20_000
    heartbeat_path: Path | None = Path("runtime/collector.heartbeat")
    heartbeat_seconds: float = 10.0
    gaps_path: Path | None = Path("runtime/collector_gaps.jsonl")
    backoff_base: float = 1.0
    backoff_cap: float = 60.0
    alert_after_failures: int = 5     # 연속 연결 실패 N회 시 notifier 로 알림


class Collector:
    """여러 거래소 스트림을 동시에 수집한다.

    >>> c = Collector(CollectorConfig(exchanges=("upbit",)))
    >>> c.add_listener(lambda ex, kind, row: ...)   # 페이퍼 엔진 등: 모든 이벤트(샘플링 전)
    >>> await c.run(duration=60)
    >>> c.last_message_age("upbit", "trades")
    """

    def __init__(self, config: CollectorConfig | None = None, notifier: Any = None,
                 adapters: dict[str, Adapter] | None = None, ws_connect: Callable | None = None):
        self.cfg = config or CollectorConfig()
        self.adapters = adapters or {ex: ADAPTERS[ex]() for ex in self.cfg.exchanges}
        self.sink = ParquetSink(self.cfg.root, self.cfg.flush_seconds, self.cfg.flush_rows,
                                self.cfg.symbol)
        self.stats = {ex: StreamStats() for ex in self.adapters}
        self.notifier = notifier
        self._listeners: list[Callable[[str, str, dict], Any]] = []
        self._last_ob_saved: dict[str, float] = {}
        self._stop = asyncio.Event()
        self._started = time.time()
        if ws_connect is None:
            import websockets
            ws_connect = websockets.connect
        self._connect = ws_connect

    # ---- 외부 API --------------------------------------------------------------------------
    def add_listener(self, fn: Callable[[str, str, dict], Any]) -> None:
        self._listeners.append(fn)

    def last_message_age(self, exchange: str | None = None, kind: str | None = None,
                         now: float | None = None) -> float:
        """마지막 수신 후 경과 초. exchange/kind 미지정 시 해당 범위의 '가장 오래된' 값(보수적).
        한 번도 수신하지 못했으면 수집 시작 이후 경과 시간."""
        now = time.time() if now is None else now
        exs = [exchange] if exchange else list(self.stats)
        ages = []
        for ex in exs:
            s = self.stats[ex]
            t = s.last_recv.get(kind) if kind else s.last_any
            ages.append(now - (t if t is not None else self._started))
        return max(ages) if ages else float("inf")

    def stop(self) -> None:
        self._stop.set()

    # ---- 내부 ------------------------------------------------------------------------------
    async def _handle(self, ex: str, raw) -> None:
        ts_recv = time.time()
        st = self.stats[ex]
        st.messages += 1
        st.last_any = ts_recv
        events = self.adapters[ex].parse(raw, ts_recv)
        need_flush = False
        for kind, row in events:
            st.counts[kind] = st.counts.get(kind, 0) + 1
            st.last_recv[kind] = ts_recv
            for fn in self._listeners:
                try:
                    r = fn(ex, kind, row)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    log.exception("listener failed")
            if kind == "orderbook":
                last = self._last_ob_saved.get(ex)
                if last is not None and ts_recv - last < self.cfg.ob_interval:
                    continue
                self._last_ob_saved[ex] = ts_recv
            st.saved[kind] = st.saved.get(kind, 0) + 1
            need_flush |= self.sink.add(ex, kind, row)
        if need_flush:
            await self.sink.flush()

    def _record_gap(self, ex: str, start: float | None, end: float, reason: str) -> None:
        if not self.cfg.gaps_path or start is None:
            return
        try:
            self.cfg.gaps_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cfg.gaps_path, "a") as f:
                f.write(json.dumps({"exchange": ex, "start": start, "end": end,
                                    "seconds": round(end - start, 3), "reason": reason}) + "\n")
        except OSError:
            log.exception("gap log write failed")

    def _alert(self, text: str) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.send(text, level="warning")
        except Exception:  # notifier 는 원래 예외를 던지지 않지만 방어
            log.exception("notifier failed")

    async def _pinger(self, ex: str, ws) -> None:
        ad = self.adapters[ex]
        msg = ad.app_ping()
        if not msg:
            return
        while True:
            await asyncio.sleep(ad.app_ping_interval)
            await ws.send(msg)

    async def _run_stream(self, ex: str) -> None:
        ad, st = self.adapters[ex], self.stats[ex]
        failures = 0
        disconnected_at: float | None = None
        while not self._stop.is_set():
            reason = "closed"
            try:
                async with self._connect(ad.url, ping_interval=20, ping_timeout=20,
                                         close_timeout=5, max_size=2 ** 22) as ws:
                    st.connects += 1
                    if st.connects > 1:
                        st.reconnects += 1
                    for m in ad.subscribe_messages():
                        await ws.send(m)
                    log.info("%s connected (#%d)", ex, st.connects)
                    pinger = asyncio.create_task(self._pinger(ex, ws))
                    got_data = False
                    try:
                        while not self._stop.is_set():
                            recv = asyncio.ensure_future(ws.recv())
                            stop = asyncio.ensure_future(self._stop.wait())
                            done, _ = await asyncio.wait({recv, stop}, timeout=self.cfg.stale_seconds,
                                                         return_when=asyncio.FIRST_COMPLETED)
                            if recv not in done:
                                recv.cancel()
                                stop.cancel()
                                if self._stop.is_set():
                                    break
                                st.stale_reconnects += 1
                                reason = f"stale>{self.cfg.stale_seconds:g}s"
                                log.warning("%s: no message for %.0fs → reconnect", ex,
                                            self.cfg.stale_seconds)
                                break
                            stop.cancel()
                            raw = recv.result()
                            await self._handle(ex, raw)
                            if not got_data:
                                got_data = True
                                failures = 0
                                if disconnected_at is not None:
                                    self._record_gap(ex, disconnected_at, time.time(), reason)
                                    disconnected_at = None
                    finally:
                        pinger.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await pinger
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 연결 실패, 프로토콜 오류, 파싱 오류 등
                st.errors += 1
                reason = f"{type(e).__name__}: {e}"
                log.warning("%s stream error: %s", ex, reason)
            if self._stop.is_set():
                break
            if disconnected_at is None:
                disconnected_at = st.last_any or time.time()
            delay = backoff_delay(failures, self.cfg.backoff_base, self.cfg.backoff_cap)
            failures += 1
            if failures == self.cfg.alert_after_failures:
                self._alert(f"[collector] {ex}: {failures}회 연속 재연결 실패/끊김 — 마지막 사유: {reason}")
            log.info("%s reconnect in %.1fs (attempt %d, reason: %s)", ex, delay, failures, reason)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)

    def heartbeat(self) -> dict:
        now = time.time()
        return {
            "ts": now, "pid": os.getpid(), "uptime": round(now - self._started, 1),
            "exchanges": {
                ex: {"age": round(self.last_message_age(ex, now=now), 3),
                     "trade_age": round(self.last_message_age(ex, "trades", now=now), 3),
                     "connects": s.connects, "reconnects": s.reconnects,
                     "stale_reconnects": s.stale_reconnects, "errors": s.errors,
                     "messages": s.messages, "counts": dict(s.counts), "saved": dict(s.saved)}
                for ex, s in self.stats.items()},
        }

    def _write_heartbeat(self) -> None:
        p = self.cfg.heartbeat_path
        if not p:
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.heartbeat()))
            os.replace(tmp, p)
        except OSError:
            log.exception("heartbeat write failed")

    async def _housekeeping(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=min(self.cfg.heartbeat_seconds, 5.0))
            self._write_heartbeat()
            if self.sink.due():
                await self.sink.flush()

    async def run(self, duration: float | None = None, install_signals: bool = True) -> dict:
        """duration 초 후(또는 stop()/SIGINT/SIGTERM 시) 종료하고 버퍼를 flush. 요약 dict 반환."""
        self._stop = asyncio.Event()
        self._started = time.time()
        loop = asyncio.get_running_loop()
        if install_signals:
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.add_signal_handler(sig, self.stop)
        tasks = [asyncio.create_task(self._run_stream(ex), name=f"ws-{ex}") for ex in self.adapters]
        tasks.append(asyncio.create_task(self._housekeeping(), name="housekeeping"))
        try:
            if duration:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=duration)
                self._stop.set()
            else:
                await self._stop.wait()
            # 스트림이 스스로 종료되길 잠시 기다린 뒤 취소
            _, pending = await asyncio.wait(tasks, timeout=10)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            self._stop.set()
            await self.sink.flush()
            self._write_heartbeat()
            if install_signals:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    with contextlib.suppress(NotImplementedError, RuntimeError):
                        loop.remove_signal_handler(sig)
        return self.summary()

    def summary(self) -> dict:
        elapsed = max(1e-9, time.time() - self._started)
        return {
            "elapsed": round(elapsed, 1),
            "exchanges": {ex: {"messages": s.messages, "counts": dict(s.counts),
                               "saved": dict(s.saved),
                               "rate_per_s": {k: round(v / elapsed, 3) for k, v in s.counts.items()},
                               "connects": s.connects, "stale_reconnects": s.stale_reconnects,
                               "errors": s.errors}
                          for ex, s in self.stats.items()},
            "files": [str(p) for p in self.sink.files],
        }


def read_heartbeat(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def check_health(path: Path, max_age: float = 90.0, max_data_age: float = 300.0,
                 now: float | None = None) -> tuple[bool, str]:
    """하트비트 파일 기반 헬스체크: 파일이 max_age 이내로 갱신되었고, 모든 거래소의
    마지막 메시지가 max_data_age 이내여야 정상."""
    now = time.time() if now is None else now
    hb = read_heartbeat(path)
    if hb is None:
        return False, f"heartbeat missing/unreadable: {path}"
    age = now - float(hb.get("ts", 0))
    if age > max_age:
        return False, f"heartbeat stale ({age:.0f}s > {max_age:g}s)"
    bad = [f"{ex} age {v.get('age', 1e9):.0f}s" for ex, v in hb.get("exchanges", {}).items()
           if v.get("age", 1e9) > max_data_age]
    if bad:
        return False, "data stale: " + ", ".join(bad)
    return True, f"ok (heartbeat {age:.0f}s ago)"


def parse_exchanges(s: str | Iterable[str]) -> tuple[str, ...]:
    items = s.split(",") if isinstance(s, str) else list(s)
    out = tuple(x.strip().lower() for x in items if x.strip())
    unknown = [x for x in out if x not in ADAPTERS]
    if unknown:
        raise ValueError(f"unknown exchange(s): {unknown}; supported: {sorted(ADAPTERS)}")
    return out
