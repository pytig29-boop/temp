"""페이퍼 서비스 — 수집기(코인원 체결·호가 + 업비트 참조가) → 1분봉 → active 1 + challenger ≤3 트레이더.

흐름
1. 수집기 시작(기본은 디스크 기록 없음 — 상시 기록은 collector 컨테이너 담당, `record=True` 로 켤 수 있음).
2. 첫 코인원 체결 수신 후 다음 분 M0 부터 실시간 봉을 만든다. M0+수 초에 REST 1분 캔들로
   [M0 − lookback, M0) 워밍업 이력을 받아 seed → 각 트레이더 attach(새로 시작 / 재시작 시 공백 재생).
   워밍업 동안 닫힌 실시간 봉은 대기열에 두었다가 처리.
3. 코인원 체결마다: 봉 생성기, 리스크 시세 갱신(업비트 참조가 괴리 판정), SL/TP 도달 순간 호가 캡처.
   0.5초 주기로 분 마감 확인 → 닫힌 봉을 모든 트레이더에 전달.
4. 10초마다 하트비트(runtime/paper/service.heartbeat + 전략별 heartbeat.json), KST 09:00 일일 요약,
   레지스트리 변경 감지(적용은 재시작), 텔레그램 알림(진입/청산/정지).
5. SIGINT/SIGTERM → 수집기 정지, 상태 저장, 종료 알림.

전략별 리스크 한도·등급: 스펙 파일 옆 `<스펙 파일명>.risk.yaml` (없으면 기본값 = config/gates.yaml 의 하한).
    grade: A|B
    limits: {daily_loss: -0.03, weekly_loss: -0.06, max_drawdown: -0.12, max_trades_per_day: 5, ...}
  (evaluation.gates.risk_limits_from_validation 의 키 daily_loss_limit/weekly_loss_limit/drawdown_limit 도 허용)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import yaml

from btc_quant.backtest import types as bt_types
from btc_quant.backtest.types import CostModel
from btc_quant.live.risk import KST, RiskLimits, RiskManager
from btc_quant.live.ws_collector import Collector, CollectorConfig
from btc_quant.strategy.spec import StrategySpec

from .bars import (DEFAULT_WARMUP_DAYS, MAX_WARMUP_DAYS, M1History, MinuteBarBuilder, fetch_warmup,
                   minute_ts, required_lookback_bars, window_minutes)
from .fills import DEFAULT_NOTIONAL
from .trader import PaperTrader, strategy_key

log = logging.getLogger("btc_quant.paper.service")

MAX_CHALLENGERS = 3
DEFAULT_LIMITS = RiskLimits(daily_loss=-0.03, weekly_loss=-0.06, max_drawdown=-0.12, max_trades_per_day=5)
COSTS: dict[str, CostModel] = {c.name: c for c in (
    bt_types.COINONE_BASE, bt_types.COINONE_CONSERVATIVE, bt_types.COINONE_STRESS,
    bt_types.UPBIT_BASE, bt_types.UPBIT_CONSERVATIVE)}


# --------------------------------------------------------------------------------------------
# 전략 슬롯
# --------------------------------------------------------------------------------------------

@dataclass
class StrategySlot:
    spec: StrategySpec
    role: str                       # active | challenger | spec
    grade: str = "B"
    limits: RiskLimits = DEFAULT_LIMITS
    source: str = ""

    @property
    def key(self) -> str:
        return strategy_key(self.spec)


_LIMIT_ALIASES = {"daily_loss_limit": "daily_loss", "weekly_loss_limit": "weekly_loss",
                  "drawdown_limit": "max_drawdown"}


def limits_from_dict(d: dict | None, base: RiskLimits = DEFAULT_LIMITS) -> RiskLimits:
    if not d:
        return base
    names = {f.name for f in fields(RiskLimits)}
    kw = {f.name: getattr(base, f.name) for f in fields(RiskLimits)}
    for k, v in d.items():
        k = _LIMIT_ALIASES.get(k, k)
        if k in names:
            kw[k] = int(math.ceil(float(v))) if k in ("max_trades_per_day", "max_api_errors") else float(v)
    return RiskLimits(**kw)


def sidecar_path(spec_path: Path) -> Path:
    p = Path(spec_path)
    return p.with_name(p.stem + ".risk.yaml")


def load_risk_meta(path: Path | None, grade: str, limits: RiskLimits) -> tuple[str, RiskLimits]:
    if path is None or not Path(path).exists():
        return grade, limits
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return str(d.get("grade", grade)).upper(), limits_from_dict(d.get("limits"), limits)


def slots_from_specs(paths: list[str | Path], grade: str = "B", risk_path: str | Path | None = None
                     ) -> list[StrategySlot]:
    base_grade, base_limits = load_risk_meta(Path(risk_path) if risk_path else None, grade, DEFAULT_LIMITS)
    out = []
    for p in paths:
        spec = StrategySpec.from_yaml(p)
        g, lim = load_risk_meta(sidecar_path(Path(p)), base_grade, base_limits)
        out.append(StrategySlot(spec, "spec", g, lim, str(p)))
    return out


def slots_from_registry(root: str | Path | None = None, grade: str = "B") -> list[StrategySlot]:
    """레지스트리의 active 1개 + challenger 최대 3개. 레지스트리를 열 수 없으면(읽기 전용 등) 파일을 직접 읽는다."""
    entries: list[tuple[str, Path]] = []
    try:
        from btc_quant.registry.strategies import StrategyRegistry
        reg = StrategyRegistry(root)
        act = reg.active()
        if act:
            entries.append(("active", Path(act["path"])))
        entries += [("challenger", Path(c["path"])) for c in reg.list("challenger")]
    except OSError as e:   # 읽기 전용 마운트에서 mkdir 실패 등
        from btc_quant.registry.ledger import repo_root
        base = Path(root) if root is not None else repo_root()
        log.warning("registry unavailable (%s); reading strategies/ directly", e)
        entries += [("active", p) for p in sorted((base / "strategies" / "active").glob("*_v*.yaml"))[:1]]
        entries += [("challenger", p) for p in sorted((base / "strategies" / "challenger").glob("*_v*.yaml"))]
    chal = [e for e in entries if e[0] == "challenger"]
    if len(chal) > MAX_CHALLENGERS:
        log.warning("%d challengers; using first %d", len(chal), MAX_CHALLENGERS)
    entries = [e for e in entries if e[0] == "active"] + chal[:MAX_CHALLENGERS]
    out = []
    for role, p in entries:
        g, lim = load_risk_meta(sidecar_path(p), grade, DEFAULT_LIMITS)
        out.append(StrategySlot(StrategySpec.from_yaml(p), role, g, lim, str(p)))
    return out


# --------------------------------------------------------------------------------------------
# 서비스
# --------------------------------------------------------------------------------------------

class NullSink:
    """수집기 저장 비활성화(페이퍼 프로세스는 디스크에 원천 데이터를 쓰지 않는다)."""
    files: list = []
    rows_written: dict = {}

    def add(self, exchange, kind, row) -> bool:
        return False

    def due(self) -> bool:
        return False

    async def flush(self) -> list:
        return []


@dataclass
class ServiceConfig:
    runtime_dir: Path = Path("runtime")
    out_root: Path | None = None            # 기본 runtime/paper
    cost: str = "coinone_conservative"
    notional: float = DEFAULT_NOTIONAL
    grace_seconds: float = 2.0
    warmup_days: float = DEFAULT_WARMUP_DAYS
    max_warmup_days: float = MAX_WARMUP_DAYS
    warmup_delay: float = 4.0               # M0 이후 REST 조회까지 대기(마지막 캔들 확정)
    first_trade_timeout: float = 120.0
    heartbeat_seconds: float = 10.0
    tick_seconds: float = 0.5
    summary_hour_kst: int = 9
    record: bool = False
    data_root: Path = Path("data/raw")
    exchanges: tuple[str, ...] = ("coinone", "upbit")
    ref_max_age: float = 60.0               # 참조가(업비트)가 이보다 오래되면 괴리 판정에 쓰지 않음
    registry_root: Path | None = None
    rescan_seconds: float = 600.0

    @property
    def out(self) -> Path:
        return Path(self.out_root) if self.out_root is not None else Path(self.runtime_dir) / "paper"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaperService:
    def __init__(self, slots: list[StrategySlot], cfg: ServiceConfig | None = None, notifier: Any = None,
                 collector: Collector | None = None, fetch: Callable | None = None,
                 clock: Callable[[], float] = time.time):
        if not slots:
            raise ValueError("no strategies to paper trade (registry has no active/challenger and no --spec)")
        keys = [s.key for s in slots]
        if len(set(keys)) != len(keys):
            raise ValueError(f"duplicate strategy keys {keys}")
        self.cfg = cfg or ServiceConfig()
        self.slots = slots
        self.notifier = notifier
        self.fetch = fetch
        self.clock = clock
        self.cost = COSTS[self.cfg.cost]
        self.cfg.out.mkdir(parents=True, exist_ok=True)
        self.traders: dict[str, PaperTrader] = {}
        for s in slots:
            self.traders[s.key] = self._make_trader(s)
        self.lookback_min = max(window_minutes(t.lookback_5m) for t in self.traders.values())
        self.builder = MinuteBarBuilder(self.cfg.grace_seconds)
        self.hist = M1History(max_rows=self.lookback_min + 2 * 1440)
        self.ready = False
        self.pending: list = []
        self.book: dict | None = None
        self.ref_price: float | None = None
        self.ref_recv: float | None = None
        self.first_trade_wall: float | None = None
        self.bars_processed = 0
        self.errors = 0
        self.started = clock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bg: set[asyncio.Task] = set()
        self._state_path = self.cfg.out / "service_state.json"
        self._svc_state = _read_json(self._state_path) or {}
        if "last_summary" not in self._svc_state:   # 첫 기동: 다음 KST 09:00 부터 요약
            today = datetime.fromtimestamp(clock(), timezone.utc).astimezone(KST)
            if today.hour >= self.cfg.summary_hour_kst:
                self._svc_state["last_summary"] = today.strftime("%Y-%m-%d")
        self._day_stats: dict[str, dict] = {k: {"trades": 0, "net": 1.0} for k in self.traders}
        self.collector = collector or self._make_collector()
        self.collector.add_listener(self.on_event)

    # ------------------------------------------------------------------ 구성
    def _make_trader(self, s: StrategySlot) -> PaperTrader:
        out = self.cfg.out / s.key
        out.mkdir(parents=True, exist_ok=True)
        rp = out / "risk.json"
        kill_dir = Path(self.cfg.runtime_dir)
        if rp.exists():
            risk = RiskManager.load(rp, s.limits, strategy_id=s.key, grade=s.grade, kill_dir=kill_dir)
        else:
            risk = RiskManager(s.limits, s.key, s.grade, kill_dir=kill_dir)
        lb = required_lookback_bars(s.spec, default_days=self.cfg.warmup_days,
                                    max_days=self.cfg.max_warmup_days)
        tr = PaperTrader(s.spec, self.cost, risk, s.grade, out_dir=out, initial_equity=self.cfg.notional,
                         lookback_5m=lb, clock=self.clock)
        restored = tr.load_state()
        (out / "spec.yaml").write_text(s.spec.to_yaml(), encoding="utf-8")
        (out / "meta.json").write_text(json.dumps({
            "key": s.key, "role": s.role, "grade": s.grade, "source": s.source, "cost": self.cost.name,
            "limits": {f.name: getattr(s.limits, f.name) for f in fields(RiskLimits)},
            "lookback_5m": lb, "restored": restored}, ensure_ascii=False, indent=1))
        log.info("trader %s (%s, grade %s, lookback %d×5m, %s)", s.key, s.role, s.grade, lb,
                 "restored" if restored else "fresh")
        return tr

    def _make_collector(self) -> Collector:
        cc = CollectorConfig(exchanges=self.cfg.exchanges, root=self.cfg.data_root,
                             heartbeat_path=None, gaps_path=self.cfg.out / "collector_gaps.jsonl")
        c = Collector(cc, notifier=self.notifier)
        if not self.cfg.record:
            c.sink = NullSink()
        return c

    # ------------------------------------------------------------------ 알림
    def notify(self, text: str, level: str = "info") -> None:
        if self.notifier is None:
            log.info("notify[%s]: %s", level, text)
            return
        if self._loop is not None and self._loop.is_running():
            t = self._loop.create_task(self.notifier.asend(text, level))
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)
        else:
            self.notifier.send(text, level)

    def _handle_events(self, key: str, events: list[dict]) -> None:
        for e in events:
            t = e.get("type")
            if t == "exit":
                st = self._day_stats.setdefault(key, {"trades": 0, "net": 1.0})
                st["trades"] += 1
                if e.get("net_ret") is not None:
                    st["net"] *= 1 + e["net_ret"]
            if not e.get("live", True):
                continue
            if t == "entry":
                bc = e.get("book_cost_bps")
                self.notify(f"[paper] {key} 진입 {e['price']:,.0f} (SL {e['sl']:,.0f} / TP {e['tp']:,.0f})"
                            + (f", 호가비용 {bc:.1f}bp" if bc is not None else ""))
            elif t == "exit":
                self.notify(f"[paper] {key} 청산 {e['reason']} {e['price']:,.0f} "
                            f"net {e['net_ret']:+.3%} → 자산 {e['equity']:,.0f}")
            elif t == "risk_event":
                lvl = "critical" if e.get("level", 0) >= 4 else "warning"
                if e.get("event") in ("halt", "flag", "pause", "cleared"):
                    self.notify(f"[paper] {key} 리스크 {e['event']} {e.get('code') or e.get('codes')} "
                                f"({e.get('level_name', '')}): {e.get('reason', '')}", lvl)

    # ------------------------------------------------------------------ 데이터 수신
    def on_event(self, ex: str, kind: str, row: dict) -> None:
        now = self.clock()
        try:
            if ex == "coinone" and kind == "orderbook":
                self.book = row
            elif ex == "upbit" and kind == "trades":
                self.ref_price, self.ref_recv = float(row["price"]), now
            elif ex == "coinone" and kind == "trades":
                if self.first_trade_wall is None:
                    self.first_trade_wall = now
                    self.builder.start_at(int(now // 60) + 1)
                self.builder.on_trade(row["ts_exchange"], row["price"], row["qty"])
                ref = (self.ref_price if self.ref_recv is not None
                       and now - self.ref_recv <= self.cfg.ref_max_age else None)
                dt = datetime.fromtimestamp(now, timezone.utc)
                for tr in self.traders.values():
                    if ref is None and tr.risk.s.last_ref_price is not None:
                        # 참조가가 오래됨 → 괴리 판정 중단 (오래된 참조가로 PAUSE 가 걸려 있지 않도록)
                        tr.risk.s.last_ref_price = tr.risk.s.last_deviation = None
                    tr.risk.on_market_data(dt, price=row["price"], ref_price=ref)
                    tr.on_price(row["ts_exchange"], row["price"], self.book)
                self.process(now)
        except Exception:
            self.errors += 1
            log.exception("on_event failed (%s/%s)", ex, kind)

    def process(self, now: float | None = None) -> int:
        """닫을 수 있는 분을 닫아 트레이더에 전달. 처리한 봉 수."""
        now = self.clock() if now is None else now
        bars = self.builder.on_time(now)
        if not self.ready:
            self.pending += bars
            return 0
        return self._feed(bars)

    def _feed(self, bars) -> int:
        n = 0
        for bar in bars:
            for nb in self.hist.append(bar):
                n += 1
                for key, tr in self.traders.items():
                    try:
                        self._handle_events(key, tr.on_bar(nb, self.hist, self.book, live=True))
                    except Exception as e:
                        self.errors += 1
                        log.exception("trader %s failed on bar %s", key, nb.ts)
                        self.notify(f"[paper] {key} 처리 오류: {type(e).__name__}: {e}", "warning")
        self.bars_processed += n
        return n

    # ------------------------------------------------------------------ 워밍업
    async def warmup(self) -> None:
        t0 = self.clock()
        while self.first_trade_wall is None:
            if self.clock() - t0 > self.cfg.first_trade_timeout:
                # 체결이 안 들어와도 시작은 한다(봉은 filled, 리스크가 stale 로 진입 차단)
                m = int(self.clock() // 60) + 1
                self.builder.start_at(m)
                self.first_trade_wall = self.clock()
                log.warning("no coinone trades for %.0fs; starting bars at %s", self.cfg.first_trade_timeout,
                            minute_ts(m))
                break
            await asyncio.sleep(0.2)
        m0 = self.builder.next_minute
        wait = m0 * 60 + self.cfg.warmup_delay - self.clock()
        if wait > 0:
            await asyncio.sleep(wait)
        end = minute_ts(m0)
        log.info("warm-up: fetching %d min of coinone 1m candles up to %s", self.lookback_min, end)
        raw = await asyncio.to_thread(fetch_warmup, end, self.lookback_min, "KRW-BTC", self.fetch)
        n = self.hist.seed(raw)
        log.info("warm-up: %d bars (%s → %s)", n, self.hist.first_ts, self.hist.last_ts)
        for key, tr in self.traders.items():
            try:
                self._handle_events(key, tr.attach(self.hist, self.book))
            except Exception:
                self.errors += 1
                log.exception("attach failed for %s", key)
        self.ready = True
        pending, self.pending = self.pending, []
        self._feed(pending)
        self.notify(f"[paper] 시작: {', '.join(f'{s.key}({s.role},{s.grade})' for s in self.slots)} "
                    f"/ 워밍업 {n}봉, 비용 {self.cost.name}")

    # ------------------------------------------------------------------ 하트비트·요약
    def heartbeat(self) -> dict:
        now = self.clock()
        dt = datetime.fromtimestamp(now, timezone.utc)
        traders = {}
        for key, tr in self.traders.items():
            st = tr.status(dt)
            st["last_decision"] = ({k: tr.last_decision.get(k) for k in
                                    ("ts", "entry_ok", "entry_ok_gated", "risk_reason", "exit_sig")}
                                   if tr.last_decision else None)
            traders[key] = st
        c = self.collector
        return {
            "ts": now, "pid": os.getpid(), "uptime": round(now - self.started, 1), "ready": self.ready,
            "bars": self.bars_processed, "hist_last": str(self.hist.last_ts), "errors": self.errors,
            "late_trades": self.builder.late_trades, "builder_next": str(minute_ts(self.builder.next_minute))
            if self.builder.next_minute is not None else None,
            "book_age": round(now - self.book["ts_recv"], 3) if self.book else None,
            "ref_price": self.ref_price,
            "data_age": {ex: round(c.last_message_age(ex, "trades", now=now), 3) for ex in c.stats},
            "traders": traders,
        }

    def write_heartbeat(self) -> None:
        hb = self.heartbeat()
        _atomic_json(self.cfg.out / "service.heartbeat", hb)
        for key, st in hb["traders"].items():
            _atomic_json(self.cfg.out / key / "heartbeat.json", {"ts": hb["ts"], **st})

    def maybe_daily_summary(self) -> bool:
        now = datetime.fromtimestamp(self.clock(), timezone.utc).astimezone(KST)
        today = now.strftime("%Y-%m-%d")
        if now.hour < self.cfg.summary_hour_kst or self._svc_state.get("last_summary") == today:
            return False
        lines = [f"[paper] 일일 요약 {today} KST"]
        for key, tr in self.traders.items():
            st = tr.status(now)
            ds = self._day_stats.get(key, {"trades": 0, "net": 1.0})
            lines.append(f"- {key}: 자산 {tr.equity:,.0f} ({st['return']:+.2%} 누적), 24h 거래 {ds['trades']}회 "
                         f"{ds['net'] - 1:+.2%}, 정지 {st['halt_level']}, 포지션 {'有' if st['position'] else '無'}")
        self.notify("\n".join(lines))
        self._day_stats = {k: {"trades": 0, "net": 1.0} for k in self.traders}
        self._svc_state["last_summary"] = today
        _atomic_json(self._state_path, self._svc_state)
        return True

    def _registry_changed(self) -> list[str] | None:
        if any(s.role == "spec" for s in self.slots):
            return None
        try:
            now_keys = sorted(s.key for s in slots_from_registry(self.cfg.registry_root))
        except Exception:
            log.exception("registry rescan failed")
            return None
        return now_keys if now_keys != sorted(self.traders) else None

    async def housekeeping(self) -> None:
        last_scan = self.clock()
        notified: list[str] | None = None
        while True:
            await asyncio.sleep(self.cfg.heartbeat_seconds)
            try:
                self.write_heartbeat()
                self.maybe_daily_summary()
                if self.clock() - last_scan >= self.cfg.rescan_seconds:
                    last_scan = self.clock()
                    ch = self._registry_changed()
                    if ch is not None and ch != notified:
                        notified = ch
                        self.notify(f"[paper] 레지스트리 변경 감지 → {ch}. 적용하려면 paper 재시작", "warning")
            except Exception:
                log.exception("housekeeping failed")

    async def ticker(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.tick_seconds)
            try:
                self.process()
            except Exception:
                self.errors += 1
                log.exception("tick failed")

    # ------------------------------------------------------------------ 실행
    async def run(self, duration: float | None = None, install_signals: bool = True) -> dict:
        self._loop = asyncio.get_running_loop()
        tasks = [asyncio.create_task(self.warmup(), name="warmup"),
                 asyncio.create_task(self.ticker(), name="ticker"),
                 asyncio.create_task(self.housekeeping(), name="housekeeping")]
        summary: dict = {}
        try:
            summary = await self.collector.run(duration=duration, install_signals=install_signals)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for w in tasks:
                if w.done() and not w.cancelled() and w.exception() is not None:
                    log.error("task %s failed: %r", w.get_name(), w.exception())
            for tr in self.traders.values():
                with contextlib.suppress(Exception):
                    tr.save_state()
            with contextlib.suppress(Exception):
                self.write_heartbeat()
            self.notify(f"[paper] 종료 (봉 {self.bars_processed}, 오류 {self.errors})", "warning")
            if self._bg:
                await asyncio.wait(list(self._bg), timeout=10)
        return {"collector": summary, "bars": self.bars_processed, "errors": self.errors,
                "late_trades": self.builder.late_trades,
                "traders": {k: tr.status(_utcnow()) for k, tr in self.traders.items()}}


# --------------------------------------------------------------------------------------------
# 유틸
# --------------------------------------------------------------------------------------------

def _atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str))
    os.replace(tmp, path)


def check_health(path: Path, max_age: float = 90.0, now: float | None = None) -> tuple[bool, str]:
    now = time.time() if now is None else now
    try:
        hb = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return False, f"heartbeat missing/unreadable: {path}"
    age = now - float(hb.get("ts", 0))
    if age > max_age:
        return False, f"heartbeat stale ({age:.0f}s > {max_age:g}s)"
    if hb.get("ready") is False and hb.get("uptime", 0) > 600:
        return False, "not ready after 600s (warm-up failed?)"
    return True, f"ok (heartbeat {age:.0f}s ago, bars {hb.get('bars')})"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None



# --------------------------------------------------------------------------------------------
# 상태·보고
# --------------------------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    with contextlib.suppress(ValueError):
                        out.append(json.loads(line))
    except OSError:
        pass
    return out


def strategy_dirs(out_root: Path) -> list[Path]:
    return sorted(p for p in Path(out_root).iterdir() if p.is_dir() and (p / "state.json").exists()) \
        if Path(out_root).exists() else []


def status_report(out_root: Path, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    svc = _read_json(Path(out_root) / "service.heartbeat")
    out = {"service": None if svc is None else {
        "heartbeat_age": round(now - float(svc.get("ts", 0)), 1), "ready": svc.get("ready"),
        "uptime": svc.get("uptime"), "bars": svc.get("bars"), "errors": svc.get("errors"),
        "data_age": svc.get("data_age"), "late_trades": svc.get("late_trades")}, "strategies": {}}
    for d in strategy_dirs(out_root):
        st = _read_json(d / "state.json") or {}
        hb = _read_json(d / "heartbeat.json") or {}
        meta = _read_json(d / "meta.json") or {}
        trades = read_jsonl(d / "trades.jsonl")
        net = 1.0
        for t in trades:
            net *= 1 + float(t.get("net_ret") or 0.0)
        out["strategies"][d.name] = {
            "role": meta.get("role"), "grade": st.get("grade"), "cost": (st.get("cost") or {}).get("name"),
            "equity": st.get("equity"), "return": (st.get("equity", 0) / st["initial_equity"] - 1)
            if st.get("initial_equity") else None,
            "trades": len(trades), "compounded_net": net - 1, "position": st.get("position"),
            "last_ts": st.get("last_ts"), "start": st.get("start"),
            "halt_level": hb.get("halt_level"), "halt_reasons": hb.get("halt_reasons"),
            "heartbeat_age": round(now - float(hb["ts"]), 1) if hb.get("ts") else None,
        }
    return out


def compare_trades(paper: pd.DataFrame, bt: pd.DataFrame, tol: float = 1e-9) -> dict:
    """진입 시각으로 짝지어 비교. paper/bt 는 TRADE_COLUMNS 를 가진 DataFrame."""
    p = paper.copy()
    b = bt.copy()
    for df in (p, b):
        for c in ("entry_time", "exit_time"):
            df[c] = pd.to_datetime(df[c], utc=True).dt.as_unit("ns")
    m = p.merge(b, on="entry_time", how="outer", suffixes=("_paper", "_bt"), indicator=True)
    both = m[m["_merge"] == "both"]
    same = both[(both["exit_time_paper"] == both["exit_time_bt"])
                & (both["exit_reason_paper"] == both["exit_reason_bt"])
                & ((both["net_ret_paper"] - both["net_ret_bt"]).abs() <= tol)]

    def comp(x):
        return float((1 + pd.Series(x, dtype=float)).prod() - 1) if len(x) else 0.0
    return {
        "n_paper": int(len(p)), "n_backtest": int(len(b)), "n_matched_entry": int(len(both)),
        "n_identical": int(len(same)),
        "paper_only": [str(t) for t in m.loc[m["_merge"] == "left_only", "entry_time"]],
        "backtest_only": [str(t) for t in m.loc[m["_merge"] == "right_only", "entry_time"]],
        "diff_exit": [{"entry_time": str(r.entry_time), "paper": [str(r.exit_time_paper), r.exit_reason_paper],
                       "bt": [str(r.exit_time_bt), r.exit_reason_bt]}
                      for r in both.drop(same.index).itertuples()],
        "net_paper": comp(p["net_ret"]), "net_backtest": comp(b["net_ret"]),
        "mean_net_paper": float(p["net_ret"].mean()) if len(p) else None,
        "mean_net_backtest": float(b["net_ret"].mean()) if len(b) else None,
    }


def build_report(key_dir: Path, start=None, end=None, m1: pd.DataFrame | None = None,
                 fetch: Callable | None = None) -> dict:
    """페이퍼 vs 백테스트 (같은 기간·같은 스펙·같은 비용 모델). m1 이 없으면 코인원 REST 로 받는다.

    백테스트에는 리스크 게이트가 없으므로 게이트로 막힌 진입은 backtest_only 로 나타난다(blocked_by_risk 참조).
    """
    from btc_quant.backtest.reference import run_reference
    from btc_quant.backtest.types import TRADE_COLUMNS
    from btc_quant.data import quality

    from .fills import slippage_stats

    key_dir = Path(key_dir)
    spec = StrategySpec.from_yaml(key_dir / "spec.yaml")
    st = _read_json(key_dir / "state.json") or {}
    cost = CostModel(**st["cost"]) if st.get("cost") else COSTS["coinone_conservative"]
    start = pd.Timestamp(start or st.get("start"))
    end = pd.Timestamp(end) if end is not None else pd.Timestamp(st["last_ts"]) + pd.Timedelta("1min")
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    if m1 is None:
        lb = int(st.get("lookback_5m") or required_lookback_bars(spec))
        minutes = int((end - start) / pd.Timedelta("1min")) + window_minutes(lb) + spec.exit.max_hold_min + 5
        raw = fetch_warmup(end + pd.Timedelta(minutes=spec.exit.max_hold_min + 5), minutes, fetch=fetch)
        m1 = quality.regularize(quality.clean_wicks(raw))
    bt = run_reference(spec, m1, cost, start, end).trades
    trades = [t for t in read_jsonl(key_dir / "trades.jsonl")
              if start <= pd.Timestamp(t["entry_time"]) < end]
    paper = pd.DataFrame(trades, columns=TRADE_COLUMNS + ["live", "book_net_ret"])
    cmp = compare_trades(paper[TRADE_COLUMNS], bt[bt["exit_reason"] != "end"])
    fills = [f for f in read_jsonl(key_dir / "fills.jsonl") if start <= pd.Timestamp(f["ts"]) < end]
    decisions = [d for d in read_jsonl(key_dir / "decisions.jsonl") if start <= pd.Timestamp(d["ts"]) < end]
    blocked = [d["ts"] for d in decisions if d.get("entry_ok") and not d.get("risk_ok")]
    book_net = [t.get("book_net_ret") for t in trades if t.get("book_net_ret") is not None]
    return {
        "key": key_dir.name, "spec_id": spec.id, "logic_hash": spec.logic_hash(), "cost": cost.name,
        "start": str(start), "end": str(end), "m1_bars": int(len(m1)),
        "decisions": len(decisions), "blocked_by_risk": blocked,
        "live_trades": int(sum(1 for t in trades if t.get("live", True))),
        "replayed_trades": int(sum(1 for t in trades if not t.get("live", True))),
        "trades": cmp,
        "net_book_paper": float(pd.Series([1 + x for x in book_net]).prod() - 1) if book_net else None,
        "slippage": slippage_stats(fills),
        "note": "backtest uses Coinone REST 1m candles; paper uses trade-stream bars — small OHLC "
                "differences can change SL/TP timing. Open positions at `end` are excluded.",
    }


def format_report(r: dict) -> str:
    t, s = r["trades"], r["slippage"]
    lines = [
        f"## {r['key']}  ({r['start']} → {r['end']}, cost {r['cost']})",
        f"- 결정 {r['decisions']}회, 리스크로 막힌 진입 신호 {len(r['blocked_by_risk'])}회",
        f"- 거래: paper {t['n_paper']} (live {r['live_trades']}, 재생 {r['replayed_trades']}) / "
        f"backtest {t['n_backtest']} / 진입 일치 {t['n_matched_entry']} / 완전 일치 {t['n_identical']}",
        f"- 누적 순수익: paper {t['net_paper']:+.3%} / backtest {t['net_backtest']:+.3%}"
        + (f" / paper(호가 체결 기준) {r['net_book_paper']:+.3%}" if r["net_book_paper"] is not None else ""),
    ]
    if t["paper_only"]:
        lines.append(f"- paper 에만 있는 진입: {t['paper_only'][:10]}")
    if t["backtest_only"]:
        lines.append(f"- backtest 에만 있는 진입: {t['backtest_only'][:10]}")
    if t["diff_exit"]:
        lines.append(f"- 청산 불일치: {t['diff_exit'][:5]}")
    if s["n"]:
        lines.append(f"- 호가 체결 편도 비용(mid 대비): 평균 {s['mean_cost_bps']:.2f}bp, 중앙 {s['median_cost_bps']:.2f}bp, "
                     f"p90 {s['p90_cost_bps']:.2f}bp (n={s['n']}) vs 보수적 가정 {s['conservative_cost_bps']:.1f}bp "
                     f"→ G8 슬리피지 {'OK' if s['g8_slippage_ok'] else 'FAIL'}; 모델 체결 대비 평균 "
                     f"{s['mean_vs_model_bps']:+.2f}bp, 보수적 이내 비율 {s['frac_within_conservative']:.0%}")
    else:
        lines.append("- 호가 체결 기록 없음")
    return "\n".join(lines)
