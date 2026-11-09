"""결정 주기 봉 신호 생성과 1분봉 결정 시점으로의 매핑 (빠른 엔진·기준 엔진 공용).

의미론은 docs/ENGINE_SPEC.md 참조. B = 결정 주기 봉 길이(분, 5 또는 1).
- 봉 s의 신호는 s+B분(= 1분봉 t_i, 분 % B == 0)의 시가 직전에 확정된다.
- `to_decisions(sig, index_1m, B)[i]` = 1분봉 i가 B분 경계일 때 `sig[t_i - B분]`, 그 외 False.
- ATR(청산 레벨용)은 `atr_series(bars, n)` — 봉 s까지의 정보만 사용 (인과).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

from btc_quant.strategy.blocks.base import REGISTRY, Context, Registry
from btc_quant.strategy.features import to_tf
from btc_quant.strategy.spec import StrategySpec

FIVE_MIN = pd.Timedelta(minutes=5)
_NS_PER_MIN = 60_000_000_000


def build_signals(spec: StrategySpec, m1: pd.DataFrame, ctx: Context | None = None,
                  registry: Registry = REGISTRY) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """(bars, entry_ok, exit_sig). bars = to_tf(m1, spec 결정 주기).
    entry_ok = entry & 모든 필터 & bars.complete."""
    bars = to_tf(m1, spec.tf_min)
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


# ---------------------------------------------------------------- ATR
def true_range(bars: pd.DataFrame) -> np.ndarray:
    """TR_j = max(H_j−L_j, |H_j−C_{j−1}|, |L_j−C_{j−1}|), j ≥ 1 (행 순서 기준). TR_0 = NaN."""
    h = bars["high"].to_numpy(np.float64)
    l = bars["low"].to_numpy(np.float64)
    c = bars["close"].to_numpy(np.float64)
    tr = np.full(len(c), np.nan)
    if len(c) > 1:
        pc = c[:-1]
        tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - pc), np.abs(l[1:] - pc)))
    return tr


@njit(cache=True)
def _atr_core(tr, n):
    out = np.full(tr.shape[0], np.nan)
    for j in range(n, tr.shape[0]):
        acc = 0.0
        for k in range(j - n + 1, j + 1):   # 왼쪽부터 순차 합산
            acc += tr[k]
        out[j] = acc / n
    return out


def atr_values(tr: np.ndarray, n: int) -> np.ndarray:
    """ATR_n(j) = (TR_{j−n+1} + … + TR_j) / n — 왼쪽부터 순차 합산한 단순 평균.
    창마다 독립 계산이므로 어떤 후행 창에서 다시 계산해도 비트 단위로 같다. j < n 이면 NaN."""
    n = int(n)
    if n < 1:
        raise ValueError("ATR window must be >= 1")
    return _atr_core(np.asarray(tr, dtype=np.float64), n)


def atr_series(bars: pd.DataFrame, n: int) -> pd.Series:
    """결정 주기 봉 s의 ATR (절대 가격 단위). index = 봉 시작 시각. 봉 s 종가 확정 시점에 알려짐."""
    return pd.Series(atr_values(true_range(bars), n), index=bars.index, name=f"atr{int(n)}")


# ---------------------------------------------------------------- 결정 시점 매핑
def minutes(index: pd.DatetimeIndex) -> np.ndarray:
    """UTC epoch 기준 분 (int64)."""
    return np.asarray(index.as_unit("ns").asi8 // _NS_PER_MIN, dtype=np.int64)


def boundary_mask(index: pd.DatetimeIndex, tf_min: int = 5) -> np.ndarray:
    return minutes(index) % int(tf_min) == 0


def _shift_to_decisions(sig: pd.Series, index_1m: pd.DatetimeIndex, tf_min: int, fill):
    """out[i] = sig[t_i − B] (없으면 fill). 중복 시각은 첫 값."""
    vals = np.asarray(sig)
    if not (index_1m.is_monotonic_increasing and index_1m.is_unique):
        shifted = pd.Series(vals, index=sig.index + pd.Timedelta(minutes=int(tf_min)))
        shifted = shifted[~shifted.index.duplicated()]
        return shifted.reindex(index_1m, fill_value=fill).to_numpy().copy()
    # 같은 결과를 reindex 없이 (3M 봉에서 수 배 빠름): UTC ns 정수로 위치 매핑
    tgt = index_1m.as_unit("ns").asi8
    src = sig.index.as_unit("ns").asi8 + int(tf_min) * _NS_PER_MIN
    out = np.full(len(tgt), fill, dtype=np.result_type(vals.dtype, np.asarray(fill).dtype))
    if len(tgt) and tgt[-1] - tgt[0] == (len(tgt) - 1) * _NS_PER_MIN:   # 연속 1분 격자(regularize 결과)
        off = src - tgt[0]
        pos = off // _NS_PER_MIN
        ok = (off % _NS_PER_MIN == 0) & (pos >= 0) & (pos < len(tgt))
    else:
        pos = np.searchsorted(tgt, src)
        ok = pos < len(tgt)
        ok[ok] = tgt[pos[ok]] == src[ok]
    pos, v = pos[ok], vals[ok]
    if len(pos) and not (np.diff(pos) > 0).all():   # 중복 시각: 첫 값 유지
        pos, first = np.unique(pos, return_index=True)
        v = v[first]
    out[pos] = v
    return out


def to_decisions(sig5: pd.Series, index_1m: pd.DatetimeIndex, tf_min: int = 5) -> np.ndarray:
    """봉 신호를 1분봉 결정 행으로 옮긴다: 경계 1분봉 t는 봉 t−B분의 신호를 쓴다(없으면 False)."""
    sig = pd.Series(np.asarray(sig5, dtype=bool), index=sig5.index)
    out = _shift_to_decisions(sig, index_1m, tf_min, False).astype(bool)
    out &= boundary_mask(index_1m, tf_min)
    return out


def atr_frac_decisions(atr: pd.Series, m1_open, index_1m: pd.DatetimeIndex, tf_min: int = 5
                       ) -> np.ndarray:
    """1분봉 결정 행 i의 ATR 비율 = ATR(봉 t_i−B) / o_i (진입 기준가 대비).
    경계가 아니거나 ATR 이 정의되지 않았거나 비율이 유한한 양수가 아니면 NaN (ATR 모드 진입 불가)."""
    a = _shift_to_decisions(pd.Series(np.asarray(atr, dtype=np.float64), index=atr.index),
                            index_1m, tf_min, np.nan).astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = a / np.asarray(m1_open, dtype=np.float64)
    bad = ~(np.isfinite(frac) & (frac > 0)) | ~boundary_mask(index_1m, tf_min)
    frac[bad] = np.nan
    return frac


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
