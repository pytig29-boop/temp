"""evaluation.selection — PSR/DSR, 실질 시도 수, PBO."""
import numpy as np
import pytest
from scipy import stats

from btc_quant.evaluation import selection as sel


def test_psr_hand_computation():
    sr, n, sk, ku = 0.1, 250, -0.5, 5.0
    z = (sr - 0.0) * np.sqrt(n - 1) / np.sqrt(1 - sk * sr + (ku - 1) / 4 * sr ** 2)
    assert sel.probabilistic_sharpe_ratio(sr, n, sk, ku) == pytest.approx(stats.norm.cdf(z))
    assert sel.probabilistic_sharpe_ratio(0.0, 100) == pytest.approx(0.5)


def test_expected_max_sharpe():
    assert sel.expected_max_sharpe(1, 0.01) == 0.0
    # 시뮬레이션과 대략 일치 (N=100, V=1)
    rng = np.random.default_rng(0)
    sim = rng.standard_normal((20000, 100)).max(axis=1).mean()
    assert sel.expected_max_sharpe(100, 1.0) == pytest.approx(sim, rel=0.03)


def test_dsr_decreases_with_trials():
    vals = [sel.deflated_sharpe_ratio(0.1, 1000, 0, 3, n, 0.002) for n in (1, 10, 100, 1000)]
    assert vals[0] == pytest.approx(sel.probabilistic_sharpe_ratio(0.1, 1000))
    assert all(a > b for a, b in zip(vals, vals[1:]))
    rng = np.random.default_rng(1)
    d = sel.dsr_from_returns(rng.normal(0.003, 0.01, 1000), 50, 0.001)
    assert 0 < d["dsr"] < 1 and d["sr0"] > 0


def test_effective_trials_cluster_and_entropy():
    rng = np.random.default_rng(2)
    T = 1000
    base = rng.standard_normal((T, 5))
    # 5개 독립 요인 × 각 4개 변형(상관 ~0.95) + 무거래 열 2개
    cols = [base[:, k] + 0.3 * rng.standard_normal(T) for k in range(5) for _ in range(4)]
    R = np.column_stack(cols + [np.zeros(T), np.zeros(T)])
    assert sel.effective_trials_cluster(R) == 6  # 5 군집 + 무거래 1
    ent = sel.effective_trials_entropy(R[:, :20])
    assert 4 < ent < 8
    indep = rng.standard_normal((T, 20))
    assert sel.effective_trials_cluster(indep) == 20
    assert sel.effective_trials_entropy(indep) > 17
    labels = sel.correlation_clusters(R[:, :20])
    assert sel.trial_sharpe_variance(R[:, :20], labels) > 0


def test_pbo_noise_near_half():
    rng = np.random.default_rng(3)
    res = sel.pbo_cscv(rng.normal(0, 0.01, (1600, 50)), n_splits=16)
    assert res["n_combinations"] == 12870
    assert 0.35 <= res["pbo"] <= 0.65
    assert res["logits"].shape == (12870,)


def test_pbo_low_with_persistent_strategy():
    rng = np.random.default_rng(4)
    M = rng.normal(0, 0.01, (1600, 50))
    M[:, 7] += 0.004  # 진짜 지속 엣지 (일간 SR 0.4)
    res = sel.pbo_cscv(M, n_splits=16)
    assert res["pbo"] < 0.05
    assert np.median(res["logits"]) > 2


def test_pbo_validation():
    with pytest.raises(ValueError):
        sel.pbo_cscv(np.zeros((100, 5)), n_splits=5)
