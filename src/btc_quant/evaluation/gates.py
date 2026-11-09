"""관문 G1~G7 판정과 §7 리스크 한도 공식. 입력은 이미 계산된 지표(dict)뿐.

모든 check_* 는 (passed: bool, details: dict) 를 반환. 수치는 config/gates.yaml.
지표 dict 키는 metrics.summarize() 출력과 같다. 낙폭은 양수 크기.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import inference

GATES_PATH = Path(__file__).resolve().parents[3] / "config" / "gates.yaml"


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_gate_config(path: str | Path | None = None) -> dict:
    return _load(str(path or GATES_PATH))


def _cfg(cfg, gate):
    return (cfg or load_gate_config())[gate]


def check_g1(metrics: dict, cfg: dict | None = None) -> tuple[bool, dict]:
    """G1 표본: 거래 ≥ 300, Dev 최근 12개월 거래 ≥ 60."""
    c = _cfg(cfg, "G1")
    n, nr = int(metrics["n_trades"]), int(metrics["recent_n_trades"])
    d = {"n_trades": n, "recent_n_trades": nr,
         "ok_trades": n >= c["min_trades"], "ok_recent": nr >= c["min_recent_trades"]}
    return d["ok_trades"] and d["ok_recent"], d


def check_g2(metrics: dict, null_sharpes, cfg: dict | None = None) -> tuple[bool, dict]:
    """G2 엣지: 가중 Sharpe > 0 그리고 무작위 진입 귀무(가중 Sharpe 분포) 대비 상위 5%."""
    c = _cfg(cfg, "G2")
    sr = float(metrics["sharpe_w"])
    pos = inference.null_percentile(sr, null_sharpes)
    d = {"sharpe_w": sr, **pos, "ok_positive": sr > c["min_weighted_sharpe"],
         "ok_null": pos["p_value"] <= c["null_top_fraction"],
         "null_runs_expected": c["null_runs"]}
    return d["ok_positive"] and d["ok_null"], d


def check_g3(selected_sharpe: float, neighbor_sharpes, cfg: dict | None = None
             ) -> tuple[bool, dict]:
    """G3 고원: median(인접 조합 Sharpe) ≥ 0.5 × 선택 조합 Sharpe. 선택 Sharpe ≤ 0 또는
    인접 조합이 없으면 실패."""
    c = _cfg(cfg, "G3")
    nb = np.asarray(list(neighbor_sharpes), dtype=float)
    nb = nb[np.isfinite(nb)]
    med = float(np.median(nb)) if nb.size else float("nan")
    need = c["neighbor_median_ratio"] * float(selected_sharpe)
    ok = bool(selected_sharpe > 0 and nb.size > 0 and med >= need)
    return ok, {"selected": float(selected_sharpe), "neighbor_median": med,
                "required": need, "n_neighbors": int(nb.size)}


def check_g4(metrics: dict, cfg: dict | None = None) -> tuple[bool, dict]:
    """G4 안정: 6개월 롤링 창 ≥ 60% 양수, 최근 12개월 Sharpe > 0.
    ("고정 파라미터 그대로" 는 절차 요건 — 호출 측이 동일 스펙임을 보장.)"""
    c = _cfg(cfg, "G4")
    fr, rs = float(metrics["rolling_frac_positive"]), float(metrics["recent_sharpe"])
    d = {"rolling_frac_positive": fr, "rolling_n_windows": int(metrics.get("rolling_n_windows", 0)),
         "recent_sharpe": rs, "ok_rolling": fr >= c["min_rolling_positive_frac"],
         "ok_recent": rs > c["min_recent_sharpe"]}
    d["ok_rolling"] = d["ok_rolling"] and d["rolling_n_windows"] > 0
    return d["ok_rolling"] and d["ok_recent"], d


def check_g5(dsr: float, pbo: float, cfg: dict | None = None) -> tuple[bool, dict]:
    """G5 선택편향: DSR ≥ 0.95 (실질 시도 수 사용), 캠페인 PBO < 0.3."""
    c = _cfg(cfg, "G5")
    d = {"dsr": float(dsr), "pbo": float(pbo), "ok_dsr": dsr >= c["min_dsr"],
         "ok_pbo": pbo < c["max_pbo"]}
    return d["ok_dsr"] and d["ok_pbo"], d


def check_g6(val_metrics: dict, stress_mdd: float, mc_mdd95: float,
             cfg: dict | None = None) -> tuple[bool, dict]:
    """G6 검증(Val): Sharpe(conservative) > 0, stress 비용 MDD ≤ 20%, 거래당 MC MDD95 ≤ 20%.
    stress_mdd / mc_mdd95 는 크기(부호 무관, abs 사용)."""
    c = _cfg(cfg, "G6")
    sr = float(val_metrics["sharpe"])
    smdd, mc = abs(float(stress_mdd)), abs(float(mc_mdd95))
    d = {"sharpe": sr, "stress_mdd": smdd, "mc_mdd95": mc,
         "ok_sharpe": sr > c["min_sharpe"], "ok_stress_mdd": smdd <= c["max_stress_mdd"],
         "ok_mc_mdd": mc <= c["max_mc_mdd95"]}
    return d["ok_sharpe"] and d["ok_stress_mdd"] and d["ok_mc_mdd"], d


def check_g7(p_value: float, prior_p_values=(), cfg: dict | None = None) -> tuple[bool, dict]:
    """G7 잠금: 잠금 구간 일평균 수익 > 0 의 p 값이 순차 Holm(α=0.10, 상한 10) 통과.
    p_value 는 inference.bootstrap_mean_test 결과. 정의는 inference.lockbox_holm_decision."""
    c = _cfg(cfg, "G7")
    d = inference.lockbox_holm_decision(p_value, prior_p_values, c["alpha"], c["cap"],
                                        c["holm_mode"])
    return d["passed"], d


def _weekly(daily: pd.Series) -> pd.Series:
    s = pd.Series(daily).dropna().astype(float)
    if isinstance(s.index, pd.DatetimeIndex):
        return (1 + s).resample("W-SUN").prod() - 1   # 월~일 주, 복리
    g = np.arange(s.size) // 7
    return (1 + s).groupby(g).prod() - 1


def risk_limits_from_validation(val_daily_returns: pd.Series, mc_mdd95: float,
                                avg_trades_per_day: float, cfg: dict | None = None) -> dict:
    """PLAN §7 공식 (음수 = 손실 수익률 한도; 'min' 은 더 큰 손실 쪽 = 더 느슨한 쪽).

    daily_loss  = min(−3%, Val 일간 수익 1백분위)
    weekly_loss = min(−6%, Val 주간(월~일, 복리) 수익 1백분위)
    max_drawdown= min(−12%, −|mc_mdd95| × 1.2)     (mc_mdd95 는 크기, 부호 무관)
    max_trades_per_day = max(5, 3 × 백테스트 일평균 거래)
    """
    c = _cfg(cfg, "risk")
    s = pd.Series(val_daily_returns).dropna().astype(float)
    q = c["loss_percentile"]
    d1 = float(np.percentile(s, q)) if s.size else 0.0
    wk = _weekly(s)
    w1 = float(np.percentile(wk, q)) if wk.size else 0.0
    dd = -abs(float(mc_mdd95)) * c["drawdown_mc_mult"]
    return {
        "daily_loss_limit": min(c["daily_loss_floor"], d1),
        "weekly_loss_limit": min(c["weekly_loss_floor"], w1),
        "drawdown_limit": min(c["drawdown_floor"], dd),
        "max_trades_per_day": max(float(c["min_max_trades_per_day"]),
                                  c["trades_per_day_mult"] * float(avg_trades_per_day)),
        "inputs": {"daily_p1": d1, "weekly_p1": w1, "mc_mdd95": abs(float(mc_mdd95)),
                   "avg_trades_per_day": float(avg_trades_per_day)},
    }
