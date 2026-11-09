"""G2 귀무 분포: 같은 필터·청산 + 무작위 진입(`random_entry`), 진입 비율 보정.

정의
- 후보 스펙 S = (진입 E, 필터 F, 청산 X). 귀무 스펙 S0(seed) = (random_entry(p, seed), F, X).
- **p 보정**: 평가 구간 [start, end) 의 5분봉 중 `F & complete` 인 봉에서 E 가 True 인 비율
      p = #(E & F & complete) / #(F & complete)
  → 무작위 진입이 후보와 **같은 기회 집합(필터 통과 봉)에서 같은 빈도로** 신호를 낸다.
  상태형 진입(연속 True)은 쿨다운 때문에 실제 거래 수가 귀무와 다를 수 있으므로 거래 수 분포를
  함께 보고한다(`n_trades_*`).
- seed = seed0 .. seed0+n-1 (기본 1,000개, config G2.null_runs).

구현 (빠른 경로, `random_entry` 블록과 비트 단위 동일)
- `random_entry(bars, p, seed)[s] = hash_uniform(s, seed) < p` 이므로, 1분봉 결정 배열에서
  `F & complete` 가 True 이고 ~filled 인 결정 위치(t = s+5분)만 미리 구해 두고, seed 마다
  그 위치의 5분봉 시각 s 에 대해 해시만 계산 → 후보 인덱스 → `fast.simulate_prepared`.
- 통계량: primary 비용 일간 수익의 **가중 Sharpe**(반감기 365일, `metrics.weighted_sharpe` 와 같은 식,
  행렬로 벡터화).
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from btc_quant.backtest import fast
from btc_quant.backtest.signals import FIVE_MIN, period_bounds
from btc_quant.backtest.types import CostModel
from btc_quant.evaluation.metrics import ANN, exp_weights
from btc_quant.strategy.blocks.null import hash_uniform


def weighted_sharpe_rows(mat: np.ndarray, days: pd.DatetimeIndex, half_life_days: float = 365,
                         ann: int = ANN) -> np.ndarray:
    """[n, T] 일간 수익 → 행별 가중 Sharpe (metrics.weighted_sharpe 와 동일 식, anchor = 마지막 날)."""
    X = np.atleast_2d(np.asarray(mat, dtype=float))
    if X.shape[1] < 2:
        return np.zeros(X.shape[0])
    w = exp_weights(days, half_life_days)
    v1, v2 = w.sum(), (w ** 2).sum()
    m = X @ w / v1
    denom = v1 - v2 / v1
    var = ((X - m[:, None]) ** 2) @ w / denom
    sd = np.sqrt(np.clip(var, 0, None))
    return np.divide(m, sd, out=np.zeros_like(m), where=sd > 0) * np.sqrt(ann)


def calibrated_rate(entry: np.ndarray, filter_ok: np.ndarray, bars_index: pd.DatetimeIndex,
                    start, end) -> dict:
    """p = #(entry & filter_ok) / #(filter_ok), 5분봉 결정 시각 s+5분 ∈ [start, end) 범위."""
    s, e, _, _ = period_bounds(bars_index, start, end)
    t = bars_index + FIVE_MIN
    m = (t >= s) & (t < e)
    base = np.asarray(filter_ok, bool) & m
    n_base = int(base.sum())
    n_hit = int((np.asarray(entry, bool) & base).sum())
    return {"p": n_hit / n_base if n_base else 0.0, "n_entry_bars": n_hit, "n_filter_bars": n_base}


class RandomEntryNull:
    """같은 필터 통과 봉 집합 위에서 seed 별 random_entry 후보를 빠르게 만든다."""

    def __init__(self, m1: pd.DataFrame, bars_index: pd.DatetimeIndex, filter_ok: np.ndarray):
        base5 = pd.Series(np.asarray(filter_ok, bool), index=bars_index)
        self.prepared = fast.prepare(m1, base5, None)     # cand = 필터 통과 결정 위치
        self.pos = self.prepared.cand
        self.bar_times = pd.DatetimeIndex(m1.index[self.pos] - FIVE_MIN)

    def candidates(self, p: float, seed: int) -> np.ndarray:
        if p <= 0 or self.pos.size == 0:
            return self.pos[:0]
        return self.pos[hash_uniform(self.bar_times, int(seed)) < p]

    def simulate(self, p: float, seed: int, sl: float, tp: float, hold: int, cost: CostModel,
                 si: int, ei: int) -> fast.TradeArrays:
        pr = replace(self.prepared, cand=self.candidates(p, seed))
        return fast.simulate_prepared(pr, sl, tp, hold, cost, si, ei)


def null_distribution(m1: pd.DataFrame, bars_index: pd.DatetimeIndex, entry: np.ndarray,
                      filter_ok: np.ndarray, sl: float, tp: float, hold: int, cost: CostModel,
                      start, end, n_seeds: int = 1000, seed0: int = 0,
                      half_life_days: float = 365) -> dict:
    """귀무 가중 Sharpe 분포. 반환: sharpe_w(np.ndarray), n_trades(np.ndarray), p, seeds 등."""
    cal = calibrated_rate(entry, filter_ok, bars_index, start, end)
    gen = RandomEntryNull(m1, bars_index, filter_ok)
    pr = gen.prepared
    _, _, si, ei = pr.bounds(start, end)
    srs, ntr = np.empty(n_seeds), np.empty(n_seeds, dtype=np.int64)
    chunk = 100
    for c0 in range(0, n_seeds, chunk):
        seeds = range(seed0 + c0, seed0 + min(n_seeds, c0 + chunk))
        trs = [gen.simulate(cal["p"], sd, sl, tp, hold, cost, si, ei) for sd in seeds]
        days, mat = fast.daily_returns_matrix(pr, trs, start, end)
        srs[c0:c0 + len(trs)] = weighted_sharpe_rows(mat, days, half_life_days)
        ntr[c0:c0 + len(trs)] = [len(t.net_ret) for t in trs]
    return {"sharpe_w": srs, "n_trades": ntr, **cal, "n_seeds": int(n_seeds), "seed0": int(seed0),
            "n_trades_median": float(np.median(ntr)) if n_seeds else 0.0,
            "sharpe_w_p95": float(np.percentile(srs, 95)) if n_seeds else float("nan"),
            "sharpe_w_median": float(np.median(srs)) if n_seeds else float("nan")}


__all__ = ["weighted_sharpe_rows", "calibrated_rate", "RandomEntryNull", "null_distribution"]
