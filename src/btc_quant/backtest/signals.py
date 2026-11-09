"""5분봉 신호 생성과 1분봉 결정 시점으로의 매핑 (빠른 엔진·기준 엔진 공용).

의미론은 docs/ENGINE_SPEC.md 참조.
- 5분봉 s의 신호는 s+5분(= 1분봉 t_i, 분 % 5 == 0)의 시가 직전에 확정된다.
- `to_decisions(sig5, index_1m)[i]` = 1분봉 i가 5분 경계일 때 `sig5[t_i - 5분]`, 그 외 False.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from btc_quant.strategy.blocks.base import REGISTRY, Context, Registry
from btc_quant.strategy.features import to_5m
from btc_quant.strategy.spec import StrategySpec

FIVE_MIN = pd.Timedelta(minutes=5)
_NS_PER_MIN = 60_000_000_000


def build_signals(spec: StrategySpec, m1: pd.DataFrame, ctx: Context | None = None,
                  registry: Registry = REGISTRY) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """(bars5, entry_ok, exit_sig). entry_ok = entry & 모든 필터 & bars5.complete."""
    bars = to_5m(m1)
    ctx = ctx or Context()
    entry = registry.evaluate(spec.entry, bars, ctx).to_numpy(bool).copy()
    for f in spec.filters:
        entry &= registry.evaluate(f, bars, ctx).to_numpy(bool)
    entry &= bars["complete"].to_numpy(bool)
    entry_ok = pd.Series(entry, index=bars.index, name="entry_ok")
    if spec.exit.signal_exit is not None:
        ex = registry.evaluate(spec.exit.signal_exit, bars, ctx).to_numpy(bool)
    else:
        ex = np.zeros(len(bars), dtype=bool)
    exit_sig = pd.Series(ex, index=bars.index, name="exit_sig")
    return bars, entry_ok, exit_sig


def minutes(index: pd.DatetimeIndex) -> np.ndarray:
    """UTC epoch 기준 분 (int64)."""
    return np.asarray(index.as_unit("ns").asi8 // _NS_PER_MIN, dtype=np.int64)


def boundary_mask(index: pd.DatetimeIndex) -> np.ndarray:
    return minutes(index) % 5 == 0


def to_decisions(sig5: pd.Series, index_1m: pd.DatetimeIndex) -> np.ndarray:
    """5분봉 신호를 1분봉 결정 행으로 옮긴다: 경계 1분봉 t는 5분봉 t-5분의 신호를 쓴다(없으면 False)."""
    shifted = pd.Series(np.asarray(sig5, dtype=bool), index=sig5.index + FIVE_MIN)
    shifted = shifted[~shifted.index.duplicated()]
    out = shifted.reindex(index_1m, fill_value=False).to_numpy(bool).copy()
    out &= boundary_mask(index_1m)
    return out


def filled_mask(m1: pd.DataFrame) -> np.ndarray:
    if "filled" in m1:
        return m1["filled"].fillna(False).to_numpy(bool)
    return np.zeros(len(m1), dtype=bool)


def as_utc(x, index: pd.DatetimeIndex) -> pd.Timestamp:
    t = pd.Timestamp(x)
    if index.tz is not None and t.tz is None:
        t = t.tz_localize("UTC").tz_convert(index.tz)
    elif index.tz is None and t.tz is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t


def period_bounds(index: pd.DatetimeIndex, start, end) -> tuple[pd.Timestamp, pd.Timestamp, int, int]:
    """(start, end, start_idx, end_idx). start_idx = 첫 t >= start, end_idx = 첫 t >= end."""
    s, e = as_utc(start, index), as_utc(end, index)
    if not s < e:
        raise ValueError("start must be < end")
    return s, e, int(index.searchsorted(s, "left")), int(index.searchsorted(e, "left"))


def day_index(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """[start, end) 구간의 모든 UTC 일자."""
    first = start.floor("D")
    last = (end - pd.Timedelta(1, "ns")).floor("D")
    return pd.date_range(first, last, freq="D", name="date")
