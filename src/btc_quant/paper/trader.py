"""페이퍼 트레이더 — 기준 엔진(`ReferenceEngine`) 1개를 감싸 실시간 1분봉으로 구동한다.

시점 (docs/ENGINE_SPEC.md 와 동일한 의미론)
- 1분봉 k 가 닫히면(`on_bar`): 엔진에 봉 k 를 넣는다(진입은 봉 k 시가, SL/TP 는 봉 k 고저 → 모델 체결).
- 봉 k 다음이 결정 주기 B(스펙 timeframe: 5분/1분) 경계이면, 방금 확정된 결정 봉 s = k+1분−B 의 신호
  (ATR 청산 모드면 ATR 도)를 **후행 창**(`M1History.window`)에서 백테스트와 같은 블록 함수로 계산
  → 리스크 게이트 → `on_decision(k+1분, ...)`. 업비트 보조 데이터 블록은 업비트 1분봉 이력(aux_hist)으로
  ctx.aux["upbit"] 를 같은 봉 길이로 만들어 넘긴다.
  블록은 인과적이므로 충분한 lookback 이면 후행 창의 마지막 값 = 전체 이력 평가 값
  (tests/test_paper_trader.py 에서 검증).
- `risk.should_flatten` 이 참이면 다음 1분봉 시가에 강제 청산(사유 "flatten").
- 실제 체결 대신 두 가지를 기록한다: 엔진의 모델 체결(1분봉 시가 ± 비용)과, 결정 시점 실시간 호가에
  실제 금액을 대입한 호가 체결 추정(fills.walk_book). 둘의 차이가 G8 슬리피지 검증 입력이다.

기록: <out_dir>/{decisions,fills,trades,risk}.jsonl, state.json(엔진·자산 상태), risk.json(RiskManager).
시각은 모두 UTC ISO. 결정·리스크 판단의 `now` 는 봉 논리 시각(재생 시 결정적)이다.

지정가 집행(`spec.execution`, docs/ENGINE_SPEC.md "지정가 집행"·"페이퍼 훅"): 엔진에 같은 집행·체결 규칙
(`LIMIT_FILL_DEFAULT`)을 넘기고, 1분봉마다 거래량과 호가 단위(그 UTC 일자의 실시간 1분봉 종가에서 `signals.infer_ticks`
— 백테스트와 같은 추정)를 준다. 걸린 진입 지정가(`engine.order`)·익절 지정가(`position.tp_order`)·지정가 진입 여부
(`position.maker`)는 상태에 저장(STATE_VERSION 2; 버전 1 상태도 읽음). 지정가 체결은 체결가 = 지정가라 호가 대입 비교를
건너뛴다(`maker: true`). 리스크 강제 청산·정지 시 걸린 진입 지정가를 취소한다. `engine.fill_oracle` 에
`backtest.queue.QueueFillOracle` 을 꽂으면(서비스 `limit_fill="queue"`) 1분봉 규칙 대신 실시간 체결·호가 대기열 재현으로
체결을 판정한다 — 서비스가 수신 체결·호가를 `add_trade/add_book` 으로 넣는다. 기본은 1분봉 규칙(= 백테스트와 같은 판정).

부분 사이징·분할 매수 사다리(`spec.sizing`/`spec.ladder`, PLAN D16, ENGINE_SPEC "부분 사이징·분할 매수"): 엔진에 같은 사이징·
사다리를 넘긴다(분할 주문이 여러 개 걸림 — `engine.lad.pending`, 지정가 분할은 `fill_oracle` 대기열 재현도 그대로). 분할 명목 =
사다리 시작(첫 분할 체결) 시점 실현 자산 × w, 청산마다 실현 자산 += 사다리 시작 자산 × w × net_ret (엔진 회계와 같음),
평가자산 = 실현 자산 + 보유 분할 평가손익. 거래 기록은 분할마다(`ladder`, `tranche`, `weight` 포함). 상태 STATE_VERSION 3
(사다리: 보유 분할·걸린 분할 주문·평균 익절 주문·사다리 시작 자산; 1·2 도 읽음). 리스크 강제 청산은 전량 청산 + 걸린 분할 주문 취소.

다자산 스펙(PLAN D15): 서비스가 자산(슬리브)마다 트레이더 하나(초기 자산 = 명목 금액/N, 슬리브 리스크 한도)를 만들고
같은 `SleeveGroup`(포트폴리오 리스크 한도 = 슬리브 자산 합계 기준)에 묶는다. 슬리브는 자기 한도와 포트폴리오 한도를
모두 통과해야 진입하고, 포트폴리오 사람 정지(누적 낙폭 등)·청산 스위치는 모든 슬리브를 청산한다. 엔진 의미론은
슬리브마다 백테스트와 같다(같은 스펙·같은 자산 1분봉 → 같은 거래).
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

from btc_quant.backtest.reference import (FillEvent, LadderState, Position, ReferenceEngine, RestingOrder, Tranche,
                                          is_boundary)
from btc_quant.backtest.signals import atr_series, infer_ticks
from btc_quant.backtest.types import COINONE_CONSERVATIVE, LIMIT_FILL_DEFAULT, CostModel
from btc_quant.live.risk import RiskManager
from btc_quant.strategy.blocks import REGISTRY
from btc_quant.strategy.blocks.base import Context, Registry
from btc_quant.strategy.features import to_tf
from btc_quant.strategy.spec import StrategySpec

from .bars import (Bar, M1History, check_live_ready, required_lookback_bars, spec_aux, ts_minute,
                   window_minutes)
from .fills import DEFAULT_NOTIONAL, BookFill, compare_to_model, walk_book

log = logging.getLogger("btc_quant.paper.trader")

ONE_MIN = pd.Timedelta(minutes=1)
STATE_VERSION = 3            # 2: 지정가 주문 상태 / 3: 부분 사이징·사다리 상태 — 1·2 도 읽는다
READABLE_STATE_VERSIONS = (1, 2, 3)


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


def evaluate_signals(spec: StrategySpec, m1_window: pd.DataFrame, bar: pd.Timestamp,
                     registry: Registry = REGISTRY, ctx: Context | None = None,
                     aux_m1: dict[str, pd.DataFrame] | None = None) -> dict:
    """결정 봉 bar 의 신호 값을 m1_window 로부터 계산 (backtest.signals.build_signals 와 같은 결합).
    aux_m1 = {"upbit": 업비트 1분봉 창} 이면 같은 봉 길이로 변환해 ctx.aux 에 넣는다.
    ATR 청산 모드면 결정 봉의 ATR(`signals.atr_series`)도 계산한다."""
    tf = spec.tf_min
    bars = to_tf(m1_window, tf)
    ctx = ctx or Context()
    if ctx.bar is None and tf >= 15:   # 긴 봉: 봉 길이를 명시 (캠페인 aux_context 와 같음; 짧은 창에서 추론 오류 방지)
        ctx = Context(aux=dict(ctx.aux), bar=pd.Timedelta(minutes=tf))
    if aux_m1:
        aux = dict(ctx.aux)
        for k, df in aux_m1.items():
            aux[k] = to_tf(df, tf) if len(df) else pd.DataFrame(columns=list(df.columns) + ["complete"])
        ctx = Context(aux=aux, bar=ctx.bar)
    out = {"bar": bar, "entry": False, "filters": [], "complete": False, "entry_ok": False,
           "exit_sig": False, "atr": None, "close": None, "n_bars": int(len(bars))}
    if bar not in bars.index:
        return out
    entry = bool(registry.evaluate(spec.entry, bars, ctx).loc[bar])
    filt = [bool(registry.evaluate(f, bars, ctx).loc[bar]) for f in spec.filters]
    complete = bool(bars["complete"].loc[bar])
    ex = (bool(registry.evaluate(spec.exit.signal_exit, bars, ctx).loc[bar])
          if spec.exit.signal_exit is not None else False)
    atr = None
    if spec.needs_atr:     # ATR 청산 · ATR 오프셋 지정가 · ATR 간격 사다리 (창 = exit.atr_bars, 고정 % 청산이면 14)
        v = float(atr_series(bars, spec.exit.atr_bars).loc[bar])
        atr = v if math.isfinite(v) else None
    out.update(entry=entry, filters=filt, complete=complete,
               entry_ok=bool(entry and all(filt) and complete), exit_sig=ex, atr=atr,
               close=float(bars["close"].loc[bar]))
    return out


def _order_dict(o: RestingOrder | None) -> dict | None:
    if o is None:
        return None
    return {"id": o.id, "kind": o.kind, "side": o.side, "price": o.price, "trigger": o.trigger,
            "placed": _iso(o.placed), "expires": _iso(o.expires), "draw_ok": bool(o.draw_ok), "atr_frac": o.atr_frac}


def _order_from(d: dict | None) -> RestingOrder | None:
    if not d:
        return None
    trig = d["trigger"]
    trig = float(trig) if trig is not None else (math.inf if d.get("side") == "sell" else -math.inf)
    return RestingOrder(str(d["id"]), str(d["kind"]), str(d["side"]), float(d["price"]), trig,
                        _ts(d["placed"]), _ts(d.get("expires")), bool(d.get("draw_ok", True)), d.get("atr_frac"))


def _ladder_dict(lad: LadderState | None) -> dict | None:
    if lad is None:
        return None
    return {"id": lad.id, "start": _iso(lad.start), "p1": lad.p1, "weight": lad.weight, "d_step": lad.d_step,
            "d_sl": lad.d_sl, "d_tp": lad.d_tp, "tick": lad.tick, "stop": lad.stop, "expiry": _iso(lad.expiry),
            "maker_first": lad.maker_first, "atr_frac": lad.atr_frac, "n_filled": lad.n_filled, "inv_sum": lad.inv_sum,
            "avg_tp": lad.avg_tp, "avg_tp_order": _order_dict(lad.avg_tp_order),
            "held": [{"j": t.j, "entry_time": _iso(t.entry_time), "ref": t.ref, "entry_price": t.entry_price,
                      "maker": t.maker, "tp": t.tp, "at_open": t.at_open, "tp_order": _order_dict(t.tp_order)}
                     for t in lad.held],
            "pending": {str(j): _order_dict(od) for j, od in lad.pending.items()}}


def _ladder_from(d: dict | None) -> LadderState | None:
    if not d:
        return None
    tick = d.get("tick")
    lad = LadderState(int(d["id"]), _ts(d["start"]), float(d["p1"]), float(d["weight"]), float(d["d_step"]),
                      float(d["d_sl"]), float(d["d_tp"]), math.nan if tick is None else float(tick), float(d["stop"]),
                      _ts(d["expiry"]), bool(d["maker_first"]), d.get("atr_frac"))
    lad.held = [Tranche(int(t["j"]), _ts(t["entry_time"]), float(t["ref"]), float(t["entry_price"]), bool(t["maker"]),
                        float(t["tp"]), bool(t.get("at_open", False)), _order_from(t.get("tp_order")))
                for t in d.get("held") or []]
    lad.pending = {int(j): _order_from(od) for j, od in (d.get("pending") or {}).items()}
    lad.n_filled = int(d.get("n_filled", len(lad.held)))
    lad.inv_sum = float(d.get("inv_sum", 0.0))
    lad.avg_tp = d.get("avg_tp")
    lad.avg_tp_order = _order_from(d.get("avg_tp_order"))
    return lad


def _ladder_status(lad: LadderState) -> dict:
    return {"ladder": lad.id, "entry_time": lad.start, "ref": lad.p1, "sl": lad.stop, "tp": lad.avg_tp,
            "expiry": lad.expiry, "tranches_held": [t.j for t in lad.held], "pending": sorted(lad.pending),
            "inventory": lad.weight * len(lad.held)}


class SleeveGroup:
    """다자산 스펙 하나의 슬리브 묶음 — 포트폴리오 리스크 한도 (PLAN D15).

    risk: 포트폴리오 RiskManager (한도 = 전략 risk.yaml `limits`, 자산 = 슬리브 평가자산 합계). 슬리브가 봉마다
    `on_sleeve_equity` 로 자기 평가자산을 알리고, 모든 슬리브 값이 모이면 합계로 일/주/낙폭 한도를 판정한다.
    진입 체결은 `on_entry` 로 포트폴리오 거래 수(하루 거래 한도)에 센다. 시세 수신은 슬리브 갱신 시각으로 둔다
    (자산별 데이터 지연·참조가 괴리는 각 슬리브 RiskManager 가 판정)."""

    def __init__(self, key: str, risk: RiskManager, sleeve_keys: list[str], path: Path | None = None):
        self.key, self.risk = key, risk
        self.sleeve_keys = list(sleeve_keys)
        self.equity: dict[str, float] = {}
        self.path = Path(path) if path is not None else None

    @property
    def n_sleeves(self) -> int:
        return len(self.sleeve_keys)

    def on_sleeve_equity(self, sleeve_key: str, equity: float, now: datetime) -> None:
        self.equity[sleeve_key] = float(equity)
        self.risk.on_market_data(now)
        if all(k in self.equity for k in self.sleeve_keys):
            self.risk.on_equity(now, sum(self.equity[k] for k in self.sleeve_keys))

    def on_entry(self, now: datetime) -> None:
        self.risk.on_fill(now, "buy", 0.0, 0.0, fee=None)

    def can_enter(self, now: datetime) -> tuple[bool, str]:
        """데이터 지연(stale_data)은 자산별(슬리브 RiskManager)로만 판정 — 포트폴리오는 손익·거래 수·킬스위치."""
        reasons = [r for r in self.risk.status(now)["reasons"] if r["code"] != "stale_data"]
        if reasons:
            return False, f"portfolio {reasons[0]['code']}: {reasons[0]['reason']}"
        return True, "ok"

    def should_flatten(self, now: datetime) -> tuple[bool, str]:
        flat, why = self.risk.should_flatten(now)
        return flat, ("ok" if not flat else f"portfolio {why}")

    def status(self, now: datetime) -> dict:
        st = self.risk.status(now)
        return {"key": self.key, "sleeves": self.sleeve_keys, "equity": sum(self.equity.values()) if self.equity
                else None, "halt_level": st["level"], "halt_reasons": st["reasons"],
                "daily_return": st["daily_return"], "drawdown": st["drawdown"], "trades_today": st["trades_today"]}

    def save(self) -> None:
        if self.path is not None:
            self.risk.save(self.path)


class PaperTrader:
    def __init__(self, spec: StrategySpec, cost: CostModel, risk: RiskManager, grade: str = "B", *,
                 key: str | None = None, out_dir: str | Path | None = None,
                 initial_equity: float = DEFAULT_NOTIONAL, lookback_bars: int | None = None,
                 start: pd.Timestamp | None = None, registry: Registry = REGISTRY,
                 ctx: Context | None = None, conservative: CostModel = COINONE_CONSERVATIVE,
                 aux_hist: dict[str, M1History] | None = None,
                 clock=time.time, persist: bool = True, group: SleeveGroup | None = None,
                 symbol: str | None = None):
        if grade not in ("A", "B"):
            raise ValueError("grade must be A or B")
        spec.validate(registry)
        from btc_quant.params import check_exposure_cap
        check_exposure_cap(spec)                      # class ③ 슬리브당 최대 노출 상한 (D16)
        self.aux_keys = spec_aux(spec, registry)
        check_live_ready(spec, registry, available_aux=tuple(aux_hist or ()))
        self.aux_hist = aux_hist or {}
        if risk.grade != grade:
            log.warning("%s: risk grade %s != trader grade %s", spec.id, risk.grade, grade)
        self.spec, self.cost, self.risk, self.grade = spec, cost, risk, grade
        self.key = key or strategy_key(spec)
        self.registry, self.ctx, self.conservative = registry, ctx, conservative
        self.out_dir = Path(out_dir) if out_dir is not None else None
        self.persist = persist and self.out_dir is not None
        self.clock = clock
        self.tf = spec.tf_min                         # 결정 주기 B (분)
        self.step = pd.Timedelta(minutes=self.tf)
        self.lookback_bars = int(lookback_bars) if lookback_bars else required_lookback_bars(spec, registry)
        self.window_min = window_minutes(self.lookback_bars, self.tf)
        # 지정가 집행 또는 사다리(분할 주문의 관통 폭에 호가 단위·거래량 필요) (ENGINE_SPEC "페이퍼 훅")
        self.limit = not spec.execution.is_default or spec.ladder is not None
        if spec.is_sized:
            self.engine = ReferenceEngine.from_exit(spec.exit, cost, start=start, end=None,
                                                    timeframe=spec.timeframe, execution=spec.execution,
                                                    fill=LIMIT_FILL_DEFAULT, sizing=spec.sizing, ladder=spec.ladder)
        elif self.limit:
            self.engine = ReferenceEngine.from_exit(spec.exit, cost, start=start, end=None,
                                                    timeframe=spec.timeframe, execution=spec.execution,
                                                    fill=LIMIT_FILL_DEFAULT)
        else:
            self.engine = ReferenceEngine.from_exit(spec.exit, cost, start=start, end=None,
                                                    timeframe=spec.timeframe)
        self._tick_cache: tuple[int, float] | None = None   # (UTC 일, 호가 단위)
        self.initial_equity = float(initial_equity)
        self.equity = float(initial_equity)          # 실현 자산 (청산마다 복리)
        self.qty = 0.0                               # 보유 수량 (BTC) — 사다리면 분할 합계
        self.ladder_equity: float | None = None      # 사다리 시작(첫 분할 체결) 시점 실현 자산 (분할 명목 기준)
        self.tranche_qty: dict[int, float] = {}      # 사다리: 단 번호 → 보유 수량
        self.last_ts: pd.Timestamp | None = None     # 마지막 처리 1분봉
        self.pending_flatten: str | None = None
        self.entry_cmp: dict | None = None           # 진입 체결 비교(호가 vs 모델)
        self.exit_capture: dict | None = None        # 체결 스트림에서 SL/TP 도달 순간의 호가
        self.book_open: dict | None = None           # 직전 봉 마감(≈ 다음 봉 시가) 시점 호가
        self.book_open_wall: float | None = None     # 그 호가를 잡은 벽시계 (결정 시각)
        self.last_decision: dict | None = None
        self.n_trades = 0
        self.n_decisions = 0
        self.group = group                           # 다자산 슬리브: 포트폴리오 리스크 묶음 (D15)
        self.symbol = symbol                         # 이 트레이더의 자산 (None = 단일 자산 BTC-KRW)
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
        lad = self.engine.lad
        if lad is not None:
            eg = self.ladder_equity if self.ladder_equity is not None else self.equity
            return self.equity + sum(eg * lad.weight * self.cost.net_return(t.entry_price, self.cost.sell_fill(price))
                                     for t in lad.held)
        pos = self.engine.position
        if pos is None:
            return self.equity
        f = float(self.spec.sizing.entry_fraction)
        return self.equity_at_entry * (1 + f * self.cost.net_return(pos.entry_price, self.cost.sell_fill(price)))

    @property
    def equity_at_entry(self) -> float:
        return self.equity   # 보유 중에는 실현 자산이 진입 시점 자산과 같다(전액 진입)

    # ------------------------------------------------------------------ 실시간 체결 (청산 감시)
    def on_price(self, ts_ms: int, price: float, book: dict | None) -> None:
        """체결 스트림 가격. 보유 중 SL/TP 레벨 최초 도달 순간의 호가를 청산 추정용으로 저장."""
        lad = self.engine.lad
        if lad is not None:
            if self.exit_capture is not None or book is None or not lad.held:
                return
            sl = lad.stop
            tp = lad.avg_tp if self.spec.ladder.average_exit else min(t.tp for t in lad.held)
        else:
            pos = self.engine.position
            if pos is None or self.exit_capture is not None or book is None:
                return
            sl, tp = pos.sl, pos.tp
        reason = None
        if price <= sl:
            reason = "sl"
        elif tp is not None and price >= tp + self.cost.tick:
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
        self.book_open, self.book_open_wall = book, self.clock()
        if self.last_ts is None:
            self.last_ts = hist.last_ts
            if self.engine.start is None:
                self.engine.start = hist.last_ts + ONE_MIN
            t = hist.last_ts + ONE_MIN
            self.risk.on_market_data(_dt(t), price=hist.last_close)
            self.risk.on_equity(_dt(t), self.equity)
            if is_boundary(t, self.tf):
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

        # 1) 강제 청산 (리스크) — 이 봉 시가. 걸린 진입 지정가도 취소
        if self.pending_flatten and self.engine.order is not None:
            self._cancel_order(ts, self.pending_flatten)
        if self.pending_flatten and self.engine.lad is not None:      # 사다리: 전량 + 걸린 분할 주문 취소
            for ev in self.engine._ladder_close_all(ts, bar.open, "flatten", at_open=True):
                events += self._on_fill(ev, bar, book, live)
        elif self.pending_flatten and self.engine.position is not None:
            ev = self.engine._close(ts, bar.open, "flatten", at_open=True)
            events += self._on_fill(ev, bar, book, live)
        self.pending_flatten = None

        # 2) 엔진 (지정가 집행이면 거래량·호가 단위도)
        if self.limit:
            evs = self.engine.on_bar_1m(ts, bar.open, bar.high, bar.low, bar.close, bar.filled,
                                        float(bar.volume), self._tick(hist, ts))
        else:
            evs = self.engine.on_bar_1m(ts, bar.open, bar.high, bar.low, bar.close, bar.filled)
        for ev in evs:
            events += self._on_fill(ev, bar, book, live)

        # 3) 리스크 갱신 (봉 마감 시각 기준)
        if not bar.filled or bar.n_trades > 0:     # 공백 직후 워밍업 봉(filled 표시, 실제 체결)도 시세 갱신
            self.risk.on_market_data(_dt(t_close), price=bar.close)
        if self.engine.in_position:               # 무포지션이면 자산 불변 (청산 시 on_trade_closed 가 갱신)
            self.risk.on_equity(_dt(t_close), self.mark_equity(bar.close))
        if self.group is not None:                # 포트폴리오 한도: 슬리브 평가자산 합계
            self.group.on_sleeve_equity(self.key, self.mark_equity(bar.close), _dt(t_close))
        self.last_ts = ts

        flat, why = self.risk.should_flatten(_dt(t_close))
        if not flat and self.group is not None:
            flat, why = self.group.should_flatten(_dt(t_close))
        if flat and self.engine.order is not None:      # 걸린 진입 지정가는 즉시 취소
            self._cancel_order(t_close, why)
        if flat and self.engine.in_position:
            self.pending_flatten = why
            self._log("risk", {"type": "flatten_pending", "ts": t_close, "reason": why})

        # 4) 결정 주기 경계 결정 (다음 봉 시가 직전)
        if is_boundary(t_close, self.tf):
            self._decide(t_close, hist, book, live)

        # 5) 다음 봉 시가 근처 호가 = 지금 호가
        self.book_open, self.book_open_wall = book, self.clock()
        for e in self.risk.drain_events():
            rec = {**{k: v for k, v in e.items() if k != "type"}, "type": "risk_event",
                   "event": e.get("type"), "ts": t_close}
            self._log("risk", rec)
            events.append(rec)
        if self.persist:
            self.save_state()
        return events

    def _tick(self, hist: M1History, ts: pd.Timestamp) -> float:
        """봉 ts 의 호가 단위: 그 UTC 일자(+ 직전 일) 실시간 1분봉 종가로 `signals.infer_ticks` (백테스트와 같은 함수)."""
        m = ts_minute(ts)
        day = m // 1440
        if self._tick_cache is not None and self._tick_cache[0] == day and self._tick_cache[1] > 0:
            return self._tick_cache[1]
        fr = hist.frame((day - 1) * 1440, m)
        tk = float(infer_ticks(fr)[-1]) if len(fr) else 0.0
        self._tick_cache = (day, tk)
        return tk

    def _cancel_order(self, ts: pd.Timestamp, why: str) -> None:
        od = self.engine.order
        self.engine.order = None
        self._log("fills", {"type": "order_cancel", "ts": ts, "order": _order_dict(od), "reason": why})

    def _decide(self, t: pd.Timestamp, hist: M1History, book: dict | None, live: bool) -> None:
        bar = t - self.step
        win = hist.window(t - ONE_MIN, self.window_min)
        aux_m1 = None
        if self.aux_keys:
            lo = ts_minute(win.index[0]) if len(win) else ts_minute(t)
            aux_m1 = {k: self.aux_hist[k].frame(lo, ts_minute(t - ONE_MIN)) for k in self.aux_keys}
        sig = evaluate_signals(self.spec, win, bar, self.registry, self.ctx, aux_m1)
        ok, why = self.risk.can_enter(_dt(t))
        if ok and self.group is not None:
            ok, why = self.group.can_enter(_dt(t))
        pos = self.engine.in_position
        cooldown = self.engine.cooldown_until is not None and t < self.engine.cooldown_until
        entry_ok = bool(sig["entry_ok"] and ok)
        self.engine.on_decision(t, entry_ok, sig["exit_sig"], sig["atr"])
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
            sl, tp = ev.sl, ev.tp        # 같은 봉에서 청산까지 되면 engine.position 은 이미 None
            expiry = ev.ts + pd.Timedelta(minutes=int(self.spec.exit.max_hold_min))
            lad = self.spec.ladder
            if ev.tranche is not None and lad is not None:   # 사다리 분할: 명목 = 사다리 시작 자산 × w
                if ev.tranche == 0 or self.ladder_equity is None:
                    self.ladder_equity = self.equity
                    self.tranche_qty = {}
                notional = self.ladder_equity * float(ev.weight)
                maker = (self.spec.execution.limit_entry if ev.tranche == 0 else lad.limit_orders)
            else:
                notional = self.equity * float(self.spec.sizing.entry_fraction)
                maker = self.spec.execution.limit_entry          # 지정가 체결 = 지정가 (호가 대입 비교 없음)
            bf = (walk_book(self.book_open, "buy", notional=notional, now=self.book_open_wall)
                  if self.book_open and not maker else None)
            cmp = compare_to_model(bf, ev.fill_price, ev.ref_price, self.cost, self.conservative)
            if maker:
                cmp["maker"] = True
            if ev.tranche is None or ev.tranche == 0:
                self.entry_cmp = cmp
            self.exit_capture = None
            q = notional / ev.fill_price
            if ev.tranche is not None and lad is not None:
                self.tranche_qty[int(ev.tranche)] = q
                self.qty = sum(self.tranche_qty.values())
            else:
                self.qty = q
            self.risk.on_fill(_dt(ev.ts), "buy", q, ev.fill_price, fee=None)
            if self.group is not None:
                self.group.on_entry(_dt(ev.ts))
            rec = {"type": "fill", "side": "buy", "ts": ev.ts, "reason": ev.reason,
                   "ref_price": ev.ref_price, "fill_price": ev.fill_price, "qty": q,
                   "notional": notional, "sl": sl, "tp": tp, "expiry": expiry,
                   **({"tranche": int(ev.tranche), "weight": float(ev.weight),
                       "ladder": int(self.engine.n_ladders - 1)} if ev.tranche is not None else {}),
                   "live": live, **cmp}
            self._log("fills", rec)
            return [{"type": "entry" if (ev.tranche is None or ev.tranche == 0) else "scale_in", "ts": ev.ts,
                     "price": ev.fill_price, "sl": sl, "tp": tp, "book_cost_bps": cmp.get("book_cost_bps"),
                     **({"tranche": int(ev.tranche)} if ev.tranche is not None else {}), "live": live}]

        # sell
        if ev.trade is not None:          # 사다리·부분 사이징: 이 청산의 거래 행 (분할마다)
            trade = ev.trade
            if any(t is trade for t in self.engine.trades):
                self.engine.trades = [t for t in self.engine.trades if t is not trade]
        else:
            trade = self.engine.trades.pop() if self.engine.trades else None
        tranche = ev.tranche
        qty = self.tranche_qty.pop(int(tranche), 0.0) if tranche is not None else self.qty
        if ev.reason in ("sl", "tp") and self.exit_capture and self.exit_capture["reason"] == ev.reason:
            src, book, at = "stream_trigger", self.exit_capture["book"], self.exit_capture["wall"]
        elif ev.reason in ("sl", "tp"):
            src, book, at = "bar_close", book_now, now
        else:
            src, book, at = "bar_open", self.book_open, self.book_open_wall
        maker_x = ev.reason == "tp" and self.spec.execution.limit_tp
        if maker_x:
            src, book = "limit_order", None
        bf = walk_book(book, "sell", qty=qty, now=at) if book and qty > 0 else None
        cmp = compare_to_model(bf, ev.fill_price, ev.ref_price, self.cost, self.conservative)
        if maker_x:
            cmp["maker"] = True
        eq_before = self.equity
        if trade and tranche is not None and self.spec.ladder is not None:   # 실현 = 사다리 시작 자산 × w × r
            eg = self.ladder_equity if self.ladder_equity is not None else eq_before
            self.equity = eq_before + eg * float(ev.weight) * trade["net_ret"]
        elif trade:
            self.equity = eq_before * (1 + float(self.spec.sizing.entry_fraction) * trade["net_ret"])
        book_net = None
        if (bf is not None and self.entry_cmp and self.entry_cmp.get("book") and math.isfinite(bf.vwap)
                and (tranche is None or tranche == 0)):        # 사다리: 첫 분할만 진입 호가 비교가 있다
            book_net = self.cost.net_return(self.entry_cmp["book"]["vwap"], bf.vwap)
        t_eff = ev.ts + (ONE_MIN if ev.reason in ("sl", "tp") else pd.Timedelta(0))
        self.risk.on_trade_closed(_dt(t_eff), self.equity - eq_before, self.equity)
        frec = {"type": "fill", "side": "sell", "ts": ev.ts, "reason": ev.reason,
                "ref_price": ev.ref_price, "fill_price": ev.fill_price, "qty": qty,
                **({"tranche": int(tranche)} if tranche is not None else {}),
                "book_source": src, "live": live, **cmp}
        self._log("fills", frec)
        self.n_trades += 1
        trec = {"type": "trade", **(trade or {}), "spec_id": self.spec.id, "version": self.spec.version,
                "cost": self.cost.name, "grade": self.grade, "qty": qty,
                "equity_before": eq_before, "equity_after": self.equity,
                "entry_book_cost_bps": (self.entry_cmp or {}).get("book_cost_bps"),
                "entry_vs_model_bps": (self.entry_cmp or {}).get("vs_model_bps"),
                "exit_book_cost_bps": cmp.get("book_cost_bps"),
                "exit_vs_model_bps": cmp.get("vs_model_bps"),
                "book_net_ret": book_net, "live": live}
        self._log("trades", trec)
        if tranche is not None and self.spec.ladder is not None:
            self.qty = sum(self.tranche_qty.values())
            if self.engine.lad is None and not self.tranche_qty:   # 사다리 종료 (같은 봉 전량 청산의 마지막 분할까지 처리 후)
                self.qty = 0.0
                self.tranche_qty = {}
                self.ladder_equity = None
                self.entry_cmp = None
                self.exit_capture = None
        else:
            self.qty = 0.0
            self.entry_cmp = None
            self.exit_capture = None
        return [{"type": "exit", "ts": ev.ts, "reason": ev.reason, "price": ev.fill_price,
                 "net_ret": trade["net_ret"] if trade else None, "equity": self.equity,
                 **({"equity_ret": self.equity / eq_before - 1 if eq_before else None,
                     "tranche": tranche} if self.spec.is_sized else {}), "live": live}]

    # ------------------------------------------------------------------ 저장/복원
    def state_dict(self) -> dict:
        e = self.engine
        pos = None
        if e.position is not None:
            p = e.position
            pos = {"entry_time": _iso(p.entry_time), "ref": p.ref, "entry_price": p.entry_price,
                   "sl": p.sl, "tp": p.tp, "expiry": _iso(p.expiry), "atr_frac": p.atr_frac,
                   "maker": bool(p.maker), "tp_order": _order_dict(p.tp_order)}
        dec = None
        if e.decision is not None:
            dec = {"ts": _iso(e.decision.ts), "entry_ok": e.decision.entry_ok,
                   "exit_sig": e.decision.exit_sig, "atr": e.decision.atr}
        return _jsonable({
            "version": STATE_VERSION, "key": self.key, "spec_id": self.spec.id,
            "spec_version": self.spec.version, "logic_hash": self.spec.logic_hash(),
            "cost": asdict(self.cost), "grade": self.grade,
            "initial_equity": self.initial_equity, "equity": self.equity, "qty": self.qty,
            "last_ts": _iso(self.last_ts), "start": _iso(e.start),
            "position": pos, "cooldown_until": _iso(e.cooldown_until), "decision": dec,
            "order": _order_dict(e.order), "execution": self.spec.execution.to_dict(),
            "ladder_state": _ladder_dict(e.lad), "n_ladders": e.n_ladders, "ladder_equity": self.ladder_equity,
            "tranche_qty": {str(k): v for k, v in self.tranche_qty.items()},
            "pending_flatten": self.pending_flatten, "entry_cmp": self.entry_cmp,
            "n_trades": self.n_trades, "n_decisions": self.n_decisions,
            "timeframe": self.spec.timeframe, "lookback_bars": self.lookback_bars, "saved_at": time.time(),
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
        if d.get("version") not in READABLE_STATE_VERSIONS:
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
            float(pos["tp"]), _ts(pos["expiry"]), pos.get("atr_frac"), bool(pos.get("maker", False)),
            _order_from(pos.get("tp_order")))
        e.order = _order_from(d.get("order"))
        e.lad = _ladder_from(d.get("ladder_state"))
        e.n_ladders = int(d.get("n_ladders", 0))
        self.ladder_equity = None if d.get("ladder_equity") is None else float(d["ladder_equity"])
        self.tranche_qty = {int(k): float(v) for k, v in (d.get("tranche_qty") or {}).items()}
        dec = d.get("decision")
        if dec is not None:
            e.on_decision(_ts(dec["ts"]), dec["entry_ok"], dec["exit_sig"], dec.get("atr"))
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
            "position": (_ladder_status(self.engine.lad) if self.engine.lad is not None else
                         None if pos is None else {"entry_time": pos.entry_time, "ref": pos.ref,
                                                   "sl": pos.sl, "tp": pos.tp, "expiry": pos.expiry}),
            "last_ts": self.last_ts, "n_trades": self.n_trades, "n_decisions": self.n_decisions,
            "halt_level": rs["level"], "halt_reasons": rs["reasons"][:3],
            "daily_return": rs["daily_return"], "drawdown": rs["drawdown"],
            "trades_today": rs["trades_today"],
        })
