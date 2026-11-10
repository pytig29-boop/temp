"""관문 일간 수익 MTM (PLAN D18) · 사다리 스펙 꼬리 검사 판정 (PLAN D19) — 사용자 승인 2026-11-10, 다음 캠페인부터 전향 적용.

- 빠른 엔진 MTM 행렬 == sizing.mtm_daily_returns (같은 식), 보유가 없으면 실현 기준과 같은 자산 경로.
- 떨어지는 칼 사다리: MTM 낙폭 ≫ 실현 낙폭 (실현 기준은 0).
- 몬테카를로 MDD: troughs=None 이면 기존과 비트 단위 동일, 포지션 중간 저점을 넣으면 더 깊다.
- 전향 적용: 승인 전에 등록된 캠페인은 realized 그대로(재계산 불변), 이후 캠페인은 mtm (매니페스트에 고정).
- 꼬리 검사: tail_enabled='ladder' → 사다리 스펙은 판정, 그 밖은 정보 전용."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from test_engine_differential import synth_m1
from test_engine_limit import REG
from test_research_execution import make_tick_root
from test_research_ladder import tpl_dict

from btc_quant.backtest import fast
from btc_quant.backtest.fast import run_fast
from btc_quant.backtest.sizing import mtm_daily_returns, position_mtm_troughs
from btc_quant.backtest.types import CostModel
from btc_quant.evaluation import gates as G
from btc_quant.evaluation import metrics as M
from btc_quant.evaluation import montecarlo as MC
from btc_quant.params import gate_config_for_campaign, load_registry
from btc_quant.research import universe as U
from btc_quant.research.campaign import (
    CampaignTemplate,
    ResearchEnv,
    campaign_accounting,
    load_segment,
    manifest_accounting,
    run_campaign,
)
from btc_quant.research.pipeline import Pipeline, run_pipeline
from btc_quant.research.robustness import (
    load_thresholds,
    robustness_suite,
    tail_enabled_for,
)
from btc_quant.strategy.spec import BlockRef, ExitSpec, LadderSpec, StrategySpec

ROOT = Path(__file__).resolve().parents[1]
T0 = pd.Timestamp("2026-01-01", tz="UTC")
COST = CostModel("c", 0.0, 2.0, 1.0, 0.0)


def knife_m1() -> pd.DataFrame:
    """100 → 60 (10일 선형 하락) → 100 (10일 반등) → 평탄, 1분봉 30일."""
    n = 30 * 1440
    idx = pd.date_range(T0, periods=n, freq="1min", name="ts")
    t = np.arange(n) / 1440.0
    px = np.where(t < 1, 100.0, np.where(t < 11, 100 - 4 * (t - 1), np.where(t < 21, 60 + 4 * (t - 11), 100.0)))
    m1 = pd.DataFrame({"open": px, "high": px, "low": px, "close": px, "volume": 1.0}, index=idx)
    m1["filled"] = False
    return m1


def knife_spec() -> StrategySpec:
    s = StrategySpec("knife", BlockRef("_lm_at", {"at": [str(T0 + pd.Timedelta(days=1))[:19]]}),
                     ExitSpec(50.0, 2.0, 25 * 1440), timeframe="1m", ladder=LadderSpec(3, step_bp=500.0, exit="average"))
    s.validate(REG)
    return s


# ------------------------------------------------------------------ 엔진 회계
def test_falling_knife_ladder_mtm_drawdown_exceeds_realized():
    m1, spec = knife_m1(), knife_spec()
    s, e = m1.index[0], m1.index[-1] + pd.Timedelta(minutes=1)
    real = run_fast(spec, m1, COST, s, e, registry=REG)
    mtm = run_fast(spec, m1, COST, s, e, registry=REG, accounting="mtm")
    assert real.accounting == "realized" and real.daily_realized is None
    assert mtm.accounting == "mtm" and mtm.daily_realized.equals(real.daily_returns)
    pd.testing.assert_frame_equal(real.trades, mtm.trades)
    assert len(real.trades) == 3 and real.trades["net_ret"].iloc[0] < 0 < real.trades["net_ret"].iloc[2]
    dd_r, dd_m = M.max_drawdown(real.daily_returns)[0], M.max_drawdown(mtm.daily_returns)[0]
    assert dd_r == 0.0 and dd_m > 0.30                                  # 실현 기준은 평가손을 숨긴다
    assert mtm.daily_returns.min() < -0.05 and real.daily_returns.min() >= 0.0
    assert np.prod(1 + mtm.daily_returns) == pytest.approx(np.prod(1 + real.daily_returns), rel=1e-12)
    ref = mtm_daily_returns(mtm.trades, m1["close"], mtm.daily_returns.index, COST)
    np.testing.assert_allclose(mtm.daily_returns.to_numpy(), ref.to_numpy(), rtol=0, atol=1e-14)
    # 몬테카를로: 포지션 중간 최악 평가 = 낙폭 ≈ MTM 낙폭
    tr = position_mtm_troughs(mtm.trades, m1["close"], mtm.daily_returns.index, COST)
    assert tr.shape == (1,) and tr[0] == pytest.approx(-dd_m, abs=0.02)
    assert MC.worst_case_mdd95(U.portfolio_trade_returns(mtm), n_sims=200) == 0.0
    assert MC.worst_case_mdd95(U.portfolio_trade_returns(mtm), n_sims=200, troughs=tr) > 0.3


@pytest.mark.parametrize("seed,ladder", [(1, None), (2, LadderSpec(3, step_bp=80.0)),
                                         (3, LadderSpec(4, step_bp=40.0, exit="per_tranche"))])
def test_fast_mtm_matrix_matches_reference_formula(seed, ladder):
    m1 = synth_m1(np.random.default_rng(seed), 20 * 1440)
    spec = StrategySpec("r", BlockRef("random_entry", {"p": 0.02, "seed": seed}), ExitSpec(4.0, 3.0, 3 * 1440),
                        ladder=ladder)
    s, e = m1.index[0], m1.index[-1] + pd.Timedelta(minutes=1)
    r = run_fast(spec, m1, COST, s, e, accounting="mtm")
    assert len(r.trades) > 5
    ref = mtm_daily_returns(r.trades, m1["close"], r.daily_returns.index, COST)
    np.testing.assert_allclose(r.daily_returns.to_numpy(), ref.to_numpy(), rtol=0, atol=1e-13)
    assert np.prod(1 + r.daily_returns) == pytest.approx(np.prod(1 + r.daily_realized), rel=1e-10)
    # 누적 자산 경로: 기간 끝(모든 분할 청산 후)에서 같다
    eq_m, eq_r = np.cumprod(1 + r.daily_returns.to_numpy()), np.cumprod(1 + r.daily_realized.to_numpy())
    assert np.isclose(eq_m[-1], eq_r[-1], rtol=1e-10)
    tr = position_mtm_troughs(r.trades, m1["close"], r.daily_returns.index, COST)
    assert tr.shape == U.portfolio_trade_returns(r).shape and (tr <= 0).all()


def test_montecarlo_troughs_none_is_unchanged_and_deeper_with_troughs():
    rng = np.random.default_rng(0)
    r = rng.normal(0.002, 0.02, 300)
    a = MC.bootstrap_mdd(r, 500, "iid", seed=1, return_samples=True)
    b = MC.bootstrap_mdd(r, 500, "iid", seed=1, return_samples=True, troughs=None)
    np.testing.assert_array_equal(a["samples"], b["samples"])
    zero = MC.bootstrap_mdd(r, 500, "block", seed=1, return_samples=True, troughs=np.zeros_like(r))
    np.testing.assert_allclose(zero["samples"], MC.bootstrap_mdd(r, 500, "block", seed=1,
                                                                 return_samples=True)["samples"], atol=1e-12)
    t = np.minimum(r, 0) - 0.03
    deep = MC.bootstrap_mdd(r, 500, "iid", seed=1, return_samples=True, troughs=t)
    assert (deep["samples"] >= a["samples"] - 1e-12).all() and deep["mdd_p95"] > a["mdd_p95"]
    with pytest.raises(ValueError):
        MC.bootstrap_mdd(r, 10, troughs=t[:-1])


# ------------------------------------------------------------------ 설정·전향 적용
def test_gate_config_prospective_and_recorded():
    old, new = gate_config_for_campaign("camp-000024"), gate_config_for_campaign("camp-000025")
    assert G.daily_returns_mode(old) == "realized" and G.daily_returns_mode(new) == "mtm"
    assert old["robustness"]["tail_enabled"] is False and new["robustness"]["tail_enabled"] == "ladder"
    assert G.daily_returns_mode({}) == "realized"
    with pytest.raises(ValueError):
        G.daily_returns_mode({"evaluation": {"daily_returns": "cash"}})
    reg = load_registry()
    for key, frm, to in (("evaluation.daily_returns", "realized", "mtm"), ("robustness.tail_enabled", False, "ladder")):
        p = reg.get(key)
        h = p.history[-1]
        assert p.cls == 2 and (h["previous"], h["value"]) == (frm, to) and h["applies_from_campaign"] == 25
        assert h["by"] == "human:user (explicit chat approval 2026-11-10)"
        rec = json.loads((ROOT / "research" / "param_approvals" / f"{h['proposal_id']}.json").read_text())
        assert "explicitly in chat" in rec["note"] and "non-tty" in rec["note"]
    assert manifest_accounting({}) == "realized" and manifest_accounting(None) == "realized"
    assert manifest_accounting({"daily_returns": "mtm"}) == "mtm"


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return make_tick_root(tmp_path_factory.mktemp("mtm"))


def test_old_campaign_recomputation_unchanged(root):
    """승인 전 등록(이 임시 원장의 캠페인 번호 < applies_from) = realized; 매니페스트에 키가 없는 옛 캠페인도 realized →
    파이프라인 재계산이 캠페인 수익 행렬과 비트 단위로 같다."""
    env = ResearchEnv.at(root)
    cr = run_campaign(CampaignTemplate.from_dict(tpl_dict(name="old")), env=env)
    assert campaign_accounting(cr.campaign_id, env) == "realized" and cr.manifest["daily_returns"] == "realized"
    del cr.manifest["daily_returns"]                                      # D18 이전 매니페스트 형태
    p = Pipeline(cr, env, lessons=False, register=False)
    assert p.accounting == "realized"
    for lh, spec in list(cr.specs.items())[:3]:
        res = p.dev_result(spec, cr.template.costs["primary"])
        assert res.accounting == "realized" and res.daily_realized is None
        np.testing.assert_array_equal(cr.returns[lh].to_numpy(), res.daily_returns.to_numpy())


def _mtm_root(tmp: Path) -> Path:
    """설정을 복사하고 D18/D19 변경의 applies_from_campaign 을 1 로 (= 이 임시 원장의 첫 캠페인부터 새 기준)."""
    root = make_tick_root(tmp)
    for f in ("gates.yaml", "params.yaml", "costs.yaml", "exchanges.yaml", "ticks.yaml"):
        shutil.copy(ROOT / "config" / f, root / "config" / f)
    pp = root / "config" / "params.yaml"
    doc = yaml.safe_load(pp.read_text(encoding="utf-8"))
    for e in doc["params"]:
        if e["key"] in ("evaluation.daily_returns", "robustness.tail_enabled"):
            e["history"][-1]["applies_from_campaign"] = 1
    pp.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return root


def test_new_campaign_uses_mtm_everywhere(tmp_path):
    root = _mtm_root(tmp_path / "r")
    env = ResearchEnv.at(root)
    cr = run_campaign(CampaignTemplate.from_dict(tpl_dict(name="new")), env=env)
    assert cr.manifest["daily_returns"] == "mtm"
    assert "mark-to-market" in cr.manifest["position"]["accounting"]
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    s, e = env.data.segment_bounds("dev")
    cost = cr.template.costs["primary"]
    from btc_quant.research.campaign import get_cost
    c = get_cost(cost, root)
    for lh, spec in list(cr.specs.items())[:2]:
        res = fast.run_fast(spec, m1, c, s, e, accounting="mtm")
        np.testing.assert_allclose(cr.returns[lh].to_numpy(), res.daily_returns.to_numpy(), rtol=0, atol=1e-15)
        row = cr.primary().loc[lh]
        assert row["max_dd"] == pytest.approx(M.max_drawdown(res.daily_returns)[0])
        assert row["max_dd"] >= M.max_drawdown(res.daily_realized)[0] - 1e-12
    cfg = gate_config_for_campaign(cr.campaign_id, root=root)
    cfg["G2"]["null_runs"] = 30
    out = run_pipeline(cr, env=env, top_k=1, n_null=30, gate_cfg=cfg, lessons=False)
    assert out["daily_returns"] == "mtm"
    cand = out["candidates"][0]
    g6 = cand["gates"].get("G6")
    if isinstance(g6, dict) and "daily_returns" in g6:
        assert g6["daily_returns"] == "mtm"
    if cand.get("robustness"):
        assert cand["robustness"]["daily_returns"] == "mtm"
        t = cand["robustness"]["checks"]["tail"]
        assert t["has_ladder"] is True and t["enabled"] is True and t["tail_enabled_setting"] == "ladder"
    from btc_quant.research.report import render
    assert "mark-to-market" in render(cr.campaign_id, env=env)


def test_pipeline_g2_null_uses_campaign_accounting(tmp_path, monkeypatch):
    root = _mtm_root(tmp_path / "g2")
    env = ResearchEnv.at(root)
    cr = run_campaign(CampaignTemplate.from_dict(tpl_dict(name="g2", exits={"sl_pct": [0.6], "tp_pct": [0.8],
                                                                            "max_hold_min": [120]},
                                                         ladder={"tranches": [3], "step_bp": [30], "order": "market",
                                                                 "exit": "per_tranche"})), env=env)
    from btc_quant.research import pipeline as PL
    seen = {}
    orig = PL.null_distribution

    def spy(*a, **k):
        seen["accounting"] = k.get("accounting")
        return orig(*a, **k)
    monkeypatch.setattr(PL, "null_distribution", spy)
    p = Pipeline(cr, env, lessons=False, register=False, n_null=5)
    spec = next(iter(cr.specs.values()))
    row = cr.primary().loc[spec.logic_hash()].to_dict()
    g2 = p.g2(spec, row, cr.template.costs["primary"])
    assert seen["accounting"] == "mtm" and g2["cost"] == cr.template.costs["primary"]
    # 몬테카를로 MDD 입력: MTM 캠페인이면 포지션 중간 저점 (실현 캠페인이면 None = 기존 계산)
    res = p.dev_result(spec, cr.template.costs["primary"])
    assert res.accounting == "mtm"
    t = p._mc_troughs(res, p.cache.m1)
    assert t.shape == U.portfolio_trade_returns(res).shape and (t <= 0).all() and t.min() < 0
    assert MC.worst_case_mdd95(U.portfolio_trade_returns(res), 300, troughs=t) >= \
        MC.worst_case_mdd95(U.portfolio_trade_returns(res), 300) - 1e-12
    p.accounting = "realized"
    assert p._mc_troughs(res, p.cache.m1) is None


# ------------------------------------------------------------------ 꼬리 검사: 사다리 스펙만 판정 (D19)
def test_tail_enabled_semantics():
    assert tail_enabled_for({"tail_enabled": "ladder"}, True) is True
    assert tail_enabled_for({"tail_enabled": "ladder"}, False) is False
    assert tail_enabled_for({"tail_enabled": False}, True) is False
    assert tail_enabled_for({"tail_enabled": True}, False) is True
    with pytest.raises(ValueError):
        tail_enabled_for({"tail_enabled": "sometimes"}, True)
    assert load_thresholds(G.load_gate_config())["tail_enabled"] == "ladder"
    assert load_thresholds(gate_config_for_campaign("camp-000024"))["tail_enabled"] is False


def test_tail_binding_for_ladder_specs_only(root):
    env = ResearchEnv.at(root)
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    s, e = env.data.segment_bounds("dev")
    from btc_quant.research.campaign import get_cost
    cost = get_cost("coinone_conservative")
    th = load_thresholds(gate_config_for_campaign("camp-000025"))
    assert th["tail_enabled"] == "ladder" and th["tail_max_worst_day"] == 0.10 and th["tail_max_worst_week"] == 0.15
    assert th["tail_max_cvar5"] == 0.03 and th["tail_max_full_inventory_days"] == 30 and th["tail_min_bear_return"] == -0.20
    th.update(tail_max_worst_day=1e-9)                                  # 무조건 flag 되는 기준
    lad = StrategySpec("t", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(0.6, 0.8, 120),
                       ladder=LadderSpec(3, step_bp=30.0))
    single = StrategySpec("u", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(0.6, 0.8, 120))
    a = robustness_suite(lad, m1, s, e, cost, checks=("tail",), thresholds=th)
    b = robustness_suite(single, m1, s, e, cost, checks=("tail",), thresholds=th)
    assert a["checks"]["tail"]["flag"] and a["checks"]["tail"]["passed"] is False and "tail" in a["failed"]
    assert b["checks"]["tail"]["flag"] and b["checks"]["tail"]["passed"] is True and b["checks"]["tail"]["informational"]
    # MTM 회계에서도 꼬리 검사의 '실현' 값은 실현 기준
    c = robustness_suite(lad, m1, s, e, cost, checks=("tail",), thresholds=th, accounting="mtm")
    assert c["daily_returns"] == "mtm"
    assert c["checks"]["tail"]["worst_day"] == pytest.approx(a["checks"]["tail"]["worst_day"])
    assert c["checks"]["tail"]["mtm_worst_day"] == pytest.approx(a["checks"]["tail"]["mtm_worst_day"])


# ------------------------------------------------------------------ 파산(자산 ≤ 0) 처리 — 두 엔진 일치
def test_mtm_ruin_day_is_minus_one_then_zero_both_engines():
    from btc_quant.backtest import fast as F
    from btc_quant.backtest.sizing import _ruin_safe_returns
    # 거래 2개: 첫 거래가 자산을 정확히 0 으로, 둘째가 그 뒤 — 예전에는 ZeroDivisionError
    eg = np.array([1.0, 1.0]); w = np.array([1.0, 1.0]); r = np.array([-1.0, 0.5])
    ef = np.array([100.0, 100.0]); ed = np.array([0, 2], np.int64); xd = np.array([1, 3], np.int64)
    dc = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
    out = F._mtm_core(eg, w, r, ef, ed, xd, dc, 0.0, 0.0, 5)
    np.testing.assert_allclose(out, [0.0, -1.0, 0.0, 0.0, 0.0])
    # 음수 자산도 같은 규칙 (−100% 아래로 내려가지 않음)
    out2 = F._mtm_core(eg, w, np.array([-1.7, 0.5]), ef, ed, xd, dc, 0.0, 0.0, 5)
    np.testing.assert_allclose(out2, [0.0, -1.0, 0.0, 0.0, 0.0])
    np.testing.assert_allclose(_ruin_safe_returns(np.array([1.0, 1.1, 0.0, -0.2, 0.3])), [0.0, 0.1, -1.0, 0.0, 0.0])
    np.testing.assert_allclose(_ruin_safe_returns(np.array([1.0, -0.5])), [0.0, -1.0])
    np.testing.assert_allclose(_ruin_safe_returns(np.array([1.2, 0.6])), [0.2, -0.5])


def test_mtm_ruin_fast_matches_reference_on_ruinous_strategy():
    m1 = synth_m1(np.random.default_rng(3), 30 * 1440)
    m1["close"] = m1["close"] * np.exp(-np.linspace(0, 6, len(m1)))     # 급락 → 장기 보유 사다리 파산 유도
    m1["open"], m1["high"], m1["low"] = m1["close"], m1["close"] * 1.0005, m1["close"] * 0.9995
    spec = StrategySpec("r", BlockRef("random_entry", {"p": 0.05, "seed": 3}), ExitSpec(50.0, 50.0, 30))
    s, e = m1.index[0], m1.index[-1] + pd.Timedelta(minutes=1)
    heavy = CostModel("heavy", fee_rate=0.05, spread_bps=1000.0, slip_bps=500.0)
    rr = run_fast(spec, m1, heavy, s, e, accounting="mtm")
    ref = mtm_daily_returns(rr.trades, m1["close"], rr.daily_returns.index, heavy)
    np.testing.assert_allclose(rr.daily_returns.to_numpy(), ref.to_numpy(), rtol=0, atol=1e-12)
    d = rr.daily_returns.to_numpy()
    assert (d >= -1.0 - 1e-12).all()
    k = int(np.flatnonzero(d == -1.0)[0])                  # 고회전·고비용 → 부동소수 자산 0 (예전 ZeroDivisionError)
    assert (d[k + 1:] == 0.0).all()


# ------------------------------------------------------------------ 임시 저장소 설정 해석: 캠페인 = 파이프라인 (같은 루트)
@pytest.mark.parametrize("variant", ["own_costs", "no_costs"])
def test_campaign_and_pipeline_read_costs_from_same_root(tmp_path, variant):
    root = _mtm_root(tmp_path / variant)
    cp = root / "config" / "costs.yaml"
    if variant == "own_costs":     # 임시 저장소 고유 비용(코드 저장소와 다름) → 둘 다 이것을 써야 한다
        doc = yaml.safe_load(cp.read_text(encoding="utf-8"))
        name = tpl_dict(name="x").get("costs", {}).get("primary") or "coinone_conservative"
        ent = doc["models"][name]
        rec = next(v for v in ent["versions"] if int(v["version"]) == int(ent["current"]))
        rec["slip_bps"] = float(rec["slip_bps"]) + 1.0
        cp.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    else:                          # 비용 파일 없음 → 둘 다 코드 저장소 비용 (v1 프리셋 아님)
        cp.unlink()
    env = ResearchEnv.at(root)
    cr = run_campaign(CampaignTemplate.from_dict(tpl_dict(name=f"croot-{variant}")), env=env)
    from btc_quant.research.campaign import config_root, get_cost
    cost_name = cr.template.costs["primary"]
    expect_root = root if variant == "own_costs" else ROOT
    assert get_cost(cost_name, config_root(env)) == get_cost(cost_name, expect_root)
    p = Pipeline(cr, env, lessons=False, register=False, n_null=5)
    assert p._cost(cost_name) == get_cost(cost_name, expect_root)
    for lh, spec in list(cr.specs.items())[:2]:
        res = p.dev_result(spec, cost_name)
        np.testing.assert_allclose(cr.returns[lh].to_numpy(), res.daily_returns.to_numpy(), rtol=0, atol=1e-13)
        assert cr.primary().loc[lh]["sharpe"] == pytest.approx(M.sharpe(res.daily_returns), rel=1e-9, abs=1e-12)
