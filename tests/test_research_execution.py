"""연구 계층의 지정가 집행 (`execution`) — 캠페인 격자·신호 캐시·G2 귀무·G3 인접·견고성·페이퍼 훅.

신호 캐시는 fast.prepare 를 직접 쓰므로 집행을 명시적으로 넘겨야 한다(조용히 시장가로 평가되면 안 된다).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest import fast
from btc_quant.backtest.types import LIMIT_FILL_DEFAULT
from btc_quant.evaluation import gates as G
from btc_quant.research import nulls
from btc_quant.research.campaign import (CampaignError, CampaignTemplate, ResearchEnv, get_cost, load_segment,
                                         run_campaign)
from btc_quant.research.pipeline import neighbor_specs, run_pipeline
from btc_quant.research.robustness import robustness_suite
from btc_quant.strategy.spec import BlockRef, ExecutionSpec, ExitSpec, StrategySpec

from test_research_synth import SPLITS, make_market, template_dict

LIMIT = {"entry": "limit", "offset_bp": [2, 5], "tif_bars": [1, 2], "take_profit": "limit"}


def tpl_dict(name="lim", **over):
    d = template_dict(name=name)
    d["entry"] = {"block": "donchian_breakout", "grid": {"lookback": [48]}}
    d["filters"] = [[]]
    d["exits"] = {"sl_pct": [0.6], "tp_pct": [0.8, 1.2], "max_hold_min": [60]}
    d["execution"] = dict(LIMIT)
    d.setdefault("costs", {})["stress"] = "coinone_base"
    d.update(over)
    return d


def make_tick_root(tmp, tick=1000.0):
    """합성 돌파 시장을 호가 단위 격자(1,000원 ≈ 2bp)에 맞춘 저장소 — 추정 tick 이 0 이면 지정가가 걸리지 않으므로."""
    import yaml

    from btc_quant.data.store import write_ohlcv
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "splits.yaml").write_text(yaml.safe_dump(SPLITS))
    df = make_market(True, 7)
    for c in ("open", "close"):
        df[c] = (df[c] / tick).round() * tick
    df["high"] = np.maximum((df["high"] / tick).round() * tick, df[["open", "close"]].max(axis=1))
    df["low"] = np.minimum((df["low"] / tick).round() * tick, df[["open", "close"]].min(axis=1))
    write_ohlcv(df, "coinone", "BTC-KRW", "1m", root=tmp / "data" / "raw")
    return tmp


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return make_tick_root(tmp_path_factory.mktemp("lim"))


def test_template_execution_grid_and_refusals():
    t = CampaignTemplate.from_dict(tpl_dict())
    xs = t.execution_combos()
    assert [(x.offset_bp, x.tif_bars) for x in xs] == [(2.0, 1), (2.0, 2), (5.0, 1), (5.0, 2)]
    assert all(x.limit_entry and x.limit_tp for x in xs)
    specs = t.enumerate()
    assert len(specs) == t.n_specs() == 2 * 4 and len({s.logic_hash() for s in specs}) == 8
    assert all(not s.execution.is_default for s in specs) and t.to_record()["execution"]["offset_bp"] == [2.0, 5.0]
    # 시장가 템플릿: 집행 없음 = 이전과 같은 스펙·해시
    m = CampaignTemplate.from_dict(template_dict())
    assert m.execution == {} and m.execution_combos() == [ExecutionSpec()]
    assert all("execution" not in s.to_dict() for s in m.enumerate())
    tp_only = CampaignTemplate.from_dict(tpl_dict(execution={"take_profit": "limit"}))
    assert tp_only.execution_combos() == [ExecutionSpec(take_profit="limit")]
    for bad, msg in (({"entry": "limit"}, "exactly one of"), ({"entry": "limit", "offset_bp": [2], "nope": 1},
                                                              "unknown keys"),
                     ({"entry": "market", "offset_bp": [2]}, "only valid with entry: limit"),
                     ({"entry": "stop"}, "entry must be one of"), ({"entry": "limit", "offset_bp": [-1]}, "≥ 0"),
                     ({"entry": "limit", "offset_bp": [2], "tif_bars": [0]}, "positive integers"), ("limit", "mapping")):
        with pytest.raises(CampaignError, match=msg):
            CampaignTemplate.from_dict(tpl_dict(execution=bad))
    # 선별 템플릿은 집행을 지원하지 않는다 → 조용히 무시하지 않고 거부
    from btc_quant.research.signal_study import ScreenTemplate
    with pytest.raises(CampaignError, match="unknown screen template keys"):
        ScreenTemplate.from_dict({"name": "s", "hypothesis": {"text": "t", "rationale": "r", "success_criteria": "c"},
                                  "signals": [{"entry": {"block": "donchian_breakout", "params": {"lookback": 48}}}],
                                  "execution": LIMIT, "budget": 5})


def test_campaign_evaluates_limit_execution_not_market(root):
    env = ResearchEnv.at(root)
    cr = run_campaign(CampaignTemplate.from_dict(tpl_dict(name="limcamp")), env=env)
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    s, e = env.data.segment_bounds("dev")
    cost = get_cost("coinone_conservative")
    for lh, spec in list(cr.specs.items())[:4]:
        res = fast.run_fast(spec, m1, cost, s, e)                     # 스펙 집행을 따르는 공개 경로
        assert len(res.trades) > 50                                   # 지정가가 실제로 체결된다
        np.testing.assert_allclose(cr.returns[lh].to_numpy(), res.daily_returns.to_numpy(), rtol=0, atol=1e-15)
        mkt = fast.run_fast(StrategySpec(spec.id, spec.entry, spec.exit, spec.filters), m1, cost, s, e)
        assert not np.array_equal(mkt.daily_returns.to_numpy(), res.daily_returns.to_numpy())
    row = cr.primary().iloc[0]
    assert "exec.offset_bp" in cr.results.columns and "exec.tif_bars" in cr.results.columns
    assert cr.manifest["execution"]["n_variants"] == 4 and cr.manifest["execution"]["fill_model"]["through_ticks"] == 1
    assert json.loads(row["spec_json"])["execution"]["entry"] == "limit"
    # G3 인접: 집행 격자 한 칸 이동 포함, 모두 캠페인 결과표에 있음
    spec = cr.specs[cr.ranked()["logic_hash"].iloc[0]]
    nb = neighbor_specs(cr, spec)
    labels = [lab for lab, _ in nb]
    assert any(lab.startswith("exec.offset_bp=") for lab in labels) and any(lab.startswith("exec.tif_bars=")
                                                                          for lab in labels)
    assert all(n.logic_hash() in cr.primary().index for _, n in nb)
    assert all(n.execution.limit_entry for _, n in nb)
    # 파이프라인: G2 귀무가 같은 집행 (귀무 거래에도 지정가 체결), G6 기준 엔진 일치
    cfg = json.loads(json.dumps(G.load_gate_config()))
    cfg["G2"]["null_runs"] = 40
    out = run_pipeline(cr, env=env, top_k=1, n_null=40, gate_cfg=cfg)
    c = out["candidates"][0]
    assert c["spec"]["execution"]["entry"] == "limit" and c["gates"]["G0"]["passed"]
    if isinstance(c["gates"].get("G6"), dict):
        assert c["gates"]["G6"].get("reference_engine_match") is True


def test_null_honours_execution(root):
    env = ResearchEnv.at(root)
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    from btc_quant.strategy.features import to_tf
    bars = to_tf(m1, 5)
    fok = bars["complete"].to_numpy(bool)
    s, e = env.data.segment_bounds("dev")
    xe = ExecutionSpec(entry="limit", offset_bp=3.0, take_profit="limit")
    cost = get_cost("coinone_conservative")
    gen_m = nulls.RandomEntryNull(m1, bars.index, fok, 5, None, bars)
    gen_l = nulls.RandomEntryNull(m1, bars.index, fok, 5, None, bars, xe, LIMIT_FILL_DEFAULT)
    _, _, si, ei = gen_l.prepared.bounds(s, e)
    tm = gen_m.simulate(0.02, 1, 0.6, 0.8, 60, cost, si, ei)
    tl = gen_l.simulate(0.02, 1, 0.6, 0.8, 60, cost, si, ei)
    assert not tm.entry_maker.any() and tl.entry_maker.all() and len(tl.net_ret) > 0
    # 같은 무작위 진입 시각 + 같은 집행으로 엔진 공개 경로와 비트 단위 일치
    pr = gen_l.prepared
    from dataclasses import replace
    ref = fast.simulate_prepared(replace(pr, cand=gen_l.candidates(0.02, 1)), 0.6, 0.8, 60, cost, si, ei,
                                 execution=xe, fill=LIMIT_FILL_DEFAULT)
    np.testing.assert_array_equal(ref.net_ret, tl.net_ret)
    nd = nulls.null_distribution(m1, bars.index, np.ones(len(bars), bool), fok, 0.6, 0.8, 60, cost, s, e,
                                 n_seeds=5, calibration="signal_rate", execution=xe, fill=LIMIT_FILL_DEFAULT)
    nd0 = nulls.null_distribution(m1, bars.index, np.ones(len(bars), bool), fok, 0.6, 0.8, 60, cost, s, e,
                                  n_seeds=5, calibration="signal_rate")
    assert not np.array_equal(nd["sharpe_w"], nd0["sharpe_w"])


def test_robustness_latency_with_atr_offset_limit(root):
    env = ResearchEnv.at(root)
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    s, e = env.data.segment_bounds("dev")
    spec = StrategySpec("rl", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(0.6, 0.8, 60),
                        execution=ExecutionSpec(entry="limit", offset_atr=0.3, take_profit="limit"))
    out = robustness_suite(spec, m1, s, e, get_cost("coinone_conservative"), checks=("latency", "cost", "outliers"))
    lat = out["checks"]["latency"]
    assert out["base"]["n_trades"] > 0
    assert all(r["n_trades"] > 0 for r in lat["scenarios"])         # 지연 후에도 ATR 오프셋 지정가가 걸린다


# ------------------------------------------------------------------ 페이퍼 훅 (ENGINE_SPEC "페이퍼 훅")
def _grid(x, tick=1000.0):
    return float(round(x / tick) * tick)


@pytest.mark.parametrize("limit_fill", ["bar", "queue"])
def test_paper_limit_execution_hooks(tmp_path, limit_fill):
    import asyncio

    from btc_quant.live.risk import RiskLimits
    from btc_quant.paper import service as S
    from btc_quant.paper.trader import STATE_VERSION
    from test_paper_bars import synth_trades
    from test_paper_service import Clock, FakeCollector, FakeNotifier
    from test_paper_trader import book_at, synth_raw
    t0 = pd.Timestamp("2026-11-12 23:00", tz="UTC")
    spec = StrategySpec("svc_lim", BlockRef("random_entry", {"p": 0.5, "seed": 1}), ExitSpec(0.1, 0.1, 10),
                        execution=ExecutionSpec(entry="limit", offset_bp=2.0, tif_bars=2, take_profit="limit"))
    rng = np.random.default_rng(3)
    raw = synth_raw(rng, t0 - pd.Timedelta(days=4), 4 * 1440 + 10)
    for c in ("open", "high", "low", "close"):
        raw[c] = (raw[c] / 1000).round() * 1000
    raw["high"] = raw[["open", "high", "close"]].max(axis=1)
    raw["low"] = raw[["open", "low", "close"]].min(axis=1)

    def fetch(market, unit, start, end, progress=False):
        return raw[(raw.index >= start) & (raw.index < end)]
    slot = S.StrategySlot(spec, "spec", "B", RiskLimits(-0.5, -0.5, -0.5, 1000))
    cfg = S.ServiceConfig(runtime_dir=tmp_path / "rt", heartbeat_seconds=1, summary_hour_kst=9, limit_fill=limit_fill)
    clock = Clock(t0.timestamp() - 30)
    svc = S.PaperService([slot], cfg, notifier=FakeNotifier(), collector=FakeCollector(), fetch=fetch, clock=clock)
    tr = svc.traders["svc_lim_v1"]
    assert tr.limit and tr.engine.execution == spec.execution
    assert (tr.engine.fill_oracle is not None) == (limit_fill == "queue")
    trades = [(ts, _grid(p), q) for ts, p, q in synth_trades(rng, t0 - pd.Timedelta(seconds=30), 150,
                                                             p0=float(raw["close"].iloc[-1]), wick_frac=0.0)]
    for ts_ms, p, q in trades:
        clock.t = ts_ms / 1000
        svc.on_event("coinone", "orderbook", {**book_at(p, spread=2000, step=1000, ts_recv=clock.t), "ts": ts_ms})
        svc.on_event("upbit", "trades", {"price": p, "ts_exchange": ts_ms})
        svc.on_event("coinone", "trades", {"ts_exchange": ts_ms, "price": p, "qty": q, "side": "buy"})
        if not svc.ready and clock.t >= t0.timestamp() + 5:
            asyncio.run(svc.warmup())
        svc.process(clock.t)
    clock.t = trades[-1][0] / 1000 + 120
    svc.process(clock.t)
    fills = S.read_jsonl(svc.cfg.out / "svc_lim_v1" / "fills.jsonl")
    buys = [f for f in fills if f.get("side") == "buy"]
    assert tr.n_decisions >= 28 and buys and all(f.get("maker") for f in buys)
    assert all(f["fill_price"] == f["ref_price"] for f in buys)          # 지정가 체결 = 지정가, 호가 대입 없음
    st = json.loads((svc.cfg.out / "svc_lim_v1" / "state.json").read_text())
    assert st["version"] == STATE_VERSION == 2 and st["execution"]["entry"] == "limit" and "order" in st
    if limit_fill == "bar":            # 1분봉 규칙 = 백테스트와 같은 판정 → 기준 엔진과 거래 일치
        rep = S.build_report(svc.cfg.out / "svc_lim_v1", m1=svc.hist.frame())
        t = rep["trades"]
        assert t["n_paper"] == tr.n_trades >= 1 and t["n_identical"] == t["n_paper"] and not t["paper_only"]
    # 재시작 복원: 지정가 주문·포지션 상태가 그대로
    svc.traders["svc_lim_v1"].save_state()
    svc2 = S.PaperService([slot], cfg, notifier=FakeNotifier(), collector=FakeCollector(), fetch=fetch, clock=clock)
    e1, e2 = tr.engine, svc2.traders["svc_lim_v1"].engine
    assert e2.order == e1.order and (e2.position is None) == (e1.position is None)
    if e1.position is not None:
        assert e2.position.tp_order == e1.position.tp_order and e2.position.maker == e1.position.maker


def test_similarity_distinguishes_execution_and_universe():
    from btc_quant.research.lessons import compare_grids
    a = CampaignTemplate.from_dict(tpl_dict()).to_record()
    b = CampaignTemplate.from_dict(tpl_dict(execution=None)).to_record()
    assert compare_grids(a, a) == "exact" and compare_grids(a, b) == "same_blocks"
    c = CampaignTemplate.from_dict(tpl_dict(execution=None, universe=["BTC-KRW", "XRP-KRW"],
                                            data={"exchange": "coinone", "timeframe": "1m"})).to_record()
    assert compare_grids(b, c) == "same_blocks"
