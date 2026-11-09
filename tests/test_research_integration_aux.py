"""v4 통합: 업비트 보조 데이터(aux) 캠페인·신호 선별 연결·견고성 관문·백테스트/페이퍼 ctx.aux 동일성.

합성 시장(research_v4_synth.make_lagged_root): 코인원이 업비트를 정확히 1분 늦게 따라간다 →
`upbit_lead_impulse`(실험 블록, requires upbit) 가 강한 엣지를 갖지만 1분 지연이면 사라진다
(견고성 latency 실패 → validated 로 올라가지 않아야 한다).
"""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest
import yaml
from research_v4_synth import aux_campaign_dict, aux_screen_dict, make_lagged_root

from btc_quant.backtest import fast
from btc_quant.backtest.reference import run_reference
from btc_quant.evaluation import gates as G
from btc_quant.registry.splits import overlaps_lockbox
from btc_quant.research import cli
from btc_quant.research.campaign import (CampaignTemplate, ResearchEnv, ScreenGateError, SignalCache,
                                         aux_context, get_cost, load_segment, run_campaign)
from btc_quant.research.pipeline import run_pipeline
from btc_quant.research.report import write_report
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

N_NULL = 200


@pytest.fixture(scope="module")
def lag(tmp_path_factory):
    root = make_lagged_root(tmp_path_factory.mktemp("lag"))
    env = ResearchEnv.at(root)
    calls = []
    orig = env.data.read_range

    def spy(start, end, *a, **k):
        calls.append((start, end, a[0] if a else k.get("exchange"), k.get("key")))
        return orig(start, end, *a, **k)
    env.data.read_range = spy
    return env, calls


@pytest.fixture(scope="module")
def screened(lag):
    env, _ = lag
    p = env.root / "screen.yaml"
    p.write_text(yaml.safe_dump(aux_screen_dict()))
    # 선별 전: requires_screen_pass 캠페인은 원장 쓰기 전에 거부
    n0 = len(env.ledger.records("campaigns"))
    with pytest.raises(ScreenGateError, match="did not pass any recorded screen"):
        run_campaign(CampaignTemplate.from_dict(aux_campaign_dict(requires_screen_pass=True)), env=env)
    assert len(env.ledger.records("campaigns")) == n0
    assert cli.main(["--root", str(env.root), "screen", str(p)]) == 0
    sid = env.ledger.records("campaigns")[-1]["id"]
    return sid


@pytest.fixture(scope="module")
def piped(lag, screened):
    env, _ = lag
    cr = run_campaign(CampaignTemplate.from_dict(aux_campaign_dict(from_screen=screened)), env=env)
    cfg = copy.deepcopy(G.load_gate_config())
    cfg["G2"]["null_runs"] = N_NULL
    out = run_pipeline(cr, env=env, top_k=2, n_null=N_NULL, gate_cfg=cfg)
    return cr, out


# ------------------------------------------------------------------ 신호 선별 연결
def test_screen_recorded_with_aux_and_lessons(lag, screened):
    env, _ = lag
    camp = env.ledger.get("campaigns", screened)
    assert camp["grid"]["kind"] == "signal_screen" and camp["grid"]["data"]["aux"] == ["upbit"]
    runs = [r for r in env.ledger.records("runs") if r["campaign_id"] == screened]
    passed = {r["spec"]["entry"]["params"]["thr_bp"] for r in runs
              if r["spec"]["entry"]["block"] == "upbit_lead_impulse" and r["metrics"]["decision"]["passed"]}
    assert passed == {8, 10, 12} and all(r["spec"]["timeframe"] == "1m" for r in runs)
    facts = [json.loads(x) for x in (env.root / "research/lessons/facts.jsonl").read_text().splitlines()]
    assert any(f["kind"] == "signal_screen" and f["campaign_id"] == screened for f in facts)
    saved = yaml.safe_load((env.campaign_dir(screened) / "template.yaml").read_text())
    assert saved["data"]["aux"] == ["upbit"]


def test_screen_gate_matching_rule(lag, screened):
    env, _ = lag
    n0 = len(env.ledger.records("campaigns"))
    bad = [
        aux_campaign_dict(from_screen=screened,                         # 선별하지 않은 값
                          entry={"block": "upbit_lead_impulse", "grid": {"k": [1], "thr_bp": [10, 15]}}),
        aux_campaign_dict(from_screen=screened, timeframe="5m",         # 다른 결정 주기
                          exits={"mode": "atr", "sl_atr": [3.0], "tp_atr": [2.0], "max_hold_min": [5]}),
        aux_campaign_dict(from_screen=screened,                         # 필터가 붙으면 다른 구성
                          filters=[[{"block": "upbit_not_dumping", "grid": {"k": [3], "max_drop_bp": [40]}}]]),
    ]
    for d in bad:
        with pytest.raises(ScreenGateError, match="did not pass screen"):
            run_campaign(CampaignTemplate.from_dict(d), env=env)
    with pytest.raises(ScreenGateError, match="not a signal screen"):
        run_campaign(CampaignTemplate.from_dict(aux_campaign_dict(from_screen="camp-999999")), env=env)
    assert len(env.ledger.records("campaigns")) == n0


# ------------------------------------------------------------------ aux 캠페인 end-to-end + 견고성 관문
def test_aux_campaign_end_to_end_robustness_gate(lag, piped, screened):
    env, calls = lag
    cr, out = piped
    scr = cr.manifest["screen"]
    assert scr["from_screen"] == screened and scr["n_configs"] == 3
    assert cr.manifest["data"]["aux"] == ["upbit"] and "|upbit:" in cr.manifest["data_snapshot"]
    c = out["candidates"][0]
    g0 = c["gates"]["G0"]
    assert g0["passed"] and g0["requires_aux"] == ["upbit"] and g0["uses_experimental"] == ["upbit_lead_impulse"]
    assert c["uses_experimental"] == ["upbit_lead_impulse"] and "human block review" in c["human_review"]
    assert out["uses_experimental"] == ["upbit_lead_impulse"]
    assert c["dev_passed"] and c["gates"]["G6"]["passed"] and c["gates"]["G6"]["reference_engine_match"]
    rb = c["robustness"]
    assert not rb["passed"] and "latency" in rb["failed"]
    assert rb["checks"]["cross_venue"]["passed"] is None and "aux" in rb["checks"]["cross_venue"]["reason"]
    # 견고성 실패 → validated 로 올리지 않음 (candidate 유지, risk.yaml 없음)
    assert c["status"].startswith("validated (robustness FAIL)")
    assert env.registry.state_of(c["spec_id"], 1) == "candidate"
    assert not list((env.registry.dir / "validated").glob("*"))
    assert out["n_robustness_fail"] == 1 and out["n_registry_validated"] == 0
    # 오답노트: 견고성 실패 유형(LATENCY_SENSITIVE) 기록
    facts = [json.loads(x) for x in (env.root / "research/lessons/facts.jsonl").read_text().splitlines()]
    f = next(x for x in facts if x["kind"] == "campaign" and x["campaign_id"] == cr.campaign_id)
    assert "LATENCY_SENSITIVE" in f["candidates"][0]["taxonomy"]["codes"]
    # 리포트
    text = write_report(cr.campaign_id, env=env).read_text()
    assert "human block review required before lockbox" in text and "robustness" in text.lower()
    assert f"screen `{screened}`" in text
    # 잠금 구간은 어느 거래소도 읽지 않음, 업비트는 Dev/Val 범위로만
    cfg = env.data.config
    assert all(k is None and not overlaps_lockbox(s, e, cfg) for s, e, _, k in calls)
    assert {"coinone", "upbit"} <= {x for _, _, x, _ in calls}
    assert env.ledger.records("lockbox") == []


# ------------------------------------------------------------------ 백테스트 = 페이퍼 ctx.aux 구성
def test_aux_backtest_paper_parity(lag, piped):
    from btc_quant.paper.trader import evaluate_signals

    env, _ = lag
    cr, _out = piped
    m1 = load_segment(env, "dev", "coinone", "BTC-KRW", "1m")
    up = load_segment(env, "dev", "upbit", "BTC-KRW", "1m")
    spec = StrategySpec("par", BlockRef("upbit_lead_impulse", {"k": 1, "thr_bp": 10}),
                        ExitSpec(max_hold_min=3, stop_loss_atr=3.0, take_profit_atr=2.0), timeframe="1m",
                        filters=(BlockRef("upbit_not_dumping", {"k": 3, "max_drop_bp": 40}),))
    ctx = aux_context({"upbit": up}, 1)
    cache = SignalCache(m1, ctx, tf_min=1, atr_bars=14)
    eok = cache.entry_ok(spec.entry, spec.filters)
    # 페이퍼: 후행 창(정규화 이력의 슬라이스) + 업비트 창 → evaluate_signals
    gap = pd.Timestamp("2023-03-03 05:00", tz="UTC")
    rng = np.random.default_rng(0)
    bars = list(m1.index[rng.integers(100, len(m1) - 10, 40)]) + [gap + pd.Timedelta(minutes=d) for d in range(-2, 23, 3)]
    bars += list(eok.index[eok.to_numpy()][:10])
    for bar in bars:
        t = bar + pd.Timedelta(minutes=1)
        win = m1.loc[t - pd.Timedelta(minutes=120): t - pd.Timedelta(minutes=1)]
        uwin = up.loc[win.index[0]: t - pd.Timedelta(minutes=1)]
        sig = evaluate_signals(spec, win, bar, aux_m1={"upbit": uwin})
        assert sig["entry_ok"] == bool(eok.loc[bar]), bar
    assert eok.loc[gap: gap + pd.Timedelta(minutes=19)].sum() == 0       # 업비트 공백(평탄 봉) → 신호 없음
    # 캠페인 결과 = run_fast(spec, ctx) (같은 ctx 구성), 기준 엔진과 거래 일치
    s, e = env.data.segment_bounds("dev")
    lh = next(lh for lh, sp in cr.specs.items() if sp.entry.params["thr_bp"] == 10 and sp.exit.sl == 3.0)
    sp = cr.specs[lh]
    res = fast.run_fast(sp, m1, get_cost("coinone_conservative"), s, e, ctx=ctx)
    assert len(res.trades) == int(cr.primary().at[lh, "n_trades"])
    e2 = s + pd.Timedelta(days=20)
    a = fast.run_fast(sp, m1, get_cost("coinone_conservative"), s, e2, ctx=ctx).trades
    b = run_reference(sp, m1, get_cost("coinone_conservative"), s, e2, ctx=ctx).trades
    assert len(a) > 50 and a["entry_time"].equals(b["entry_time"]) and np.allclose(a["net_ret"], b["net_ret"])
