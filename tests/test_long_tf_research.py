"""긴 결정 주기(15m/1h/4h) — 블록 호환성, 신호 선별 지평 기본값, 캠페인(4h), gross 지표·COST_EATEN,
페이퍼 1h 재생 일치, Forward 워밍업."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest import fast
from btc_quant.backtest.reference import run_reference
from btc_quant.backtest.types import CostModel
from btc_quant.research import signal_study as S
from btc_quant.research.campaign import (CampaignError, CampaignTemplate, ResearchEnv, gross_metrics,
                                         run_campaign)
from btc_quant.research.lessons import LessonBook, classify_candidate, classify_pipeline
from btc_quant.strategy.blocks import REGISTRY, Context
from btc_quant.strategy.features import to_tf
from btc_quant.strategy.spec import TIMEFRAMES, BlockRef, ExitSpec, StrategySpec

from test_research_lessons import cand, gate
from test_research_synth import make_root, template_dict

# 긴 봉에서 무의미해서 거부되는 블록 (docs/BLOCKS.md "결정 주기 호환성")
REJECT_4H = {"session_kst", "trend_regime_1h", "range_compression_break"}


def _m1(days=40, seed=0, start="2025-01-01"):
    rng = np.random.default_rng(seed)
    n = days * 1440
    idx = pd.date_range(start, periods=n, freq="1min", tz="UTC", name="ts")
    c = 1e8 * np.exp(np.cumsum(rng.normal(0, 8e-4, n)))
    o = np.r_[1e8, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 3e-4, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 3e-4, n)))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": rng.lognormal(0, 0.5, n),
                         "filled": False}, index=idx)


# ================================================================ 블록 호환성
def test_block_timeframe_compatibility_table():
    defs = REGISTRY.all(include_experimental=True)
    for d in defs:
        for tf, b in TIMEFRAMES.items():
            prob = d.tf_problem(b)
            if b <= 60:
                assert prob is None, (d.name, tf)
            else:
                assert (prob is not None) == (d.name in REJECT_4H), (d.name, tf)
    assert {d.name for d in defs if d.tf_problem(240)} == REJECT_4H


def test_spec_validation_rejects_meaningless_blocks_at_4h():
    ex = ExitSpec(1.0, 1.0, 480)
    e = BlockRef("donchian_breakout", {"lookback": 48})
    for f in ("session_kst", "trend_regime_1h"):
        StrategySpec("a", e, ex, (BlockRef(f),), timeframe="1h").validate(REGISTRY)
        with pytest.raises(ValueError, match="meaningless at a 240-minute"):
            StrategySpec("a", e, ex, (BlockRef(f),), timeframe="4h").validate(REGISTRY)
    with pytest.raises(ValueError, match="range_compression_break"):
        StrategySpec("a", BlockRef("range_compression_break"), ex, timeframe="4h").validate(REGISTRY)
    # 레지스트리 없이(구조만) 검증하면 블록 호환성은 보지 않는다 — 기존 호출 그대로
    StrategySpec("a", e, ex, (BlockRef("session_kst"),), timeframe="4h").validate()


def test_rejected_blocks_are_actually_degenerate_at_4h():
    m1 = _m1(30)
    bars = to_tf(m1, 240)
    ctx = Context(bar=pd.Timedelta(minutes=240))
    assert not REGISTRY.evaluate(BlockRef("trend_regime_1h", {"span": 12}), bars, ctx).any()
    assert not REGISTRY.evaluate(BlockRef("session_kst", {"session": "us_open"}), bars, ctx).any()
    assert not REGISTRY.evaluate(BlockRef("range_compression_break", {"lookback": 12, "squeeze_pct": 0.1}),
                                 bars, ctx).any()
    # 1h 에서 trend_regime_1h = 결정 봉 자신의 종가 > EMA(span 봉)
    b1 = to_tf(m1, 60)
    got = REGISTRY.evaluate(BlockRef("trend_regime_1h", {"span": 12}), b1, Context(bar=pd.Timedelta("1h")))
    c = b1["close"]
    want = (c > c.ewm(span=12, adjust=False).mean()) & (np.arange(len(c)) >= 12)
    assert np.array_equal(got.to_numpy(), want.to_numpy())


@pytest.mark.parametrize("b", [15, 60, 240])
def test_allowed_blocks_run_causally_on_long_bars(b):
    m1 = _m1(60, seed=b)
    bars = to_tf(m1, b)
    aux = {"upbit": bars.assign(close=bars["close"] * 1.0003), "binance": bars.assign(close=bars["close"] * 0.9998),
           "coinone_btc": bars.assign(close=bars["close"] * 1.0001)}
    ctx = Context(aux=aux, bar=pd.Timedelta(minutes=b))
    cut = len(bars) * 2 // 3
    for d in REGISTRY.all(include_experimental=True):
        if d.tf_problem(b):
            continue
        p = {k: v[len(v) // 2] for k, v in d.grid.items()}
        full = REGISTRY.evaluate(BlockRef(d.name, p), bars, ctx).to_numpy(bool)
        part = REGISTRY.evaluate(BlockRef(d.name, p), bars.iloc[:cut],
                                 Context(aux={k: v.iloc[:cut] for k, v in aux.items()}, bar=ctx.bar)).to_numpy(bool)
        assert np.array_equal(full[:cut], part), (d.name, b)


# ================================================================ 캠페인 템플릿
def _tpl4h(**over):
    d = template_dict(name="swing4h", timeframe="4h",
                      entry={"block": "donchian_breakout", "grid": {"lookback": [12, 24, 48]}},
                      filters=[[], [{"block": "vol_regime", "grid": {"window": [12], "min_pct": [0.5]}}]],
                      exits={"mode": "atr", "sl_atr": [1.5], "tp_atr": [2.0, 3.0], "max_hold_min": [1440, 2880]})
    d.update(over)
    return d


def test_campaign_template_long_timeframes():
    tpl = CampaignTemplate.from_dict(_tpl4h())
    assert tpl.decision_tf == "4h" and tpl.tf_min == 240
    specs = tpl.enumerate()
    assert all(s.timeframe == "4h" and s.to_dict()["timeframe"] == "4h" for s in specs)
    with pytest.raises(CampaignError, match="multiples of 240"):
        CampaignTemplate.from_dict(_tpl4h(exits={"sl_pct": [1.0], "tp_pct": [2.0], "max_hold_min": [300]}))
    with pytest.raises(CampaignError, match="meaningless"):
        CampaignTemplate.from_dict(_tpl4h(filters=[[{"block": "session_kst"}]]))
    CampaignTemplate.from_dict(_tpl4h(timeframe="1h", filters=[[{"block": "trend_regime_1h"}]]))
    CampaignTemplate.from_dict(_tpl4h(timeframe="15m", exits={"sl_pct": [1.0], "tp_pct": [2.0],
                                                              "max_hold_min": [45, 60]}))


# ================================================================ 신호 선별
def _screen(bm, **over):
    d = {"name": f"sw{bm}", "hypothesis": {"text": "t", "rationale": "r", "success_criteria": "s"},
         "data": {"exchange": "coinone", "symbol": "BTC-KRW", "timeframe": "1m"}, "bar_minutes": bm,
         "signals": [{"entry": {"block": "donchian_breakout", "grid": {"lookback": [12, 24]}}, "filters": [[]]}],
         "budget": 10}
    d.update(over)
    return d


@pytest.mark.parametrize("bm", [1, 5, 15, 60, 240])
def test_screen_horizon_presets(bm):
    tpl = S.ScreenTemplate.from_dict(_screen(bm))
    pre = S.horizon_preset(bm)
    assert tpl.horizons == list(pre["horizons"]) and tpl.primary_horizons == list(pre["primary"])
    assert tpl.min_gap_min == pre["min_gap_min"]
    if bm in (1, 5):    # 기존 기본값 그대로 (하위 호환)
        assert tuple(tpl.horizons) == S.DEFAULT_HORIZONS and tpl.min_gap_min == S.DEFAULT_MIN_GAP_MIN
    else:
        assert tpl.min_gap_min == max(60, 2 * bm)
    spec = tpl.trial_spec(*tpl.configs()[0])
    assert spec.tf_min == bm and spec.exit.max_hold_min % max(bm, 5) == 0
    spec.validate(REGISTRY)


def test_screen_presets_values():
    assert S.horizon_preset(60)["horizons"] == (60, 120, 240, 480, 1440)
    assert S.horizon_preset(240)["horizons"] == (240, 480, 1440, 2880)
    with pytest.raises(CampaignError, match="bar_minutes"):
        S.ScreenTemplate.from_dict(_screen(30))
    with pytest.raises(CampaignError, match="meaningless"):
        S.ScreenTemplate.from_dict(_screen(240, signals=[{"entry": {"block": "donchian_breakout"},
                                                          "filters": [[{"block": "trend_regime_1h"}]]}]))


def test_study_signal_4h_declustering_and_strata():
    m1 = _m1(120, seed=4)
    st = S.study_signal(m1, BlockRef("random_entry", {"p": 0.5, "seed": 1}), (), bar_minutes=240, n_null=50,
                        min_events=10)
    assert st["horizons"] == [240, 480, 1440, 2880] and st["primary_horizons"] == [480, 1440, 2880]
    assert st["min_gap_min"] == 480 and st["n_events"] > 50
    # 결정 시각(4h 경계) KST 시는 01/05/09/13/17/21 뿐 → 층의 시(hour) 도 그 6개만
    hours = {g // S.N_VOL_BINS for g in st["strata_events"]}
    assert hours <= {1, 5, 9, 13, 17, 21}
    assert st["suggestion"] is None or st["suggestion"]["max_hold_min"] % 240 == 0
    # 군집 제거: 채택 이벤트 간격 ≥ 480분 — 같은 함수로 직접 확인
    t = pd.date_range("2025-01-01", periods=10, freq="240min", tz="UTC").as_unit("ns").asi8
    keep = S.decluster(t, 480)
    assert keep.tolist() == [True, False] * 5


# ================================================================ 캠페인 4h (합성 Dev) + gross 지표
@pytest.fixture(scope="module")
def synth_root(tmp_path_factory):
    return make_root(tmp_path_factory.mktemp("lt"), planted=False, seed=5)


def test_campaign_4h_runs_and_records_gross(synth_root):
    env = ResearchEnv.at(synth_root)
    cr = run_campaign(CampaignTemplate.from_dict(_tpl4h()), env=env)
    r = cr.results
    assert len(r) == 2 * cr.template.n_specs() and cr.manifest["timeframe"] == "4h"
    assert {"gross_sharpe_w", "gross_expectancy"} <= set(r.columns)
    # 원장 runs 에도 gross 지표
    runs = [x for x in env.ledger.records("runs") if x["campaign_id"] == cr.campaign_id]
    assert all("gross_sharpe_w" in x["metrics"] for x in runs)
    # 한 스펙: gross_expectancy = 거래 gross 수익 평균, 비용 > 0 이면 gross ≥ net (같은 거래)
    row = r[r.cost == cr.template.costs["primary"]].iloc[0]
    spec = cr.specs[row["logic_hash"]]
    m1 = __import__("btc_quant.research.campaign", fromlist=["load_segment"]).load_segment(
        env, "dev", "coinone", "BTC-KRW", "1m")
    s, e = env.data.segment_bounds("dev")
    res = fast.run_fast(spec, m1, CostModel("zero", 0.0, 0.0, 0.0, 0.0), s, e)
    assert row["n_trades"] == len(res.trades) > 0
    assert row["gross_expectancy"] == pytest.approx(float(res.trades["gross_ret"].mean()), rel=1e-12)
    assert row["gross_expectancy"] >= row["expectancy"]
    from btc_quant.evaluation.metrics import weighted_sharpe
    assert row["gross_sharpe_w"] == pytest.approx(weighted_sharpe(res.daily_returns, 365).sharpe, rel=1e-9)
    entries = pd.DatetimeIndex(res.trades["entry_time"])
    assert set(entries.hour) <= {0, 4, 8, 12, 16, 20} and (entries.minute == 0).all()

    # 파이프라인 G3 에 비용 전 인접 중앙값, 깔때기에 gross 양수 수
    from btc_quant.research.pipeline import Pipeline
    pl = Pipeline(cr, env, register=False, lessons=False, robustness=False)
    fa = pl.funnel_all()
    assert fa["gross_sharpe_w_positive"] == int((cr.primary()["gross_sharpe_w"] > 0).sum())
    g3 = pl.g3(spec, float(row["sharpe_w"]))
    assert "neighbor_median_gross" in g3 and all("gross_sharpe_w" in n for n in g3["neighbors"])


def test_gross_metrics_zero_cost_equals_gross():
    m1 = _m1(20, seed=9)
    spec = StrategySpec("g", BlockRef("random_entry", {"p": 0.3, "seed": 2}), ExitSpec(0.5, 0.5, 240),
                        timeframe="1h")
    p = fast.prepare_spec(spec, m1)
    s, e = m1.index[0], m1.index[-1]
    cost = CostModel("c", 0.0005, 2.0, 3.0, 0.0)
    tr = fast.simulate_exit(p, spec.exit, cost, *p.bounds(s, e)[2:])
    z = fast.reprice(tr, CostModel("z", 0.0, 0.0, 0.0, 0.0))
    assert np.array_equal(z.net_ret, tr.gross_ret) and (tr.net_ret < z.net_ret).all()
    g = gross_metrics(p, tr, cost, s, e)
    assert g["gross_expectancy"] == float(tr.gross_ret.mean())


# ================================================================ 오답노트: G2/G3 COST_EATEN
def test_g3_failure_with_positive_gross_is_cost_eaten():
    g3 = gate(False, selected=0.04, neighbor_median=-0.86, required=0.02, n_neighbors=6,
              neighbor_median_gross=0.9)
    c = cand(first_failed="G3", gates={"G2": gate(True, sharpe_w=0.04), "G3": g3},
             metrics={"sharpe_w": 0.04, "gross_sharpe_w": 1.2, "n_trades": 900})
    t = classify_candidate(c)
    assert t["primary"] == "COST_EATEN" and "PARAM_SPIKE" not in t["codes"]
    # 비용 전에도 인접이 약하면 PARAM_SPIKE 그대로
    c2 = cand(first_failed="G3", gates={"G2": gate(True), "G3": {**g3, "neighbor_median_gross": -0.1}},
              metrics={"sharpe_w": 0.04, "gross_sharpe_w": 1.2})
    assert classify_candidate(c2)["primary"] == "PARAM_SPIKE"
    # 비용 후 인접이 양(+)이면(고원 비율만 미달) 비용 탓 아님
    c3 = cand(first_failed="G3", gates={"G2": gate(True), "G3": {**g3, "neighbor_median": 0.01}})
    assert classify_candidate(c3)["primary"] == "PARAM_SPIKE"
    # gross 인접 값이 없는 예전 결과: 후보 gross 지표로 대체
    g3_old = {k: v for k, v in g3.items() if k != "neighbor_median_gross"}
    c4 = cand(first_failed="G3", gates={"G2": gate(True), "G3": g3_old},
              metrics={"sharpe_w": 0.04, "gross_sharpe_w": 1.2})
    assert classify_candidate(c4)["primary"] == "COST_EATEN"


def test_g2_failure_uses_campaign_gross_metric():
    c = cand(metrics={"sharpe_w": -0.2, "gross_sharpe_w": 0.6, "gross_expectancy": 3e-4, "n_trades": 900})
    assert classify_candidate(c)["primary"] == "COST_EATEN"
    c = cand(metrics={"sharpe_w": -0.2, "gross_sharpe_w": -0.1, "n_trades": 900})
    assert classify_candidate(c)["primary"] == "NO_SIGNAL"


def test_pipeline_level_cost_eaten_from_funnel_gross():
    p = {"funnel_all": {"n_specs": 10, "sharpe_w_positive": 0, "gross_sharpe_w_positive": 4},
         "candidates": [cand(metrics={"sharpe_w": -0.2})]}
    assert classify_pipeline(p)["primary"] == "COST_EATEN"
    p["funnel_all"]["gross_sharpe_w_positive"] = 0
    assert classify_pipeline(p)["primary"] == "NO_SIGNAL"


def test_record_facts_from_pipeline_classifies_cost_eaten(tmp_path):
    from test_research_lessons import grid_record, pipeline_out
    book = LessonBook.at(tmp_path)
    led = book.ledger
    h = led.register_hypothesis("4h breakout", "swing", ">=1 pass")
    cid = led.create_campaign(h, grid=grid_record(), budget=100, cost_models=["coinone_conservative"],
                              period={"segment": "dev", "start": "2021-01-01", "end": "2025-11-01",
                                      "data_snapshot": "s"}, family="donchian_breakout")
    g3 = gate(False, selected=0.04, neighbor_median=-0.86, required=0.02, n_neighbors=6,
              neighbor_median_gross=0.9)
    c = cand(first_failed="G3", gates={"G2": gate(True, sharpe_w=0.04), "G3": g3},
             metrics={"sharpe_w": 0.04, "gross_sharpe_w": 1.2, "gross_expectancy": 2e-4, "n_trades": 900})
    f = book.record_facts_from_pipeline(pipeline_out(cid, h, [c]))
    assert f["taxonomy"]["primary"] == "COST_EATEN"
    fc = f["candidates"][0]
    assert fc["gates_dev"]["G3"]["neighbor_median_gross"] == 0.9 and fc["taxonomy"]["primary"] == "COST_EATEN"
    assert fc["metrics_dev"]["gross_sharpe_w"] == 1.2 and fc["metrics_dev"]["gross_expectancy"] == 2e-4


# ================================================================ 페이퍼 1h 재생 == run_reference
def test_paper_replay_identical_1h(tmp_path):
    from test_paper_trader import _setup, assert_same_trades, book_at, make_trader, paper_trades, \
        read_jsonl, run_live, COST, T0
    from btc_quant.paper.bars import M1History, required_lookback_bars, ts_minute, window_minutes
    spec = StrategySpec("p_1h", BlockRef("random_entry", {"p": 0.6, "seed": 5}),
                        ExitSpec(stop_loss_atr=0.7, take_profit_atr=0.7, max_hold_min=180,
                                 signal_exit=BlockRef("ema_cross_down", {"fast": 5, "slow": 26})),
                        (BlockRef("session_kst", {"session": "all_but_quiet"}),), timeframe="1h")
    spec.validate(REGISTRY)
    live = 3 * 1440
    raw_seed, trades = _setup(41, live_minutes=live, seed_days=12)
    hist = M1History(max_rows=10 ** 6)
    hist.seed(raw_seed)
    tr = make_trader(spec, tmp_path)
    assert tr.window_min == window_minutes(required_lookback_bars(spec), 60)
    tr.attach(hist, book_at(hist.last_close))
    run_live(tr, hist, trades, ts_minute(T0), (T0 + pd.Timedelta(minutes=live + 1)).timestamp())
    m1 = hist.frame()
    ref = run_reference(spec, m1, COST, T0, m1.index[-1] + pd.Timedelta("1min")).trades
    paper = paper_trades(tr)
    assert len(paper) >= 3
    assert_same_trades(paper, ref)
    dec = read_jsonl(tr.path("decisions.jsonl"))
    assert len(dec) == live // 60 + 1 and all(pd.Timestamp(d["ts"]).minute == 0 for d in dec)


def test_paper_window_and_lookback_long_bars():
    from btc_quant.paper.bars import required_lookback_bars, window_minutes
    assert window_minutes(100, 5) == 100 * 5 + 60 and window_minutes(100, 1) == 160      # 기존 그대로
    assert window_minutes(100, 240) == 101 * 240                                          # 완전한 봉 1개 여유
    s = StrategySpec("x", BlockRef("donchian_breakout", {"lookback": 288}), ExitSpec(1.0, 1.0, 480),
                     timeframe="4h")
    assert required_lookback_bars(s) == 290


# ================================================================ Forward 워밍업
def test_forward_warmup_scales_for_long_bars():
    from btc_quant.research.forward import spec_warmup_days
    mk = lambda tf, ref: StrategySpec("x", ref, ExitSpec(1.0, 1.0, 480), timeframe=tf)  # noqa: E731
    don = BlockRef("donchian_breakout", {"lookback": 288})
    assert spec_warmup_days(mk("5m", don), 14) == 14 and spec_warmup_days(mk("1m", don), 14) == 14
    assert spec_warmup_days(mk("1h", don), 14) == 14            # 290봉 ≈ 12일 < 14일
    assert spec_warmup_days(mk("4h", don), 14) == 49            # 290봉 × 4h ≈ 48.3일
    ema = BlockRef("ema_slope", {"span": 96, "min_slope_bp": 2.0})
    assert spec_warmup_days(mk("4h", ema), 14) == 238           # 1,425봉 × 4h (4h 상한 420일 안 — Phase 5)
    assert spec_warmup_days(mk("1h", ema), 14) == 60            # 1h 는 기존 120일 상한 안 (1,425봉 ≈ 59.4일)


def test_4h_warmup_cap_reproduces_trend_ema_180():
    """Phase 5 (managed_hold): 4h EMA(180) 의 초기값 잔존 가중 ≤ 1e-12 — 페이퍼·전진이 백테스트와 같은 상태."""
    from btc_quant.paper.bars import MAX_WARMUP_DAYS, max_warmup_days_for, required_lookback_bars
    assert max_warmup_days_for(240) == 420.0 and max_warmup_days_for(60) == MAX_WARMUP_DAYS == 120.0
    for span in (90, 180):
        s = StrategySpec("x", BlockRef("trend_ema_4h", {"span": span, "band_bp": 0.0}), ExitSpec(50.0, 1e5, 2592000),
                         timeframe="4h")
        n = required_lookback_bars(s)
        assert (1 - 2 / (span + 1)) ** n <= 1e-12, (span, n)
