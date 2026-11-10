"""검정력·예산 계산기 (research/power.py)."""
from __future__ import annotations

import json
import math
from itertools import pairwise

import numpy as np
import pytest
from scipy import stats

from btc_quant.evaluation import selection
from btc_quant.registry.ledger import Ledger
from btc_quant.research import power as P

T = 1764     # Dev 2021-01-01 ~ 2025-11-01 일수 근사


def test_single_trial_hand_value():
    # N=1 → SR*=0. 정규(왜도0, 첨도3): SR·√(T−1)/√(1+SR²/2) = Φ⁻¹(0.95) 를 손으로 풀면
    z = stats.norm.ppf(0.95)
    sr = z / math.sqrt(T - 1 - z ** 2 / 2)
    assert P.required_sharpe(T, 1, min_dsr=0.95) == pytest.approx(sr * math.sqrt(365), rel=1e-6)
    assert P.required_sharpe(T, 1, min_dsr=0.95) == pytest.approx(0.749, abs=0.002)


def test_ten_trials_matches_plan_range():
    # PLAN §10b: 실질 시도 10회 1.5~2.3 (V = 1/T … 4/T)
    lo = P.required_sharpe(T, 10, 1 / T, min_dsr=0.95)
    hi = P.required_sharpe(T, 10, 4 / T, min_dsr=0.95)
    assert 1.4 < lo < 1.6 and 2.1 < hi < 2.3
    # 해에서 DSR 이 정확히 문턱
    d = selection.deflated_sharpe_ratio(lo / math.sqrt(365), T, 0.0, 3.0, 10, 1 / T)
    assert d == pytest.approx(0.95, abs=1e-6)


def test_monotonicity():
    ns = [1, 5, 20, 100, 1000]
    req = [P.required_sharpe(T, n, min_dsr=0.95) for n in ns]
    assert all(a < b for a, b in pairwise(req))
    vs = [P.required_sharpe(T, 100, m / T, min_dsr=0.95) for m in (1, 2, 4, 8)]
    assert all(a < b for a, b in pairwise(vs))
    # 같은 V(절대값) 에서 표본이 길수록 요구 Sharpe 감소
    days = [365, 730, 1764, 3650]
    dd = [P.required_sharpe(d, 50, 1 / 1764, min_dsr=0.95) for d in days]
    assert all(a > b for a, b in pairwise(dd))
    # 음의 왜도·두꺼운 꼬리 → 더 높은 Sharpe 필요
    assert P.required_sharpe(T, 50, skew=-1.0, kurtosis=10.0, min_dsr=0.95) > P.required_sharpe(T, 50, min_dsr=0.95)
    # 문턱이 높을수록
    assert P.required_sharpe(T, 50, min_dsr=0.99) > P.required_sharpe(T, 50, min_dsr=0.95)


def test_max_trials_is_inverse():
    for s in (1.5, 2.0, 3.0):
        n = P.max_trials(s, T, min_dsr=0.95)
        assert P.required_sharpe(T, n, min_dsr=0.95) == pytest.approx(s, rel=1e-5)
    assert P.max_trials(2.0, T, min_dsr=0.95) > P.max_trials(1.8, T, min_dsr=0.95)
    assert P.max_trials(0.5, T, min_dsr=0.95) == 0.0          # 시도 1회로도 부족


def test_power_table_shape():
    df = P.power_table(T, trials=(1, 10, 100), v_mults=(1, 4), min_dsr=0.95)
    assert df.shape == (3, 2) and np.all(np.diff(df.to_numpy(), axis=0) > 0)
    assert (df["V=4/T"] >= df["V=1/T"]).all()
    assert "| n_trials |" in P.table_markdown(df)


def test_effective_trials_default_and_history(tmp_path):
    led = Ledger(tmp_path / "ledger")
    camps = tmp_path / "campaigns"
    est = P.estimate_effective_trials(1280, family="x", ledger=led, campaigns_dir=camps)
    assert est["ratio"] == P.DEFAULT_COMPRESSION and est["n_trials_dsr"] == pytest.approx(64)
    for i, (n, k) in enumerate([(100, 10), (200, 30), (400, 20)]):
        d = camps / f"camp-{i}"
        d.mkdir(parents=True)
        (d / "pipeline.json").write_text(json.dumps(
            {"campaign_stats": {"n_campaign_specs": n, "k_eff_clusters": k, "var_trial_sr": 0.001 * (i + 1)}}))
    est = P.estimate_effective_trials(1000, family="x", ledger=led, campaigns_dir=camps)
    assert est["ratio"] == pytest.approx(0.1) and "history" in est["ratio_source"]
    assert est["n_trials_dsr"] == pytest.approx(100) and est["var_trial_sr_history_median"] == pytest.approx(0.002)
    rep = P.budget_report(1000, T, family="x", target_sharpe=2.0, ledger=led, campaigns_dir=camps,
                          min_dsr=0.95)
    assert rep["var_source"] == "history median"
    assert rep["required_sharpe_ann"] == pytest.approx(P.required_sharpe(T, 100, 0.002, min_dsr=0.95))
    assert rep["max_grid_for_target"] == pytest.approx(rep["max_trials_for_target"] / 0.1)
    assert "G5 통과 최소 연 Sharpe" in rep["markdown"]


def test_effective_trials_small_grid_floor(tmp_path):
    """pm-000007: 4 스펙 단일 군집 격자 — 실현 K/N = 1/4 ≥ 이력 중앙값 0.125 → 압축비 하한 1/n_grid."""
    from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

    led = Ledger(tmp_path / "ledger")
    camps = tmp_path / "campaigns"
    for i, (n, k) in enumerate([(80, 10), (160, 20), (40, 5)]):        # 이력 중앙값 0.125
        d = camps / f"camp-{i}"
        d.mkdir(parents=True)
        (d / "pipeline.json").write_text(json.dumps({"campaign_stats": {"n_campaign_specs": n, "k_eff_clusters": k}}))
    h = led.register_hypothesis("t", "r", "s")
    c = led.create_campaign(h, {}, 100, ["coinone_conservative"], "dev", family="fam")
    for i in range(88):
        sp = StrategySpec(f"s{i}", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(0.5 + i, 1.0, 60))
        led.record_run(c, sp, "coinone_conservative", "dev", "snap", "code", {})
    est = P.estimate_effective_trials(4, family="fam", ledger=led, campaigns_dir=camps)
    assert est["ratio_base"] == pytest.approx(0.125) and est["ratio"] == pytest.approx(0.25)
    assert "1/n_grid" in est["ratio_source"] and est["ratio_floor"] == pytest.approx(0.25)
    # pipeline G5 와 같은 값: N_family·K/N = (88 + 4)·1/4 = 23 (이전 추정 11.5)
    assert est["n_trials_dsr"] == pytest.approx(23.0)
    # 하한은 지정 압축비에도 적용, 큰 격자에는 영향 없음
    assert P.estimate_effective_trials(4, ledger=led, campaigns_dir=camps, ratio=0.05)["ratio"] == pytest.approx(0.25)
    big = P.estimate_effective_trials(1000, family="fam", ledger=led, campaigns_dir=camps)
    assert big["ratio"] == big["ratio_base"] == pytest.approx(0.125) and "floored" not in big["ratio_source"]
    small = P.budget_report(4, T, family="fam", ledger=led, campaigns_dir=camps, min_dsr=0.95, var_trial_sr=1 / T)
    assert small["required_sharpe_ann"] == pytest.approx(P.required_sharpe(T, 23.0, 1 / T, min_dsr=0.95))


def test_family_prior_trials_raise_requirement(tmp_path):
    from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

    led = Ledger(tmp_path / "ledger")
    h = led.register_hypothesis("t", "r", "s")
    c = led.create_campaign(h, {}, 500, ["coinone_conservative"], "dev", family="fam")
    for i in range(400):
        sp = StrategySpec(f"s{i}", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(0.5 + i, 1.0, 60))
        led.record_run(c, sp, "coinone_conservative", "dev", "snap", "code", {})
    a = P.budget_report(200, T, family="fam", ledger=led, campaigns_dir=tmp_path / "none", ratio=0.05)
    b = P.budget_report(200, T, family="other", ledger=led, campaigns_dir=tmp_path / "none", ratio=0.05)
    assert a["n_family_prior"] == 400 and a["n_trials_dsr"] == pytest.approx(30)
    assert b["n_trials_dsr"] == pytest.approx(10) and a["required_sharpe_ann"] > b["required_sharpe_ann"]
