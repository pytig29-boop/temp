"""기준 엔진 — 읽기 쉬운 이벤트 기반 구현 (numba 없음). 페이퍼 엔진의 핵심이 된다.

의미론은 docs/ENGINE_SPEC.md. 빠른 엔진(fast.py)과 거래 목록이 정확히 일치해야 한다.

구동 방법 (실시간/재생 공통). B = 결정 주기 봉 길이(분, 기본 5; 1/15/60/240 도 가능):
    eng = ReferenceEngine(sl_pct, tp_pct, max_hold_min, cost, start, end)        # 5분·고정 % (기존)
    eng = ReferenceEngine.from_exit(spec.exit, cost, start, end, spec.timeframe)   # 일반
    매 B분 경계 t (봉 t-B 확정 직후):  eng.on_decision(t, entry_ok, exit_sig[, atr])
    매 1분봉 t (시가부터 알려짐):       events = eng.on_bar_1m(t, o, h, l, c, filled)
    데이터가 끝났을 때:                 eng.finish(last_ts, last_close)

ATR 청산 모드(sl_atr/tp_atr 지정) 또는 ATR 오프셋 지정가 진입에서는 on_decision 에 봉 t-B 의 ATR(절대값,
`signals.atr_series`)을 넘긴다.

지정가 집행 (`execution`, docs/ENGINE_SPEC.md "지정가 집행"):
    eng = ReferenceEngine.from_exit(spec.exit, cost, start, end, spec.timeframe,
                                    execution=spec.execution, fill=LimitFillModel(...))
- 진입 지정가는 `eng.order`(RestingOrder)로 드러나고, 1분봉마다 체결 여부를 판정한다:
  기본은 1분봉 관통 규칙(`LimitFillModel`), `eng.fill_oracle` 을 주면 그 함수가 판정한다(페이퍼: 실시간
  호가·체결 대기열 재현 — `backtest.queue.QueueFillOracle`). 익절 지정가는 `position.tp_order`.
- 1분봉에 거래량을 넘기려면 on_bar_1m(..., volume=v) (LimitFillModel.min_volume > 0 일 때만 쓰임).

부분 사이징·분할 매수 사다리 (`sizing`, `ladder`, docs/ENGINE_SPEC.md "부분 사이징·분할 매수"):
    eng = ReferenceEngine.from_exit(spec.exit, cost, start, end, spec.timeframe, execution=spec.execution,
                                    fill=..., sizing=spec.sizing, ladder=spec.ladder)
- 사다리가 열려 있으면 `eng.lad`(LadderState: 보유 분할 `held`, 걸린 분할 주문 `pending`{j: RestingOrder},
  공동 손절 `stop`, 만기 `expiry`)이고 `eng.position` 은 None 이다 — 보유 여부는 `eng.in_position`.
- 분할 체결은 FillEvent(side="buy", reason="entry"(j=0) | "scale_in"(j≥1), tranche=j, weight=w),
  청산은 분할마다 FillEvent(side="sell", tranche=j, trade=<거래 행 dict>).
- 거래 행에는 weight/ladder/tranche 열이 붙는다(`sizing.SIZED_COLUMNS`), 일간 수익은 `sizing.equity_returns`.

엔진은 I/O가 없고 모든 상태는 속성으로 드러나 있다(position, lad, order, cooldown_until, trades).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from btc_quant.backtest.signals import (
    FIVE_MIN,
    atr_series,
    build_signals,
    day_index,
    infer_ticks,
    period_bounds,
)
from btc_quant.backtest.sizing import SIZED_COLUMNS, frame_equity_returns, is_sized_frame
from btc_quant.backtest.types import (
    LIMIT_FILL_DEFAULT,
    TRADE_COLUMNS,
    BacktestResult,
    CostModel,
    LimitFillModel,
)
from btc_quant.strategy.blocks.base import REGISTRY, Context, Registry
from btc_quant.strategy.spec import (
    FULL,
    MARKET,
    ExecutionSpec,
    ExitSpec,
    LadderSpec,
    SizingSpec,
    StrategySpec,
    tf_minutes,
)

ONE_MIN = pd.Timedelta(minutes=1)


_NS_PER_MIN = 60_000_000_000


def is_boundary(ts: pd.Timestamp, tf_min: int = 5) -> bool:
    """결정 시점(B분 경계) 여부 — UTC epoch 분 % B == 0 (빠른 엔진 `signals.boundary_mask` 와 같은 정의).
    B=1 이면 모든 정각 분. B ≤ 60 이면 '시각의 분 % B == 0' 과 같고, B=240 이면 00/04/08… UTC 정각."""
    ts = pd.Timestamp(ts)
    if ts.second or ts.microsecond or ts.nanosecond:
        return False
    return (ts.value // _NS_PER_MIN) % int(tf_min) == 0


def _ceil_boundary(t: pd.Timestamp, tf_min: int) -> pd.Timestamp:
    """t 이상인 첫 B분 경계 (UTC epoch 기준; 시간대와 무관)."""
    step = int(tf_min) * _NS_PER_MIN
    v = -(-pd.Timestamp(t).value // step) * step
    return pd.Timestamp(v, tz="UTC").tz_convert(t.tz) if t.tz is not None else pd.Timestamp(v)


@dataclass(frozen=True)
class Decision:
    ts: pd.Timestamp       # 이 결정이 적용되는 1분봉 시각(B분 경계)
    entry_ok: bool         # 봉 ts-B 기준 진입 신호
    exit_sig: bool         # 봉 ts-B 기준 신호 청산
    atr: float | None = None   # 봉 ts-B 의 ATR (ATR 청산 모드에서만 사용)


@dataclass(frozen=True)
class FillEvent:
    ts: pd.Timestamp       # 체결 1분봉 시각
    side: str              # "buy" | "sell"
    ref_price: float       # 비용 전 기준가
    fill_price: float      # 비용 반영 체결가
    reason: str            # "entry" | "sl" | "tp" | "signal" | "time" | "end"
    sl: float | None = None   # buy 이벤트: 진입 시 확정된 손절·익절 레벨 (같은 봉 청산으로
    tp: float | None = None   # position 이 이미 None 이어도 알 수 있게). sell 이벤트는 None
    tranche: int | None = None    # 사다리: 단 번호 j (단일 진입 = None)
    weight: float | None = None   # 사다리·부분 사이징: 분할 크기 (사다리 시작 자산 대비)
    trade: dict | None = None     # sell 이벤트(사다리·부분 사이징): 이 청산의 거래 행


@dataclass(frozen=True)
class RestingOrder:
    """걸려 있는 지정가 주문 (진입 매수 또는 익절 매도). 체결 판정: 1분봉 관통 규칙 또는 fill_oracle."""
    id: str                # 결정적 식별자 "<kind>@<placed ISO>"
    kind: str              # "entry" | "tp"
    side: str              # "buy" | "sell"
    price: float           # 지정가 (체결가)
    trigger: float         # 1분봉 규칙: 매수 low ≤ trigger, 매도 high ≥ trigger
    placed: pd.Timestamp   # 주문 시각 = 이 1분봉 시가 (진입: 결정 시점 / 익절: 진입 체결 봉)
    expires: pd.Timestamp | None = None   # 이 시각 이상의 1분봉에서는 이미 취소됨 (익절: None = 포지션 동안)
    draw_ok: bool = True   # LimitFillModel.fill_prob 해시 통과 여부
    atr_frac: float | None = None         # 진입 주문: 결정 시 ATR / o_i (청산 레벨용)


@dataclass
class Position:
    entry_time: pd.Timestamp
    ref: float             # 진입 기준가 (시장가: 진입 봉 시가, 지정가: 지정가)
    entry_price: float     # 비용 반영 체결가
    sl: float
    tp: float
    expiry: pd.Timestamp
    atr_frac: float | None = None   # ATR 모드: 진입 시 ATR / ref
    maker: bool = False             # 지정가 진입 (체결 봉에서는 익절 없음 — 봉 내 순서를 모름)
    tp_order: RestingOrder | None = None   # 익절 지정가 (execution.take_profit = "limit")


@dataclass
class Tranche:
    """사다리의 체결된 분할 하나."""
    j: int
    entry_time: pd.Timestamp
    ref: float             # 기준가 (시장가 = 시가/단 가격, 지정가 = 지정가)
    entry_price: float     # 비용 반영 체결가
    maker: bool
    tp: float              # 분할별 익절가 (per_tranche; 익절 지정가면 호가 단위로 올림한 지정가)
    at_open: bool = False  # 봉 시가 체결(시장가 첫 진입) — 그 봉에서도 익절 가능. 봉 내 체결은 그 봉에서 익절 없음
    tp_order: RestingOrder | None = None


@dataclass
class LadderState:
    """열린 사다리 (docs/ENGINE_SPEC.md "부분 사이징·분할 매수")."""
    id: int                      # 사다리 번호 (0, 1, … 시간순) = 거래 행 `ladder`
    start: pd.Timestamp          # 첫 분할 체결 1분봉
    p1: float                    # 첫 분할 기준가
    weight: float                # 분할 크기 w = max_exposure / N
    d_step: float
    d_sl: float
    d_tp: float
    tick: float                  # 사다리 주문의 호가 단위 (첫 체결 봉; NaN = 모름 → 분할·익절 지정가 체결 없음)
    stop: float                  # 공동 손절가 = P1·(1 − (N−1)·d_step) · (1 − d_sl)
    expiry: pd.Timestamp         # 첫 분할 체결 + max_hold_min
    maker_first: bool            # 첫 분할이 지정가 체결 (그 봉에서는 분할 주문 비활성·익절 없음)
    atr_frac: float | None = None
    held: list[Tranche] = field(default_factory=list)
    pending: dict[int, RestingOrder] = field(default_factory=dict)   # j → 걸린 분할 매수 주문
    n_filled: int = 0
    inv_sum: float = 0.0         # Σ 1/ref (평균단가 = 체결 수 / inv_sum, 체결 순서대로 합산)
    avg_tp: float | None = None  # 평균단가 익절가 (average)
    avg_tp_order: RestingOrder | None = None


@dataclass
class ReferenceEngine:
    sl_pct: float
    tp_pct: float
    max_hold_min: int
    cost: CostModel
    start: pd.Timestamp | None = None   # 진입 허용 [start, end)
    end: pd.Timestamp | None = None

    position: Position | None = None
    cooldown_until: pd.Timestamp | None = None   # 이 시각 이상의 결정부터 진입 가능
    decision: Decision | None = None
    trades: list[dict] = field(default_factory=list)
    done: bool = False                            # end 청산 후 True
    timeframe_min: int = 5                        # 결정 주기 B (분)
    sl_atr: float | None = None                   # 지정 시 ATR 모드 (sl_pct/tp_pct 무시)
    tp_atr: float | None = None
    execution: ExecutionSpec = MARKET             # 지정가 진입·익절 (기본 = 시장가, 기존 의미론)
    fill: LimitFillModel = LIMIT_FILL_DEFAULT     # 지정가 1분봉 체결 규칙
    # 지정 시 1분봉 규칙 대신 체결 판정: fill_oracle(order, ts, bar) -> bool ("1분봉 ts 동안 체결됐나").
    # 주문마다 시간순으로 1분봉당 한 번 호출된다(주문 봉부터). bar = dict(open, high, low, close, volume, filled).
    fill_oracle: Callable[[RestingOrder, pd.Timestamp, dict], bool] | None = None
    order: RestingOrder | None = None             # 걸려 있는 진입 지정가
    orders_log: list[dict] = field(default_factory=list)   # 지정가 진입 주문 기록 (placed/filled/expired)
    sizing: SizingSpec = FULL                     # 부분 사이징 (기본 전액 = 기존)
    ladder: LadderSpec | None = None              # 분할 매수 사다리 (기본 없음)
    lad: LadderState | None = None                # 열린 사다리
    n_ladders: int = 0                            # 지금까지 연 사다리(포지션) 수 = 다음 사다리 번호

    @classmethod
    def from_exit(cls, exit: ExitSpec, cost: CostModel, start: pd.Timestamp | None = None,
                  end: pd.Timestamp | None = None, timeframe: str = "5m",
                  execution: ExecutionSpec | None = None,
                  fill: LimitFillModel | None = None, sizing: SizingSpec | None = None,
                  ladder: LadderSpec | None = None) -> ReferenceEngine:
        return cls(exit.stop_loss_pct, exit.take_profit_pct, exit.max_hold_min, cost, start, end,
                   timeframe_min=tf_minutes(timeframe), sl_atr=exit.stop_loss_atr,
                   tp_atr=exit.take_profit_atr, execution=execution or MARKET,
                   fill=fill or LIMIT_FILL_DEFAULT, sizing=sizing or FULL, ladder=ladder)

    def __post_init__(self):
        self.execution.validate()
        self.sizing.validate()
        if self.ladder is not None:
            self.ladder.validate()
            if not self.sizing.is_default:
                raise ValueError("sizing.entry_fraction must be 1.0 with a ladder")
        if not self.execution.is_default or self.ladder is not None:
            self.fill.validate()

    @property
    def sized(self) -> bool:
        """거래 행에 weight/ladder/tranche 가 붙는가 (부분 사이징 또는 사다리)."""
        return self.ladder is not None or not self.sizing.is_default

    @property
    def in_position(self) -> bool:
        """보유 중 (단일 포지션 또는 열린 사다리)."""
        return self.position is not None or self.lad is not None

    @property
    def atr_mode(self) -> bool:
        return self.sl_atr is not None

    @property
    def needs_atr(self) -> bool:
        """결정 시 ATR 이 필요한가 (ATR 청산 · ATR 오프셋 지정가 · ATR 간격 사다리)."""
        return (self.atr_mode or self.execution.offset_mode == "atr"
                or (self.ladder is not None and self.ladder.step_mode == "atr"))

    # ---------- 이벤트 ----------
    def on_decision(self, ts: pd.Timestamp, entry_ok: bool, exit_sig: bool,
                    atr: float | None = None) -> None:
        """B분 경계 ts의 1분봉 시가 직전에 호출. 다음 on_bar_1m(ts, ...)에서 사용된다.
        atr = 봉 ts-B 의 ATR (ATR 청산 모드에서 없거나 NaN 이면 진입 불가)."""
        self.decision = Decision(ts, bool(entry_ok), bool(exit_sig),
                                 None if atr is None else float(atr))

    def on_bar_1m(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float,
                  filled: bool = False, volume: float | None = None,
                  tick: float | None = None) -> list[FillEvent]:
        """tick: 이 1분봉의 호가 단위 (LimitFillModel.tick_from_data 일 때 지정가 가격·관통 폭에 쓰임;
        백테스트는 `signals.infer_ticks`, 페이퍼는 거래소 호가 단위)."""
        events: list[FillEvent] = []
        if self.done:
            return events
        dec = self.decision if (self.decision is not None and self.decision.ts == ts) else None
        self.decision = None
        past_end = self.end is not None and ts >= self.end
        bar = {"open": o, "high": h, "low": l, "close": c, "volume": volume, "filled": bool(filled)}
        if self.ladder is not None:
            return self._on_bar_ladder(ts, o, h, l, c, bool(filled), bar, dec, past_end, tick)

        # 1) 보유 중: 시가 청산 (만기 > 신호 > 기간 종료) — 이 봉의 SL/TP 검사 전에 나간다
        if self.position is not None:
            reason = None
            if ts >= self.position.expiry:
                reason = "time"
            elif dec is not None and dec.exit_sig:
                reason = "signal"
            elif past_end:
                reason = "end"
            if reason is not None:
                events.append(self._close(ts, o, reason, at_open=True))
                if past_end:
                    self.done = True
                    return events

        # 1b) 진입 지정가 만료·기간 종료 → 이 봉 시가 전에 취소 (같은 봉에서 새 주문 가능)
        if self.order is not None and (past_end or (self.order.expires is not None and ts >= self.order.expires)):
            self._log_order(self.order, "expired", ts)
            self.order = None

        # 2) 무포지션: 진입 (시장가 = 이 봉 시가, 지정가 = 이 봉 시가에 주문)
        frac = self._atr_frac(dec, o)
        entered_now = False
        lim_tick = self.fill.order_tick(tick) if not self.execution.is_default else math.nan
        if self.position is None and self.order is None and self._can_enter(ts, dec, filled, past_end, frac):
            if self.execution.limit_entry:
                if not math.isnan(lim_tick):          # 호가 단위를 모르면 지정가를 걸지 않는다 (진입 신호 False 와 동일)
                    self.order = self._place_entry(ts, o, frac, lim_tick)
            else:
                # 시장가 진입: 기본 집행이면 기존 호출 형태 그대로 (하위 클래스 호환)
                events.append(self._open(ts, o, frac, tick=lim_tick) if self.execution.limit_tp
                              else self._open(ts, o, frac))
                entered_now = True

        # 2b) 진입 지정가 체결 판정 (주문 봉 포함)
        maker_now = False
        if (self.order is not None and self.position is None and not past_end
                and self._limit_hit(self.order, ts, bar)):
            od = self.order
            self.order = None
            self._log_order(od, "filled", ts)
            events.append(self._open(ts, od.price, od.atr_frac, maker=True, tick=lim_tick))
            maker_now = True

        # 3) 보유 중: 봉 내 청산 — SL 갭 > SL 터치 > TP (같은 봉 SL·TP 동시 → SL)
        #    지정가 체결 봉: 체결이 봉 안 어디서 일어났는지 모르므로 SL 만 (갭 포함), 익절 없음 (보수적)
        pos = self.position
        if pos is not None:
            if (maker_now or not entered_now) and o <= pos.sl:
                events.append(self._close(ts, o, "sl", at_open=False))
            elif l <= pos.sl:
                events.append(self._close(ts, pos.sl, "sl", at_open=False))
            elif not maker_now:
                if pos.tp_order is not None:
                    if self._limit_hit(pos.tp_order, ts, bar):
                        events.append(self._close(ts, pos.tp_order.price, "tp", at_open=False, maker=True))
                elif h >= pos.tp + self.cost.tick:
                    events.append(self._close(ts, pos.tp, "tp", at_open=False))
        if past_end:
            self.done = True
        return events

    def finish(self, last_ts: pd.Timestamp, last_close: float) -> list[FillEvent]:
        """end 이후 봉이 없을 때: 열린 포지션(사다리면 전량)을 마지막 1분봉 종가로 청산."""
        if self.lad is not None and not self.done:
            evs = self._ladder_close_all(last_ts, last_close, "end", at_open=False)
            self.done = True
            return evs
        if self.position is None or self.done:
            self.done = True
            return []
        ev = self._close(last_ts, last_close, "end", at_open=False)
        self.done = True
        return [ev]

    # ---------- 내부 ----------
    def _atr_frac(self, dec: Decision | None, o: float) -> float | None:
        """ATR 모드: ATR(봉 ts-B) / o. 유한한 양수가 아니면 None (진입 불가)."""
        if not self.needs_atr or dec is None or dec.atr is None:
            return None
        frac = dec.atr / o if o != 0 else math.nan
        return frac if (math.isfinite(frac) and frac > 0) else None

    def _can_enter(self, ts, dec: Decision | None, filled: bool, past_end: bool,
                   frac: float | None = None) -> bool:
        if dec is None or not dec.entry_ok or filled or past_end:
            return False
        if self.needs_atr and frac is None:
            return False
        if self.start is not None and ts < self.start:
            return False
        return self.cooldown_until is None or ts >= self.cooldown_until

    def _open(self, ts: pd.Timestamp, o: float, frac: float | None = None,
              maker: bool = False, tick: float = math.nan) -> FillEvent:
        """진입. 시장가: o = 봉 시가 (비용 반영), 지정가(maker): o = 지정가 (체결가 = 지정가, 비용 없음).
        tick: 익절 지정가용 호가 단위 (이 봉; NaN 이면 익절 지정가는 체결되지 않음)."""
        ref = o
        if self.atr_mode:
            d_sl, d_tp = self.sl_atr * frac, self.tp_atr * frac
        else:
            d_sl, d_tp = self.sl_pct / 100.0, self.tp_pct / 100.0
        tp = ref * (1.0 + d_tp)
        tp_order = None
        if self.execution.limit_tp:
            if not math.isnan(tick):
                px = self.fill.sell_price(tp, tick)
                trig = self.fill.sell_trigger(px, tick)
                ok = self.fill.draw_ok(_epoch_min(ts), 1)
            else:                                   # 호가 단위 모름 → 익절 지정가 체결 없음
                px, trig, ok = tp, math.inf, False
            tp_order = RestingOrder(f"tp@{ts.isoformat()}", "tp", "sell", px, trig, ts, None, ok)
            tp = px
        self.position = Position(
            entry_time=ts, ref=ref, entry_price=ref if maker else self.cost.buy_fill(ref),
            sl=ref * (1.0 - d_sl), tp=tp,
            expiry=ts + pd.Timedelta(minutes=int(self.max_hold_min)),
            atr_frac=frac if self.atr_mode else None, maker=maker, tp_order=tp_order,
        )
        pos = self.position
        return FillEvent(ts, "buy", ref, pos.entry_price, "entry", sl=pos.sl, tp=pos.tp)

    def _place_entry(self, ts: pd.Timestamp, o: float, frac: float | None, tick: float) -> RestingOrder:
        """진입 지정가: L = snap(o × (1 − d_off)), d_off = offset_bp/1e4 또는 offset_atr × (ATR/o)."""
        ex = self.execution
        d_off = ex.offset_atr * frac if ex.offset_mode == "atr" else ex.offset_bp / 1e4
        px = self.fill.buy_price(o * (1.0 - d_off), tick)
        od = RestingOrder(f"entry@{ts.isoformat()}", "entry", "buy", px, self.fill.buy_trigger(px, tick), ts,
                          ts + pd.Timedelta(minutes=int(ex.tif_bars) * int(self.timeframe_min)),
                          self.fill.draw_ok(_epoch_min(ts), 0), frac)
        self._log_order(od, "placed", ts)
        return od

    def _limit_hit(self, od: RestingOrder, ts: pd.Timestamp, bar: dict) -> bool:
        """지정가 od 가 1분봉 ts 동안 체결됐는가."""
        if self.fill_oracle is not None:
            return bool(self.fill_oracle(od, ts, bar))
        if bar["filled"] or not od.draw_ok:
            return False
        mv = self.fill.min_volume
        if mv > 0 and not (bar["volume"] is not None and bar["volume"] >= mv):
            return False
        return bar["low"] <= od.trigger if od.side == "buy" else bar["high"] >= od.trigger

    def _log_order(self, od: RestingOrder, status: str, ts: pd.Timestamp) -> None:
        self.orders_log.append({"id": od.id, "kind": od.kind, "status": status, "ts": ts, "price": od.price})

    def _close(self, ts: pd.Timestamp, ref_price: float, reason: str, at_open: bool,
               maker: bool = False) -> FillEvent:
        pos = self.position
        exit_fill = ref_price if maker else self.cost.sell_fill(ref_price)
        # 시가 청산(만기·신호·end)은 봉 시각, 봉 내 청산(SL·TP, SL 갭 포함)은 봉 시각 + 1분
        t_eff = ts if at_open else ts + ONE_MIN
        row = {
            "entry_time": pos.entry_time,
            "entry_price": pos.entry_price,
            "exit_time": ts,
            "exit_price": exit_fill,
            "exit_reason": reason,
            "gross_ret": ref_price / pos.ref - 1,
            "net_ret": self.cost.net_return(pos.entry_price, exit_fill),
            "hold_min": int((t_eff - pos.entry_time) / ONE_MIN),
        }
        if self.sized:            # 부분 사이징 (단일 진입): 거래마다 사다리 하나
            row.update(weight=float(self.sizing.entry_fraction), ladder=self.n_ladders, tranche=0)
            self.n_ladders += 1
        self.trades.append(row)
        self.position = None
        self._set_cooldown(t_eff)
        return FillEvent(ts, "sell", ref_price, exit_fill, reason, trade=row if self.sized else None)

    def _set_cooldown(self, t_eff: pd.Timestamp) -> None:
        # 쿨다운: t_eff 이후 첫 B분 경계 결정은 건너뛴다 (= 완전한 봉 1개 관찰 후 진입)
        b = int(self.timeframe_min)
        if b == 5:
            self.cooldown_until = t_eff.ceil("5min") + FIVE_MIN
        elif b == 1:
            self.cooldown_until = t_eff.ceil("1min") + ONE_MIN
        else:   # 15m/1h/4h: epoch 정렬 올림 (Timestamp.ceil 은 tz-aware 에서 벽시계 기준이라 쓰지 않는다)
            self.cooldown_until = _ceil_boundary(t_eff, b) + pd.Timedelta(minutes=b)

    # ---------- 분할 매수 사다리 ----------
    def _on_bar_ladder(self, ts, o, h, l, c, filled: bool, bar: dict, dec: Decision | None,
                       past_end: bool, tick) -> list[FillEvent]:
        """사다리 모드의 1분봉 처리 (docs/ENGINE_SPEC.md "부분 사이징·분할 매수" — 처리 순서 1~6)."""
        events: list[FillEvent] = []
        lad = self.lad
        # 1) 시가 청산 (만기 > 신호 > 기간 종료) — 전량, 걸린 분할 주문 취소
        if lad is not None:
            reason = None
            if ts >= lad.expiry:
                reason = "time"
            elif dec is not None and dec.exit_sig:
                reason = "signal"
            elif past_end:
                reason = "end"
            if reason is not None:
                events += self._ladder_close_all(ts, o, reason, at_open=True)
                if past_end:
                    self.done = True
                    return events
        # 1b) 진입 지정가 만료·기간 종료
        if self.order is not None and (past_end or (self.order.expires is not None and ts >= self.order.expires)):
            self._log_order(self.order, "expired", ts)
            self.order = None
        # 2) 첫 분할: 시장가 = 이 봉 시가, 지정가 = 이 봉 시가에 주문
        frac = self._atr_frac(dec, o)
        lim_tick = self.fill.order_tick(tick)
        if self.lad is None and self.order is None and self._can_enter(ts, dec, filled, past_end, frac):
            if self.execution.limit_entry:
                if not math.isnan(lim_tick):
                    self.order = self._place_entry(ts, o, frac, lim_tick)
            else:
                events += self._open_ladder(ts, o, frac, maker=False, tick=lim_tick)
        # 2b) 첫 분할 지정가 체결
        if self.order is not None and self.lad is None and not past_end and self._limit_hit(self.order, ts, bar):
            od = self.order
            self.order = None
            self._log_order(od, "filled", ts)
            events += self._open_ladder(ts, od.price, od.atr_frac, maker=True, tick=lim_tick)
        lad = self.lad
        if lad is None:
            if past_end:
                self.done = True
            return events
        # 3) 분할 체결 (단 번호 오름차순 = 가격 내림차순). 지정가 첫 분할의 체결 봉에서는 비활성
        intrabar = lad.maker_first and ts == lad.start
        if ts > lad.start or not lad.maker_first:
            for j in sorted(lad.pending):
                od = lad.pending[j]
                px = None
                if self.ladder.limit_orders:
                    if self._limit_hit(od, ts, bar):
                        px, maker = od.price, True
                elif od.draw_ok and not filled:      # 시장가 트리거: 시가가 이미 단 아래면 시가, 아니면 관통 시 단 가격
                    if o <= od.price:
                        px, maker = o, False
                    elif l <= od.trigger:
                        px, maker = od.price, False
                if px is not None:
                    del lad.pending[j]
                    self._log_order(od, "filled", ts)
                    events.append(self._fill_tranche(ts, j, px, maker, at_open=False))
                    intrabar = True
        # 4) 공동 손절 (갭 > 터치) — 전량. 이 봉에 체결된 분할 포함
        if o <= lad.stop:
            return events + self._ladder_close_all(ts, o, "sl", at_open=False)
        if l <= lad.stop:
            return events + self._ladder_close_all(ts, lad.stop, "sl", at_open=False)
        # 5) 익절 — 이 봉 안에서 체결된 분할은 이 봉에서 익절하지 않는다
        if self.ladder.average_exit:
            if not intrabar:
                hit, px, mk = False, None, False
                if lad.avg_tp_order is not None:
                    if self._limit_hit(lad.avg_tp_order, ts, bar):
                        hit, px, mk = True, lad.avg_tp_order.price, True
                elif h >= lad.avg_tp + self.cost.tick:
                    hit, px = True, lad.avg_tp
                if hit:
                    return events + self._ladder_close_all(ts, px, "tp", at_open=False, maker=mk)
        else:
            for t in list(lad.held):
                if not (t.entry_time < ts or t.at_open):
                    continue
                if t.tp_order is not None:
                    if self._limit_hit(t.tp_order, ts, bar):
                        events.append(self._close_tranche(ts, t, t.tp_order.price, "tp", False, maker=True))
                elif h >= t.tp + self.cost.tick:
                    events.append(self._close_tranche(ts, t, t.tp, "tp", False))
            if not lad.held:
                self._end_ladder(ts + ONE_MIN, ts)
        if past_end:
            self.done = True
        return events

    def _levels(self, frac: float | None) -> tuple[float, float, float]:
        """(d_sl, d_tp, d_step) — ATR 모드는 결정 시 ATR 비율 기준."""
        if self.atr_mode:
            d_sl, d_tp = self.sl_atr * frac, self.tp_atr * frac
        else:
            d_sl, d_tp = self.sl_pct / 100.0, self.tp_pct / 100.0
        lad = self.ladder
        d_step = lad.step_atr * frac if lad.step_mode == "atr" else lad.step_bp / 1e4
        return d_sl, d_tp, d_step

    def _open_ladder(self, ts: pd.Timestamp, p1: float, frac: float | None, maker: bool,
                     tick: float) -> list[FillEvent]:
        lad_spec = self.ladder
        d_sl, d_tp, d_step = self._levels(frac)
        n = int(lad_spec.tranches)
        last = p1 * (1.0 - (n - 1) * d_step)
        lad = LadderState(id=self.n_ladders, start=ts, p1=p1, weight=lad_spec.weight, d_step=d_step, d_sl=d_sl,
                          d_tp=d_tp, tick=tick, stop=last * (1.0 - d_sl),
                          expiry=ts + pd.Timedelta(minutes=int(self.max_hold_min)), maker_first=maker,
                          atr_frac=frac)
        self.n_ladders += 1
        self.lad = lad
        m0 = _epoch_min(ts)
        known = not math.isnan(tick)
        for j in range(1, n):
            raw = p1 * (1.0 - j * d_step)
            if lad_spec.limit_orders:
                if known:
                    px = self.fill.buy_price(raw, tick)
                    od = RestingOrder(f"ladder{j}@{ts.isoformat()}", "ladder", "buy", px, self.fill.buy_trigger(px, tick),
                                      ts, None, self.fill.draw_ok(m0 * 8 + j, 0))
                else:
                    od = RestingOrder(f"ladder{j}@{ts.isoformat()}", "ladder", "buy", raw, -math.inf, ts, None, False)
            else:
                trig = raw - self.fill.threshold(raw, tick) if known else -math.inf
                od = RestingOrder(f"trigger{j}@{ts.isoformat()}", "trigger", "buy", raw, trig, ts, None, known)
            lad.pending[j] = od
            self._log_order(od, "placed", ts)
        return [self._fill_tranche(ts, 0, p1, maker, at_open=not maker)]

    def _fill_tranche(self, ts: pd.Timestamp, j: int, ref: float, maker: bool, at_open: bool) -> FillEvent:
        lad = self.lad
        tp = ref * (1.0 + lad.d_tp)
        tp_order = None
        m = _epoch_min(ts)
        known = not math.isnan(lad.tick)
        if not self.ladder.average_exit and self.execution.limit_tp:
            if known:
                px = self.fill.sell_price(tp, lad.tick)
                tp_order = RestingOrder(f"tp{j}@{ts.isoformat()}", "tp", "sell", px, self.fill.sell_trigger(px, lad.tick),
                                        ts, None, self.fill.draw_ok(m * 8 + j, 1))
                tp = px
            else:
                tp_order = RestingOrder(f"tp{j}@{ts.isoformat()}", "tp", "sell", tp, math.inf, ts, None, False)
        t = Tranche(j, ts, ref, ref if maker else self.cost.buy_fill(ref), maker, tp, at_open, tp_order)
        lad.held.append(t)
        lad.n_filled += 1
        lad.inv_sum += 1.0 / ref
        if self.ladder.average_exit:
            avg = lad.n_filled / lad.inv_sum
            atp = avg * (1.0 + lad.d_tp)
            if self.execution.limit_tp:
                if known:
                    px = self.fill.sell_price(atp, lad.tick)
                    lad.avg_tp_order = RestingOrder(f"tpavg@{ts.isoformat()}", "tp", "sell", px,
                                                    self.fill.sell_trigger(px, lad.tick), ts, None,
                                                    self.fill.draw_ok(m * 8 + 7, 1))
                    atp = px
                else:
                    lad.avg_tp_order = RestingOrder(f"tpavg@{ts.isoformat()}", "tp", "sell", atp, math.inf, ts, None,
                                                    False)
            lad.avg_tp = atp
        return FillEvent(ts, "buy", ref, t.entry_price, "entry" if j == 0 else "scale_in", sl=lad.stop,
                         tp=lad.avg_tp if self.ladder.average_exit else t.tp, tranche=j, weight=lad.weight)

    def _close_tranche(self, ts: pd.Timestamp, t: Tranche, ref_price: float, reason: str, at_open: bool,
                       maker: bool = False) -> FillEvent:
        lad = self.lad
        exit_fill = ref_price if maker else self.cost.sell_fill(ref_price)
        t_eff = ts if at_open else ts + ONE_MIN
        row = {"entry_time": t.entry_time, "entry_price": t.entry_price, "exit_time": ts, "exit_price": exit_fill,
               "exit_reason": reason, "gross_ret": ref_price / t.ref - 1,
               "net_ret": self.cost.net_return(t.entry_price, exit_fill),
               "hold_min": int((t_eff - t.entry_time) / ONE_MIN),
               "weight": float(lad.weight), "ladder": int(lad.id), "tranche": int(t.j)}
        self.trades.append(row)
        lad.held.remove(t)
        return FillEvent(ts, "sell", ref_price, exit_fill, reason, tranche=t.j, weight=lad.weight, trade=row)

    def _ladder_close_all(self, ts: pd.Timestamp, ref_price: float, reason: str, at_open: bool,
                          maker: bool = False) -> list[FillEvent]:
        lad = self.lad
        evs = [self._close_tranche(ts, t, ref_price, reason, at_open, maker)
               for t in sorted(lad.held, key=lambda x: x.j)]
        self._end_ladder(ts if at_open else ts + ONE_MIN, ts)
        return evs

    def _end_ladder(self, t_eff: pd.Timestamp, ts: pd.Timestamp) -> None:
        """보유 0 → 사다리 종료: 걸린 분할 주문 취소, 쿨다운 (마지막 청산 t_eff 기준)."""
        lad = self.lad
        for od in lad.pending.values():
            self._log_order(od, "cancelled", ts)
        self.lad = None
        self._set_cooldown(t_eff)

    def trades_frame(self) -> pd.DataFrame:
        if self.sized:
            df = pd.DataFrame(self.trades, columns=TRADE_COLUMNS + SIZED_COLUMNS)
            if len(df):
                df = df.sort_values(["ladder", "tranche"], kind="stable").reset_index(drop=True)
            return df
        return pd.DataFrame(self.trades, columns=TRADE_COLUMNS)


def _epoch_min(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).value // _NS_PER_MIN)


def daily_returns(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """UTC 일자별 Π(1+net_ret) − 1, 청산일 기준. 기간 밖 청산일은 마지막 날로 귀속.
    부분 사이징·사다리 거래 표(weight/ladder/tranche 열)는 net_ret 대신 자산 기여 c (`sizing.equity_returns`)."""
    days = day_index(start, end)
    prod = {d: 1.0 for d in days}
    rets = frame_equity_returns(trades) if is_sized_frame(trades) else trades["net_ret"]
    for t, r in zip(trades["exit_time"], rets):
        d = min(t.floor("D"), days[-1])
        prod[d] *= 1.0 + r
    return pd.Series([prod[d] - 1.0 for d in days], index=days, name="ret")


def run_reference_signals(m1: pd.DataFrame, entry_ok: pd.Series, exit_sig: pd.Series | None,
                          sl_pct: float | None, tp_pct: float | None, max_hold_min: int,
                          cost: CostModel, start, end, spec_id: str = "", logic_hash: str = "",
                          *, timeframe: str = "5m", atr: pd.Series | None = None,
                          sl_atr: float | None = None, tp_atr: float | None = None,
                          execution: ExecutionSpec | None = None, fill: LimitFillModel | None = None,
                          sizing: SizingSpec | None = None, ladder: LadderSpec | None = None
                          ) -> BacktestResult:
    """역사 데이터를 이벤트로 재생한다. entry_ok/exit_sig(/atr) 는 결정 주기 봉 index의 값.

    ATR 모드: sl_atr/tp_atr(배수)와 atr(봉별 ATR, `signals.atr_series`)를 함께 준다(sl_pct/tp_pct 무시).
    execution: 지정가 진입/익절 (기본 시장가). ATR 오프셋 지정가도 atr 가 필요하다. fill: 1분봉 체결 규칙.
    sizing/ladder: 부분 사이징·분할 매수 사다리 (ATR 간격 사다리도 atr 필요)."""
    if (sl_atr is None) != (tp_atr is None):
        raise ValueError("sl_atr and tp_atr must be given together")
    execution = execution or MARKET
    if (sl_atr is not None or execution.offset_mode == "atr"
            or (ladder is not None and ladder.step_mode == "atr")) and atr is None:
        raise ValueError("ATR exits / ATR limit offsets / ATR ladder steps need the atr series")
    b = tf_minutes(timeframe)
    step = pd.Timedelta(minutes=b)
    s, e, start_idx, end_idx = period_bounds(m1.index, start, end)
    eng = ReferenceEngine(sl_pct, tp_pct, max_hold_min, cost, s, e, timeframe_min=b,
                          sl_atr=sl_atr, tp_atr=tp_atr, execution=execution,
                          fill=fill or LIMIT_FILL_DEFAULT, sizing=sizing or FULL, ladder=ladder)
    entry_map = {t: bool(v) for t, v in entry_ok.items()}
    exit_map = {t: bool(v) for t, v in exit_sig.items()} if exit_sig is not None else {}
    atr_map = {t: float(v) for t, v in atr.items()} if (atr is not None and eng.needs_atr) else {}
    o, h, l, c = (m1[k].tolist() for k in ("open", "high", "low", "close"))
    vol = m1["volume"].tolist() if "volume" in m1 else [None] * len(m1)
    ticks = (infer_ticks(m1).tolist() if ((not execution.is_default or ladder is not None)
                                          and eng.fill.tick_from_data)
             else [None] * len(m1))
    filled = (m1["filled"].fillna(False).astype(bool).tolist() if "filled" in m1
              else [False] * len(m1))
    last = min(end_idx, len(m1) - 1)
    for i in range(start_idx, last + 1):
        ts = m1.index[i]
        if is_boundary(ts, b):
            prev = ts - step
            eng.on_decision(ts, entry_map.get(prev, False), exit_map.get(prev, False),
                            atr_map.get(prev))
        eng.on_bar_1m(ts, o[i], h[i], l[i], c[i], filled[i], vol[i], ticks[i])
        if eng.done:
            break
    if not eng.done and len(m1):
        eng.finish(m1.index[-1], float(m1["close"].iloc[-1]))
    dtypes = {"entry_time": m1.index.dtype, "exit_time": m1.index.dtype, "entry_price": float,
              "exit_price": float, "exit_reason": "str", "gross_ret": float, "net_ret": float,
              "hold_min": "int64"}
    if eng.sized:
        dtypes.update(weight=float, ladder="int64", tranche="int64")
    trades = eng.trades_frame().astype(dtypes)
    return BacktestResult(spec_id, logic_hash, cost, s, e, trades, daily_returns(trades, s, e))


def run_reference(spec: StrategySpec, m1: pd.DataFrame, cost: CostModel, start, end,
                  ctx: Context | None = None, registry: Registry = REGISTRY,
                  fill: LimitFillModel | None = None) -> BacktestResult:
    """fill: 지정가 체결 규칙 (spec.execution 이 지정가일 때만 쓰임; 기본 LIMIT_FILL_DEFAULT)."""
    bars, entry_ok, exit_sig = build_signals(spec, m1, ctx, registry)
    ex = spec.exit
    atr = atr_series(bars, ex.atr_bars) if spec.needs_atr else None
    return run_reference_signals(m1, entry_ok, exit_sig, ex.stop_loss_pct, ex.take_profit_pct,
                                 ex.max_hold_min, cost, start, end, spec.id, spec.logic_hash(),
                                 timeframe=spec.timeframe, atr=atr, sl_atr=ex.stop_loss_atr,
                                 tp_atr=ex.take_profit_atr, execution=spec.execution, fill=fill,
                                 sizing=spec.sizing, ladder=spec.ladder)
