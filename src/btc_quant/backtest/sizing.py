"""부분 사이징·분할 매수 사다리의 자산 회계 (빠른 엔진·기준 엔진 공용; docs/ENGINE_SPEC.md "부분 사이징·분할 매수").

거래 행 = 분할(tranche) 하나. 열 `weight` = 그 분할의 명목 / 사다리 시작(첫 분할 체결) 시점 슬리브 자산,
`ladder` = 사다리 번호(0, 1, … 시간순), `tranche` = 단 번호 j. 단일 진입(부분 사이징만)은 거래마다 사다리 하나
(weight = entry_fraction, tranche 0).

일간 수익 (실현 기준 — 기존 '청산일 귀속 복리' 규약의 일반화, 기본값에서 비트 단위 동일):
    E = 1
    사다리 g 를 시간순으로:  E_g = E (사다리 시작 시 자산; 사다리는 겹치지 않는다 — 완전 청산 후에만 새 사다리)
        g 의 분할을 (청산 분, 단 번호) 순으로:  c = w · r · (E_g / E);  E ← E · (1 + c)
    일간 수익 = Π_{그날 청산된 분할} (1 + c) − 1   (기간 밖 청산은 마지막 날)
  c = 그 분할의 실현 손익(E_g · w · r) / 실현 직전 자산. 현금(1 − 보유 비중)은 수익 0.
  weight = 1, 사다리 = 거래마다 하나면 c = r (비트 단위) → 기존 일간 수익과 같다.
평가 기준(MTM) 일간 수익 (꼬리 위험 보고, 그리고 PLAN D18 이후 등록된 캠페인에서는 관문 일간 수익 — 빠른 엔진 `fast.mtm_daily_matrix`
가 같은 식을 배열로 계산한다): 보유 분할을 UTC 일 종가에 청산했다고 보고(`CostModel.sell_fill` + 수수료)
자산을 매일 평가한다(`mtm_daily_returns`). 꼬리 위험 지표(최악 일·주, CVaR)는 이것으로도 보고한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

SIZED_COLUMNS = ["weight", "ladder", "tranche"]


@njit(cache=True)
def _equity_core(order, w, r, lad):
    n = order.shape[0]
    out = np.empty(w.shape[0], np.float64)
    e = 1.0
    eg = 1.0
    cur = -(2 ** 62)
    for q in range(n):
        i = order[q]
        if lad[i] != cur:
            cur = lad[i]
            eg = e
        c = w[i] * r[i] * (eg / e)
        e = e * (1.0 + c)
        out[i] = c
    return out


def equity_returns(weight, net_ret, ladder, tranche, exit_min) -> np.ndarray:
    """행별 자산 기여 c (입력과 같은 순서). 처리 순서 = (사다리, 청산 분, 단 번호)."""
    w = np.asarray(weight, np.float64)
    if w.size == 0:
        return np.zeros(0)
    lad = np.asarray(ladder, np.int64)
    order = np.lexsort((np.asarray(tranche, np.int64), np.asarray(exit_min, np.int64), lad)).astype(np.int64)
    return _equity_core(order, w, np.asarray(net_ret, np.float64), lad)


def position_returns(weight, net_ret, ladder) -> np.ndarray:
    """사다리(포지션)별 손익 / 사다리 시작 자산 = Σ w·r (사다리 번호 순). 몬테카를로 MDD 입력."""
    w = np.asarray(weight, np.float64)
    if w.size == 0:
        return np.zeros(0)
    lad = np.asarray(ladder, np.int64)
    u, inv = np.unique(lad, return_inverse=True)
    out = np.zeros(u.size)
    np.add.at(out, inv, w * np.asarray(net_ret, np.float64))
    return out


def frame_equity_returns(trades: pd.DataFrame) -> np.ndarray:
    """거래 표(SIZED_COLUMNS 있으면) → 행별 자산 기여 c. 없으면 net_ret 그대로."""
    r = trades["net_ret"].to_numpy(np.float64)
    if not is_sized_frame(trades) or not len(trades):
        return r
    xm = pd.DatetimeIndex(trades["exit_time"]).as_unit("ns").asi8 // 60_000_000_000
    return equity_returns(trades["weight"], r, trades["ladder"], trades["tranche"], xm)


def is_sized_frame(trades: pd.DataFrame | None) -> bool:
    return trades is not None and all(c in trades.columns for c in SIZED_COLUMNS)


def trade_contributions(trades: pd.DataFrame | None) -> np.ndarray:
    """거래 행별 '슬리브 자산 대비' 손익 근사 = weight × net_ret (기본 = net_ret). 상위 거래 제거 등 순위용."""
    if trades is None or not len(trades):
        return np.zeros(0)
    r = trades["net_ret"].to_numpy(np.float64)
    return r * trades["weight"].to_numpy(np.float64) if is_sized_frame(trades) else r


def frame_position_returns(trades: pd.DataFrame | None) -> np.ndarray:
    """포지션(사다리)별 슬리브 손익 Σ w·r. 기본 거래 표 = net_ret 그대로."""
    if trades is None or not len(trades):
        return np.zeros(0)
    if not is_sized_frame(trades):
        return trades["net_ret"].to_numpy(np.float64)
    return position_returns(trades["weight"], trades["net_ret"], trades["ladder"])


def n_positions(trades: pd.DataFrame | None) -> int:
    """독립 포지션(사다리) 수. 기본 거래 표 = 거래 수."""
    if trades is None or not len(trades):
        return 0
    if not is_sized_frame(trades):
        return int(len(trades))
    if "symbol" in trades.columns:          # 다자산 풀링 거래 표: 사다리 번호는 슬리브마다 0 부터
        return int(trades[["symbol", "ladder"]].drop_duplicates().shape[0])
    return int(trades["ladder"].nunique())


# ============================================================== 노출·평가 기준 (정보 전용)
def _norm_times(t, ref_tz):
    t = pd.DatetimeIndex(pd.to_datetime(t))
    if ref_tz is not None and t.tz is None:
        t = t.tz_localize(ref_tz)
    elif ref_tz is None and t.tz is not None:
        t = t.tz_convert(None)
    return t


def inventory_profile(trades: pd.DataFrame | None, n_sleeves: int = 1) -> dict:
    """보유 비중(슬리브 자산 대비, 사다리 시작 자산 기준 명목 합) 의 시간 경로 요약.
    n_sleeves > 1 (다자산 풀링 거래 표): 포트폴리오 비중 = 슬리브 비중 합 / N.
    max_inventory = 최대 보유 비중, max_inventory_minutes = 최대 비중에 연속으로 머문 최장 시간(분),
    max_inventory_episodes = 최대 비중 도달 횟수. 보유 구간 = [entry_time, entry_time + hold_min)."""
    out = {"max_inventory": 0.0, "max_inventory_minutes": 0, "max_inventory_episodes": 0}
    if trades is None or not len(trades):
        return out
    w = (trades["weight"].to_numpy(np.float64) if is_sized_frame(trades)
         else np.ones(len(trades))) / max(int(n_sleeves), 1)
    et = pd.DatetimeIndex(pd.to_datetime(trades["entry_time"])).as_unit("ns").asi8 // 60_000_000_000
    xt = et + trades["hold_min"].to_numpy(np.int64)
    times = np.concatenate([et, xt])
    deltas = np.concatenate([w, -w])
    order = np.lexsort((deltas, times))           # 같은 분: 청산(−)을 먼저 → 연속 보유만 겹침으로 셈
    times, deltas = times[order], deltas[order]
    uniq, idx = np.unique(times, return_index=True)
    sums = np.add.reduceat(deltas, idx)
    level = np.round(np.cumsum(sums), 12)
    mx = float(level.max()) if level.size else 0.0
    if mx <= 0:
        return out
    at = np.isclose(level, mx, rtol=0, atol=1e-9)
    longest, episodes = 0, 0
    i = 0
    while i < at.size:
        if at[i]:
            j = i
            while j + 1 < at.size and at[j + 1]:
                j += 1
            end_t = uniq[j + 1] if j + 1 < uniq.size else uniq[j]
            longest = max(longest, int(end_t - uniq[i]))
            episodes += 1
            i = j + 1
        else:
            i += 1
    out.update(max_inventory=round(mx, 9), max_inventory_minutes=int(longest), max_inventory_episodes=int(episodes))
    return out


def mtm_daily_returns(trades: pd.DataFrame, close: pd.Series, days: pd.DatetimeIndex, cost=None) -> pd.Series:
    """평가 기준(MTM) 일간 수익 — 보유 분할을 매 UTC 일 종가(그날 마지막 1분봉 close)에 청산했다고 본 자산의 일간 변화.
    청산 평가가 = cost.sell_fill(close) (cost 가 있으면, 수수료 포함 `net_return`), 실현 손익은 실제 청산일에.
    사다리 시작 자산(E_g)은 실현 회계(`equity_returns`)의 경로를 쓴다. 기본 거래 표(전액·단일)에도 적용된다."""
    days = pd.DatetimeIndex(days)
    n = len(days)
    if n == 0:
        return pd.Series(dtype=float)
    if trades is None or not len(trades):
        return pd.Series(0.0, index=days, name="ret_mtm")
    sized = is_sized_frame(trades)
    m = len(trades)
    w = trades["weight"].to_numpy(np.float64) if sized else np.ones(m)
    lad = trades["ladder"].to_numpy(np.int64) if sized else np.arange(m)
    r = trades["net_ret"].to_numpy(np.float64)
    tz = days.tz
    et = _norm_times(trades["entry_time"], tz)
    xt = _norm_times(trades["exit_time"], tz)
    # 사다리 시작 자산 E_g (실현 회계 경로): 자산 기여 c 를 (사다리, 청산 분, 단) 순으로 누적
    xm = xt.as_unit("ns").asi8 // 60_000_000_000
    tr_ = trades["tranche"].to_numpy(np.int64) if sized else np.zeros(m, np.int64)
    c = equity_returns(w, r, lad, tr_, xm)
    order = np.lexsort((tr_, xm, lad))
    eq_before = np.empty(m)
    e, cur, eg_map = 1.0, None, {}
    for i in order:
        if lad[i] != cur:
            cur = lad[i]
            eg_map[cur] = e
        eq_before[i] = e
        e *= 1.0 + c[i]
    eg = np.array([eg_map[g] for g in lad])
    # 일 종가
    cl = pd.Series(close).dropna().astype(float)
    if isinstance(cl.index, pd.DatetimeIndex):
        cl.index = _norm_times(cl.index, tz)
    day_close = cl.groupby(cl.index.floor("D")).last().reindex(days).ffill().to_numpy(float)
    ed = np.clip(np.searchsorted(days.as_unit("ns").asi8, et.floor("D").as_unit("ns").asi8, side="left"), 0, n)
    xd = np.clip(np.searchsorted(days.as_unit("ns").asi8, xt.floor("D").as_unit("ns").asi8, side="left"), 0, n - 1)
    ep = trades["entry_price"].to_numpy(np.float64)
    realized = np.zeros(n)
    np.add.at(realized, xd, eg * w * r)
    real_cum = np.cumsum(realized)
    unreal = np.zeros(n)
    for i in range(m):
        a, b = int(ed[i]), int(xd[i])        # 진입일 a 부터 청산 전날 b−1 까지 평가 (청산일은 실현)
        if b <= a:
            continue
        px = day_close[a:b]
        if cost is not None:
            mark = (px * (1 - cost.side_cost) * (1 - cost.fee_rate)) / (ep[i] * (1 + cost.fee_rate)) - 1
        else:
            mark = px / ep[i] - 1
        mark = np.where(np.isfinite(mark), mark, 0.0)
        unreal[a:b] += eg[i] * w[i] * mark
    eq = 1.0 + real_cum + unreal
    prev = np.concatenate([[1.0], eq[:-1]])
    return pd.Series(eq / prev - 1.0, index=days, name="ret_mtm")


def _marks(trades: pd.DataFrame, close: pd.Series, days: pd.DatetimeIndex, cost=None):
    """(진입일 슬롯, 청산일 슬롯, 일 종가) — `mtm_daily_returns` 와 같은 규칙."""
    tz = days.tz
    n = len(days)
    et = _norm_times(trades["entry_time"], tz)
    xt = _norm_times(trades["exit_time"], tz)
    cl = pd.Series(close).dropna().astype(float)
    if isinstance(cl.index, pd.DatetimeIndex):
        cl.index = _norm_times(cl.index, tz)
    day_close = cl.groupby(cl.index.floor("D")).last().reindex(days).ffill().to_numpy(float)
    di = days.as_unit("ns").asi8
    ed = np.clip(np.searchsorted(di, et.floor("D").as_unit("ns").asi8, side="left"), 0, n)
    xd = np.clip(np.searchsorted(di, xt.floor("D").as_unit("ns").asi8, side="left"), 0, n - 1)
    return ed, xd, day_close


def position_mtm_troughs(trades: pd.DataFrame | None, close: pd.Series, days: pd.DatetimeIndex,
                         cost=None) -> np.ndarray:
    """포지션(사다리)별 보유 중 최악 평가 손익 (≤ 0, 포지션 시작 자산 대비) — `frame_position_returns` 와 같은 순서.
    각 UTC 일 종가(포지션의 첫 진입일 ~ 마지막 청산 전날)에서: 이미 청산된 분할의 실현 w·r + 열린 분할의 w·평가손익
    (`mtm_daily_returns` 와 같은 청산 평가). 몬테카를로 MTM 낙폭 입력(`montecarlo.bootstrap_mdd(troughs=…)`, PLAN D18)."""
    if trades is None or not len(trades):
        return np.zeros(0)
    days = pd.DatetimeIndex(days)
    if len(days) == 0:
        return np.zeros(int(len(np.unique(trades["ladder"])) if is_sized_frame(trades) else len(trades)))
    m = len(trades)
    sized = is_sized_frame(trades)
    w = trades["weight"].to_numpy(np.float64) if sized else np.ones(m)
    lad = trades["ladder"].to_numpy(np.int64) if sized else np.arange(m)
    r = trades["net_ret"].to_numpy(np.float64)
    ep = trades["entry_price"].to_numpy(np.float64)
    ed, xd, day_close = _marks(trades, close, days, cost)
    u, inv = np.unique(lad, return_inverse=True)
    out = np.zeros(u.size)
    for g in range(u.size):
        idx = np.flatnonzero(inv == g)
        lo, hi = int(ed[idx].min()), int(xd[idx].max())
        if hi <= lo:
            continue
        vals = np.zeros(hi - lo)
        for i in idx:
            a, b = int(ed[i]), int(xd[i])
            if b < hi:
                vals[max(b, lo) - lo:] += w[i] * r[i]          # 청산된 분할: 실현
            if b > a:
                px = day_close[a:b]
                if cost is not None:
                    mark = (px * (1 - cost.side_cost) * (1 - cost.fee_rate)) / (ep[i] * (1 + cost.fee_rate)) - 1
                else:
                    mark = px / ep[i] - 1
                vals[a - lo:b - lo] += w[i] * np.where(np.isfinite(mark), mark, 0.0)
        out[g] = min(0.0, float(vals.min()))
    return out


__all__ = ["SIZED_COLUMNS", "position_mtm_troughs", "equity_returns", "position_returns", "frame_equity_returns", "is_sized_frame",
           "trade_contributions", "frame_position_returns", "n_positions", "inventory_profile",
           "mtm_daily_returns"]
