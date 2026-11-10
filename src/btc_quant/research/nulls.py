"""G2 귀무 분포: 같은 필터·청산 + 무작위 진입(`random_entry`), 진입 비율 보정.

정의
- 후보 스펙 S = (진입 E, 필터 F, 청산 X). 귀무 스펙 S0(seed) = (random_entry(p, seed), F, X).
- **p 보정** (2단계):
  1) 신호 비율: 평가 구간 [start, end) 의 5분봉 중 `F & complete` 인 봉에서 E 가 True 인 비율
         p_signal = #(E & F & complete) / #(F & complete)
     → 무작위 진입이 후보와 **같은 기회 집합(필터 통과 봉)에서 같은 빈도로** 신호를 낸다.
  2) 거래 수 맞춤(기본): p_signal 에서 출발해, 탐색용 seed 16개의 귀무 거래 수 중앙값이 후보 거래 수와
     (±3%) 같아지도록 로그 공간 이분법으로 p 조정. 이유: 상태형·군집형 진입(연속 True)은 쿨다운 때문에
     신호 비율이 같아도 귀무가 훨씬 자주 거래 → 비용을 더 내서, 후보가 '덜 거래한다'는 이유만으로
     귀무를 이긴다(무작위 보행 합성 시장에서 p≈0.004 로 확인). `calibration="signal_rate"` 로 1)만 쓸 수 있다.
  두 p 와 귀무 거래 수 분포를 모두 보고한다.
- seed = seed0 .. seed0+n-1 (기본 1,000개, config G2.null_runs).
- **결정 주기 B** (`tf_min`, D10): 신호 비율·무작위 진입 해시·쿨다운 모두 결정 주기 봉 기준
  (5m: 5분봉, 1m: 1분봉 — 후보 스펙과 같은 결정 행). **ATR 청산**(`atr_bars`): 귀무도 후보와 같은 결정 행
  ATR 비율 배열과 같은 ATR 배수로 실행 (ATR 이 정의되지 않은 결정에서는 진입하지 않음 — 엔진 규칙 그대로).

구현 (빠른 경로, `random_entry` 블록과 비트 단위 동일)
- `random_entry(bars, p, seed)[s] = hash_uniform(s, seed) < p` 이므로, 1분봉 결정 배열에서
  `F & complete` 가 True 이고 ~filled 인 결정 위치(t = s+B분)만 미리 구해 두고, seed 마다
  그 위치의 결정 봉 시각 s 에 대해 해시만 계산 → 후보 인덱스 → `fast.simulate_prepared`.
- 통계량: primary 비용 일간 수익의 **가중 Sharpe**(반감기 365일, `metrics.weighted_sharpe` 와 같은 식,
  행렬로 벡터화).
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from btc_quant.backtest import fast
from btc_quant.backtest.signals import period_bounds
from btc_quant.backtest.types import CostModel
from btc_quant.evaluation.metrics import ANN, exp_weights
from btc_quant.strategy.blocks.null import _splitmix64
from btc_quant.strategy.spec import tf_name


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
                    start, end, tf_min: int = 5) -> dict:
    """p = #(entry & filter_ok) / #(filter_ok), 결정 봉(B = tf_min 분) 결정 시각 s+B분 ∈ [start, end) 범위."""
    s, e, _, _ = period_bounds(bars_index, start, end)
    t = bars_index + pd.Timedelta(minutes=int(tf_min))
    m = (t >= s) & (t < e)
    base = np.asarray(filter_ok, bool) & m
    n_base = int(base.sum())
    n_hit = int((np.asarray(entry, bool) & base).sum())
    return {"p": n_hit / n_base if n_base else 0.0, "n_entry_bars": n_hit, "n_filter_bars": n_base}


class RandomEntryNull:
    """같은 필터 통과 봉 집합 위에서 seed 별 random_entry 후보를 빠르게 만든다."""

    def __init__(self, m1: pd.DataFrame, bars_index: pd.DatetimeIndex, filter_ok: np.ndarray,
                 tf_min: int = 5, atr_bars: int | None = None, bars: pd.DataFrame | None = None,
                 execution=None, fill=None):
        """execution/fill: 후보 스펙의 지정가 집행 (None = 시장가). 무작위 진입 시각에 같은 지정가 규칙을 적용한다
        (ATR 오프셋 + 고정 % 청산이면 오프셋 ATR 창 14 를 함께 준비)."""
        base = pd.Series(np.asarray(filter_ok, bool), index=bars_index)
        self.atr_bars = atr_bars
        self.execution = None if (execution is None or execution.is_default) else execution
        self.fill = fill
        prep_atr = atr_bars
        if self.execution is not None and self.execution.offset_mode == "atr" and atr_bars is None:
            from btc_quant.strategy.spec import DEFAULT_ATR_BARS
            prep_atr = DEFAULT_ATR_BARS
        # cand = 필터 통과 결정 위치. ATR 청산이면 같은 결정 행 ATR 비율(후보 스펙과 동일 배열)
        self.prepared = fast.prepare(m1, base, None, timeframe=tf_name(int(tf_min)), atr_bars=prep_atr,
                                     bars=bars)
        self.pos = self.prepared.cand
        bar_times = pd.DatetimeIndex(m1.index[self.pos] - pd.Timedelta(minutes=int(tf_min)))
        self._ns = np.asarray(bar_times.as_unit("ns").asi8, dtype=np.int64).view(np.uint64)

    def uniform(self, seed: int) -> np.ndarray:
        """null.hash_uniform(bar_times, seed) 와 같은 값 (pandas 변환 없이)."""
        with np.errstate(over="ignore"):
            h = _splitmix64(np.full(1, int(seed), dtype=np.uint64))
            x = _splitmix64(self._ns ^ h)
        return (x >> np.uint64(11)).astype(np.float64) / float(1 << 53)

    def candidates(self, p: float, seed: int) -> np.ndarray:
        if p <= 0 or self.pos.size == 0:
            return self.pos[:0]
        return self.pos[self.uniform(seed) < p]

    def simulate(self, p: float, seed: int, sl: float, tp: float, hold: int, cost: CostModel,
                 si: int, ei: int) -> fast.TradeArrays:
        pr = replace(self.prepared, cand=self.candidates(p, seed))
        if self.execution is None:
            return fast.simulate_prepared(pr, sl, tp, hold, cost, si, ei, self.atr_bars)
        return fast.simulate_prepared(pr, sl, tp, hold, cost, si, ei, self.atr_bars, execution=self.execution,
                                      fill=self.fill)


def calibrate_p_to_trades(gen: RandomEntryNull, p0: float, target_trades: int, sl: float, tp: float,
                          hold: int, cost: CostModel, si: int, ei: int, n_probe: int = 16,
                          seed0: int = 1_000_000, max_iter: int = 14, tol: float = 0.03) -> dict:
    """p 를 조정해 귀무의 거래 수 중앙값(n_probe 개 seed)이 후보 거래 수에 맞도록 (로그 공간 이분법).

    시작점 p0 = 신호 비율. 탐색용 seed 는 귀무 seed 와 겹치지 않게 seed0 부터.
    """
    def med(p):
        if p <= 0:
            return 0.0
        return float(np.median([len(gen.simulate(p, seed0 + k, sl, tp, hold, cost, si, ei).net_ret)
                                for k in range(n_probe)]))

    if target_trades <= 0 or p0 <= 0:
        return {"p": 0.0, "probe_trades": 0.0, "iterations": 0}
    lo = hi = p0
    m0 = med(p0)
    it = 0
    if abs(m0 - target_trades) <= tol * target_trades:
        return {"p": p0, "probe_trades": m0, "iterations": 0}
    if m0 < target_trades:               # 거래가 부족 → p 증가 방향으로 구간 확보
        while it < max_iter and hi < 1.0:
            lo, hi = hi, min(1.0, hi * 2)
            it += 1
            if med(hi) >= target_trades:
                break
    else:
        while it < max_iter:
            hi, lo = lo, lo / 2
            it += 1
            if med(lo) <= target_trades:
                break
    best, best_m = p0, m0
    for _ in range(max_iter):
        mid = float(np.sqrt(lo * hi))
        m = med(mid)
        it += 1
        if abs(m - target_trades) < abs(best_m - target_trades):
            best, best_m = mid, m
        if abs(m - target_trades) <= tol * target_trades:
            break
        if m < target_trades:
            lo = mid
        else:
            hi = mid
    return {"p": best, "probe_trades": best_m, "iterations": it}


def null_distribution(m1: pd.DataFrame, bars_index: pd.DatetimeIndex, entry: np.ndarray,
                      filter_ok: np.ndarray, sl: float, tp: float, hold: int, cost: CostModel,
                      start, end, n_seeds: int = 1000, seed0: int = 0,
                      half_life_days: float = 365, calibration: str = "trade_count",
                      target_trades: int | None = None, tf_min: int = 5, atr_bars: int | None = None,
                      bars: pd.DataFrame | None = None, execution=None, fill=None) -> dict:
    """귀무 가중 Sharpe 분포. 반환: sharpe_w(np.ndarray), n_trades(np.ndarray), p, seeds 등.

    calibration="signal_rate": p = 필터 통과 봉 중 진입 신호 비율 (calibrated_rate).
    calibration="trade_count" (기본): 신호 비율에서 출발해 귀무 거래 수 중앙값이 후보 거래 수
      (target_trades)와 같아지도록 p 를 조정. 상태형·군집형 진입은 쿨다운 때문에 신호 비율이
      같아도 귀무가 훨씬 많이 거래해 비용을 더 내므로, 신호 비율 보정만으로는 '거래를 덜 하는'
      것만으로 귀무를 이기는 편향이 생긴다(무작위 보행 합성 데이터에서 확인).
    """
    cal = calibrated_rate(entry, filter_ok, bars_index, start, end, tf_min)
    gen = RandomEntryNull(m1, bars_index, filter_ok, tf_min, atr_bars, bars, execution, fill)
    pr = gen.prepared
    _, _, si, ei = pr.bounds(start, end)
    info = {"p_signal_rate": cal["p"], "calibration": calibration}
    p = cal["p"]
    if calibration == "trade_count":
        if target_trades is None:
            raise ValueError("trade_count calibration needs target_trades")
        c2 = calibrate_p_to_trades(gen, cal["p"], int(target_trades), sl, tp, hold, cost, si, ei)
        p = c2["p"]
        info.update(calibration_probe_trades=c2["probe_trades"], calibration_iterations=c2["iterations"],
                    target_trades=int(target_trades))
    elif calibration != "signal_rate":
        raise ValueError("calibration must be 'signal_rate' or 'trade_count'")
    srs, ntr = np.empty(n_seeds), np.empty(n_seeds, dtype=np.int64)
    chunk = 100
    for c0 in range(0, n_seeds, chunk):
        seeds = range(seed0 + c0, seed0 + min(n_seeds, c0 + chunk))
        trs = [gen.simulate(p, sd, sl, tp, hold, cost, si, ei) for sd in seeds]
        days, mat = fast.daily_returns_matrix(pr, trs, start, end)
        srs[c0:c0 + len(trs)] = weighted_sharpe_rows(mat, days, half_life_days)
        ntr[c0:c0 + len(trs)] = [len(t.net_ret) for t in trs]
    return {"sharpe_w": srs, "n_trades": ntr, **cal, **info, "p": p, "n_seeds": int(n_seeds),
            "tf_min": int(tf_min), "atr_bars": atr_bars,
            "seed0": int(seed0),
            "n_trades_median": float(np.median(ntr)) if n_seeds else 0.0,
            "sharpe_w_p95": float(np.percentile(srs, 95)) if n_seeds else float("nan"),
            "sharpe_w_median": float(np.median(srs)) if n_seeds else float("nan")}


def null_distribution_universe(sleeves: list[dict], sl: float, tp: float, hold: int, cost: CostModel,
                               start, end, n_seeds: int = 1000, seed0: int = 0, half_life_days: float = 365,
                               calibration: str = "trade_count", tf_min: int = 5,
                               atr_bars: int | None = None, execution=None, fill=None) -> dict:
    """다자산 유니버스(PLAN D15) G2 귀무: **자산마다** 같은 필터·청산 + random_entry, p 는 자산별로 보정
    (그 자산의 신호 비율 → 그 자산 후보 거래 수에 맞춤). seed 마다 슬리브 일간 수익을 평균(포트폴리오, 후보와
    같은 회계)해 가중 Sharpe 를 계산한다.

    같은 seed 를 모든 자산에 쓴다: random_entry 해시는 (결정 봉 시각, seed) 의 함수라 같은 시각에 필터를 통과한
    자산들은 같은 균등 난수를 받는다(자산별 p 로 비교) → 무작위 진입도 시간상 동기화되어 후보의 자산 간 진입
    상관을 (필터가 겹치는 만큼) 보존한다. 독립 seed 보다 귀무 분산이 커서 보수적이다.

    sleeves: [{"symbol", "m1", "bars_index", "entry", "filter_ok", "bars", "target_trades", "cost"(선택 — 슬리브 비용)}].
    반환은 null_distribution 과 같은 키 (+ per_asset; p·p_signal_rate·n_entry_bars·n_filter_bars 는 자산별 dict)."""
    if len(sleeves) == 1:
        sv = sleeves[0]
        return null_distribution(sv["m1"], sv["bars_index"], sv["entry"], sv["filter_ok"], sl, tp, hold, cost,
                                 start, end, n_seeds, seed0, half_life_days, calibration,
                                 sv.get("target_trades"), tf_min, atr_bars, sv.get("bars"), execution, fill)
    gens, ps, per, scost = [], [], {}, []
    for sv in sleeves:
        sym = sv["symbol"]
        cost_s = sv.get("cost") or cost
        scost.append(cost_s)
        cal = calibrated_rate(sv["entry"], sv["filter_ok"], sv["bars_index"], start, end, tf_min)
        gen = RandomEntryNull(sv["m1"], sv["bars_index"], sv["filter_ok"], tf_min, atr_bars, sv.get("bars"),
                              execution, fill)
        _, _, si, ei = gen.prepared.bounds(start, end)
        info = {"p_signal_rate": cal["p"], "n_entry_bars": cal["n_entry_bars"], "n_filter_bars": cal["n_filter_bars"]}
        p = cal["p"]
        if calibration == "trade_count":
            if sv.get("target_trades") is None:
                raise ValueError("trade_count calibration needs target_trades per sleeve")
            c2 = calibrate_p_to_trades(gen, cal["p"], int(sv["target_trades"]), sl, tp, hold, cost_s, si, ei)
            p = c2["p"]
            info.update(calibration_probe_trades=c2["probe_trades"], target_trades=int(sv["target_trades"]))
        elif calibration != "signal_rate":
            raise ValueError("calibration must be 'signal_rate' or 'trade_count'")
        info["p"] = p
        gens.append((gen, si, ei))
        ps.append(p)
        per[sym] = info
    srs, ntr = np.empty(n_seeds), np.zeros(n_seeds, dtype=np.int64)
    chunk = 100
    for c0 in range(0, n_seeds, chunk):
        seeds = list(range(seed0 + c0, seed0 + min(n_seeds, c0 + chunk)))
        acc, days = None, None
        for (gen, si, ei), p, cs in zip(gens, ps, scost):
            trs = [gen.simulate(p, sd, sl, tp, hold, cs, si, ei) for sd in seeds]
            days, mat = fast.daily_returns_matrix(gen.prepared, trs, start, end)
            acc = mat if acc is None else acc + mat
            ntr[c0:c0 + len(seeds)] += np.array([len(t.net_ret) for t in trs], dtype=np.int64)
        srs[c0:c0 + len(seeds)] = weighted_sharpe_rows(acc / len(gens), days, half_life_days)

    def by(k):
        return {sym: v.get(k) for sym, v in per.items()}
    return {"sharpe_w": srs, "n_trades": ntr, "p": by("p"), "p_signal_rate": by("p_signal_rate"),
            "n_entry_bars": by("n_entry_bars"), "n_filter_bars": by("n_filter_bars"),
            "calibration": calibration,
            **({"calibration_probe_trades": by("calibration_probe_trades"), "target_trades": by("target_trades")}
               if calibration == "trade_count" else {}),
            "n_seeds": int(n_seeds), "tf_min": int(tf_min), "atr_bars": atr_bars, "seed0": int(seed0),
            "n_trades_median": float(np.median(ntr)) if n_seeds else 0.0,
            "sharpe_w_p95": float(np.percentile(srs, 95)) if n_seeds else float("nan"),
            "sharpe_w_median": float(np.median(srs)) if n_seeds else float("nan"),
            "universe": [sv["symbol"] for sv in sleeves], "per_asset": per,
            "null_rule": "per-asset random entry (same filters/exits, per-asset p calibrated to that asset's trade "
                         "count), same seed on every sleeve, portfolio = mean of sleeve daily returns"}


__all__ = ["weighted_sharpe_rows", "calibrated_rate", "RandomEntryNull", "calibrate_p_to_trades",
           "null_distribution", "null_distribution_universe"]
