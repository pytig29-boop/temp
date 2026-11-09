"""v4 통합: 결정 주기 1m/5m + ATR 청산 캠페인 end-to-end, 검정력 점검, risk.yaml, 오답노트, 리포트.

합성 시장 = test_research_synth 의 planted(돌파 후 1시간 드리프트). 귀무 seed 는 200 으로 줄인 설정을 쓴다
(gate_cfg G2.null_runs = 200 — 관문 의미는 같고 실행 시간만 줄인다).
"""
from __future__ import annotations

import copy
import json

import pytest
import yaml
from test_research_synth import make_root, template_dict

from btc_quant.evaluation import gates as G
from btc_quant.research.campaign import (CampaignError, CampaignTemplate, PowerCheckError, ResearchEnv,
                                         load_campaign, run_campaign)
from btc_quant.research.pipeline import run_pipeline
from btc_quant.research.report import write_report
from btc_quant.strategy.spec import StrategySpec

N_NULL = 200


def _cfg():
    cfg = copy.deepcopy(G.load_gate_config())
    cfg["G2"]["null_runs"] = N_NULL
    return cfg


def _atr_tpl(name, tf, lookbacks, sl, tp, **over):
    d = template_dict(name=name, timeframe=tf, filters=[[]],
                      entry={"block": "donchian_breakout", "grid": {"lookback": lookbacks}},
                      exits={"mode": "atr", "sl_atr": sl, "tp_atr": tp, "max_hold_min": [60], "atr_bars": 14})
    d["costs"] = {"stress": "coinone_base"}      # test_research_pipeline 과 같은 이유 (합성 엣지 vs 고정 stress)
    d.update(over)
    return d


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    return ResearchEnv.at(make_root(tmp_path_factory.mktemp("tfatr"), planted=True, seed=7))


@pytest.fixture(scope="module")
def run_1m(env):
    cr = run_campaign(CampaignTemplate.from_dict(
        _atr_tpl("tf1", "1m", [120, 240, 480], [8.0, 12.0], [10.0, 15.0])), env=env)
    out = run_pipeline(cr, env=env, top_k=2, n_null=N_NULL, gate_cfg=_cfg())
    return cr, out


@pytest.fixture(scope="module")
def run_5m(env):
    cr = run_campaign(CampaignTemplate.from_dict(
        _atr_tpl("tf5", "5m", [24, 48, 96], [2.0, 3.0], [3.0, 4.0], power={"target_sharpe": 3.0})), env=env)
    out = run_pipeline(cr, env=env, top_k=2, n_null=N_NULL, gate_cfg=_cfg())
    return cr, out


# ------------------------------------------------------------------ 템플릿
@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d.update(timeframe="30m"), "timeframe must be one of"),
    (lambda d: d["exits"].update(max_hold_min=[62]), "multiples of 5"),
    (lambda d: d["exits"].update(sl_pct=[1.0]), "atr mode"),
    (lambda d: d["exits"].update(atr_bars=[14, 20]), "atr_bars"),
    (lambda d: d["exits"].update(mode="trail"), "exits.mode"),
    (lambda d: d["data"].update(aux=["binance"]), "not supported"),
    (lambda d: d.update(entry={"block": "upbit_lead_impulse"}), "require aux"),
    (lambda d: d.update(power={"target": 2}), "power"),
])
def test_v4_template_validation(mutate, msg):
    d = _atr_tpl("v", "5m", [48], [2.0], [3.0])
    mutate(d)
    with pytest.raises(CampaignError, match=msg):
        CampaignTemplate.from_dict(d)


def test_template_specs_carry_timeframe_and_atr():
    tpl = CampaignTemplate.from_dict(_atr_tpl("v", "1m", [240], [2.0, 3.0], [3.0], ))
    tpl1 = CampaignTemplate.from_dict({**_atr_tpl("v", "1m", [240], [2.0], [3.0]),
                                       "exits": {"mode": "atr", "sl_atr": [2.0], "tp_atr": [3.0],
                                                 "max_hold_min": [7]}})       # 1m: 임의 분
    assert tpl1.exit_combos() == [(2.0, 3.0, 7)] and tpl1.atr_bars == 14
    specs = tpl.enumerate()
    assert len(specs) == 2 and all(s.timeframe == "1m" and s.exit.is_atr for s in specs)
    assert {s.exit.stop_loss_atr for s in specs} == {2.0, 3.0}
    rec = tpl.to_record()
    assert rec["timeframe"] == "1m" and rec["exits"]["mode"] == "atr" and rec["exits"]["atr_bars"] == 14
    legacy = CampaignTemplate.from_dict(template_dict())          # 구 템플릿: 5m, 고정 %
    assert legacy.decision_tf == "5m" and legacy.exit_mode == "pct"
    assert all(s.timeframe == "5m" and not s.exit.is_atr for s in legacy.enumerate())
    assert "timeframe" not in legacy.enumerate()[0].to_dict()      # 해시 하위 호환


# ------------------------------------------------------------------ 검정력
def test_power_check_warns_or_refuses_before_ledger(env):
    n_camp = len(env.ledger.records("campaigns"))
    d = _atr_tpl("pw", "5m", [48], [2.0], [3.0], power={"target_sharpe": 0.5})
    with pytest.raises(PowerCheckError, match="required annual Sharpe"):
        run_campaign(CampaignTemplate.from_dict(d), env=env, strict_power=True)
    with pytest.raises(PowerCheckError, match="no power.target_sharpe"):
        run_campaign(CampaignTemplate.from_dict(_atr_tpl("pw", "5m", [48], [2.0], [3.0])), env=env,
                     strict_power=True)
    assert len(env.ledger.records("campaigns")) == n_camp           # 거부 = 원장 쓰기 없음


# ------------------------------------------------------------------ 1m + ATR end-to-end
def test_1m_atr_campaign_end_to_end(env, run_1m):
    cr, out = run_1m
    assert cr.manifest["timeframe"] == "1m" and cr.manifest["exits"]["mode"] == "atr"
    assert {"sl_atr", "tp_atr", "atr_bars"} <= set(cr.results.columns)
    assert cr.manifest["power"]["required_sharpe_ann"] > 0 and cr.manifest["power"]["warning"]
    led = [r for r in env.ledger.records("runs") if r["campaign_id"] == cr.campaign_id]
    assert led and all(r["spec"]["timeframe"] == "1m" and "stop_loss_atr" in r["spec"]["exit"] for r in led)
    top = out["candidates"][0]
    assert top["spec"]["entry"]["params"] == {"lookback": 240} and top["spec"]["timeframe"] == "1m"
    g = top["gates"]
    assert all(g[k]["passed"] for k in ("G0", "G1", "G2", "G3", "G4", "G5")), {k: g[k]["passed"] for k in g}
    assert g["G0"]["timeframe"] == "1m" and g["G0"]["exit_mode"] == "atr"
    assert g["G2"]["tf_min"] == 1 and g["G2"]["atr_bars"] == 14 and g["G2"]["p_value"] <= 0.05
    changes = {n["change"].split("=")[0] for n in g["G3"]["neighbors"]}
    assert {"entry.lookback", "sl_atr", "tp_atr"} <= changes            # ATR 격자에서도 한 칸 이동
    g6 = g["G6"]
    assert g6["reference_engine_match"] and g6["rule"] == "b" and g6["val_k"] == 1
    # 재로딩: 저장된 템플릿·스펙이 같은 해시
    cr2 = load_campaign(cr.campaign_id, env=env)
    assert set(cr2.specs) == set(cr.specs) and cr2.template.decision_tf == "1m"
    _check_robustness_registry_consistency(env, top)


def _check_robustness_registry_consistency(env, c):
    """견고성 실패 ⇔ status 'robustness FAIL' ⇔ 레지스트리 candidate·risk.yaml 없음."""
    assert c["gates"]["G6"]["passed"] and c["robustness"] is not None
    sid = c["spec_id"]
    risk = env.registry.dir / "validated" / f"{sid}_v1.risk.yaml"
    if c["robustness"]["passed"]:
        assert c["status"].startswith("validated (grade")
        assert env.registry.state_of(sid, 1) == "validated" and risk.exists()
    else:
        assert c["status"].startswith("validated (robustness FAIL)")
        assert env.registry.state_of(sid, 1) == "candidate" and not risk.exists()
        assert "blocked" in c["registry"]
        trs = [t["to_state"] for t in env.ledger.transitions(sid)]
        assert trs == ["draft", "candidate"]


# ------------------------------------------------------------------ 5m + ATR → validated, risk.yaml, 오답노트, 리포트
def test_5m_atr_validated_risk_yaml_for_paper(env, run_5m):
    from btc_quant.live.risk import RiskLimits
    from btc_quant.paper.service import DEFAULT_LIMITS, load_risk_meta, sidecar_path

    cr, out = run_5m
    c = out["candidates"][0]
    assert c["robustness"]["passed"], c["robustness"]["verdict"]
    _check_robustness_registry_consistency(env, c)
    spec_path = env.registry.dir / "validated" / f"{c['spec_id']}_v1.yaml"
    assert StrategySpec.from_yaml(spec_path).logic_hash() == c["logic_hash"]
    rp = sidecar_path(spec_path)
    assert rp.exists() and str(rp) == c["registry"]["risk_yaml"]
    doc = yaml.safe_load(rp.read_text())
    assert set(doc["limits"]) == {"daily_loss", "weekly_loss", "max_drawdown", "max_trades_per_day",
                                  "expected_fee_rate"}
    grade, lim = load_risk_meta(rp, "B", DEFAULT_LIMITS)
    rl = c["risk_limits"]
    assert grade == c["grade"] and isinstance(lim, RiskLimits)
    assert lim.daily_loss == pytest.approx(rl["daily_loss_limit"])
    assert lim.weekly_loss == pytest.approx(rl["weekly_loss_limit"])
    assert lim.max_drawdown == pytest.approx(rl["drawdown_limit"])
    assert lim.max_trades_per_day >= rl["max_trades_per_day"] and lim.expected_fee_rate == 0.0
    assert cr.manifest["power"]["target_sharpe_ann"] == 3.0


def test_lessons_fact_and_report(env, run_1m, run_5m):
    for cr, out in (run_1m, run_5m):
        assert out["lessons_fact"]
        pj = json.loads((cr.dir / "pipeline.json").read_text())
        assert pj["lessons_fact"] == out["lessons_fact"]
    facts = [json.loads(x) for x in (env.root / "research" / "lessons" / "facts.jsonl").read_text().splitlines()]
    by = {f["campaign_id"]: f for f in facts if f["kind"] == "campaign"}
    assert {run_1m[0].campaign_id, run_5m[0].campaign_id} <= set(by)
    f1 = by[run_1m[0].campaign_id]["candidates"][0]
    assert f1["timeframe"] == "1m" and f1["robustness_dev"] is not None
    assert (env.root / "research" / "lessons" / "LESSONS.md").exists()
    text = write_report(run_5m[0].campaign_id, env=env).read_text()
    for s in ("Power check", "Required annual Sharpe stored", "Candidate selection", "Signal screen linkage",
              "Robustness suite", "val_holm_passed", "stress_sharpe", "rule=b", "risk.yaml",
              "Decision timeframe **5m**", "atr mode"):
        assert s in text, s
