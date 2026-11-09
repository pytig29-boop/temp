"""기준 엔진 — 읽기 쉬운 이벤트 기반 구현 (numba 없음). 페이퍼 엔진의 핵심이 된다.

의미론은 docs/ENGINE_SPEC.md. 빠른 엔진(fast.py)과 거래 목록이 정확히 일치해야 한다.

구동 방법 (실시간/재생 공통). B = 결정 주기 봉 길이(분, 기본 5; 1/15/60/240 도 가능):
    eng = ReferenceEngine(sl_pct, tp_pct, max_hold_min, cost, start, end)        # 5분·고정 % (기존)
    eng = ReferenceEngine.from_exit(spec.exit, cost, start, end, spec.timeframe)   # 일반
    매 B분 경계 t (봉 t-B 확정 직후):  eng.on_decision(t, entry_ok, exit_sig[, atr])
    매 1분봉 t (시가부터 알려짐):       events = eng.on_bar_1m(t, o, h, l, c, filled)
    데이터가 끝났을 때:                 eng.finish(last_ts, last_close)

ATR 청산 모드(sl_atr/tp_atr 지정)에서는 on_decision 에 봉 t-B 의 ATR(절대값, `signals.atr_series`)을 넘긴다.

엔진은 I/O가 없고 모든 상태는 속성으로 드러나 있다(position, cooldown_until, trades).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from btc_quant.backtest.signals import (
    FIVE_MIN,
    atr_series,
    build_signals,
    day_index,
    period_bounds,
)
from btc_quant.backtest.types import TRADE_COLUMNS, BacktestResult, CostModel
from btc_quant.strategy.blocks.base import REGISTRY, Context, Registry
from btc_quant.strategy.spec import ExitSpec, StrategySpec, tf_minutes

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


@dataclass
class Position:
    entry_time: pd.Timestamp
    ref: float             # 진입 기준가 (진입 봉 시가)
    entry_price: float     # 비용 반영 체결가
    sl: float
    tp: float
    expiry: pd.Timestamp
    atr_frac: float | None = None   # ATR 모드: 진입 시 ATR / ref


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

    @classmethod
    def from_exit(cls, exit: ExitSpec, cost: CostModel, start: pd.Timestamp | None = None,
                  end: pd.Timestamp | None = None, timeframe: str = "5m") -> ReferenceEngine:
        return cls(exit.stop_loss_pct, exit.take_profit_pct, exit.max_hold_min, cost, start, end,
                   timeframe_min=tf_minutes(timeframe), sl_atr=exit.stop_loss_atr,
                   tp_atr=exit.take_profit_atr)

    @property
    def atr_mode(self) -> bool:
        return self.sl_atr is not None

    # ---------- 이벤트 ----------
    def on_decision(self, ts: pd.Timestamp, entry_ok: bool, exit_sig: bool,
                    atr: float | None = None) -> None:
        """B분 경계 ts의 1분봉 시가 직전에 호출. 다음 on_bar_1m(ts, ...)에서 사용된다.
        atr = 봉 ts-B 의 ATR (ATR 청산 모드에서 없거나 NaN 이면 진입 불가)."""
        self.decision = Decision(ts, bool(entry_ok), bool(exit_sig),
                                 None if atr is None else float(atr))

    def on_bar_1m(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float,
                  filled: bool = False) -> list[FillEvent]:
        events: list[FillEvent] = []
        if self.done:
            return events
        dec = self.decision if (self.decision is not None and self.decision.ts == ts) else None
        self.decision = None
        past_end = self.end is not None and ts >= self.end

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

        # 2) 무포지션: 진입 (이 봉 시가)
        frac = self._atr_frac(dec, o)
        if self.position is None and self._can_enter(ts, dec, filled, past_end, frac):
            events.append(self._open(ts, o, frac))
            entered_now = True
        else:
            entered_now = False

        # 3) 보유 중: 봉 내 청산 — SL 갭 > SL 터치 > TP (같은 봉 SL·TP 동시 → SL)
        pos = self.position
        if pos is not None:
            if not entered_now and o <= pos.sl:
                events.append(self._close(ts, o, "sl", at_open=False))
            elif l <= pos.sl:
                events.append(self._close(ts, pos.sl, "sl", at_open=False))
            elif h >= pos.tp + self.cost.tick:
                events.append(self._close(ts, pos.tp, "tp", at_open=False))
        if past_end:
            self.done = True
        return events

    def finish(self, last_ts: pd.Timestamp, last_close: float) -> list[FillEvent]:
        """end 이후 봉이 없을 때: 열린 포지션을 마지막 1분봉 종가로 청산."""
        if self.position is None or self.done:
            self.done = True
            return []
        ev = self._close(last_ts, last_close, "end", at_open=False)
        self.done = True
        return [ev]

    # ---------- 내부 ----------
    def _atr_frac(self, dec: Decision | None, o: float) -> float | None:
        """ATR 모드: ATR(봉 ts-B) / o. 유한한 양수가 아니면 None (진입 불가)."""
        if not self.atr_mode or dec is None or dec.atr is None:
            return None
        frac = dec.atr / o if o != 0 else math.nan
        return frac if (math.isfinite(frac) and frac > 0) else None

    def _can_enter(self, ts, dec: Decision | None, filled: bool, past_end: bool,
                   frac: float | None = None) -> bool:
        if dec is None or not dec.entry_ok or filled or past_end:
            return False
        if self.atr_mode and frac is None:
            return False
        if self.start is not None and ts < self.start:
            return False
        return self.cooldown_until is None or ts >= self.cooldown_until

    def _open(self, ts: pd.Timestamp, o: float, frac: float | None = None) -> FillEvent:
        ref = o
        if self.atr_mode:
            d_sl, d_tp = self.sl_atr * frac, self.tp_atr * frac
        else:
            d_sl, d_tp = self.sl_pct / 100.0, self.tp_pct / 100.0
        self.position = Position(
            entry_time=ts, ref=ref, entry_price=self.cost.buy_fill(ref),
            sl=ref * (1.0 - d_sl), tp=ref * (1.0 + d_tp),
            expiry=ts + pd.Timedelta(minutes=int(self.max_hold_min)),
            atr_frac=frac if self.atr_mode else None,
        )
        pos = self.position
        return FillEvent(ts, "buy", ref, pos.entry_price, "entry", sl=pos.sl, tp=pos.tp)

    def _close(self, ts: pd.Timestamp, ref_price: float, reason: str, at_open: bool) -> FillEvent:
        pos = self.position
        exit_fill = self.cost.sell_fill(ref_price)
        # 시가 청산(만기·신호·end)은 봉 시각, 봉 내 청산(SL·TP, SL 갭 포함)은 봉 시각 + 1분
        t_eff = ts if at_open else ts + ONE_MIN
        self.trades.append({
            "entry_time": pos.entry_time,
            "entry_price": pos.entry_price,
            "exit_time": ts,
            "exit_price": exit_fill,
            "exit_reason": reason,
            "gross_ret": ref_price / pos.ref - 1,
            "net_ret": self.cost.net_return(pos.entry_price, exit_fill),
            "hold_min": int((t_eff - pos.entry_time) / ONE_MIN),
        })
        self.position = None
        # 쿨다운: t_eff 이후 첫 B분 경계 결정은 건너뛴다 (= 완전한 봉 1개 관찰 후 진입)
        b = int(self.timeframe_min)
        if b == 5:
            self.cooldown_until = t_eff.ceil("5min") + FIVE_MIN
        elif b == 1:
            self.cooldown_until = t_eff.ceil("1min") + ONE_MIN
        else:   # 15m/1h/4h: epoch 정렬 올림 (Timestamp.ceil 은 tz-aware 에서 벽시계 기준이라 쓰지 않는다)
            self.cooldown_until = _ceil_boundary(t_eff, b) + pd.Timedelta(minutes=b)
        return FillEvent(ts, "sell", ref_price, exit_fill, reason)

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.trades, columns=TRADE_COLUMNS)


def daily_returns(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """UTC 일자별 Π(1+net_ret) − 1, 청산일 기준. 기간 밖 청산일은 마지막 날로 귀속."""
    days = day_index(start, end)
    prod = {d: 1.0 for d in days}
    for t, r in zip(trades["exit_time"], trades["net_ret"]):
        d = min(t.floor("D"), days[-1])
        prod[d] *= 1.0 + r
    return pd.Series([prod[d] - 1.0 for d in days], index=days, name="ret")


def run_reference_signals(m1: pd.DataFrame, entry_ok: pd.Series, exit_sig: pd.Series | None,
                          sl_pct: float | None, tp_pct: float | None, max_hold_min: int,
                          cost: CostModel, start, end, spec_id: str = "", logic_hash: str = "",
                          *, timeframe: str = "5m", atr: pd.Series | None = None,
                          sl_atr: float | None = None, tp_atr: float | None = None
                          ) -> BacktestResult:
    """역사 데이터를 이벤트로 재생한다. entry_ok/exit_sig(/atr) 는 결정 주기 봉 index의 값.

    ATR 모드: sl_atr/tp_atr(배수)와 atr(봉별 ATR, `signals.atr_series`)를 함께 준다(sl_pct/tp_pct 무시)."""
    if (sl_atr is None) != (tp_atr is None):
        raise ValueError("sl_atr and tp_atr must be given together")
    if sl_atr is not None and atr is None:
        raise ValueError("ATR exits need the atr series")
    b = tf_minutes(timeframe)
    step = pd.Timedelta(minutes=b)
    s, e, start_idx, end_idx = period_bounds(m1.index, start, end)
    eng = ReferenceEngine(sl_pct, tp_pct, max_hold_min, cost, s, e, timeframe_min=b,
                          sl_atr=sl_atr, tp_atr=tp_atr)
    entry_map = {t: bool(v) for t, v in entry_ok.items()}
    exit_map = {t: bool(v) for t, v in exit_sig.items()} if exit_sig is not None else {}
    atr_map = {t: float(v) for t, v in atr.items()} if (atr is not None and sl_atr is not None) else {}
    o, h, l, c = (m1[k].tolist() for k in ("open", "high", "low", "close"))
    filled = (m1["filled"].fillna(False).astype(bool).tolist() if "filled" in m1
              else [False] * len(m1))
    last = min(end_idx, len(m1) - 1)
    for i in range(start_idx, last + 1):
        ts = m1.index[i]
        if is_boundary(ts, b):
            prev = ts - step
            eng.on_decision(ts, entry_map.get(prev, False), exit_map.get(prev, False),
                            atr_map.get(prev))
        eng.on_bar_1m(ts, o[i], h[i], l[i], c[i], filled[i])
        if eng.done:
            break
    if not eng.done and len(m1):
        eng.finish(m1.index[-1], float(m1["close"].iloc[-1]))
    trades = eng.trades_frame().astype(
        {"entry_time": m1.index.dtype, "exit_time": m1.index.dtype, "entry_price": float,
         "exit_price": float, "exit_reason": "str", "gross_ret": float, "net_ret": float,
         "hold_min": "int64"})
    return BacktestResult(spec_id, logic_hash, cost, s, e, trades, daily_returns(trades, s, e))


def run_reference(spec: StrategySpec, m1: pd.DataFrame, cost: CostModel, start, end,
                  ctx: Context | None = None, registry: Registry = REGISTRY) -> BacktestResult:
    bars, entry_ok, exit_sig = build_signals(spec, m1, ctx, registry)
    ex = spec.exit
    atr = atr_series(bars, ex.atr_bars) if ex.is_atr else None
    return run_reference_signals(m1, entry_ok, exit_sig, ex.stop_loss_pct, ex.take_profit_pct,
                                 ex.max_hold_min, cost, start, end, spec.id, spec.logic_hash(),
                                 timeframe=spec.timeframe, atr=atr, sl_atr=ex.stop_loss_atr,
                                 tp_atr=ex.take_profit_atr)
