"""evaluation.inference — 부트스트랩 검정, 귀무 백분위, 순차 Holm."""
import numpy as np
import pytest

from btc_quant.evaluation import inference as inf


def test_bootstrap_small_p_for_strong_positive():
    rng = np.random.default_rng(0)
    r = inf.bootstrap_mean_test(rng.normal(0.003, 0.01, 180), n_boot=2000, seed=1)
    assert r["p_value"] < 0.01 and r["mean_block"] == 6


def test_bootstrap_large_p_for_negative():
    rng = np.random.default_rng(0)
    assert inf.bootstrap_mean_test(rng.normal(-0.003, 0.01, 180), 2000)["p_value"] > 0.95


def test_bootstrap_roughly_uniform_under_null():
    rng = np.random.default_rng(5)
    ps = np.array([inf.bootstrap_mean_test(rng.normal(0, 0.01, 120), 400, seed=i)["p_value"]
                   for i in range(200)])
    assert 0.4 < ps.mean() < 0.6
    assert (ps < 0.10).mean() < 0.2


def test_stationary_indices_shape_and_range():
    idx = inf.stationary_bootstrap_indices(50, 10, 5, np.random.default_rng(0))
    assert idx.shape == (10, 50) and idx.min() >= 0 and idx.max() < 50


def test_null_percentile():
    null = np.arange(1000)  # 0..999
    r = inf.null_percentile(960, null)
    assert r["percentile"] == pytest.approx(960.5 / 1000)
    assert r["p_value"] == pytest.approx((1 + 40) / 1001)
    assert inf.null_percentile(950, null)["p_value"] > 0.05  # 51/1001


def test_holm_reject_basic():
    rej = inf.holm_reject([0.01, 0.04, 0.03, 0.2], alpha=0.10)
    # 정렬 .01<=.025 ✓, .03<=.0333 ✓, .04<=.05 ✓, .2<=.1 ✗
    assert rej.tolist() == [True, True, True, False]
    assert inf.holm_reject([0.03, 0.01], 0.10, m=10).tolist() == [False, True]


def test_lockbox_cap_mode_sequence():
    # 첫 개봉 문턱 α/10 = 0.01
    assert inf.lockbox_holm_decision(0.009)["passed"]
    d = inf.lockbox_holm_decision(0.011)
    assert not d["passed"] and d["threshold"] == pytest.approx(0.01)
    # 이전 통과가 있으면 다음 문턱 α/9
    d2 = inf.lockbox_holm_decision(0.0105, [0.005])
    assert d2["passed"] and d2["threshold"] == pytest.approx(0.1 / 9)
    # 이전 후보가 더 작은 p 로 실패했다면 현재도 실패 (step-down)
    assert not inf.lockbox_holm_decision(0.012, [0.011])["passed"]
    # 단조성: 이미 통과한 p 는 나중 개봉이 추가돼도 Holm 기각 유지
    seq = [0.009, 0.5, 0.003, 0.02, 0.8]
    for k in range(1, len(seq) + 1):
        assert inf.holm_reject(seq[:k], 0.10, m=10)[0]
    with pytest.raises(ValueError):
        inf.lockbox_holm_decision(0.001, [0.5] * 10)
    assert inf.lockbox_holm_decision(0.001, [0.5] * 9)["retire_lockbox"]


def test_lockbox_cumulative_mode_is_looser_at_start():
    assert inf.lockbox_holm_decision(0.05, mode="cumulative")["passed"]
    assert not inf.lockbox_holm_decision(0.05, mode="cap")["passed"]
    assert not inf.lockbox_holm_decision(0.06, [0.5], mode="cumulative")["passed"]  # α/2
