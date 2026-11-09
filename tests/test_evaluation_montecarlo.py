"""evaluation.montecarlo — 거래 부트스트랩 MDD."""
import numpy as np
import pytest

from btc_quant.evaluation import montecarlo as mc


def test_constant_losses_exact_mdd():
    r = mc.bootstrap_mdd([-0.01] * 10, n_sims=50)
    assert r["mdd_p95"] == pytest.approx(1 - 0.99 ** 10)
    assert mc.bootstrap_mdd([0.01] * 10, n_sims=50)["mdd_p95"] == 0.0


def test_percentiles_ordered_and_block():
    rng = np.random.default_rng(0)
    t = rng.normal(0.001, 0.01, 500)
    a = mc.bootstrap_mdd(t, 2000, "iid")
    b = mc.bootstrap_mdd(t, 2000, "block")
    for r in (a, b):
        qs = [r[f"mdd_p{q}"] for q in mc.PERCENTILES]
        assert qs == sorted(qs) and 0 < qs[0] and qs[-1] < 1
    assert b["block_len"] == 8
    assert mc.worst_case_mdd95(t, 1000) >= min(a["mdd_p95"], b["mdd_p95"]) - 0.05


def test_block_preserves_loss_clusters():
    # 연패 군집: block 은 iid 보다 꼬리가 더 두꺼워야 함
    t = np.tile(np.r_[np.full(10, -0.02), np.full(10, 0.021)], 20)
    iid = mc.bootstrap_mdd(t, 3000, "iid", seed=1)["mdd_p95"]
    blk = mc.bootstrap_mdd(t, 3000, "block", block_len=10, seed=1)["mdd_p95"]
    assert blk > iid


def test_samples_and_errors():
    r = mc.bootstrap_mdd([0.01, -0.02, 0.03], 100, return_samples=True)
    assert r["samples"].shape == (100,)
    with pytest.raises(ValueError):
        mc.bootstrap_mdd([0.01], 10, method="foo")
