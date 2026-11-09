"""evaluation.gates — 관문 판정과 §7 리스크 공식 부호."""
import numpy as np
import pandas as pd
import pytest

from btc_quant.evaluation import gates as g


def test_config_numbers_match_plan():
    c = g.load_gate_config()
    assert c["G1"] == {"min_trades": 300, "min_recent_trades": 60, "recent_days": 365}
    assert c["G2"]["null_runs"] == 1000 and c["G2"]["null_top_fraction"] == 0.05
    assert c["G3"]["neighbor_median_ratio"] == 0.5
    assert c["G4"]["min_rolling_positive_frac"] == 0.60
    assert c["G5"]["min_dsr"] == 0.95 and c["G5"]["max_pbo"] == 0.30
    assert c["G6"]["max_stress_mdd"] == 0.20 and c["G6"]["max_mc_mdd95"] == 0.20
    assert c["G7"]["alpha"] == 0.10 and c["G7"]["cap"] == 10
    assert c["primary_metric"]["half_life_days"] == 365


def test_g1():
    assert g.check_g1({"n_trades": 300, "recent_n_trades": 60})[0]
    assert not g.check_g1({"n_trades": 299, "recent_n_trades": 100})[0]
    assert not g.check_g1({"n_trades": 1000, "recent_n_trades": 59})[0]


def test_g2():
    null = np.linspace(-1, 1, 1000)
    assert g.check_g2({"sharpe_w": 1.5}, null)[0]
    ok, d = g.check_g2({"sharpe_w": 0.5}, null)
    assert not ok and d["ok_positive"] and not d["ok_null"]
    assert not g.check_g2({"sharpe_w": -0.1}, null - 5)[0]


def test_g3():
    assert g.check_g3(2.0, [1.0, 1.2, 0.5])[0]
    assert not g.check_g3(2.0, [0.9, 0.5, 2.0])[0]
    assert not g.check_g3(-1.0, [1.0])[0]
    assert not g.check_g3(1.0, [])[0]


def test_g4_g5():
    assert g.check_g4({"rolling_frac_positive": 0.6, "rolling_n_windows": 10,
                       "recent_sharpe": 0.1})[0]
    assert not g.check_g4({"rolling_frac_positive": 0.59, "rolling_n_windows": 10,
                           "recent_sharpe": 1})[0]
    assert not g.check_g4({"rolling_frac_positive": 1, "rolling_n_windows": 10,
                           "recent_sharpe": 0.0})[0]
    assert g.check_g5(0.95, 0.29)[0]
    assert not g.check_g5(0.949, 0.1)[0] and not g.check_g5(0.99, 0.30)[0]


def test_g6_sign_agnostic():
    assert g.check_g6({"sharpe": 0.5}, 0.20, -0.19)[0]
    assert not g.check_g6({"sharpe": 0.5}, -0.21, 0.1)[0]
    assert not g.check_g6({"sharpe": 0.0}, 0.1, 0.1)[0]


def test_g7():
    assert g.check_g7(0.005)[0]
    ok, d = g.check_g7(0.02, [0.5])
    assert not ok and d["k"] == 2 and d["m"] == 10


def test_risk_limits_floors_and_signs():
    idx = pd.date_range("2025-11-03", periods=182, freq="D", tz="UTC")  # 월요일 시작
    calm = pd.Series(np.tile([0.004, -0.004], 91), index=idx)
    r = g.risk_limits_from_validation(calm, mc_mdd95=0.05, avg_trades_per_day=1)
    assert r["daily_loss_limit"] == -0.03       # 1백분위 -0.4% → 바닥 -3%
    assert r["weekly_loss_limit"] == -0.06
    assert r["drawdown_limit"] == -0.12         # -0.06 → 바닥 -12%
    assert r["max_trades_per_day"] == 5
    wild = calm.copy()
    wild.iloc[10] = -0.08                       # 큰 손실일 → 일간 한도 확장
    wild.iloc[11:14] = -0.03
    wild.iloc[50], wild.iloc[90] = -0.07, -0.05
    r2 = g.risk_limits_from_validation(wild, mc_mdd95=-0.15, avg_trades_per_day=4)
    assert r2["daily_loss_limit"] < -0.03 and r2["daily_loss_limit"] >= -0.08
    assert r2["weekly_loss_limit"] < -0.06
    assert r2["drawdown_limit"] == pytest.approx(-0.18)  # 부호 무관 |mc|×1.2
    assert r2["max_trades_per_day"] == 12
    for k in ("daily_loss_limit", "weekly_loss_limit", "drawdown_limit"):
        assert r2[k] < 0
