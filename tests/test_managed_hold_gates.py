"""managed_hold 관문 프로필 (제안, 승인 대기) — 판정 함수의 의미."""
import numpy as np
import pandas as pd
import pytest

from btc_quant.research import managed_hold_gates as MG
from btc_quant.research import managed_hold_study as MS


def test_daily_exposure_fractions():
    days = pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC")
    tr = pd.DataFrame({"entry_time": [pd.Timestamp("2026-01-01 12:00", tz="UTC")],
                       "exit_time": [pd.Timestamp("2026-01-03 06:00", tz="UTC")]})
    np.testing.assert_allclose(MG.daily_exposure(tr, days).to_numpy(), [0.5, 1.0, 0.25, 0.0])


def test_timing_null_detects_real_timing_and_not_random():
    rng = np.random.default_rng(0)
    n = 1500
    regime = np.repeat(rng.choice([-1.0, 1.0], n // 60 + 1), 60)[:n]
    r = regime * 0.004 + rng.normal(0, 0.03, n)
    lagged = np.r_[1.0, (regime[:-1] > 0).astype(float)]          # 하루 늦은 추세 상태 = 진짜 타이밍
    rand = (rng.random(n) < 0.5).astype(float)                     # 무작위 노출
    for expo, want in ((lagged, True), (rand, False)):
        e, a = {"A": expo}, {"A": r}
        obs = MG.active_sharpe(MG.gross_active(e, a, r))
        null = MG.timing_null(e, a, r, 300, 30, seed=1)
        assert bool(np.mean(null >= obs) <= 0.05) is want


def test_regime_drift_null_vs_persistent():
    days = pd.date_range("2021-01-01", periods=2000, freq="D", tz="UTC")
    fast = MS.regime_drift(days, 1, 0.003, 1).to_numpy()
    slow = MS.regime_drift(days, 90, 0.003, 1).to_numpy()
    assert set(np.unique(np.abs(fast))) == {0.003}
    runs = lambda x: np.mean(np.diff(np.flatnonzero(np.diff(np.sign(x)) != 0)))  # noqa: E731
    assert runs(fast) < 3 and runs(slow) > 40


def test_evaluate_structure_and_profile_defaults():
    assert MG.PROFILE_DEFAULT["g5_min_dsr"] == 0.95 and MG.PROFILE_DEFAULT["g6_min_position_scale"] == 0.25
    rng = np.random.default_rng(3)
    n = 800
    r = rng.normal(0.001, 0.03, n)
    seg = {"active": pd.Series(rng.normal(0, 0.01, n)), "expo": {"A": np.ones(n)}, "asset_ret": {"A": r},
           "bh": r, "n_switches": 10, "mdd": 0.3, "bh_mdd": 0.5, "mc_mdd95": 0.2}
    out = MG.evaluate(seg, None, {"x": 0.1, "y": 0.0}, ["y"], "x", 0.01, 4)
    assert out["first_failed"] == "G1" and not out["validated"] and set(out["gates"]) == {"G1", "G2", "G3", "G4", "G5"}
