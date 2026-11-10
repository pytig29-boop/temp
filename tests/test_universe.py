"""다자산 유니버스 (PLAN D15): 기본 유니버스 하위 호환 · 풀링 캠페인/선별/파이프라인 · 슬리브 회계 · 자산별 보고.

합성 3자산 시장(BTC/XRP/ETH-KRW, 코인원 + 업비트): 자산마다 다른 seed·가격 수준의 donchian 돌파 드리프트 시장.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from btc_quant.backtest import fast
from btc_quant.data.store import write_ohlcv
from btc_quant.evaluation import gates as G
from btc_quant.evaluation import metrics as M
from btc_quant.research import universe as U
from btc_quant.research.campaign import (CampaignError, CampaignTemplate, ResearchEnv, get_cost, load_segment,
                                         run_campaign, run_screen)
from btc_quant.research.pipeline import public_pipeline, run_pipeline
from btc_quant.research.report import render
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

from test_research_synth import SPLITS, make_market, template_dict

UNI = ["BTC-KRW", "XRP-KRW", "ETH-KRW"]
SCALE = {"BTC-KRW": 1.0, "XRP-KRW": 1 / 20_000, "ETH-KRW": 1 / 25}
SEEDS = {"BTC-KRW": 7, "XRP-KRW": 8, "ETH-KRW": 9}


def make_universe_root(tmp: Path, planted: bool = True, upbit: bool = True) -> Path:
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "splits.yaml").write_text(yaml.safe_dump(SPLITS))
    for sym in UNI:
        df = make_market(planted, SEEDS[sym])
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] * SCALE[sym]
        df["quote_volume"] = df["volume"] * df["close"]
        write_ohlcv(df, "coinone", sym, "1m", root=tmp / "data" / "raw")
        if upbit:
            rng = np.random.default_rng(SEEDS[sym] + 100)
            u = df.copy()
            k = np.exp(rng.normal(0, 0.00003, len(u)))
            for c in ("open", "high", "low", "close"):
                u[c] = u[c] * k
            u["high"] = u[["open", "high", "close"]].max(axis=1)
            u["low"] = u[["open", "low", "close"]].min(axis=1)
            write_ohlcv(u, "upbit", sym, "1m", root=tmp / "data" / "raw")
    return tmp


def uni_template(name="uni", **over) -> dict:
    d = template_dict(name=name)
    d["data"] = {"exchange": "coinone", "timeframe": "1m", "aux": []}
    d["universe"] = list(UNI)
    d["entry"] = {"block": "donchian_breakout", "grid": {"lookback": [24, 48]}}
    d["filters"] = [[]]
    d["exits"] = {"sl_pct": [0.6, 1.0], "tp_pct": [0.8], "max_hold_min": [60]}
    d.setdefault("costs", {})["stress"] = "coinone_base"
    d.update(over)
    return d


@pytest.fixture(scope="module")
def uroot(tmp_path_factory):
    return make_universe_root(tmp_path_factory.mktemp("uni"))


# ------------------------------------------------------------------ 기본 유니버스 = 이전과 동일
def test_spec_default_universe_hash_unchanged():
    e = BlockRef("donchian_breakout", {"lookback": 48})
    s = StrategySpec("x", e, ExitSpec(0.6, 0.8, 60))
    assert "universe" not in s.to_dict()
    # 이 해시는 universe 필드 도입 전 코드로 계산한 값 (2026-11-10)
    assert s.logic_hash() == StrategySpec("y", e, ExitSpec(0.6, 0.8, 60), universe=("BTC-KRW",)).logic_hash()
    m = StrategySpec("x", e, ExitSpec(0.6, 0.8, 60), universe=["BTC-KRW", "XRP-KRW", "ETH-KRW"])
    assert m.universe == ("BTC-KRW", "XRP-KRW", "ETH-KRW") and m.to_dict()["universe"] == UNI
    assert m.logic_hash() != s.logic_hash() and m.n_params() == s.n_params()
    assert StrategySpec.from_dict(m.to_dict()) == m and StrategySpec.from_dict(s.to_dict()) == s


def test_repo_ledger_hashes_reproduce():
    """원장에 기록된 모든 실행의 logic_hash 가 지금 코드로 그대로 재현된다(기본 유니버스 하위 호환)."""
    p = Path(__file__).resolve().parents[1] / "research" / "ledger" / "runs.jsonl"
    if not p.exists():
        pytest.skip("no repo ledger")
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if not r.get("spec") or "entry" not in r["spec"]:
            continue
        assert StrategySpec.from_dict(r["spec"]).logic_hash() == r["logic_hash"], r["id"]
        n += 1
    assert n > 0


def test_template_universe_parsing():
    t = CampaignTemplate.from_dict(template_dict())
    assert t.universe == ("BTC-KRW",) and "universe" not in t.to_record()
    assert all("universe" not in s.to_dict() for s in t.enumerate())
    t3 = CampaignTemplate.from_dict(uni_template(universe=["ETH-KRW", "BTC-KRW", "XRP-KRW"]))
    assert t3.universe == tuple(UNI) and t3.symbol == "BTC-KRW" and t3.to_record()["universe"] == UNI
    assert all(s.universe == tuple(UNI) for s in t3.enumerate())
    assert t3.n_specs() == 4                      # 유니버스는 격자를 늘리지 않는다 (시도 수 그대로)
    for bad, msg in ((["DOGE-KRW"], "fixed asset"), (["BTC-KRW", "BTC-KRW"], "duplicate"), ([], "non-empty")):
        with pytest.raises(CampaignError, match=msg):
            CampaignTemplate.from_dict(uni_template(universe=bad))
    d = uni_template()
    d["data"]["symbol"] = "SOL-KRW"
    with pytest.raises(CampaignError, match="not in universe"):
        CampaignTemplate.from_dict(d)


# ------------------------------------------------------------------ 슬리브 회계
def test_sleeve_accounting():
    days = pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC", name="date")
    from btc_quant.backtest.types import BacktestResult
    from btc_quant.backtest.types import COINONE_BASE as C

    def res(vals, holds):
        tr = pd.DataFrame({"entry_time": days[: len(holds)], "entry_price": 1.0,
                           "exit_time": days[: len(holds)] + pd.Timedelta(hours=1), "exit_price": 1.0,
                           "exit_reason": "tp", "gross_ret": 0.01, "net_ret": [0.01] * len(holds),
                           "hold_min": holds})
        return BacktestResult("s", "h", C, days[0], days[-1], tr, pd.Series(vals, index=days))
    a, b, c = res([0.01, 0, 0, 0.02], [60, 60]), res([0, 0.03, 0, 0], [720]), res([0, 0, 0, 0], [])
    one = U.portfolio_result({"BTC-KRW": a})
    assert one is a                                             # 1개 = 그대로
    p = U.portfolio_result({"BTC-KRW": a, "XRP-KRW": b, "ETH-KRW": c})
    np.testing.assert_allclose(p.daily_returns.to_numpy(), [0.01 / 3, 0.01, 0, 0.02 / 3])
    assert len(p.trades) == 3 and set(p.trades["symbol"]) == {"BTC-KRW", "XRP-KRW"} and p.n_sleeves == 3
    np.testing.assert_allclose(U.portfolio_trade_returns(p), [0.01 / 3] * 3)
    m = M.summarize(p)
    assert m["n_trades"] == 3
    assert m["exposure_time"] == pytest.approx((60 + 60 + 720) / (4 * 1440 * 3))
    assert M.summarize(a)["exposure_time"] == pytest.approx(120 / (4 * 1440))
    bh = U.buy_hold_universe({"A": pd.Series([1.0, 2.0], index=[days[0], days[1]]),
                              "B": pd.Series([1.0, 1.0], index=[days[0], days[1]])}, days[:2])
    np.testing.assert_allclose(bh.to_numpy(), [0.0, 0.5])


# ------------------------------------------------------------------ 풀링 캠페인
@pytest.fixture(scope="module")
def ucamp(uroot):
    env = ResearchEnv.at(uroot)
    cr = run_campaign(CampaignTemplate.from_dict(uni_template()), env=env)
    return env, cr


def test_pooled_campaign_equals_mean_of_sleeves(ucamp):
    env, cr = ucamp
    s, e = env.data.segment_bounds("dev")
    lh = cr.ranked()["logic_hash"].iloc[0]
    spec = cr.specs[lh]
    assert spec.universe == tuple(UNI)
    cost = get_cost("coinone_conservative")
    sleeves = {sym: fast.run_fast(spec, load_segment(env, "dev", "coinone", sym, "1m"), cost, s, e) for sym in UNI}
    port = np.mean([r.daily_returns.to_numpy() for r in sleeves.values()], axis=0)
    np.testing.assert_allclose(cr.returns[lh].to_numpy(), port, rtol=0, atol=1e-15)
    row = cr.primary().loc[lh]
    assert row["n_trades"] == sum(len(r.trades) for r in sleeves.values())        # G1 = 자산 합계
    for sym in UNI:
        assert row[f"asset.{sym}.n_trades"] == len(sleeves[sym].trades)
    # 원장: 스펙 하나 = 시도 1회 (비용 2개 × 스펙), per_asset 기록
    runs = [r for r in env.ledger.records("runs") if r["campaign_id"] == cr.campaign_id]
    assert len(runs) == cr.template.n_specs() * 2
    assert env.ledger.trials_count(campaign_id=cr.campaign_id) == cr.template.n_specs()
    assert set(runs[0]["metrics"]["per_asset"]) == set(UNI) and runs[0]["spec"]["universe"] == UNI
    man = cr.manifest
    assert man["universe"] == UNI and set(man["data"]["by_symbol"]) == set(UNI)
    assert man["benchmark"]["kind"].startswith("equal-weight 3-asset")
    assert all(f"|{sym}:snap-" in man["data_snapshot"] for sym in UNI[1:])
    bh = U.buy_hold_universe({sym: load_segment(env, "dev", "coinone", sym, "1m")["close"] for sym in UNI},
                             cr.returns.index)
    assert man["benchmark"]["bh_total_return"] == pytest.approx(float(np.prod(1 + bh.to_numpy()) - 1))


def test_pooled_pipeline_and_per_asset_report(ucamp):
    env, cr = ucamp
    cfg = G.load_gate_config()
    cfg = json.loads(json.dumps(cfg))
    cfg["G2"]["null_runs"] = 60
    out = run_pipeline(cr, env=env, top_k=1, n_null=60, gate_cfg=cfg)
    c = out["candidates"][0]
    assert out["universe"] == UNI and c["universe"] == UNI and set(c["per_asset"]) == set(UNI)
    g2 = c["gates"]["G2"]
    assert g2["universe"] == UNI and set(g2["p"]) == set(UNI)
    assert sum(g2["candidate_n_trades_by_asset"].values()) == c["metrics"]["n_trades"]
    assert c["gates"]["G0"]["passed"] and c["gates"]["G0"]["universe"] == UNI
    assert c["gates"]["G1"]["passed"]
    assert c["dev_passed"], c.get("first_failed")                 # 3자산 모두 같은 엣지가 심어진 시장
    assert str(c["status"]).startswith("validated"), c["status"]
    if c.get("gates", {}).get("G6"):
        g6 = c["gates"]["G6"]
        assert g6.get("reference_engine_match") is True and set(g6["per_asset_val"]) == set(UNI)
        pub = public_pipeline(out)["candidates"][0]
        assert "per_asset_val" not in json.dumps(pub)                 # Val 자산별 세부는 사람 전용
    if c.get("robustness"):
        rb = c["robustness"]
        assert rb["universe"] == UNI and set(rb["checks"]["cross_venue"]["per_asset"]) == set(UNI)
    assert c["registry"]["state"] == "validated"
    if c.get("registry", {}).get("state") == "validated":
        risk = yaml.safe_load(Path(c["registry"]["risk_yaml"]).read_text())
        assert risk["universe"] == UNI and set(risk["sleeve_limits"]) == set(UNI)
    md = render(cr.campaign_id, env=env, audience="agent")
    assert "Universe" in md and "Per-asset" in md and "XRP-KRW" in md


# ------------------------------------------------------------------ 풀링 선별
def test_pooled_screen(uroot):
    env = ResearchEnv.at(uroot)
    d = {"name": "uscreen", "hypothesis": {"text": "donchian info", "rationale": "synthetic",
                                           "success_criteria": "p<0.1"},
         "data": {"exchange": "coinone", "timeframe": "1m"}, "universe": list(UNI), "bar_minutes": 5,
         "signals": [{"entry": {"block": "donchian_breakout", "params": {"lookback": 48}}}],
         "study": {"n_null": 100, "min_events": 50}, "budget": 5}
    res = run_screen(d, env=env, lessons=False)
    st = res.studies[0]
    assert set(st["per_asset"]) == set(UNI)
    assert st["n_events"] == sum(v["n_events"] for v in st["per_asset"].values())
    assert st["universe"] == UNI and res.template.to_record()["data"]["universe"] == UNI
    run = [r for r in env.ledger.records("runs") if r["campaign_id"] == res.campaign_id]
    assert len(run) == 1 and run[0]["spec"]["universe"] == UNI       # 신호 구성당 시도 1회
    # 같은 신호를 자산별로 따로 본 결과와 이벤트 수가 같다 (귀무는 자산별로 매칭)
    single = run_screen({**d, "name": "uscreen_btc", "universe": ["BTC-KRW"]}, env=env, lessons=False)
    assert single.studies[0]["n_events"] == st["per_asset"]["BTC-KRW"]["n_events"]
    assert "universe" not in single.template.to_record()["data"] or single.template.universe == ("BTC-KRW",)
    # 3자산 캠페인의 선별 게이트는 3자산 풀링 선별만 인정
    from btc_quant.research.campaign import check_screen_gate, ScreenGateError
    ctpl = CampaignTemplate.from_dict(uni_template(name="gated", entry={"block": "donchian_breakout",
                                                                         "grid": {"lookback": [48]}},
                                                   from_screen=single.campaign_id))
    with pytest.raises(ScreenGateError):
        check_screen_gate(env.ledger, ctpl)
    if st["decision"]["passed"]:
        ok = CampaignTemplate.from_dict(uni_template(name="gated2", entry={"block": "donchian_breakout",
                                                                            "grid": {"lookback": [48]}},
                                                     from_screen=res.campaign_id))
        assert check_screen_gate(env.ledger, ok)["n_configs"] == 1


# ------------------------------------------------------------------ 전진 인큐베이션 (유니버스별)
def test_forward_universe(tmp_path):
    from btc_quant.research import forward as F
    from test_research_forward import _market
    from test_research_synth import make_root
    root = make_root(tmp_path, planted=False, seed=11)
    env = ResearchEnv.at(root)
    led = env.ledger
    h = led.register_hypothesis("fwd uni", "r", "c")
    camp = led.create_campaign(h, {"data": {"exchange": "coinone"}, "universe": UNI,
                                   "costs": {"primary": "coinone_conservative"}}, 1, ["coinone_conservative"],
                               {"segment": "dev"}, family="donchian_breakout")
    spec = StrategySpec("fwduni", BlockRef("donchian_breakout", {"lookback": 24}), ExitSpec(0.6, 0.8, 60),
                        universe=tuple(UNI))
    led.record_runs(camp, [(spec, "coinone_conservative", {"mean_daily": 0.0005, "std_daily": 0.01,
                                                            "trades_per_day": 6.0, "sharpe": 1.0})],
                    {"segment": "dev", "start": "2023-01-01", "end": "2024-01-01"}, "snap", "abc")
    reg = env.registry
    reg.register(spec, actor="test")
    reg.transition(spec.id, 1, "candidate", "dev", actor="test", evidence={"campaign_id": camp})
    reg.transition(spec.id, 1, "validated", "val", actor="test", evidence={})
    data = {sym: _market(seed=i + 3) * (1.0 if sym == "BTC-KRW" else SCALE[sym]) for i, sym in enumerate(UNI)}
    calls = []

    def fake(exchange, start, end, symbol="BTC-KRW"):
        calls.append((exchange, symbol, start))
        d = data[symbol]
        return d[(d.index >= start) & (d.index < end)].copy()
    out = F.run_forward(env=env, fetcher=fake, now=pd.Timestamp("2024-05-01 05:00", tz="UTC"))
    assert {(ex, sym) for ex, sym, _ in calls} == {(ex, s) for ex in ("coinone", "upbit") for s in UNI}
    assert all(st >= pd.Timestamp("2024-04-01", tz="UTC") for _, _, st in calls)       # 잠금 구간 미접근
    assert set(out["coverage"]) == {"coinone", "upbit", "coinone:XRP-KRW", "upbit:XRP-KRW", "coinone:ETH-KRW",
                                    "upbit:ETH-KRW"}
    r = next(x for x in out["results"] if x["strategy_key"] == "fwduni_v1")
    assert r["status"] == "ok" and r["universe"] == UNI and set(r["per_asset"]) == set(UNI)
    assert r["n_trades"] == sum(v["n_trades"] for v in r["per_asset"].values())
    # 포트폴리오 = 자산별 엔진 실행의 평균
    eval_start = pd.Timestamp(r["period"]["eval_start"])
    end = pd.Timestamp(r["period"]["end"])
    from btc_quant.research.campaign import prepare_m1
    rets = []
    for sym in UNI:
        m1 = prepare_m1(env.data.read_range("2024-04-01", end, "coinone", sym, "1m"), "coinone")
        rets.append(fast.run_fast(spec, m1, get_cost("coinone_conservative"), eval_start, end).daily_returns)
    assert r["net_return"] == pytest.approx(float(np.prod(1 + np.mean(rets, axis=0)) - 1), abs=1e-12)


# ------------------------------------------------------------------ 페이퍼: 3 슬리브 (자산별 엔진 + 포트폴리오 한도)
def test_paper_three_sleeves_parity(tmp_path):
    import asyncio

    from btc_quant.live.risk import RiskLimits
    from btc_quant.paper import service as S
    from test_paper_bars import synth_trades
    from test_paper_service import Clock, FakeCollector, FakeNotifier
    from test_paper_trader import book_at, synth_raw
    t0 = pd.Timestamp("2026-11-12 23:00", tz="UTC")
    spec = StrategySpec("svc_uni", BlockRef("random_entry", {"p": 0.5, "seed": 1}), ExitSpec(0.1, 0.1, 10),
                        universe=tuple(UNI))
    p0 = {"BTC-KRW": 1e8, "XRP-KRW": 3_000.0, "ETH-KRW": 5_000_000.0}
    rng = np.random.default_rng(5)
    raws = {S._market(sym): synth_raw(rng, t0 - pd.Timedelta(days=4), 4 * 1440 + 10, p0=p0[sym]) for sym in UNI}
    calls = []

    def fetch(market, unit, start, end, progress=False):
        calls.append(market)
        r = raws[market]
        return r[(r.index >= start) & (r.index < end)]
    loose = RiskLimits(-0.5, -0.5, -0.5, 1000)
    slot = S.StrategySlot(spec, "spec", "B", loose, sleeve_limits={"XRP-KRW": RiskLimits(-0.4, -0.5, -0.5, 999)})
    cfg = S.ServiceConfig(runtime_dir=tmp_path / "rt", heartbeat_seconds=1, summary_hour_kst=9, notional=300_000)
    clock = Clock(t0.timestamp() - 30)
    svc = S.PaperService([slot], cfg, notifier=FakeNotifier(), collector=FakeCollector(), fetch=fetch, clock=clock)
    keys = [f"svc_uni_v1@{sym}" for sym in UNI]
    assert svc.symbols == UNI and sorted(svc.traders) == sorted(keys) and "svc_uni_v1" in svc.groups
    assert all(svc.traders[k].initial_equity == pytest.approx(100_000) for k in keys)       # 명목/3
    assert svc.traders["svc_uni_v1@XRP-KRW"].risk.limits.daily_loss == -0.4                # 슬리브 한도
    assert svc.traders["svc_uni_v1@ETH-KRW"].risk.limits.daily_loss == -0.5
    streams = {sym: synth_trades(np.random.default_rng(i + 11), t0 - pd.Timedelta(seconds=30), 150,
                                 p0=float(raws[S._market(sym)]["close"].iloc[-1]), wick_frac=0.0)
               for i, sym in enumerate(UNI)}
    events = sorted((ts, sym, px, q) for sym, tr in streams.items() for ts, px, q in tr)
    for ts_ms, sym, px, q in events:
        clock.t = ts_ms / 1000
        svc.on_event("coinone", "orderbook", {**book_at(px, spread=px * 2e-5, step=px * 1e-5, ts_recv=clock.t),
                                              "symbol": sym})
        svc.on_event("upbit", "trades", {"price": px * 1.0005, "ts_exchange": ts_ms, "qty": 0.1, "symbol": sym})
        svc.on_event("coinone", "trades", {"ts_exchange": ts_ms, "price": px, "qty": q, "symbol": sym})
        if not svc.ready and clock.t >= t0.timestamp() + 5:
            asyncio.run(svc.warmup())
        svc.process(clock.t)
    clock.t = events[-1][0] / 1000 + 120
    svc.process(clock.t)
    assert set(calls) == {"KRW-BTC", "KRW-XRP", "KRW-ETH"}
    total = 0
    for sym in UNI:
        k = f"svc_uni_v1@{sym}"
        tr = svc.traders[k]
        assert tr.n_decisions >= 28 and tr.symbol == sym
        rep = S.build_report(svc.cfg.out / k, m1=svc.hists[sym].frame())       # 슬리브 = 그 자산 백테스트
        t = rep["trades"]
        assert t["n_paper"] == tr.n_trades and t["n_identical"] == t["n_paper"] and not t["paper_only"]
        # 백테스트에만 있는 거래 = 리스크 게이트(합성 체결 공백 → stale_data)로 막힌 진입뿐
        dec = S.read_jsonl(svc.cfg.out / k / "decisions.jsonl")
        assert {d["risk_reason"].split(":")[0] for d in dec if not d["risk_ok"]} <= {"stale_data"}
        assert t["n_backtest"] - t["n_paper"] <= len(rep["blocked_by_risk"])
        total += tr.n_trades
    assert total >= 3
    g = svc.groups["svc_uni_v1"]
    assert g.risk.s.equity == pytest.approx(sum(svc.traders[k].mark_equity(svc.hists[s].last_close)
                                                for k, s in zip(keys, UNI)), rel=1e-6)
    svc.write_heartbeat()
    hb = json.loads((svc.cfg.out / "service.heartbeat").read_text())
    assert hb["symbols"] == UNI and "svc_uni_v1" in hb["portfolios"]
    st = S.status_report(svc.cfg.out, now=clock.t)
    assert set(keys) <= set(st["strategies"])


def test_sleeve_group_portfolio_limits_gate_and_flatten(tmp_path):
    from datetime import datetime, timezone

    from btc_quant.live.risk import RiskLimits, RiskManager
    from btc_quant.paper.trader import SleeveGroup
    now = datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)
    g = SleeveGroup("k", RiskManager(RiskLimits(-0.03, -0.06, -0.10, 4), "k@portfolio", "B", kill_dir=None),
                    ["k@A", "k@B"])
    g.on_sleeve_equity("k@A", 100.0, now)
    assert g.risk.s.equity is None                     # 모든 슬리브가 보고하기 전에는 판정하지 않음
    g.on_sleeve_equity("k@B", 100.0, now)
    assert g.risk.s.equity == 200.0 and g.can_enter(now)[0]
    g.on_sleeve_equity("k@A", 92.0, now)               # 포트폴리오 −4% ≤ 일일 −3% → 진입 금지
    ok, why = g.can_enter(now)
    assert not ok and why.startswith("portfolio daily_loss")
    g.on_sleeve_equity("k@B", 85.0, now)               # 누적 −11.5% ≤ −10% → 사람 정지 → 모든 슬리브 청산
    assert g.should_flatten(now)[0]
    for _ in range(4):
        g.on_entry(now)
    assert g.risk.s.trades_today == 4


# ------------------------------------------------------------------ MCP: universe 인자
def test_mcp_universe_argument(tmp_path):
    import copy

    from btc_quant.mcp.tools import ResearchTools
    from test_mcp_tools import call
    root = make_universe_root(tmp_path / "m", upbit=False)
    cfg = copy.deepcopy(G.load_gate_config())
    cfg["G2"]["null_runs"] = 40
    tools = ResearchTools(root, gate_cfg=cfg, n_null=40, load_accepted_blocks=False, progress=lambda s: None)
    scr = {"name": "mscreen", "hypothesis": {"text": "pooled donchian info", "rationale": "synthetic",
                                             "success_criteria": "p<0.1"},
           "data": {"exchange": "coinone", "timeframe": "1m"}, "bar_minutes": 5,
           "signals": [{"entry": {"block": "donchian_breakout", "params": {"lookback": 48}}}],
           "study": {"n_null": 100, "min_events": 50}, "budget": 5}
    bad = call(tools, "screen_signals", template=scr, universe=["BTC-KRW", "DOGE-KRW"])
    assert bad["ok"] is False and bad["error"]["code"] == "invalid_argument"
    r = call(tools, "screen_signals", template=scr, universe=["ETH-KRW", "XRP-KRW", "BTC-KRW"])
    assert r["ok"] and r["universe"] == UNI and any("n_events[XRP-KRW]" in c for c in r["configs"])
    assert r["passed"], r["configs"]
    d = uni_template(name="mcamp", entry={"block": "donchian_breakout", "grid": {"lookback": [48]}},
                     from_screen=r["campaign_id"], power={"target_sharpe": 100.0})
    d.pop("universe")
    c = call(tools, "create_and_run_campaign", template=d, universe=UNI, max_candidates=1)
    assert c["ok"] and c["universe"] == UNI
    cand = c["pipeline"]["candidates"][0]
    assert cand["universe"] == UNI and set(cand["per_asset_dev"]) == set(UNI)
    assert cand["G6_outcome"] in ("pass", "fail", "not evaluated")
    s = call(tools, "get_data_summary", exchanges=["coinone"], symbols=["XRP-KRW"], segments=["dev"])
    assert [x["symbol"] for x in s["series"]] == ["XRP-KRW"]


def test_sleeve_cost_lookup(monkeypatch):
    """자산별 비용 모델 `<name>@<SYM>` 이 레지스트리에 있으면 그 슬리브에 쓰고, 없으면 같은 모델 (BTC 는 항상 같은 모델)."""
    from btc_quant.research import campaign as C
    doc = C._cost_doc()
    assert C.sleeve_cost_name("coinone_conservative", "XRP-KRW") == "coinone_conservative"
    fake = {**doc, "models": {**doc["models"], "coinone_conservative@XRP-KRW": doc["models"]["coinone_conservative"],
                              "coinone_conservative@BTC-KRW": doc["models"]["coinone_conservative"]}}
    monkeypatch.setattr(C, "_cost_doc", lambda root=None: fake)
    assert C.sleeve_cost_name("coinone_conservative", "XRP-KRW") == "coinone_conservative@XRP-KRW"
    assert C.sleeve_cost_name("coinone_conservative", "ETH-KRW") == "coinone_conservative"
    assert C.sleeve_cost_name("coinone_conservative", "BTC-KRW") == "coinone_conservative"
