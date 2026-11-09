"""빠른 엔진 — numba 루프. 의미론은 docs/ENGINE_SPEC.md (기준 엔진과 거래 목록이 정확히 일치해야 함).

계층:
- `simulate(...)`            : 순수 배열 API (1분봉 OHLC + 결정 배열 → 거래 배열).
- `prepare(...)` / `Prepared`: 신호를 한 번 계산해 두고 청산 파라미터만 바꿔 반복 실행(캠페인 격자용).
- `simulate_prepared`, `simulate_many`, `daily_returns_matrix`: 격자 스윕용 저수준 함수.
- `run_fast(spec, m1, cost, start, end)` → BacktestResult.

포지션 비보유 구간은 진입 후보 인덱스로 건너뛰므로 실행 시간은 대체로 보유 기간 길이에 비례한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, NamedTuple

import numpy as np
import pandas as pd
from numba import njit

from btc_quant.backtest.signals import (build_signals, day_index, filled_mask, minutes,
                                        period_bounds, to_decisions)
from btc_quant.backtest.types import TRADE_COLUMNS, BacktestResult, CostModel
from btc_quant.strategy.blocks.base import REGISTRY, Context, Registry
from btc_quant.strategy.spec import StrategySpec

R_SL, R_TP, R_SIGNAL, R_TIME, R_END = 0, 1, 2, 3, 4
REASONS = np.array(["sl", "tp", "signal", "time", "end"], dtype=object)
_NEG = -(2 ** 62)


@njit(cache=True)
def _core(o, h, l, c, tmin, cand, exit_dec, sl_pct, tp_pct, max_hold_min, tick,
          start_idx, end_idx):
    n = o.shape[0]
    nc = cand.shape[0]
    e_idx = np.empty(nc, np.int64)
    x_idx = np.empty(nc, np.int64)
    e_ref = np.empty(nc, np.float64)
    x_ref = np.empty(nc, np.float64)
    reason = np.empty(nc, np.int8)
    hold = np.empty(nc, np.int64)
    nt = 0
    next_allowed = _NEG
    ci = 0
    while ci < nc:
        i = cand[ci]
        ci += 1
        if i < start_idx:
            continue
        if i >= end_idx:
            break
        if tmin[i] < next_allowed:
            continue
        ref = o[i]
        slv = ref * (1.0 - sl_pct / 100.0)
        tpv = ref * (1.0 + tp_pct / 100.0)
        tp_trig = tpv + tick
        texp = tmin[i] + max_hold_min
        k = i
        r = -1
        xk = i
        xr = 0.0
        teff = 0
        while True:
            if k > i:
                if k >= n:  # 데이터 끝(end 이후 봉 없음) → 마지막 종가
                    r = R_END
                    xk = n - 1
                    xr = c[n - 1]
                    teff = tmin[n - 1] + 1
                    break
                # 시가 청산: 만기 > 신호 > 기간 종료
                if tmin[k] >= texp:
                    r = R_TIME
                elif exit_dec[k]:
                    r = R_SIGNAL
                elif k >= end_idx:
                    r = R_END
                if r >= 0:
                    xk = k
                    xr = o[k]
                    teff = tmin[k]
                    break
                if o[k] <= slv:  # SL 갭
                    r = R_SL
                    xk = k
                    xr = o[k]
                    teff = tmin[k] + 1
                    break
            if l[k] <= slv:
                r = R_SL
                xk = k
                xr = slv
                teff = tmin[k] + 1
                break
            if h[k] >= tp_trig:
                r = R_TP
                xk = k
                xr = tpv
                teff = tmin[k] + 1
                break
            k += 1
        e_idx[nt] = i
        x_idx[nt] = xk
        e_ref[nt] = ref
        x_ref[nt] = xr
        reason[nt] = r
        hold[nt] = teff - tmin[i]
        nt += 1
        # 쿨다운: teff 이후 첫 5분 경계는 건너뛰고 그 다음 경계부터
        next_allowed = ((teff + 4) // 5) * 5 + 5
        if r == R_END:
            break
    return e_idx[:nt], x_idx[:nt], e_ref[:nt], x_ref[:nt], reason[:nt], hold[:nt]


class TradeArrays(NamedTuple):
    entry_idx: np.ndarray   # 진입 1분봉 인덱스
    exit_idx: np.ndarray    # 청산 1분봉 인덱스
    reason: np.ndarray      # int8 코드 (REASONS 참조)
    entry_ref: np.ndarray
    exit_ref: np.ndarray
    entry_price: np.ndarray
    exit_price: np.ndarray
    gross_ret: np.ndarray
    net_ret: np.ndarray
    hold_min: np.ndarray


def _finish(raw, side_cost: float, fee_rate: float) -> TradeArrays:
    e_idx, x_idx, e_ref, x_ref, reason, hold = raw
    # CostModel.buy_fill / sell_fill / net_return 와 같은 연산 순서 (기준 엔진과 비트 단위 일치)
    ef = e_ref * (1 + side_cost)
    xf = x_ref * (1 - side_cost)
    net = (xf * (1 - fee_rate)) / (ef * (1 + fee_rate)) - 1
    gross = x_ref / e_ref - 1
    return TradeArrays(e_idx, x_idx, reason, e_ref, x_ref, ef, xf, gross, net, hold)


def simulate(o, h, l, c, ts_min, entry_decision, exit_decision, sl_pct: float, tp_pct: float,
             max_hold_min: int, side_cost: float, fee_rate: float, tick: float,
             start_idx: int, end_idx: int) -> TradeArrays:
    """배열 API. entry/exit_decision 은 1분봉 결정 배열(경계 행에만 True 가능).

    entry_decision 은 이미 '진입 가능한 봉'으로 정리되어 있어야 한다(filled 봉 제외 등; `prepare` 참조).
    ts_min: UTC epoch 분(int64). 진입 허용 인덱스 [start_idx, end_idx), end_idx 봉 시가에 end 청산.
    """
    cand = np.flatnonzero(np.asarray(entry_decision, dtype=bool)).astype(np.int64)
    raw = _core(np.asarray(o, np.float64), np.asarray(h, np.float64), np.asarray(l, np.float64),
                np.asarray(c, np.float64), np.asarray(ts_min, np.int64), cand,
                np.asarray(exit_decision, dtype=np.bool_), float(sl_pct), float(tp_pct),
                int(max_hold_min), float(tick), int(start_idx), int(end_idx))
    return _finish(raw, side_cost, fee_rate)


@dataclass(frozen=True)
class Prepared:
    """한 번 계산한 신호 + 1분봉 배열. 청산 파라미터 격자 스윕에서 재사용."""
    index: pd.DatetimeIndex
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    tmin: np.ndarray
    cand: np.ndarray        # 진입 결정 1분봉 인덱스 (경계 & entry_ok[t-5m] & ~filled[t])
    exit_dec: np.ndarray    # bool[n]: 경계 & exit_sig[t-5m]

    def bounds(self, start, end) -> tuple[pd.Timestamp, pd.Timestamp, int, int]:
        return period_bounds(self.index, start, end)


def prepare(m1: pd.DataFrame, entry_ok: pd.Series, exit_sig: pd.Series | None = None) -> Prepared:
    """5분봉 신호(entry_ok, exit_sig)를 1분봉 결정 배열로 변환."""
    idx = m1.index
    ent = to_decisions(entry_ok, idx) & ~filled_mask(m1)  # filled 봉 시가(합성 가격)에는 진입하지 않음
    ex = to_decisions(exit_sig, idx) if exit_sig is not None else np.zeros(len(idx), dtype=bool)
    return Prepared(idx, m1["open"].to_numpy(np.float64), m1["high"].to_numpy(np.float64),
                    m1["low"].to_numpy(np.float64), m1["close"].to_numpy(np.float64),
                    minutes(idx), np.flatnonzero(ent).astype(np.int64), ex)


def prepare_spec(spec: StrategySpec, m1: pd.DataFrame, ctx: Context | None = None,
                 registry: Registry = REGISTRY) -> Prepared:
    _, entry_ok, exit_sig = build_signals(spec, m1, ctx, registry)
    return prepare(m1, entry_ok, exit_sig)


def simulate_prepared(p: Prepared, sl_pct: float, tp_pct: float, max_hold_min: int,
                      cost: CostModel, start_idx: int, end_idx: int) -> TradeArrays:
    raw = _core(p.o, p.h, p.l, p.c, p.tmin, p.cand, p.exit_dec, float(sl_pct), float(tp_pct),
                int(max_hold_min), float(cost.tick), int(start_idx), int(end_idx))
    return _finish(raw, cost.side_cost, cost.fee_rate)


def simulate_many(p: Prepared, combos: Iterable[tuple[float, float, int]], cost: CostModel,
                  start, end) -> list[TradeArrays]:
    """같은 진입 신호에 대해 (sl_pct, tp_pct, max_hold_min) 조합들을 반복 실행."""
    _, _, si, ei = p.bounds(start, end)
    return [simulate_prepared(p, sl, tp, hold, cost, si, ei) for sl, tp, hold in combos]


def _day_slots(p: Prepared, tr: TradeArrays, days: pd.DatetimeIndex) -> np.ndarray:
    d0 = days[0].as_unit("ns").value // 86_400_000_000_000
    exit_day = p.tmin[tr.exit_idx] // 1440 - d0
    return np.clip(exit_day, 0, len(days) - 1)  # 기간 밖 청산일(end 청산)은 마지막 날로 귀속


def daily_returns_matrix(p: Prepared, results: list[TradeArrays], start, end
                         ) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """격자 결과 → (일자, [n_combo, n_days] 일간 수익률). 청산일 기준 복리."""
    s, e, _, _ = p.bounds(start, end)
    days = day_index(s, e)
    out = np.ones((len(results), len(days)))
    for j, tr in enumerate(results):
        np.multiply.at(out[j], _day_slots(p, tr, days), 1.0 + tr.net_ret)
    return days, out - 1.0


def to_result(p: Prepared, tr: TradeArrays, cost: CostModel, start, end,
              spec_id: str = "", logic_hash: str = "") -> BacktestResult:
    s, e, _, _ = p.bounds(start, end)
    trades = pd.DataFrame({
        "entry_time": p.index[tr.entry_idx],
        "entry_price": tr.entry_price,
        "exit_time": p.index[tr.exit_idx],
        "exit_price": tr.exit_price,
        "exit_reason": REASONS[tr.reason.astype(np.int64)] if len(tr.reason) else np.array([], dtype=object),
        "gross_ret": tr.gross_ret,
        "net_ret": tr.net_ret,
        "hold_min": tr.hold_min.astype(np.int64),
    }, columns=TRADE_COLUMNS).astype({"exit_reason": "str"})
    days, mat = daily_returns_matrix(p, [tr], s, e)
    daily = pd.Series(mat[0], index=days, name="ret")
    return BacktestResult(spec_id, logic_hash, cost, s, e, trades, daily)


def run_fast_signals(m1: pd.DataFrame, entry_ok: pd.Series, exit_sig: pd.Series | None,
                     sl_pct: float, tp_pct: float, max_hold_min: int, cost: CostModel,
                     start, end, spec_id: str = "", logic_hash: str = "") -> BacktestResult:
    p = prepare(m1, entry_ok, exit_sig)
    _, _, si, ei = p.bounds(start, end)
    tr = simulate_prepared(p, sl_pct, tp_pct, max_hold_min, cost, si, ei)
    return to_result(p, tr, cost, start, end, spec_id, logic_hash)


def run_fast(spec: StrategySpec, m1: pd.DataFrame, cost: CostModel, start, end,
             ctx: Context | None = None, registry: Registry = REGISTRY) -> BacktestResult:
    _, entry_ok, exit_sig = build_signals(spec, m1, ctx, registry)
    ex = spec.exit
    return run_fast_signals(m1, entry_ok, exit_sig, ex.stop_loss_pct, ex.take_profit_pct,
                            ex.max_hold_min, cost, start, end, spec.id, spec.logic_hash())
