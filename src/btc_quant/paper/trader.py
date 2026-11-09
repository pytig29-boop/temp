"""페이퍼 트레이더 — 기준 엔진(`ReferenceEngine`) 1개를 감싸 실시간 1분봉으로 구동한다.

시점 (docs/ENGINE_SPEC.md 와 동일한 의미론)
- 1분봉 k 가 닫히면(`on_bar`): 엔진에 봉 k 를 넣는다(진입은 봉 k 시가, SL/TP 는 봉 k 고저 → 모델 체결).
- 봉 k 가 5분 경계 직전 봉(분 % 5 == 4)이면, 방금 확정된 5분봉 s = k−4분의 신호를 **후행 창**
  (`M1History.window`)에서 백테스트와 같은 블록 함수로 계산 → 리스크 게이트 → `on_decision(k+1분)`.
  블록은 인과적이므로 충분한 lookback 이면 후행 창의 마지막 값 = 전체 이력 평가 값
  (tests/test_paper_trader.py 에서 검증).
- `risk.should_flatten` 이 참이면 다음 1분봉 시가에 강제 청산(사유 "flatten").
- 실제 체결 대신 두 가지를 기록한다: 엔진의 모델 체결(1분봉 시가 ± 비용)과, 결정 시점 실시간 호가에
  실제 금액을 대입한 호가 체결 추정(fills.walk_book). 둘의 차이가 G8 슬리피지 검증 입력이다.

기록: <out_dir>/{decisions,fills,trades,risk}.jsonl, state.json(엔진·자산 상태), risk.json(RiskManager).
시각은 모두 UTC ISO. 결정·리스크 판단의 `now` 는 봉 논리 시각(재생 시 결정적)이다.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from btc_quant.backtest.reference import FillEvent, Position, ReferenceEngine, is_boundary
from btc_quant.backtest.signals import FIVE_MIN
from btc_quant.backtest.types import COINONE_CONSERVATIVE, CostModel
from btc_quant.live.risk import RiskManager
from btc_quant.strategy.blocks import REGISTRY
from btc_quant.strategy.blocks.base import Context, Registry
from btc_quant.strategy.features import to_5m
from btc_quant.strategy.spec import StrategySpec

from .bars import Bar, M1History, check_live_ready, required_lookback_bars, window_minutes
from .fills import DEFAULT_NOTIONAL, BookFill, compare_to_model, walk_book

log = logging.getLogger("btc_quant.paper.trader")

ONE_MIN = pd.Timedelta(minutes=1)
STATE_VERSION = 1


def strategy_key(spec: StrategySpec) -> str:
    return f"{spec.id}_v{int(spec.version)}"


def _iso(t) -> str | None:
    if t is None:
        return None
    return pd.Timestamp(t).isoformat()


def _ts(s) -> pd.Timestamp | None:
    return None if s is None else pd.Timestamp(s)


def _dt(t: pd.Timestamp) -> datetime:
    return pd.Timestamp(t).to_pydatetime()


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        try:
            return o.item()
        except (ValueError, AttributeError):
            pass
    return o


def evaluate_signals(spec: StrategySpec, m1_window: pd.DataFrame, bar5: pd.Timestamp,
                     registry: Registry = REGISTRY, ctx: Context | None = None) -> dict:
    """5분봉 bar5 의 신호 값을 m1_window 로부터 계산 (backtest.signals.build_signals 와 같은 결합)."""
    bars = to_5m(m1_window)
    ctx = ctx or Context()
    out = {"bar5": bar5, "entry": False, "filters": [], "complete": False, "entry_ok": False,
           "exit_sig": False, "close5": None, "n5": int(len(bars))}
    if bar5 not in bars.index:
        return out
    entry = bool(registry.evaluate(spec.entry, bars, ctx).loc[bar5])
    filt = [bool(registry.evaluate(f, bars, ctx).loc[bar5]) for f in spec.filters]
    complete = bool(bars["complete"].loc[bar5])
    ex = (bool(registry.evaluate(spec.exit.signal_exit, bars, ctx).loc[bar5])
          if spec.exit.signal_exit is not None else False)
    out.update(entry=entry, filters=filt, complete=complete,
               entry_ok=bool(entry and all(filt) and complete), exit_sig=ex,
               close5=float(bars["close"].loc[bar5]))
    return out


class PaperTrader:
    def __init__(self, spec: StrategySpec, cost: CostModel, risk: RiskManager, grade: str = "B", *,
                 key: str | None = None, out_dir: str | Path | None = None,
                 initial_equity: float = DEFAULT_NOTIONAL, lookback_5m: int | None = None,
                 start: pd.Timestamp | None = None, registry: Registry = REGISTRY,
                 ctx: Context | None = None, conservative: CostModel = COINONE_CONSERVATIVE,
                 clock=time.time, persist: bool = True):
        if grade not in ("A", "B"):
            raise ValueError("grade must be A or B")
        spec.validate(registry)
        check_live_ready(spec, registry)
        if risk.grade != grade:
            log.warning("%s: risk grade %s != trader grade %s", spec.id, risk.grade, grade)
        self.spec, self.cost, self.risk, self.grade = spec, cost, risk, grade
        self.key = key or strategy_key(spec)
        self.registry, self.ctx, self.conservative = registry, ctx, conservative
        self.out_dir = Path(out_dir) if out_dir is not None else None
        self.persist = persist and self.out_dir is not None
        self.clock = clock
        self.lookback_5m = int(lookback_5m) if lookback_5m else required_lookback_bars(spec, registry)
        self.window_min = window_minutes(self.lookback_5m)
        ex = spec.exit
        self.engine = ReferenceEngine(ex.stop_loss_pct, ex.take_profit_pct, ex.max_hold_min, cost,
                                      start=start, end=None)
        self.initial_equity = float(initial_equity)
        self.equity = float(initial_equity)          # 실현 자산 (청산마다 복리)
        self.qty = 0.0                               # 보유 수량 (BTC)
        self.last_ts: pd.Timestamp | None = None     # 마지막 처리 1분봉
        self.pending_flatten: str | None = None
        self.entry_cmp: dict | None = None           # 진입 체결 비교(호가 vs 모델)
        self.exit_capture: dict | None = None        # 체결 스트림에서 SL/TP 도달 순간의 호가
        self.book_open: dict | None = None           # 직전 봉 마감(≈ 다음 봉 시가) 시점 호가
        self.last_decision: dict | None = None
        self.n_trades = 0
        self.n_decisions = 0
        if self.out_dir is not None:
            self.out_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 경로
    def path(self, name: str) -> Path:
        assert self.out_dir is not None
        return self.out_dir / name

    def _log(self, name: str, rec: dict) -> None:
        if self.out_dir is None:
            return
        rec = {"key": self.key, "wall": round(self.clock(), 3), **rec}
        with open(self.path(f"{name}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(_jsonable(rec), ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ 상태
    @property
    def position(self) -> Position | None:
        return self.engine.position

    def mark_equity(self, price: float) -> float:
        pos = self.engine.position
        if pos is None:
            return self.equity
        return self.equity_at_entry * (1 + self.cost.net_return(pos.entry_price, self.cost.sell_fill(price)))

    @property
    def equity_at_entry(self) -> float:
        return self.equity   # 보유 중에는 실현 자산이 진입 시점 자산과 같다(전액 진입)

    # ------------------------------------------------------------------ 실시간 체결 (청산 감시)
    def on_price(self, ts_ms: int, price: float, book: dict | None) -> None:
        """체결 스트림 가격. 보유 중 SL/TP 레벨 최초 도달 순간의 호가를 청산 추정용으로 저장."""
        pos = self.engine.position
        if pos is None or self.exit_capture is not None or book is None:
            return
        reason = None
        if price <= pos.sl:
            reason = "sl"
        elif price >= pos.tp + self.cost.tick:
            reason = "tp"
        if reason:
            self.exit_capture = {"reason": reason, "ts_ms": int(ts_ms), "price": float(price),
                                 "book": book, "wall": self.clock()}

    # ------------------------------------------------------------------ 시작/재시작
    def attach(self, hist: M1History, book: dict | None = None) -> list[dict]:
        """이력(seed) 준비 직후 1회 호출.
        - 새 트레이더: 이력의 마지막 봉까지 처리한 것으로 간주(진입 허용 시작 = 다음 봉)하고,
          다음 봉이 5분 경계면 그 결정을 바로 계산한다.
        - 복원된 트레이더: last_ts 이후 이력 봉을 재생(catch-up, live=False)해 공백 동안의 결정·청산을
          백테스트와 같은 규칙으로 반영한다."""
        if hist.last_ts is None:
            return []
        self.book_open = book
        if self.last_ts is None:
            self.last_ts = hist.last_ts
            if self.engine.start is None:
                self.engine.start = hist.last_ts + ONE_MIN
            t = hist.last_ts + ONE_MIN
            self.risk.on_market_data(_dt(t), price=hist.last_close)
            self.risk.on_equity(_dt(t), self.equity)
            if is_boundary(t):
                self._decide(t, hist, book, live=True)
            self._log("risk", {"type": "attach", "ts": t, "mode": "fresh", "start": self.engine.start})
            if self.persist:
                self.save_state()
            return []
        missed = hist.bars_after(self.last_ts)
        if hist.first_ts is not None and hist.first_ts > self.last_ts + ONE_MIN:
            self._log("risk", {"type": "history_gap", "last_ts": self.last_ts, "hist_first": hist.first_ts})
            log.warning("%s: downtime longer than warm-up history (%s → %s)", self.key, self.last_ts,
                        hist.first_ts)
        self._log("risk", {"type": "attach", "mode": "restore", "last_ts": self.last_ts,
                           "replay_bars": len(missed)})
        events: list[dict] = []
        for b in missed:
            events += self.on_bar(b, hist, book=None, live=False)
        return events

    # ------------------------------------------------------------------ 1분봉
    def on_bar(self, bar: Bar, hist: M1History, book: dict | None = None, live: bool = True) -> list[dict]:
        """닫힌(정규화된) 1분봉 처리. hist 에는 이 봉까지 들어 있어야 한다. 알림용 이벤트 목록 반환."""
        ts = pd.Timestamp(bar.ts)
        if self.last_ts is not None and ts <= self.last_ts:
            return []
        if self.last_ts is not None and ts != self.last_ts + ONE_MIN:
            self._log("risk", {"type": "bar_gap", "ts": ts, "prev": self.last_ts})
        events: list[dict] = []
        t_close = ts + ONE_MIN

        # 1) 강제 청산 (리스크) — 이 봉 시가
        if self.pending_flatten and self.engine.position is not None:
            ev = self.engine._close(ts, bar.open, "flatten", at_open=True)
            events += self._on_fill(ev, bar, book, live)
        self.pending_flatten = None

        # 2) 엔진
        for ev in self.engine.on_bar_1m(ts, bar.open, bar.high, bar.low, bar.close, bar.filled):
            events += self._on_fill(ev, bar, book, live)

        # 3) 리스크 갱신 (봉 마감 시각 기준)
        if not bar.filled:
            self.risk.on_market_data(_dt(t_close), price=bar.close)
        if self.engine.position is not None:      # 무포지션이면 자산 불변 (청산 시 on_trade_closed 가 갱신)
            self.risk.on_equity(_dt(t_close), self.mark_equity(bar.close))
        self.last_ts = ts

        flat, why = self.risk.should_flatten(_dt(t_close))
        if flat and self.engine.position is not None:
            self.pending_flatten = why
            self._log("risk", {"type": "flatten_pending", "ts": t_close, "reason": why})

        # 4) 5분 경계 결정 (다음 봉 시가 직전)
        if is_boundary(t_close):
            self._decide(t_close, hist, book, live)

        # 5) 다음 봉 시가 근처 호가 = 지금 호가
        self.book_open = book
        for e in self.risk.drain_events():
            rec = {**{k: v for k, v in e.items() if k != "type"}, "type": "risk_event",
                   "event": e.get("type"), "ts": t_close}
            self._log("risk", rec)
            events.append(rec)
        if self.persist:
            self.save_state()
        return events

    def _decide(self, t: pd.Timestamp, hist: M1History, book: dict | None, live: bool) -> None:
        bar5 = t - FIVE_MIN
        win = hist.window(t - ONE_MIN, self.window_min)
        sig = evaluate_signals(self.spec, win, bar5, self.registry, self.ctx)
        ok, why = self.risk.can_enter(_dt(t))
        pos = self.engine.position is not None
        cooldown = self.engine.cooldown_until is not None and t < self.engine.cooldown_until
        entry_ok = bool(sig["entry_ok"] and ok)
        self.engine.on_decision(t, entry_ok, sig["exit_sig"])
        self.n_decisions += 1
        rec = {"type": "decision", "ts": t, **sig, "risk_ok": ok, "risk_reason": why,
               "entry_ok_gated": entry_ok, "in_position": pos, "cooldown": bool(cooldown),
               "would_enter": bool(entry_ok and not pos and not cooldown
                                   and (self.engine.start is None or t >= self.engine.start)),
               "would_exit_signal": bool(sig["exit_sig"] and pos), "live": live,
               "window_min": int(len(win)),
               "book_mid": book.get("mid") if book else None,
               "book_spread_bps": book.get("spread_bps") if book else None}
        self.last_decision = rec
        self._log("decisions", rec)

    def _on_fill(self, ev: FillEvent, bar: Bar, book_now: dict | None, live: bool) -> list[dict]:
        now = self.clock()
        if ev.side == "buy":
            ex = self.spec.exit          # 같은 봉에서 청산까지 되면 engine.position 은 이미 None
            sl, tp = ev.ref_price * (1 - ex.stop_loss_pct / 100), ev.ref_price * (1 + ex.take_profit_pct / 100)
            expiry = ev.ts + pd.Timedelta(minutes=int(ex.max_hold_min))
            notional = self.equity
            bf = walk_book(self.book_open, "buy", notional=notional, now=now) if self.book_open else None
            cmp = compare_to_model(bf, ev.fill_price, ev.ref_price, self.cost, self.conservative)
            self.entry_cmp = cmp
            self.exit_capture = None
            self.qty = notional / ev.fill_price
            self.risk.on_fill(_dt(ev.ts), "buy", self.qty, ev.fill_price, fee=None)
            rec = {"type": "fill", "side": "buy", "ts": ev.ts, "reason": ev.reason,
                   "ref_price": ev.ref_price, "fill_price": ev.fill_price, "qty": self.qty,
                   "notional": notional, "sl": sl, "tp": tp, "expiry": expiry,
                   "live": live, **cmp}
            self._log("fills", rec)
            return [{"type": "entry", "ts": ev.ts, "price": ev.fill_price, "sl": sl, "tp": tp,
                     "book_cost_bps": cmp.get("book_cost_bps"), "live": live}]

        # sell
        trade = self.engine.trades.pop() if self.engine.trades else None
        if ev.reason in ("sl", "tp") and self.exit_capture and self.exit_capture["reason"] == ev.reason:
            src, book = "stream_trigger", self.exit_capture["book"]
        elif ev.reason in ("sl", "tp"):
            src, book = "bar_close", book_now
        else:
            src, book = "bar_open", self.book_open
        bf = walk_book(book, "sell", qty=self.qty, now=now) if book and self.qty > 0 else None
        cmp = compare_to_model(bf, ev.fill_price, ev.ref_price, self.cost, self.conservative)
        eq_before = self.equity
        self.equity = eq_before * (1 + trade["net_ret"]) if trade else eq_before
        book_net = None
        if bf is not None and self.entry_cmp and self.entry_cmp.get("book") and math.isfinite(bf.vwap):
            book_net = self.cost.net_return(self.entry_cmp["book"]["vwap"], bf.vwap)
        t_eff = ev.ts + (ONE_MIN if ev.reason in ("sl", "tp") else pd.Timedelta(0))
        self.risk.on_trade_closed(_dt(t_eff), self.equity - eq_before, self.equity)
        frec = {"type": "fill", "side": "sell", "ts": ev.ts, "reason": ev.reason,
                "ref_price": ev.ref_price, "fill_price": ev.fill_price, "qty": self.qty,
                "book_source": src, "live": live, **cmp}
        self._log("fills", frec)
        self.n_trades += 1
        trec = {"type": "trade", **(trade or {}), "spec_id": self.spec.id, "version": self.spec.version,
                "cost": self.cost.name, "grade": self.grade, "qty": self.qty,
                "equity_before": eq_before, "equity_after": self.equity,
                "entry_book_cost_bps": (self.entry_cmp or {}).get("book_cost_bps"),
                "entry_vs_model_bps": (self.entry_cmp or {}).get("vs_model_bps"),
                "exit_book_cost_bps": cmp.get("book_cost_bps"),
                "exit_vs_model_bps": cmp.get("vs_model_bps"),
                "book_net_ret": book_net, "live": live}
        self._log("trades", trec)
        self.qty = 0.0
        self.entry_cmp = None
        self.exit_capture = None
        return [{"type": "exit", "ts": ev.ts, "reason": ev.reason, "price": ev.fill_price,
                 "net_ret": trade["net_ret"] if trade else None, "equity": self.equity, "live": live}]

    # ------------------------------------------------------------------ 저장/복원
    def state_dict(self) -> dict:
        e = self.engine
        pos = None
        if e.position is not None:
            p = e.position
            pos = {"entry_time": _iso(p.entry_time), "ref": p.ref, "entry_price": p.entry_price,
                   "sl": p.sl, "tp": p.tp, "expiry": _iso(p.expiry)}
        dec = None
        if e.decision is not None:
            dec = {"ts": _iso(e.decision.ts), "entry_ok": e.decision.entry_ok,
                   "exit_sig": e.decision.exit_sig}
        return _jsonable({
            "version": STATE_VERSION, "key": self.key, "spec_id": self.spec.id,
            "spec_version": self.spec.version, "logic_hash": self.spec.logic_hash(),
            "cost": asdict(self.cost), "grade": self.grade,
            "initial_equity": self.initial_equity, "equity": self.equity, "qty": self.qty,
            "last_ts": _iso(self.last_ts), "start": _iso(e.start),
            "position": pos, "cooldown_until": _iso(e.cooldown_until), "decision": dec,
            "pending_flatten": self.pending_flatten, "entry_cmp": self.entry_cmp,
            "n_trades": self.n_trades, "n_decisions": self.n_decisions,
            "lookback_5m": self.lookback_5m, "saved_at": time.time(),
        })

    def save_state(self) -> None:
        if self.out_dir is None:
            return
        p = self.path("state.json")
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state_dict(), ensure_ascii=False, indent=1))
        os.replace(tmp, p)
        self.risk.save(self.path("risk.json"))

    def load_state(self, path: str | Path | None = None) -> bool:
        """state.json 복원. 규칙(logic_hash)이 다르면 복원하지 않고 False."""
        p = Path(path) if path is not None else (self.path("state.json") if self.out_dir else None)
        if p is None or not p.exists():
            return False
        d = json.loads(p.read_text())
        if d.get("version") != STATE_VERSION:
            raise ValueError(f"unsupported paper state version {d.get('version')}")
        if d.get("logic_hash") != self.spec.logic_hash():
            log.warning("%s: saved state logic_hash %s != spec %s — starting fresh", self.key,
                        d.get("logic_hash"), self.spec.logic_hash())
            return False
        e = self.engine
        self.initial_equity = float(d["initial_equity"])
        self.equity = float(d["equity"])
        self.qty = float(d.get("qty") or 0.0)
        self.last_ts = _ts(d.get("last_ts"))
        e.start = _ts(d.get("start"))
        e.cooldown_until = _ts(d.get("cooldown_until"))
        pos = d.get("position")
        e.position = None if pos is None else Position(
            _ts(pos["entry_time"]), float(pos["ref"]), float(pos["entry_price"]), float(pos["sl"]),
            float(pos["tp"]), _ts(pos["expiry"]))
        dec = d.get("decision")
        if dec is not None:
            e.on_decision(_ts(dec["ts"]), dec["entry_ok"], dec["exit_sig"])
        self.pending_flatten = d.get("pending_flatten")
        self.entry_cmp = d.get("entry_cmp")
        self.n_trades = int(d.get("n_trades", 0))
        self.n_decisions = int(d.get("n_decisions", 0))
        return True

    def status(self, now: datetime | None = None) -> dict:
        pos = self.engine.position
        now = now or datetime.now().astimezone()
        rs = self.risk.status(now)
        return _jsonable({
            "key": self.key, "grade": self.grade, "cost": self.cost.name,
            "equity": self.equity, "initial_equity": self.initial_equity,
            "return": self.equity / self.initial_equity - 1,
            "position": None if pos is None else {"entry_time": pos.entry_time, "ref": pos.ref,
                                                  "sl": pos.sl, "tp": pos.tp, "expiry": pos.expiry},
            "last_ts": self.last_ts, "n_trades": self.n_trades, "n_decisions": self.n_decisions,
            "halt_level": rs["level"], "halt_reasons": rs["reasons"][:3],
            "daily_return": rs["daily_return"], "drawdown": rs["drawdown"],
            "trades_today": rs["trades_today"],
        })
