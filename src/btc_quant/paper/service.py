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
    sleeve_limits: {XRP-KRW: {...}, ...}     # 다자산 스펙만 (D15) — 없으면 슬리브도 limits 를 쓴다
  (evaluation.gates.risk_limits_from_validation 의 키 daily_loss_limit/weekly_loss_limit/drawdown_limit 도 허용)

다자산 스펙 (PLAN D15, spec.universe 예: BTC/XRP/ETH-KRW): 자산(슬리브)마다 트레이더 1개 — 키 `<id>_v<ver>@<SYM>`,
기록 디렉터리 `<out>/<id>_v<ver>@<SYM>/`, 초기 자산 = 명목 금액/N, 슬리브 리스크 한도(`sleeve_limits`) — 와 포트폴리오
리스크 묶음(`trader.SleeveGroup`, 한도 = `limits`, 상태 `<out>/portfolio_<id>_v<ver>.risk.json`). 수집기는 필요한 모든
심볼을 구독하고(코인원 체결·호가 + 업비트 참조가/보조 1분봉, 심볼별), 1분봉·워밍업 REST·호가·참조가 괴리는 심볼별이다.
단일 자산(BTC-KRW) 전략만 있으면 이전과 같은 구성·같은 키·같은 기록.
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
                   minute_ts, required_lookback_bars, spec_aux, window_minutes)
from .fills import DEFAULT_NOTIONAL
from .trader import PaperTrader, SleeveGroup, strategy_key

DEFAULT_SYMBOL = "BTC-KRW"


def _market(sym: str) -> str:
    a, q = sym.split("-")
    return f"{q}-{a}"


def sleeve_key(key: str, sym: str) -> str:
    return f"{key}@{sym}"


def slot_symbols(spec: StrategySpec) -> list[str]:
    return list(getattr(spec, "universe", (DEFAULT_SYMBOL,)) or (DEFAULT_SYMBOL,))

log = logging.getLogger("btc_quant.paper.service")

MAX_CHALLENGERS = 3
DEFAULT_LIMITS = RiskLimits(daily_loss=-0.03, weekly_loss=-0.06, max_drawdown=-0.12, max_trades_per_day=5)
_PRESET_COSTS: dict[str, CostModel] = {c.name: c for c in (
    bt_types.COINONE_BASE, bt_types.COINONE_CONSERVATIVE, bt_types.COINONE_STRESS,
    bt_types.UPBIT_BASE, bt_types.UPBIT_CONSERVATIVE)}


def load_costs(root: str | Path | None = None) -> dict[str, CostModel]:
    """비용 모델 = 버전 관리 레지스트리 config/costs.yaml 의 현재 버전 (`btc_quant.params.all_cost_models`).
    이름은 그대로(coinone_base … upbit_stress). 레지스트리를 못 읽으면(파일 손상 등) 하드코딩 프리셋으로
    대체하고 경고를 남긴다 — 페이퍼 서비스가 비용 설정 문제로 시작조차 못 하는 일을 막는다."""
    from btc_quant.params import all_cost_models
    try:
        return all_cost_models(root)
    except Exception as e:  # noqa: BLE001
        log.warning("cost registry unavailable (%s); falling back to presets", e)
        return dict(_PRESET_COSTS)


COSTS: dict[str, CostModel] = load_costs()     # 모듈 로드 시점의 현재 버전 (CLI choices 호환)


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
    sleeve_limits: dict = field(default_factory=dict)   # 다자산: {심볼: RiskLimits} (없으면 limits)
    position_scale: float = 1.0     # risk.yaml position_scale (G6 크기 조절, R1; 없으면 1.0)

    @property
    def multi_asset(self) -> bool:
        return slot_symbols(self.spec) != [DEFAULT_SYMBOL]

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


def load_position_scale(path: Path | None) -> float:
    """risk.yaml `position_scale` (G6 크기 조절, R1). 파일·키가 없으면 1.0 (이전과 같음)."""
    if path is None or not Path(path).exists():
        return 1.0
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    v = float(d.get("position_scale", 1.0) or 1.0)
    if not (0.0 < v <= 1.0):
        raise ValueError(f"{path}: position_scale must be in (0, 1], got {v}")
    return v


def load_sleeve_limits(path: Path | None, base: RiskLimits) -> dict[str, RiskLimits]:
    """risk.yaml `sleeve_limits` (다자산 스펙, 파이프라인이 슬리브 Val 수익으로 같은 §7 공식) → {심볼: RiskLimits}."""
    if path is None or not Path(path).exists():
        return {}
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {str(k): limits_from_dict(v, base) for k, v in (d.get("sleeve_limits") or {}).items()}


def slots_from_specs(paths: list[str | Path], grade: str = "B", risk_path: str | Path | None = None
                     ) -> list[StrategySlot]:
    base_grade, base_limits = load_risk_meta(Path(risk_path) if risk_path else None, grade, DEFAULT_LIMITS)
    out = []
    for p in paths:
        spec = StrategySpec.from_yaml(p)
        g, lim = load_risk_meta(sidecar_path(Path(p)), base_grade, base_limits)
        out.append(StrategySlot(spec, "spec", g, lim, str(p), load_sleeve_limits(sidecar_path(Path(p)), lim),
                                load_position_scale(sidecar_path(Path(p)))))
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
        out.append(StrategySlot(StrategySpec.from_yaml(p), role, g, lim, str(p),
                                load_sleeve_limits(sidecar_path(p), lim), load_position_scale(sidecar_path(p))))
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
    # 지정가 집행 스펙의 체결 판정 (ENGINE_SPEC "페이퍼 훅"): "bar" = 1분봉 관통 규칙(백테스트와 같음),
    # "queue" = 실시간 체결·호가 대기열 재현(`backtest.queue.QueueFillOracle`)
    limit_fill: str = "bar"
    limit_latency_s: float = 1.0

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
        costs = load_costs()                       # 시작 시점의 현재 버전 (보정·승인 반영)
        if self.cfg.cost not in costs:
            raise ValueError(f"unknown cost model {self.cfg.cost!r}; one of {sorted(costs)}")
        self.cost = costs[self.cfg.cost]
        self._costs = costs
        self.cfg.out.mkdir(parents=True, exist_ok=True)
        # 심볼: 전략 유니버스의 합집합 (단일 자산 BTC 전략만 있으면 ["BTC-KRW"] — 이전과 같음)
        order = {"BTC-KRW": 0, "XRP-KRW": 1, "ETH-KRW": 2}
        self.symbols: list[str] = sorted({sym for s in slots for sym in slot_symbols(s.spec)},
                                         key=lambda x: (order.get(x, 9), x))
        self.primary = self.symbols[0]
        # 업비트 보조 데이터(ctx.aux["upbit"]): 필요한 전략이 있을 때만 업비트 1분봉 이력을 만든다 (심볼별)
        self.need_upbit = any("upbit" in spec_aux(s.spec) for s in slots)
        if self.need_upbit and "upbit" not in self.cfg.exchanges:
            raise ValueError("a strategy needs upbit aux data but upbit stream is disabled")
        need_aux_syms = {sym for s in slots if "upbit" in spec_aux(s.spec) for sym in slot_symbols(s.spec)}
        self.aux_builders_by: dict[str, dict[str, MinuteBarBuilder]] = {sym: {} for sym in self.symbols}
        self.aux_hist_by: dict[str, dict[str, M1History]] = {sym: {} for sym in self.symbols}
        for sym in need_aux_syms:
            self.aux_builders_by[sym]["upbit"] = MinuteBarBuilder(self.cfg.grace_seconds)
            self.aux_hist_by[sym]["upbit"] = M1History(max_rows=1, exchange="upbit")   # 크기는 아래에서 재설정
        self.aux_builders = self.aux_builders_by[self.primary]      # 단일 자산 호환 별칭
        self.aux_hist = self.aux_hist_by[self.primary]
        self.traders: dict[str, PaperTrader] = {}
        self.trader_sym: dict[str, str] = {}
        self.groups: dict[str, SleeveGroup] = {}
        for s in slots:
            if not s.multi_asset:
                self.traders[s.key] = self._make_trader(s)
                self.trader_sym[s.key] = DEFAULT_SYMBOL
                continue
            syms = slot_symbols(s.spec)
            gkey = s.key
            gpath = self.cfg.out / f"portfolio_{gkey}.risk.json"
            kill_dir = Path(self.cfg.runtime_dir)
            grisk = (RiskManager.load(gpath, s.limits, strategy_id=f"{gkey}@portfolio", grade=s.grade,
                                      kill_dir=kill_dir) if gpath.exists()
                     else RiskManager(s.limits, f"{gkey}@portfolio", s.grade, kill_dir=kill_dir))
            grp = SleeveGroup(gkey, grisk, [sleeve_key(gkey, x) for x in syms], gpath)
            self.groups[gkey] = grp
            for sym in syms:
                k = sleeve_key(gkey, sym)
                self.traders[k] = self._make_trader(s, symbol=sym, group=grp, n_sleeves=len(syms))
                self.trader_sym[k] = sym
        self.lookback_min = max(t.window_min for t in self.traders.values())
        max_rows = self.lookback_min + 2 * 1440
        self.builders = {sym: MinuteBarBuilder(self.cfg.grace_seconds) for sym in self.symbols}
        self.hists = {sym: M1History(max_rows=max_rows, exchange="coinone") for sym in self.symbols}   # 체결 venue
        self.builder = self.builders[self.primary]                  # 단일 자산 호환 별칭
        self.hist = self.hists[self.primary]
        for d in self.aux_hist_by.values():
            for h in d.values():
                h.max_rows = max_rows
        self.ready = False
        self.pending_by: dict[str, list] = {sym: [] for sym in self.symbols}
        self.pending = self.pending_by[self.primary]
        self.aux_pending_by: dict[str, dict[str, list]] = {sym: {} for sym in self.symbols}
        self.aux_pending = self.aux_pending_by[self.primary]
        self.books: dict[str, dict | None] = {sym: None for sym in self.symbols}
        self.refs: dict[str, tuple[float | None, float | None]] = {sym: (None, None) for sym in self.symbols}
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
    def _make_trader(self, s: StrategySlot, symbol: str | None = None, group: SleeveGroup | None = None,
                     n_sleeves: int = 1) -> PaperTrader:
        """symbol 이 있으면 다자산 스펙의 슬리브 트레이더 (키 `<key>@<SYM>`, 명목/N, 슬리브 한도, 포트폴리오 묶음)."""
        key = s.key if symbol is None else sleeve_key(s.key, symbol)
        limits = s.limits if symbol is None else s.sleeve_limits.get(symbol, s.limits)
        out = self.cfg.out / key
        out.mkdir(parents=True, exist_ok=True)
        rp = out / "risk.json"
        kill_dir = Path(self.cfg.runtime_dir)
        if rp.exists():
            risk = RiskManager.load(rp, limits, strategy_id=key, grade=s.grade, kill_dir=kill_dir)
        else:
            risk = RiskManager(limits, key, s.grade, kill_dir=kill_dir)
        lb = required_lookback_bars(s.spec, default_days=self.cfg.warmup_days,
                                    max_days=self.cfg.max_warmup_days)
        aux = self.aux_hist_by[symbol] if symbol is not None else self.aux_hist
        cost = self.cost
        if symbol is not None and symbol != DEFAULT_SYMBOL:   # 자산별 비용 모델 `<name>@<SYM>` 이 있으면 (D15)
            cost = self._costs.get(f"{self.cfg.cost}@{symbol}", self.cost)
        tr = PaperTrader(s.spec, cost, risk, s.grade, key=key, out_dir=out,
                         initial_equity=self.cfg.notional / max(int(n_sleeves), 1),
                         lookback_bars=lb, clock=self.clock, aux_hist=aux, group=group, symbol=symbol,
                         position_scale=s.position_scale)
        if tr.limit and self.cfg.limit_fill == "queue":
            from btc_quant.backtest.queue import QueueFillOracle
            tr.engine.fill_oracle = QueueFillOracle(latency_s=self.cfg.limit_latency_s, notional=tr.initial_equity)
        elif self.cfg.limit_fill not in ("bar", "queue"):
            raise ValueError(f"limit_fill must be 'bar' or 'queue', got {self.cfg.limit_fill!r}")
        restored = tr.load_state()
        (out / "spec.yaml").write_text(s.spec.to_yaml(), encoding="utf-8")
        meta = {"key": key, "role": s.role, "grade": s.grade, "source": s.source, "cost": cost.name,
                "position_scale": s.position_scale,
                "limits": {f.name: getattr(limits, f.name) for f in fields(RiskLimits)},
                "timeframe": s.spec.timeframe, "lookback_bars": lb, "aux": list(spec_aux(s.spec)),
                "restored": restored,
                **({"execution": s.spec.execution.to_dict(), "limit_fill": self.cfg.limit_fill} if tr.limit else {})}
        if symbol is not None:
            meta.update(symbol=symbol, strategy_key=s.key, universe=slot_symbols(s.spec), n_sleeves=int(n_sleeves),
                        sleeve_weight=1.0 / max(int(n_sleeves), 1),
                        portfolio_limits={f.name: getattr(s.limits, f.name) for f in fields(RiskLimits)})
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        log.info("trader %s (%s, grade %s, timeframe %s, lookback %d bars, %s)", key, s.role, s.grade,
                 s.spec.timeframe, lb, "restored" if restored else "fresh")
        return tr

    def _make_collector(self) -> Collector:
        cc = CollectorConfig(exchanges=self.cfg.exchanges, root=self.cfg.data_root,
                             heartbeat_path=None, gaps_path=self.cfg.out / "collector_gaps.jsonl",
                             symbols=tuple(self.symbols) if self.symbols != [DEFAULT_SYMBOL] else None)
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
                r = e.get("equity_ret", e.get("net_ret"))     # 사다리·부분 사이징: 슬리브 자산 대비 (D16)
                if r is not None:
                    st["net"] *= 1 + r
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
        sym = row.get("symbol", DEFAULT_SYMBOL)   # 다중 심볼 수집기는 행에 symbol 을 붙인다 (단일 = BTC-KRW)
        if sym not in self.books:
            return
        try:
            if ex == "coinone" and kind == "orderbook":
                self.books[sym] = row
                if sym == self.primary:
                    self.book = row
                self._oracle_feed(sym, "book", row)
            elif ex == "upbit" and kind == "trades":
                self.refs[sym] = (float(row["price"]), now)
                if sym == self.primary:
                    self.ref_price, self.ref_recv = float(row["price"]), now
                ab = self.aux_builders_by[sym]
                if "upbit" in ab and self.builders[sym].next_minute is not None:
                    ab["upbit"].on_trade(row["ts_exchange"], row["price"], row["qty"])
            elif ex == "coinone" and kind == "trades":
                if self.first_trade_wall is None:
                    self._start_bars(int(now // 60) + 1, now)
                self.builders[sym].on_trade(row["ts_exchange"], row["price"], row["qty"])
                self._oracle_feed(sym, "trade", row)
                rp, rr = self.refs[sym]
                ref = rp if rr is not None and now - rr <= self.cfg.ref_max_age else None
                dt = datetime.fromtimestamp(now, timezone.utc)
                book = self.books[sym]
                for key, tr in self.traders.items():
                    if self.trader_sym[key] != sym:
                        continue
                    if ref is None and tr.risk.s.last_ref_price is not None:
                        # 참조가가 오래됨 → 괴리 판정 중단 (오래된 참조가로 PAUSE 가 걸려 있지 않도록)
                        tr.risk.s.last_ref_price = tr.risk.s.last_deviation = None
                    tr.risk.on_market_data(dt, price=row["price"], ref_price=ref)
                    tr.on_price(row["ts_exchange"], row["price"], book)
                self.process(now)
        except Exception:
            self.errors += 1
            log.exception("on_event failed (%s/%s)", ex, kind)

    def _oracle_feed(self, sym: str, kind: str, row: dict) -> None:
        """지정가 대기열 재현 오라클(limit_fill="queue")에 체결·호가를 시간순으로 넣는다 (그 심볼의 트레이더만)."""
        for key, tr in self.traders.items():
            orc = getattr(tr.engine, "fill_oracle", None)
            if orc is None or self.trader_sym[key] != sym:
                continue
            if kind == "trade":
                orc.add_trade(int(row["ts_exchange"]) * 1_000_000, float(row["price"]), float(row["qty"]),
                              "buy" if row.get("side", "buy") == "buy" else "sell")
            else:
                import numpy as np
                orc.add_book(int(row["ts"]) * 1_000_000 if row.get("ts") else int(row["ts_recv"] * 1e9),
                             {k: np.asarray(row[k], float) for k in ("bid_px", "bid_qty", "ask_px", "ask_qty")})

    def _start_bars(self, minute: int, now: float) -> None:
        self.first_trade_wall = now
        for b in self.builders.values():          # 모든 심볼이 같은 분부터 (첫 체결이 어느 심볼이든)
            b.start_at(minute)
        for d in self.aux_builders_by.values():
            for b in d.values():
                b.start_at(minute)

    def process(self, now: float | None = None) -> int:
        """닫을 수 있는 분을 닫아 트레이더에 전달. 처리한 봉 수.
        보조(업비트) 봉을 먼저 닫아 이력에 넣으므로 결정 시점에 같은 분까지의 보조 데이터가 준비된다."""
        now = self.clock() if now is None else now
        n = 0
        for sym in self.symbols:
            for k, b in self.aux_builders_by[sym].items():
                aux_bars = b.on_time(now)
                if not self.ready:
                    self.aux_pending_by[sym].setdefault(k, []).extend(aux_bars)
                else:
                    for bar in aux_bars:
                        self.aux_hist_by[sym][k].append(bar)
            bars = self.builders[sym].on_time(now)
            if not self.ready:
                self.pending_by[sym] += bars
                continue
            n += self._feed(bars, sym)
        return n

    def _feed(self, bars, sym: str | None = None) -> int:
        sym = sym or self.primary
        hist, book = self.hists[sym], self.books[sym]
        n = 0
        for bar in bars:
            for nb in hist.append(bar):
                n += 1
                for key, tr in self.traders.items():
                    if self.trader_sym[key] != sym:
                        continue
                    try:
                        self._handle_events(key, tr.on_bar(nb, hist, book, live=True))
                    except Exception as e:
                        self.errors += 1
                        log.exception("trader %s failed on bar %s", key, nb.ts)
                        self.notify(f"[paper] {key} 처리 오류: {type(e).__name__}: {e}", "warning")
        self._drain_groups()
        self.bars_processed += n
        return n

    def _drain_groups(self) -> None:
        """포트폴리오 리스크 묶음의 정지·해제 이벤트 → 알림·기록 (다자산 스펙)."""
        for gkey, g in self.groups.items():
            evs = g.risk.drain_events()
            if not evs:
                continue
            self._handle_events(f"{gkey}@portfolio",
                                [{**{k: v for k, v in e.items() if k != "type"}, "type": "risk_event",
                                  "event": e.get("type")} for e in evs])
            with contextlib.suppress(Exception):
                g.save()

    # ------------------------------------------------------------------ 워밍업
    async def warmup(self) -> None:
        t0 = self.clock()
        while self.first_trade_wall is None:
            if self.clock() - t0 > self.cfg.first_trade_timeout:
                # 체결이 안 들어와도 시작은 한다(봉은 filled, 리스크가 stale 로 진입 차단)
                m = int(self.clock() // 60) + 1
                self._start_bars(m, self.clock())
                log.warning("no coinone trades for %.0fs; starting bars at %s", self.cfg.first_trade_timeout,
                            minute_ts(m))
                break
            await asyncio.sleep(0.2)
        m0 = self.builder.next_minute
        wait = m0 * 60 + self.cfg.warmup_delay - self.clock()
        if wait > 0:
            await asyncio.sleep(wait)
        end = minute_ts(m0)
        n = 0
        for sym in self.symbols:
            log.info("warm-up: fetching %d min of coinone %s 1m candles up to %s", self.lookback_min, sym, end)
            raw = await self._fetch_retry(end, "coinone", sym)
            for k, h in self.aux_hist_by[sym].items():
                araw = await self._fetch_retry(end, k, sym)
                h.seed(araw)
                for bar in self.aux_pending_by[sym].pop(k, []):
                    h.append(bar)
                log.info("warm-up %s %s: %d bars → %s", k, sym, len(h), h.last_ts)
            hist = self.hists[sym]
            n_sym = hist.seed(raw)
            n = n_sym if sym == self.primary else n
            log.info("warm-up %s: %d bars (%s → %s)", sym, n_sym, hist.first_ts, hist.last_ts)
        for key, tr in self.traders.items():
            sym = self.trader_sym[key]
            try:
                self._handle_events(key, tr.attach(self.hists[sym], self.books[sym]))
            except Exception:
                self.errors += 1
                log.exception("attach failed for %s", key)
        self.ready = True
        for sym in self.symbols:
            pending, self.pending_by[sym] = self.pending_by[sym], []
            self._feed(pending, sym)
        self.pending = self.pending_by[self.primary]
        self.notify(f"[paper] 시작: {', '.join(f'{s.key}({s.role},{s.grade})' for s in self.slots)} "
                    f"/ 워밍업 {n}봉, 비용 {self.cost.name}")

    async def _fetch_retry(self, end: pd.Timestamp, exchange: str, symbol: str = DEFAULT_SYMBOL) -> pd.DataFrame:
        fetch = self.fetch.get(exchange) if isinstance(self.fetch, dict) else (
            self.fetch if exchange == "coinone" else None)
        attempt = 0
        while True:
            try:
                raw = await asyncio.to_thread(fetch_warmup, end, self.lookback_min, _market(symbol), fetch,
                                              exchange)
                if raw is not None and len(raw):
                    return raw
                err = "empty response"
            except Exception as e:     # 네트워크·요청 제한 등 → 재시도 (실시간 봉은 대기열에 쌓인다)
                err = f"{type(e).__name__}: {e}"
            attempt += 1
            delay = min(60.0, 5.0 * 2 ** (attempt - 1))
            log.warning("warm-up fetch %s %s failed (%s); retry %d in %.0fs", exchange, symbol, err, attempt, delay)
            if attempt == 3:
                self.notify(f"[paper] 워밍업 REST({exchange} {symbol}) 실패 3회: {err}", "warning")
            await asyncio.sleep(delay)

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
            **({"symbols": list(self.symbols),
                "portfolios": {k: g.status(dt) for k, g in self.groups.items()}} if self.groups else {}),
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
            for g in self.groups.values():
                with contextlib.suppress(Exception):
                    g.save()
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
    if hb.get("idle"):
        return True, f"idle: waiting for active/challenger strategy (heartbeat {age:.0f}s ago)"
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
        for t in trades:      # 슬리브 자산 대비 (사다리·부분 사이징이면 equity_after/equity_before; 기본 = net_ret)
            if t.get("weight") is not None and t.get("equity_before"):
                net *= float(t["equity_after"]) / float(t["equity_before"])
            else:
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
                 fetch: Callable | None = None, aux_m1: dict[str, pd.DataFrame] | None = None) -> dict:
    """페이퍼 vs 백테스트 (같은 기간·같은 스펙·같은 비용 모델). m1 이 없으면 코인원 REST 로 받는다.

    백테스트에는 리스크 게이트가 없으므로 게이트로 막힌 진입은 backtest_only 로 나타난다(blocked_by_risk 참조).
    """
    from btc_quant.backtest.reference import run_reference
    from btc_quant.backtest.types import TRADE_COLUMNS
    from btc_quant.data import quality
    from btc_quant.strategy.blocks.base import Context
    from btc_quant.strategy.features import to_tf

    from .fills import slippage_stats

    key_dir = Path(key_dir)
    spec = StrategySpec.from_yaml(key_dir / "spec.yaml")
    st = _read_json(key_dir / "state.json") or {}
    cost = CostModel(**st["cost"]) if st.get("cost") else load_costs()["coinone_conservative"]
    start = pd.Timestamp(start or st.get("start"))
    end = pd.Timestamp(end) if end is not None else pd.Timestamp(st["last_ts"]) + pd.Timedelta("1min")
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    lb = int(st.get("lookback_bars") or required_lookback_bars(spec))
    minutes = (int((end - start) / pd.Timedelta("1min")) + window_minutes(lb, spec.tf_min)
               + spec.exit.max_hold_min + 5)
    fetch_end = end + pd.Timedelta(minutes=spec.exit.max_hold_min + 5)
    sym = (_read_json(key_dir / "meta.json") or {}).get("symbol") or DEFAULT_SYMBOL   # 다자산 슬리브 = 그 자산
    if m1 is None:
        raw = fetch_warmup(fetch_end, minutes, _market(sym), fetch=fetch)
        m1 = quality.prepare_venue_m1(raw, "coinone")
    ctx = None
    keys = spec_aux(spec)
    if keys:
        aux_m1 = dict(aux_m1 or {})
        for k in keys:
            if k not in aux_m1:
                aux_m1[k] = quality.prepare_venue_m1(fetch_warmup(fetch_end, minutes, _market(sym), exchange=k), k)
        ctx = Context(aux={k: to_tf(aux_m1[k], spec.tf_min) for k in keys})
    bt = run_reference(spec, m1, cost, start, end, ctx=ctx).trades
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
