"""managed_hold 전용 관문 프로필 (PLAN Phase 5, docs/MANAGED_HOLD.md §5 Q1~Q11) — **제안 단계, 사람 승인 대기**.

기본 롱 / 추세 꺼지면 현금 전략은 '무작위 진입보다 나은 짧은 엣지'가 아니라 **매수보유(B&H) 대비 타이밍 가치**가 질문이다.
그래서 모든 증거 관문을 동일 비중 3코인 B&H 대비 **능동(액티브) 일간 수익** a_t = r_t(전략) − r_t(B&H) 위에서 본다.

| 관문 | 프로필 규칙 (기본값, `PROFILE_DEFAULT`) | 이유 (MANAGED_HOLD.md 질문) |
|---|---|---|
| G1 | 풀링 전환(진입) 수 ≥ 30, Dev 일수 ≥ 365 | Q1: 표본 단위 = 전환·일수 (거래 300 은 보유 전략에 맞지 않음) |
| G2 | **타이밍 귀무**: 자산별 일간 노출 상태열을 무작위 원형 이동(노출·전환 수·보유 구간 길이 보존, 가격 정렬만 파괴) 500회, 능동 Sharpe(총) 가 상위 5% **그리고** 순 능동 Sharpe > 0 | Q2 (a) |
| G3 | 격자 인접 능동 Sharpe 중앙값 ≥ 0.25 × 선택 (선택 > 0) | Q3 (R1-plus 와 같은 비) |
| G4 | 6개월 롤링 창(30일 간격)의 능동 수익 ≥ 0 비율 ≥ 0.5 | Q4: 약세장 현금 창이 '실패'로 세지지 않도록 능동 기준 |
| G5 | 능동 수익 DSR ≥ 0.95 (시도 수 = 격자 스펙 수) | Q5: 절대 Sharpe 는 베타만으로 통과 |
| G6 | Val: 능동 수익 결합 증거 (Dev 능동 DSR z + Val 능동 부트스트랩 p, Stouffer) ≤ 0.05 그리고 Val 능동 Sharpe > 0; 낙폭 = 크기 조절(R1-a, 하한 0.25), B&H 대비 MDD 비는 보고 | Q6 |
| G8 | 페이퍼 = 기간 기준(≥ 8주) + 백테스트 노출 상태 일치율 ≥ 95% (거래 수 아님) | Q7 (페이퍼 구현은 승인 후) |

이 모듈은 일간 수익·노출 행렬만 받아 판정한다(원장·Val 예산과 무관) — 합성 보정(`managed_hold_study`)과, 승인 후의 파이프라인
연결이 같은 함수를 쓴다. 파이프라인 연결은 **사용자 승인 후**(기본 프로필 불변).
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from btc_quant.evaluation import inference
from btc_quant.evaluation import metrics as M
from btc_quant.evaluation.selection import dsr_from_returns

PROFILE_DEFAULT = {
    "min_switches": 30, "min_dev_days": 365,
    "g2_null_runs": 500, "g2_top_fraction": 0.05, "g2_min_shift_days": 30,
    "g3_neighbor_ratio": 0.25,
    "g4_window_days": 182, "g4_step_days": 30, "g4_min_frac": 0.5,
    "g5_min_dsr": 0.95,
    "g6_combined_alpha": 0.05, "g6_max_mdd": 0.20, "g6_max_mc_mdd95": 0.20, "g6_min_position_scale": 0.25,
    "g8_min_weeks": 8, "g8_min_state_match": 0.95,
}
_ND = NormalDist()


def daily_exposure(trades: pd.DataFrame, days: pd.DatetimeIndex) -> pd.Series:
    """거래 표 → UTC 일별 보유 시간 비율 (0~1). entry_time 부터 exit_time 까지."""
    out = np.zeros(len(days))
    if trades is None or not len(trades):
        return pd.Series(out, index=days)
    d0 = days[0]
    for et, xt in zip(pd.to_datetime(trades["entry_time"], utc=True), pd.to_datetime(trades["exit_time"], utc=True)):
        a = (et - d0) / pd.Timedelta(days=1)
        b = (xt - d0) / pd.Timedelta(days=1)
        i0, i1 = int(math.floor(max(a, 0))), int(math.floor(min(b, len(days) - 1e-9)))
        for i in range(i0, i1 + 1):
            lo, hi = max(a, i), min(b, i + 1)
            if hi > lo:
                out[i] += hi - lo
    return pd.Series(np.clip(out, 0.0, 1.0), index=days)


def active_sharpe(a: pd.Series | np.ndarray) -> float:
    return float(M.sharpe(pd.Series(np.asarray(a, float))))


def timing_null(expo: dict[str, np.ndarray], asset_ret: dict[str, np.ndarray], bh: np.ndarray, n: int,
                min_shift: int, seed: int = 0) -> np.ndarray:
    """원형 이동 타이밍 귀무: 자산마다 노출열을 [min_shift, n_days − min_shift] 만큼 무작위 원형 이동 → 총 능동 Sharpe 분포."""
    rng = np.random.default_rng(seed)
    syms = list(expo)
    nd = len(bh)
    out = np.empty(n)
    for j in range(n):
        port = np.zeros(nd)
        for s in syms:
            k = int(rng.integers(min_shift, max(min_shift + 1, nd - min_shift)))
            port += np.roll(expo[s], k) * asset_ret[s]
        out[j] = active_sharpe(port / len(syms) - bh)
    return out


def gross_active(expo: dict[str, np.ndarray], asset_ret: dict[str, np.ndarray], bh: np.ndarray) -> np.ndarray:
    return sum(expo[s] * asset_ret[s] for s in expo) / len(expo) - bh


def rolling_frac_nonneg(a: pd.Series, window: int, step: int) -> tuple[float, int]:
    x = np.asarray(a, float)
    vals = [np.prod(1 + x[i:i + window]) - 1 for i in range(0, len(x) - window + 1, step)]
    return (float(np.mean(np.asarray(vals) >= 0)) if vals else 0.0), len(vals)


def dsr_z(a: pd.Series, n_trials: float, var_trial_sr: float) -> tuple[float, dict]:
    d = dsr_from_returns(np.asarray(a, float), n_trials, var_trial_sr)
    n = d["n_obs"]
    if n < 2:
        return 0.0, d
    sk = d["skew"] if np.isfinite(d["skew"]) else 0.0
    ku = d["kurtosis"] if np.isfinite(d["kurtosis"]) else 3.0
    vt = max(1.0 - sk * d["sr"] + (ku - 1.0) / 4.0 * d["sr"] ** 2, 1e-12)
    return (d["sr"] - d["sr0"]) * math.sqrt(n - 1) / math.sqrt(vt), d


def evaluate(dev: dict, val: dict, grid_active_sharpes: dict[str, float], neighbors: list[str], sel: str,
             trial_var: float, n_trials: int, prof: dict | None = None, seed: int = 0) -> dict:
    """선택 스펙 하나의 프로필 판정.

    dev/val = {"active": 순 능동 일간 수익 Series, "expo": {sym: 일간 노출 ndarray}, "asset_ret": {sym: ndarray},
               "bh": B&H 일간 ndarray, "n_switches": int, "mdd": 전략 MDD, "bh_mdd": B&H MDD, "mc_mdd95": float}
    grid_active_sharpes = 격자 전체 Dev 순 능동 Sharpe (G3 인접용), neighbors = 선택의 인접 스펙 키."""
    p = {**PROFILE_DEFAULT, **(prof or {})}
    g: dict = {}
    a = dev["active"]
    nd = len(a)
    g["G1"] = {"passed": dev["n_switches"] >= p["min_switches"] and nd >= p["min_dev_days"],
               "n_switches": int(dev["n_switches"]), "n_days": nd}
    obs_gross = active_sharpe(gross_active(dev["expo"], dev["asset_ret"], dev["bh"]))
    null = timing_null(dev["expo"], dev["asset_ret"], dev["bh"], p["g2_null_runs"], p["g2_min_shift_days"], seed)
    pv = float((1 + np.sum(null >= obs_gross)) / (len(null) + 1))
    net = active_sharpe(a)
    g["G2"] = {"passed": pv <= p["g2_top_fraction"] and net > 0, "p_value": pv, "gross_active_sharpe": obs_gross,
               "net_active_sharpe": net, "null_p95": float(np.quantile(null, 0.95))}
    sel_sr = grid_active_sharpes[sel]
    nb = [grid_active_sharpes[k] for k in neighbors]
    med = float(np.median(nb)) if nb else float("nan")
    g["G3"] = {"passed": bool(sel_sr > 0 and nb and med >= p["g3_neighbor_ratio"] * sel_sr), "selected": sel_sr,
               "neighbor_median": med}
    fr, nw = rolling_frac_nonneg(a, p["g4_window_days"], p["g4_step_days"])
    g["G4"] = {"passed": nw > 0 and fr >= p["g4_min_frac"], "frac_nonneg": fr, "n_windows": nw}
    z_dev, dd = dsr_z(a, max(float(n_trials), 1.0), trial_var)
    dsr = _ND.cdf(z_dev)
    g["G5"] = {"passed": dsr >= p["g5_min_dsr"], "dsr": dsr, "z": z_dev, "n_trials": n_trials}
    dev_ok = all(g[k]["passed"] for k in ("G1", "G2", "G3", "G4", "G5"))
    if not dev_ok or val is None:
        return {"dev_passed": dev_ok, "gates": g, "validated": False,
                "first_failed": next((k for k in ("G1", "G2", "G3", "G4", "G5") if not g[k]["passed"]), None)}
    va = val["active"]
    bt = inference.bootstrap_mean_test(np.asarray(va, float), n_boot=2000, seed=0)
    zv = _ND.inv_cdf(min(max(1.0 - bt["p_value"], 1e-9), 1 - 1e-9))
    zc = (z_dev + zv) / math.sqrt(2.0)
    pc = 1.0 - _ND.cdf(zc)
    vsr = active_sharpe(va)
    scale = 1.0
    for lim, x in ((p["g6_max_mdd"], val["mdd"]), (p["g6_max_mc_mdd95"], val["mc_mdd95"])):
        x = abs(float(x))
        if not np.isfinite(x):
            scale = 0.0
        elif x > 0:
            scale = min(scale, lim / x)
    ok_ev = pc <= p["g6_combined_alpha"] and vsr > 0
    ok_sz = scale >= p["g6_min_position_scale"]
    g["G6"] = {"passed": bool(ok_ev and ok_sz), "p_combined": pc, "val_active_sharpe": vsr, "val_p": bt["p_value"],
               "position_scale": scale, "mdd_ratio_vs_bh": (abs(val["mdd"]) / abs(val["bh_mdd"])
                                                            if val.get("bh_mdd") else None),
               "ok_evidence": bool(ok_ev), "ok_position_scale": bool(ok_sz)}
    return {"dev_passed": True, "gates": g, "validated": bool(g["G6"]["passed"]),
            "first_failed": None if g["G6"]["passed"] else "G6"}


__all__ = ["PROFILE_DEFAULT", "active_sharpe", "daily_exposure", "dsr_z", "evaluate", "gross_active",
           "rolling_frac_nonneg", "timing_null"]
