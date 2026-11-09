"""캠페인 템플릿·실행·원장 기록·귀무 생성기 (합성 데이터, 임시 저장소)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest import fast
from btc_quant.backtest.types import COINONE_CONSERVATIVE
from btc_quant.evaluation.metrics import weighted_sharpe
from btc_quant.registry.ledger import Ledger
from btc_quant.research import campaign as C
from btc_quant.research import cli, nulls
from btc_quant.research.campaign import (CampaignBudgetError, CampaignError, CampaignTemplate,
                                         ResearchEnv, SignalCache, run_campaign)
from btc_quant.research.pipeline import neighbor_specs
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

from test_research_synth import make_root, template_dict


@pytest.fixture(scope="module")
def rw_root(tmp_path_factory):
    return make_root(tmp_path_factory.mktemp("rw"), planted=False, seed=11)


# ------------------------------------------------------------------ 템플릿
def test_template_defaults_and_enumeration():
    d = template_dict()
    d["entry"] = {"block": "atr_channel_breakout", "grid": {"mult": [2.0, 3.0]}}   # span = 블록 기본 격자
    tpl = CampaignTemplate.from_dict(d)
    assert tpl.entry.grid == {"span": [12, 24, 48], "mult": [2.0, 3.0]}
    assert tpl.family == "atr_channel_breakout"
    specs = tpl.enumerate()
    assert len(specs) == tpl.n_specs() == 3 * 2 * 2 * (2 * 2 * 1)
    assert len({s.logic_hash() for s in specs}) == len(specs)
    for s in specs:
        s.validate(C.REGISTRY)


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d["entry"].update(block="vol_regime"), "used as entry"),
    (lambda d: d["entry"].update(grid={"nope": [1]}), "unknown params"),
    (lambda d: d.update(filters=[[{"block": "vol_regime"}, {"block": "volume_z"},
                                  {"block": "session_kst"}]]), "filters > 2"),
    (lambda d: d.update(entry={"block": "atr_channel_breakout"},
                        filters=[[{"block": "vol_regime"}, {"block": "volume_z"}]]), "params 9 > 8"),
    (lambda d: d["exits"].update(max_hold_min=[62]), "multiples of 5"),
    (lambda d: d["exits"].update(sl_pct=[0.5, 0.5]), "duplicate"),
    (lambda d: d.update(data={"exchange": "coinone", "symbol": "BTC-KRW", "aux": ["binance"]}), "aux"),
    (lambda d: d.update(costs={"primary": "nope"}), "unknown cost"),
    (lambda d: d["hypothesis"].update(text=""), "hypothesis.text"),
    (lambda d: d.update(segment="val"), "dev segment"),
])
def test_template_validation(mutate, msg):
    d = template_dict()
    mutate(d)
    with pytest.raises(CampaignError, match=msg):
        CampaignTemplate.from_dict(d)


def test_budget_enforced_before_any_ledger_write(tmp_path):
    env = ResearchEnv.at(tmp_path)
    tpl = CampaignTemplate.from_dict(template_dict(budget=10))   # 24 specs
    with pytest.raises(CampaignBudgetError, match="24 specs > budget 10"):
        run_campaign(tpl, env=env)
    assert env.ledger.records("hypotheses") == [] and env.ledger.records("campaigns") == []


def test_repo_templates_valid():
    from pathlib import Path
    files = sorted((Path(__file__).resolve().parents[1] / "research" / "campaign_templates").glob("*.yaml"))
    assert len(files) >= 2
    for f in files:
        tpl = CampaignTemplate.from_yaml(f)
        assert tpl.check_budget() <= tpl.budget
        assert tpl.costs["primary"] == "coinone_conservative" and tpl.costs["grade"] == "upbit_conservative"


# ------------------------------------------------------------------ 실행·원장
def test_ledger_before_runs_and_every_run_recorded(rw_root, monkeypatch):
    env = ResearchEnv.at(rw_root)
    seen = {}
    orig_sim, orig_load = fast.simulate_many, C.load_segment

    def load_spy(env_, *a, **k):
        seen.setdefault("load", (len(env_.ledger.records("hypotheses")), len(env_.ledger.records("campaigns")),
                                 len(env_.ledger.records("runs"))))
        return orig_load(env_, *a, **k)

    def sim_spy(*a, **k):
        seen.setdefault("sim", (len(env.ledger.records("campaigns")), len(env.ledger.records("runs"))))
        return orig_sim(*a, **k)

    monkeypatch.setattr(C, "load_segment", load_spy)
    monkeypatch.setattr(C.fast, "simulate_many", sim_spy)
    tpl = CampaignTemplate.from_dict(template_dict(name="ledger_order"))
    cr = run_campaign(tpl, env=env)
    assert seen["load"] == (1, 1, 0)      # 가설·캠페인 등록 후, 실행 기록 전에 데이터 로드
    assert seen["sim"] == (1, 0)
    camp = env.ledger.get("campaigns", cr.campaign_id)
    runs = [r for r in env.ledger.records("runs") if r["campaign_id"] == cr.campaign_id]
    assert len(runs) == tpl.n_specs() * 2              # 스펙 × (primary, grade)
    assert {r["cost_name"] for r in runs} == {"coinone_conservative", "upbit_conservative"}
    assert len({r["logic_hash"] for r in runs}) == tpl.n_specs() == camp["budget"]
    assert all(r["data_snapshot"].startswith("snap-") and r["code_commit"] for r in runs)
    assert all(r["ts"] >= camp["ts"] for r in runs) and camp["seq"] == 1
    assert [r["seq"] for r in env.ledger.records("runs")] == list(range(1, len(env.ledger.records("runs")) + 1))
    assert env.ledger.trials_count(campaign_id=cr.campaign_id) == tpl.n_specs()
    # 결과 저장물
    assert (cr.dir / "results.parquet").exists() and (cr.dir / "returns_coinone_conservative.parquet").exists()
    assert (cr.dir / "summary_top50.csv").exists() and "*.parquet" in (cr.dir / ".gitignore").read_text()
    man = json.loads((cr.dir / "manifest.json").read_text())
    assert man["n_runs"] == len(runs) and man["data_snapshot"] == camp["period"]["data_snapshot"]
    # 파라미터 거버넌스 버전 (docs/PARAMS.md §4): 매니페스트와 모든 실행 기록에
    from btc_quant.params import cost_versions, params_snapshot
    snap = params_snapshot()
    assert man["params_version"] == snap["params_version"] and len(man["params_version"]) == 12
    assert man["cost_versions"] == cost_versions() and man["params"]["params_version"] == snap["params_version"]
    assert all(r["params_version"] == snap["params_version"] and r["cost_versions"] == cost_versions()
               for r in runs)
    # 원장 레코드 형식이 Ledger.record_run 과 같은지
    lg = Ledger(rw_root / "ledger_fmt")
    h = lg.register_hypothesis("t", "r", "s")
    c2 = lg.create_campaign(h, {}, 5, ["coinone_conservative"], "dev", family="donchian_breakout")
    lg.record_run(c2, runs[0]["spec"], "coinone_conservative", runs[0]["period"], "snap-x", "abc", {"a": 1},
                  params_version="x" * 12, cost_versions={"coinone_conservative": 1})
    assert set(lg.records("runs")[0]) == set(runs[0])
    # 저장된 수익 행렬 == 엔진 직접 실행
    lh = cr.ranked()["logic_hash"].iloc[0]
    spec = cr.specs[lh]
    m1 = C.load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    s, e = env.data.segment_bounds("dev")
    res = fast.run_fast(spec, m1, C.get_cost("coinone_conservative"), s, e)   # 레지스트리 현재 버전
    np.testing.assert_allclose(res.daily_returns.to_numpy(), cr.returns[lh].to_numpy(), rtol=0, atol=1e-15)
    loaded = C.load_campaign(cr.campaign_id, env=env)
    assert loaded.specs[lh] == spec and len(loaded.results) == len(cr.results)
    # CLI list
    assert cli.main(["--root", str(rw_root), "list"]) == 0


def test_signal_cache_matches_build_signals(rw_root):
    env = ResearchEnv.at(rw_root)
    m1 = C.load_segment(env, "dev", "coinone", "BTC-KRW", "1m").iloc[: 60 * 24 * 20]
    cache = SignalCache(m1)
    spec = StrategySpec("x", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(0.8, 1.0, 60),
                        (BlockRef("vol_regime", {"window": 24, "min_pct": 0.5}),
                         BlockRef("session_kst", {"session": "all_but_quiet"})))
    a, b = cache.prepared(spec.entry, spec.filters), fast.prepare_spec(spec, m1)
    np.testing.assert_array_equal(a.cand, b.cand)


# ------------------------------------------------------------------ 귀무
def test_null_fast_path_equals_random_entry_block(rw_root):
    env = ResearchEnv.at(rw_root)
    m1 = C.load_segment(env, "dev", "coinone", "BTC-KRW", "1m").iloc[: 60 * 24 * 30]
    cache = SignalCache(m1)
    filt = (BlockRef("session_kst", {"session": "night"}),)
    gen = nulls.RandomEntryNull(m1, cache.bars.index, cache.filter_ok(filt))
    s, e = m1.index[0], m1.index[-1]
    for seed in (0, 5):
        spec = StrategySpec("n", BlockRef("random_entry", {"p": 0.05, "seed": seed}), ExitSpec(0.5, 0.7, 30), filt)
        ref = fast.run_fast(spec, m1, COINONE_CONSERVATIVE, s, e)
        p = fast.prepare_spec(spec, m1)
        np.testing.assert_array_equal(gen.candidates(0.05, seed), p.cand)
        _, _, si, ei = p.bounds(s, e)
        tr = gen.simulate(0.05, seed, 0.5, 0.7, 30, COINONE_CONSERVATIVE, si, ei)
        assert len(tr.net_ret) == len(ref.trades)
        np.testing.assert_array_equal(tr.net_ret, ref.trades["net_ret"].to_numpy())


def test_weighted_sharpe_rows_matches_metrics():
    rng = np.random.default_rng(0)
    days = pd.date_range("2023-01-01", periods=400, freq="D", tz="UTC")
    X = rng.normal(0.001, 0.01, (3, 400))
    X[2] = 0.0
    got = nulls.weighted_sharpe_rows(X, days)
    for i in range(3):
        assert got[i] == pytest.approx(weighted_sharpe(pd.Series(X[i], index=days), 365).sharpe, abs=1e-10)


def test_null_calibration_signal_rate_and_trade_count(rw_root):
    env = ResearchEnv.at(rw_root)
    m1 = C.load_segment(env, "dev", "coinone", "BTC-KRW", "1m").iloc[: 60 * 24 * 60]
    cache = SignalCache(m1)
    entry = cache.block(BlockRef("donchian_breakout", {"lookback": 48}))
    fok = cache.filter_ok(())
    s, e = m1.index[0], m1.index[-1]
    cal = nulls.calibrated_rate(entry, fok, cache.bars.index, s, e)
    assert 0 < cal["p"] < 0.2 and cal["n_entry_bars"] == pytest.approx(cal["p"] * cal["n_filter_bars"])
    sr = nulls.null_distribution(m1, cache.bars.index, entry, fok, 0.8, 1.0, 60, COINONE_CONSERVATIVE, s, e,
                                 n_seeds=50, calibration="signal_rate")
    assert sr["p"] == cal["p"] and sr["sharpe_w"].shape == (50,)
    target = int(sr["n_trades_median"] * 0.6)
    tc = nulls.null_distribution(m1, cache.bars.index, entry, fok, 0.8, 1.0, 60, COINONE_CONSERVATIVE, s, e,
                                 n_seeds=50, calibration="trade_count", target_trades=target)
    assert tc["p"] < sr["p"]
    assert abs(tc["n_trades_median"] - target) <= 0.1 * target


# ------------------------------------------------------------------ G3 인접
def test_neighbor_definition():
    d = template_dict()
    d["filters"] = [[{"block": "vol_regime", "grid": {"window": [12, 24], "min_pct": [0.33, 0.5, 0.67]}}]]
    d["exits"] = {"sl_pct": [0.5, 0.8, 1.2], "tp_pct": [1.0], "max_hold_min": [30, 60]}
    tpl = CampaignTemplate.from_dict(d)

    class Fake:
        template = tpl
    spec = tpl.make_spec(BlockRef("donchian_breakout", {"lookback": 48}),
                         (BlockRef("vol_regime", {"window": 24, "min_pct": 0.5}),), 0.8, 1.0, 30)
    nb = dict(neighbor_specs(Fake, spec))
    assert set(nb) == {"entry.lookback=24", "entry.lookback=96", "vol_regime.window=12",
                       "vol_regime.min_pct=0.33", "vol_regime.min_pct=0.67", "sl_pct=0.5", "sl_pct=1.2",
                       "max_hold_min=60"}
    grid = {s.logic_hash() for s in tpl.enumerate()}
    assert all(s.logic_hash() in grid for s in nb.values())
