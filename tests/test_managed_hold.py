"""위험 관리 보유(managed_hold) 준비 — 추세 상태 블록, 템플릿 `signal_exit`, 캠페인·파이프라인 신호 청산 일치,
DCA 오버레이, 저장소 템플릿 파싱 (실행 없음). docs/MANAGED_HOLD.md."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from test_research_synth import make_root, template_dict

from btc_quant.backtest import fast
from btc_quant.research import managed_hold as MH
from btc_quant.research.campaign import (
    CampaignError,
    CampaignTemplate,
    ResearchEnv,
    SignalCache,
    get_cost,
    load_segment,
    run_campaign,
)
from btc_quant.strategy.blocks import REGISTRY, Context
from btc_quant.strategy.blocks.momentum import ema
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

ROOT = Path(__file__).resolve().parents[1]
HOLD = 1800 * 1440


# ------------------------------------------------------------------ 블록
def _bars(n=400, seed=0):
    idx = pd.date_range("2024-01-01", periods=n, freq="240min", tz="UTC")
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n) + 0.01 * np.sin(np.arange(n) / 25)))
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0,
                         "quote_volume": c}, index=idx)


def test_trend_state_blocks_formula_and_hysteresis():
    b = _bars()
    ctx = Context(bar=pd.Timedelta(minutes=240))
    for span, band in ((42, 0.0), (90, 100.0)):
        on = REGISTRY.evaluate(BlockRef("trend_ema_4h", {"span": span, "band_bp": band}), b, ctx)
        off = REGISTRY.evaluate(BlockRef("trend_ema_off", {"span": span, "band_bp": band}), b, ctx)
        e = ema(b["close"], span)
        exp_on = (b["close"] / e > 1 + band / 1e4).to_numpy().copy()
        exp_off = (b["close"] / e < 1 - band / 1e4).to_numpy().copy()
        exp_on[:span] = False
        exp_off[:span] = False
        assert on.dtype == bool and (on.to_numpy() == exp_on).all() and (off.to_numpy() == exp_off).all()
        assert not (on & off).any()                                 # 띠 안에서는 둘 다 False
        if band == 0:
            assert ((on | off).to_numpy()[span:]).mean() > 0.99     # band 0: 진입 = ¬청산 (같은 값 제외)
        else:
            assert ((~on & ~off).to_numpy()[span:]).any()           # 히스테리시스 띠
    for n in ("trend_ema_4h", "trend_ema_off"):
        d = REGISTRY.get(n)
        assert d.experimental and d.lookback_bars({"span": 180}) > 2000


# ------------------------------------------------------------------ 템플릿 signal_exit
def _mh_template(**over):
    d = template_dict(name="mh4h", timeframe="4h", budget=10,
                      entry={"block": "trend_ema_4h", "grid": {"span": [42, 90], "band_bp": [0.0, 100.0]}},
                      filters=[[]],
                      signal_exit={"block": "trend_ema_off", "match_entry": ["span", "band_bp"]},
                      exits={"sl_pct": [50.0], "tp_pct": [100000.0], "max_hold_min": [HOLD]})
    d.update(over)
    return d


def test_template_signal_exit_tied_to_entry():
    tpl = CampaignTemplate.from_dict(_mh_template())
    specs = tpl.enumerate()
    assert tpl.n_specs() == len(specs) == 4                         # 신호 청산은 격자를 넓히지 않는다
    for s in specs:
        assert s.exit.signal_exit == BlockRef("trend_ema_off", dict(s.entry.params))
        assert s.n_params() == 2 + 3 + 2 and s.timeframe == "4h"
        s.validate(REGISTRY)
    assert len({s.logic_hash() for s in specs}) == 4
    assert tpl.to_record()["signal_exit"] == {"block": "trend_ema_off", "params": {},
                                              "match_entry": ["span", "band_bp"]}
    # 고정 값 + 일부만 묶기
    t2 = CampaignTemplate.from_dict(_mh_template(signal_exit={"block": "trend_ema_off", "grid": {"band_bp": [50.0]},
                                                              "match_entry": ["span"]}))
    assert {s.exit.signal_exit.params["band_bp"] for s in t2.enumerate()} == {50.0}
    # 신호 청산이 없으면 기존과 같은 스펙(해시)
    base = template_dict(name="nos")
    assert all(s.exit.signal_exit is None for s in CampaignTemplate.from_dict(base).enumerate())
    assert "signal_exit" not in CampaignTemplate.from_dict(base).to_record()
    bad = [
        ({"block": "trend_ema_off"}, "exactly one value"),                                  # 기본 격자 4개 값
        ({"block": "trend_ema_off", "grid": {"band_bp": [0.0, 50.0]}, "match_entry": ["span"]}, "exactly one"),
        ({"block": "trend_ema_off", "match_entry": ["nope"]}, "match_entry"),
        ({"block": "trend_ema_4h", "match_entry": ["span", "band_bp"]}, "used as exit_signal"),
        ({"block": "trend_ema_off", "grid": {"span": [42]}, "match_entry": ["span", "band_bp"]}, "cannot also"),
        ({"block": "trend_ema_off", "foo": 1}, "unknown keys"),
    ]
    for se, msg in bad:
        with pytest.raises(CampaignError, match=msg):
            CampaignTemplate.from_dict(_mh_template(signal_exit=se))


def test_repo_managed_hold_templates_parse():
    """저장소 템플릿(실행하지 않음): 선별·캠페인 모두 파싱·검증만."""
    from btc_quant.research.signal_study import ScreenTemplate
    scr = ScreenTemplate.from_yaml(ROOT / "research/campaign_templates/screens/managed_hold_4h_u3_screen.yaml")
    assert scr.bar_minutes == 240 and scr.multi_asset and len(scr.configs()) == 4 and scr.budget == 4
    assert scr.primary_horizons == [2880, 4320, 10080] and scr.family == "managed_hold"
    tpl = CampaignTemplate.from_yaml(ROOT / "research/campaign_templates/managed_hold_4h_u3.yaml")
    assert tpl.decision_tf == "4h" and tpl.n_specs() == 4 <= tpl.budget and tpl.requires_screen_pass
    assert tpl.family == "managed_hold" and tpl.universe == ("BTC-KRW", "XRP-KRW", "ETH-KRW")
    for s in tpl.enumerate():
        assert s.exit.signal_exit.block == "trend_ema_off" and s.exit.signal_exit.params == s.entry.params
        assert REGISTRY.spec_uses_experimental(s)        # 잠금 요청 전 사람 검토 대상


# ------------------------------------------------------------------ 엔진 의미론 · 캠페인/파이프라인 일치
@pytest.fixture(scope="module")
def synth_root(tmp_path_factory):
    return make_root(tmp_path_factory.mktemp("mh"), planted=False, seed=5)


def test_default_long_exit_on_filter_engine_and_campaign_parity(synth_root):
    env = ResearchEnv.at(synth_root)
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    s, e = env.data.segment_bounds("dev")
    cost = get_cost("coinone_conservative")
    spec = StrategySpec("mh", BlockRef("trend_ema_4h", {"span": 42, "band_bp": 0.0}),
                        ExitSpec(50.0, 100000.0, HOLD, signal_exit=BlockRef("trend_ema_off",
                                                                            {"span": 42, "band_bp": 0.0})),
                        timeframe="4h")
    res = fast.run_fast(spec, m1, cost, s, e, accounting="mtm")
    tr = res.trades
    assert len(tr) > 5
    # 추세가 꺼질 때만 나간다 (SL/TP/만기 없음; 마지막은 기간 끝일 수 있음)
    assert set(tr["exit_reason"].iloc[:-1]) == {"signal"}
    # 보유 중 결정 경계의 추세 상태: 청산 직전까지 off 신호가 없었다 → 추세 동안 보유
    ctx = Context(bar=pd.Timedelta(minutes=240))
    from btc_quant.strategy.features import to_tf
    bars = to_tf(m1, 240)
    off = REGISTRY.evaluate(spec.exit.signal_exit, bars, ctx)
    on = REGISTRY.evaluate(spec.entry, bars, ctx)
    for _, t in tr.iloc[:-1].iterrows():
        held = off[(off.index >= t["entry_time"]) & (off.index + pd.Timedelta(minutes=240) < t["exit_time"])]
        assert not held.any()
        assert off.loc[t["exit_time"] - pd.Timedelta(minutes=240)]
    # 노출이 크다 (기본 롱): 추세 on 비율과 비슷 (쿨다운 봉만큼 작음)
    days_frac = float(on.iloc[42:].mean())
    expo = float(tr["hold_min"].sum()) / ((e - s) / pd.Timedelta(minutes=1))
    assert 0.5 * days_frac < expo <= days_frac + 0.02
    # 캠페인 경로(SignalCache.prepared + 신호 청산) == run_fast
    c = SignalCache(m1, ctx, tf_min=240)
    p = c.prepared(spec.entry, spec.filters, spec.exit.signal_exit)
    _, _, si, ei = p.bounds(s, e)
    tr2 = fast.simulate_prepared(p, 50.0, 100000.0, HOLD, cost, si, ei, None)
    r2 = fast.to_result(p, tr2, cost, s, e, accounting="mtm")
    pd.testing.assert_frame_equal(r2.trades.reset_index(drop=True), tr.reset_index(drop=True))
    assert np.allclose(r2.daily_returns.to_numpy(), res.daily_returns.to_numpy())
    # 신호 청산을 빼면 다른 결과 (검사가 공허하지 않다)
    p0 = c.prepared(spec.entry, spec.filters)
    assert len(fast.simulate_prepared(p0, 50.0, 100000.0, HOLD, cost, si, ei, None).net_ret) < len(tr)


def test_campaign_and_pipeline_use_signal_exit(synth_root):
    env = ResearchEnv.at(synth_root)
    tpl = CampaignTemplate.from_dict(_mh_template(entry={"block": "trend_ema_4h",
                                                         "grid": {"span": [42], "band_bp": [0.0, 100.0]}}))
    cr = run_campaign(tpl, env=env)                  # 임시 저장소의 합성 Dev (공식 원장 아님)
    assert cr.manifest["signal_exit"]["block"] == "trend_ema_off"
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    s, e = env.data.segment_bounds("dev")
    from btc_quant.research.pipeline import Pipeline
    pl = Pipeline(cr, env, register=False, lessons=False, robustness=False, reference_check=False)
    prim = cr.primary()
    for lh, row in prim.iterrows():
        spec = cr.specs[lh]
        assert spec.exit.signal_exit is not None
        res = fast.run_fast(spec, m1, get_cost(cr.template.costs["primary"]), s, e, accounting=pl.accounting)
        assert int(row["n_trades"]) == len(res.trades)
        dev = pl.dev_result(spec, cr.template.costs["primary"])
        assert len(dev.trades) == len(res.trades)
        assert np.allclose(dev.daily_returns.to_numpy(), res.daily_returns.to_numpy())


# ------------------------------------------------------------------ DCA 오버레이
def test_dca_overlay_and_irr():
    days = pd.date_range("2024-01-01", periods=28, freq="D", tz="UTC")     # 월요일 시작, 4주
    flat = pd.Series(0.0, index=days)
    out = MH.dca_overlay(flat, 100_000, "W")
    assert out["n_contributions"] == 4 and out["invested"] == out["final_value"] == 400_000
    assert out["irr_ann"] == pytest.approx(0.0, abs=1e-9)
    # 매일 +0.1%: 손으로 계산한 평가액, IRR = (1.001)^365 − 1
    up = pd.Series(0.001, index=days)
    out = MH.dca_overlay(up, 100_000, "W")
    exp = sum(100_000 * 1.001 ** (28 - 7 * k) for k in range(4))
    assert out["final_value"] == pytest.approx(exp, rel=1e-12)
    assert out["irr_ann"] == pytest.approx(1.001 ** 365 - 1, rel=1e-8)
    # 적립 비용은 보유 비중만큼: 현금 상태(노출 0)인 날 적립은 비용 없음
    expo = pd.Series(0.0, index=days)
    a = MH.dca_overlay(flat, 100_000, "W", exposure=expo, buy_cost=0.003)
    b = MH.dca_overlay(flat, 100_000, "W", buy_cost=0.003)
    assert a["final_value"] == 400_000 and b["final_value"] == pytest.approx(400_000 * 0.997)
    cmp = MH.dca_compare(flat, up, 100_000, "W")
    assert cmp["final_value_ratio"] < 1 and cmp["irr_diff"] < 0
    assert list(MH.contribution_days(days, "M")) == [days[0]]
