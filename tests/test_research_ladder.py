"""연구 계층의 부분 사이징·분할 매수 사다리 — 템플릿 격자·캠페인·G2 귀무(같은 사다리 구조)·G3 인접·관문의 크기 의존성·
꼬리 위험 지표·견고성 'tail'(기본 정보 전용)·MCP 요약 (PLAN D16, docs/ENGINE_SPEC.md "부분 사이징·분할 매수")."""
from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest import fast
from btc_quant.backtest.types import LIMIT_FILL_DEFAULT
from btc_quant.evaluation import gates as G
from btc_quant.evaluation import metrics as M
from btc_quant.evaluation import selection
from btc_quant.research import nulls
from btc_quant.research.campaign import (CampaignError, CampaignTemplate, ResearchEnv, SignalCache, get_cost,
                                         load_segment, run_campaign)
from btc_quant.research.pipeline import neighbor_specs, run_pipeline
from btc_quant.research.robustness import CHECKS, load_thresholds, robustness_suite
from btc_quant.strategy.features import to_tf
from btc_quant.strategy.spec import BlockRef, ExitSpec, LadderSpec, SizingSpec, StrategySpec

from test_research_execution import make_tick_root
from test_research_synth import template_dict

LADDER = {"tranches": [3, 4], "step_bp": [30, 60], "order": "market", "exit": "per_tranche"}


def tpl_dict(name="lad", **over):
    d = template_dict(name=name)
    d["entry"] = {"block": "donchian_breakout", "grid": {"lookback": [48]}}
    d["filters"] = [[]]
    d["exits"] = {"sl_pct": [0.6], "tp_pct": [0.8, 1.2], "max_hold_min": [120]}
    d["ladder"] = dict(LADDER)
    d.setdefault("costs", {})["stress"] = "coinone_base"
    d.update(over)
    return d


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return make_tick_root(tmp_path_factory.mktemp("lad"))


# ------------------------------------------------------------------ 템플릿
def test_template_ladder_and_sizing_grids():
    t = CampaignTemplate.from_dict(tpl_dict())
    pcs = t.position_combos()
    assert [(lad.tranches, lad.step_bp) for _, lad in pcs] == [(3, 30.0), (3, 60.0), (4, 30.0), (4, 60.0)]
    specs = t.enumerate()
    assert len(specs) == t.n_specs() == 2 * 4 and len({s.logic_hash() for s in specs}) == 8
    assert all(s.ladder is not None and s.ladder.exit == "per_tranche" for s in specs)
    assert t.gridded_mode_params() == 0 and t.to_record()["ladder"]["tranches"] == [3, 4]
    both = CampaignTemplate.from_dict(tpl_dict(ladder={**LADDER, "exit": ["per_tranche", "average"]}))
    assert both.n_specs() == 16 and both.gridded_mode_params() == 1
    sz = CampaignTemplate.from_dict(tpl_dict(ladder=None, sizing={"entry_fraction": [0.5, 1.0]}))
    assert [s.entry_fraction for s, _ in sz.position_combos()] == [0.5, 1.0] and sz.gridded_mode_params() == 1
    # 기본 템플릿: 사이징·사다리 없음 = 이전과 같은 스펙
    m = CampaignTemplate.from_dict(template_dict())
    assert m.position_combos() == [(SizingSpec(), None)]
    assert all("ladder" not in s.to_dict() and "sizing" not in s.to_dict() for s in m.enumerate())


@pytest.mark.parametrize("over,msg", [
    (dict(sizing={"entry_fraction": [0.5]}), "cannot be combined"),
    (dict(ladder={**LADDER, "tranches": [2, 3, 4]}), "at most 2"),
    (dict(ladder={**LADDER, "step_bp": [10, 20, 30, 40]}), "at most 3"),
    (dict(ladder={**LADDER, "tranches": [6]}), r"\[2, 5\]"),
    (dict(ladder={**LADDER, "weights": [1, 2, 3]}), "unknown keys"),
    (dict(ladder={**LADDER, "step_atr": [1.0]}), "exactly one"),
    (dict(ladder={**LADDER, "order": ["market", "limit"]}), "single value"),
    (dict(ladder={**LADDER, "max_exposure": 1.5}), "max_exposure"),
    (dict(ladder={**LADDER, "max_exposure": [0.5, 1.0]}), "single value"),
    (dict(ladder=None, sizing={"entry_fraction": [0.0]}), r"\(0, 1\]"),
    (dict(ladder=None, sizing={"entry_fraction": [0.1, 0.2, 0.3, 0.4]}), "at most 3"),
])
def test_template_refusals(over, msg):
    d = tpl_dict(**{k: v for k, v in over.items() if v is not None})
    for k, v in over.items():
        if v is None:
            d.pop(k, None)
    with pytest.raises(CampaignError, match=msg):
        CampaignTemplate.from_dict(d)


def test_exposure_cap_from_params(monkeypatch):
    import btc_quant.params as P
    monkeypatch.setattr(P, "max_exposure_cap", lambda root=None: 0.5)
    with pytest.raises(CampaignError, match="class ③ cap"):
        CampaignTemplate.from_dict(tpl_dict())
    CampaignTemplate.from_dict(tpl_dict(ladder={**LADDER, "max_exposure": 0.5}))
    assert P.DEFAULT_MAX_EXPOSURE_CAP == 1.0


def test_max_params_binding_is_reported_not_raised():
    """진입 2 + 필터 1 + 청산 3 + 사다리 2 = 8 = MAX_PARAMS; 청산 방식까지 격자로 고르면 9 → 거부 (MAX_PARAMS 는 그대로)."""
    d = tpl_dict(ladder={**LADDER, "exit": ["per_tranche", "average"]})
    d["entry"] = {"block": "bollinger_z_revert", "grid": {"window": [48], "z": [2.5]}}
    d["filters"] = [[{"block": "session_kst", "grid": {"session": ["all_but_quiet"]}}]]
    with pytest.raises(CampaignError, match="MAX_PARAMS"):
        CampaignTemplate.from_dict(d)
    d["ladder"]["exit"] = "per_tranche"
    CampaignTemplate.from_dict(d)


# ------------------------------------------------------------------ 캠페인 → 관문
def test_campaign_pipeline_with_ladder(root):
    env = ResearchEnv.at(root)
    cr = run_campaign(CampaignTemplate.from_dict(tpl_dict(name="ladcamp")), env=env)
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    s, e = env.data.segment_bounds("dev")
    cost = get_cost("coinone_conservative")
    for lh, spec in list(cr.specs.items())[:3]:
        res = fast.run_fast(spec, m1, cost, s, e)                      # 스펙 사다리를 따르는 공개 경로
        assert (res.trades["tranche"] > 0).any()
        np.testing.assert_allclose(cr.returns[lh].to_numpy(), res.daily_returns.to_numpy(), rtol=0, atol=1e-15)
    assert {"ladder.tranches", "ladder.step_bp", "ladder.exit", "n_positions", "worst_day",
            "max_inventory"} <= set(cr.results.columns)
    assert cr.manifest["position"]["n_variants"] == 4 and cr.manifest["position"]["max_exposure_cap"] == 1.0
    row = cr.primary().iloc[0]
    assert row["n_positions"] < row["n_trades"]                        # 분할 행 > 사다리 수
    spec = cr.specs[cr.ranked()["logic_hash"].iloc[0]]
    labels = [lab for lab, _ in neighbor_specs(cr, spec)]
    assert any(x.startswith("ladder.tranches=") for x in labels) and any(x.startswith("ladder.step_bp=")
                                                                       for x in labels)
    assert all(n.logic_hash() in cr.primary().index for _, n in neighbor_specs(cr, spec))
    cfg = json.loads(json.dumps(G.load_gate_config()))
    cfg["G2"]["null_runs"] = 30
    out = run_pipeline(cr, env=env, top_k=1, n_null=30, gate_cfg=cfg)
    c = out["candidates"][0]
    g1, g2 = c["gates"]["G1"], c["gates"]["G2"]
    assert g1["n_trades"] == c["metrics"]["n_positions"] and g1.get("unit") == "positions (ladders)"
    assert g2["candidate_n_positions"] == c["metrics"]["n_positions"]
    assert abs(g2["n_trades_median"] - g2["candidate_n_positions"]) <= 0.15 * g2["candidate_n_positions"] + 2
    if isinstance(c["gates"].get("G6"), dict) and "reference_engine_match" in c["gates"]["G6"]:
        assert c["gates"]["G6"]["reference_engine_match"] is True
    from btc_quant.mcp.tools import ResearchTools, _tail_summary
    ts = _tail_summary(c)
    assert ts["n_positions"] == c["metrics"]["n_positions"] and "worst_day" in ts
    summ = ResearchTools._candidate_summary(c)
    assert summ["tail_risk_dev"]["worst_day"] == c["metrics"]["worst_day"]
    assert "max_inventory" in summ["metrics_dev"] and "n_positions" in summ["metrics_dev"]
    from btc_quant.research.report import render
    md = render(cr.campaign_id, env=env)
    assert "Tail risk (Dev" in md and "ladder.tranches" in md


def test_null_uses_same_ladder_structure(root):
    env = ResearchEnv.at(root)
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    bars = to_tf(m1, 5)
    fok = bars["complete"].to_numpy(bool)
    s, e = env.data.segment_bounds("dev")
    lad = LadderSpec(3, step_bp=30.0, exit="average")
    cost = get_cost("coinone_conservative")
    gen = nulls.RandomEntryNull(m1, bars.index, fok, 5, None, bars, None, LIMIT_FILL_DEFAULT, None, lad)
    _, _, si, ei = gen.prepared.bounds(s, e)
    tr = gen.simulate(0.02, 1, 0.6, 0.8, 120, cost, si, ei)
    assert tr.weight is not None and np.allclose(tr.weight, 1 / 3) and (tr.tranche > 0).any()
    assert tr.n_positions == np.unique(tr.ladder).size < len(tr.net_ret)
    ref = fast.simulate_prepared(replace(gen.prepared, cand=gen.candidates(0.02, 1)), 0.6, 0.8, 120, cost, si, ei,
                                 fill=LIMIT_FILL_DEFAULT, ladder=lad)
    np.testing.assert_array_equal(ref.net_ret, tr.net_ret)
    nd = nulls.null_distribution(m1, bars.index, np.ones(len(bars), bool), fok, 0.6, 0.8, 120, cost, s, e,
                                 n_seeds=8, calibration="trade_count", target_trades=150, ladder=lad,
                                 fill=LIMIT_FILL_DEFAULT)
    assert abs(nd["n_trades_median"] - 150) <= 0.1 * 150     # 독립 포지션 수 맞춤


def test_shrinking_entry_fraction_cannot_trivially_pass_g2_g5(root):
    """G2(같은 사이징 귀무 대비 백분위)·G5(Sharpe 기반 DSR)는 크기에 거의 불변 — entry_fraction 을 줄여 통과할 수 없다.
    낙폭(G6 MDD·MC MDD95)은 크기에 비례해 줄어드는 것이 의도(실제 사이징으로 판정)."""
    env = ResearchEnv.at(root)
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    bars = to_tf(m1, 5)
    s, e = env.data.segment_bounds("dev")
    cost = get_cost("coinone_conservative")
    entry = BlockRef("donchian_breakout", {"lookback": 48})
    out = {}
    cfg = G.load_gate_config()
    for f in (1.0, 0.1):
        spec = StrategySpec("x", entry, ExitSpec(0.6, 0.8, 60), sizing=SizingSpec(f))
        res = fast.run_fast(spec, m1, cost, s, e)
        met = M.summarize(res)
        sc = SignalCache(m1)
        nd = nulls.null_distribution(m1, bars.index, sc.block(entry), sc.filter_ok(()), 0.6, 0.8, 60, cost, s, e,
                                     n_seeds=60, calibration="trade_count", target_trades=int(met["n_positions"]),
                                     sizing=None if f == 1.0 else SizingSpec(f), fill=LIMIT_FILL_DEFAULT)
        ok2, d2 = G.check_g2(met, nd["sharpe_w"], cfg)
        x = res.daily_returns.to_numpy(float)
        dsr = selection.dsr_from_returns(x, 20.0, 0.25)["dsr"]
        out[f] = {"sw": met["sharpe_w"], "p": d2["p_value"], "ok2": ok2, "dsr": dsr, "mdd": met["max_dd"]}
    a, b = out[1.0], out[0.1]
    assert b["sw"] == pytest.approx(a["sw"], abs=0.1 * abs(a["sw"]) + 0.02)
    assert abs(b["p"] - a["p"]) <= 0.1 and b["ok2"] == a["ok2"]
    assert b["dsr"] == pytest.approx(a["dsr"], abs=0.05)
    assert b["mdd"] < 0.2 * a["mdd"] + 1e-9                          # 낙폭은 크기에 비례 (의도)


def test_tail_check_informational_by_default(root):
    env = ResearchEnv.at(root)
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    s, e = env.data.segment_bounds("dev")
    assert "tail" in CHECKS and load_thresholds()["tail_enabled"] is False
    spec = StrategySpec("t", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(0.6, 0.8, 120),
                        ladder=LadderSpec(3, step_bp=30.0))
    th = load_thresholds()
    th.update(tail_max_worst_day=1e-9)                                 # 무조건 flag 되는 기준
    out = robustness_suite(spec, m1, s, e, get_cost("coinone_conservative"), checks=("tail",), thresholds=th)
    t = out["checks"]["tail"]
    assert t["flag"] and t["passed"] is True and t["informational"] and out["passed"]
    assert {"mtm_worst_day", "mtm_cvar5_daily", "max_inventory", "max_inventory_minutes", "bear_total_return"} <= set(t)
    assert t["mtm_worst_day"] <= t["worst_day"] + 1e-12 or t["mtm_worst_day"] <= 0
    th["tail_enabled"] = True                                          # 켜면 판정에 쓰인다 (사람 결정 후)
    out2 = robustness_suite(spec, m1, s, e, get_cost("coinone_conservative"), checks=("tail",), thresholds=th)
    assert out2["checks"]["tail"]["passed"] is False and "tail" in out2["failed"]


def test_tail_metrics_values():
    idx = pd.date_range("2022-01-01", periods=400, freq="D", tz="UTC")
    x = np.zeros(400)
    x[10], x[200] = -0.08, 0.05
    d = pd.Series(x, index=idx)
    t = M.tail_metrics(d)
    assert t["worst_day"] == -0.08 and t["cvar5_daily"] == pytest.approx(-0.08 / 20)
    assert t["bear_n_days"] == 365 and t["bear_total_return"] == pytest.approx(0.92 * 1.05 - 1)
    assert t["max_inventory"] == 0.0


def test_similarity_distinguishes_sizing_and_ladder():
    from btc_quant.research.lessons import compare_grids
    a = CampaignTemplate.from_dict(tpl_dict(name="a")).to_record()
    b = CampaignTemplate.from_dict(tpl_dict(name="b")).to_record()
    plain = dict(a)
    plain.pop("ladder")
    other = CampaignTemplate.from_dict(tpl_dict(name="c", ladder={**LADDER, "order": "limit"})).to_record()
    sz = CampaignTemplate.from_dict({k: v for k, v in tpl_dict(name="d", sizing={"entry_fraction": [0.5]}).items()
                                     if k != "ladder"}).to_record()
    assert compare_grids(a, b) == "exact"
    assert compare_grids(a, plain) == "same_blocks" and compare_grids(a, other) == "same_blocks"
    assert compare_grids(plain, sz) == "same_blocks"
