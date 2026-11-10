"""R1 관문 옵션 (gate_review_20261110, Phase 2): 기본값 = 현재 동작, 각 모드의 의미, 오프라인 평가기와 같은 식,
크기 조절 배수의 기록·적용(위험 사이드카·페이퍼·전진), Val 재사용 규칙 불변."""
from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

from btc_quant.evaluation import gates as G
from btc_quant.research import gate_review_study as GR

sys.path.insert(0, str(Path(__file__).parent))

VAL = {"sharpe": 1.2, "max_dd": 0.10}
STRESS = {"sharpe": -0.3, "max_dd": 0.15}


def cfg_with(**g6) -> dict:
    c = copy.deepcopy(G.load_gate_config())
    vb = g6.pop("val_budget", None)
    c["G6"].update(g6)
    if vb:
        c["G6"]["val_budget"].update(vb)
    return c


# ------------------------------------------------------------------ 기본값 = 현재 동작
def test_defaults_are_current_behaviour():
    c = G.load_gate_config()["G6"]
    assert c["stress_mode"] == "sharpe" and c["drawdown_mode"] == "reject" and c["min_position_scale"] == 0.25
    assert c["val_budget"]["evidence"] == "holm"
    assert G.g6_stress_mode({}) == "sharpe" and G.g6_drawdown_mode({}) == "reject"
    assert G.val_evidence_mode({}) == "holm"
    ok, d = G.check_g6(VAL, STRESS, 0.05)
    assert ok is False and d["ok_stress_sharpe"] is False and d["position_scale"] == 1.0
    assert d["drawdown_mode"] == "reject" and d["stress_mode"] == "sharpe"
    ok, d = G.check_g6({**VAL, "max_dd": 0.30}, {**STRESS, "sharpe": 0.1}, 0.05)
    assert ok is False and d["ok_mdd"] is False
    for bad in ({"stress_mode": "maybe"}, {"drawdown_mode": "shrink"}):
        with pytest.raises(ValueError):
            G.check_g6(VAL, STRESS, 0.05, cfg_with(**bad))
    with pytest.raises(ValueError):
        G.val_evidence_mode({"evidence": "bayes"})
    # 오프라인 평가기의 현재 패키지도 같은 기본
    pkg = GR.current_package()
    assert pkg["val_mode"] == "holm" and pkg["g6_dd_mode"] == "reject" and pkg["g6_stress"] == 0.0


# ------------------------------------------------------------------ 각 모드의 의미
def test_stress_mode_off_reports_but_does_not_gate():
    c = cfg_with(stress_mode="off")
    ok, d = G.check_g6(VAL, STRESS, 0.05, c)
    assert ok is True and d["ok_stress_sharpe"] is False and d["stress_sharpe"] == -0.3
    assert GR.current_package(c)["g6_stress"] is None


@pytest.mark.parametrize("mdd,mc,scale", [(0.10, 0.05, 1.0), (0.40, 0.05, 0.5), (0.10, 0.80, 0.25),
                                          (0.50, 0.40, 0.4), (1.00, 0.05, 0.2), (0.0, 0.0, 1.0)])
def test_position_scale_formula(mdd, mc, scale):
    c = cfg_with(drawdown_mode="size")
    assert G.position_scale(mdd, mc, c["G6"]) == pytest.approx(scale)
    ok, d = G.check_g6({**VAL, "max_dd": mdd}, {**STRESS, "sharpe": 0.1}, mc, c)
    assert d["position_scale"] == pytest.approx(scale)
    assert ok is (scale >= 0.25) and d["ok_position_scale"] is (scale >= 0.25)
    # 오프라인 평가기 같은 식
    pkg = GR.current_package(c)
    assert GR.size_scale({"val_mdd": mdd, "val_mc_mdd95": mc}, pkg) == pytest.approx(scale)


def test_position_scale_nonfinite_fails_and_floor_is_a_preference():
    c = cfg_with(drawdown_mode="size")
    assert G.position_scale(float("nan"), 0.1, c["G6"]) == 0.0
    assert G.position_scale(0.1, None, c["G6"]) == 0.0
    c5 = cfg_with(drawdown_mode="size", min_position_scale=0.5)
    assert G.check_g6({**VAL, "max_dd": 0.30}, {**STRESS, "sharpe": 0.1}, 0.05, c)[0] is True     # 0.667
    assert G.check_g6({**VAL, "max_dd": 0.50}, {**STRESS, "sharpe": 0.1}, 0.05, c5)[0] is False   # 0.4 < 0.5


def test_combined_evidence_same_formula_as_offline():
    rng = np.random.default_rng(0)
    for _ in range(200):
        g5 = {"sr": float(rng.normal(0.08, 0.05)), "sr0": float(abs(rng.normal(0.03, 0.02))),
              "skew": float(rng.normal(0, 1)), "kurtosis": float(3 + abs(rng.normal(0, 3))),
              "n_obs": int(rng.integers(300, 1800))}
        p, vsr = float(rng.uniform(0, 1)), float(rng.normal(0.5, 1.5))
        ce = G.combined_val_evidence(G.dsr_z(g5), p, vsr, 0.05)
        c = {"g5_n_obs": g5["n_obs"], "g5_sr": g5["sr"], "g5_skew": g5["skew"], "g5_kurt": g5["kurtosis"],
             "g5_var": 0.0, "val_p": p, "val_sharpe": vsr}
        ok, info = GR.val_evidence(c, {"val_mode": "stouffer", "val_alpha": 0.05}, [], G.dsr_z(g5))
        assert ce["passed"] is ok and ce["z"] == pytest.approx(info["z"], abs=1e-12)
    # DSR z 와 DSR 값의 관계: Φ(z) = selection.deflated_sharpe_ratio
    from btc_quant.evaluation.selection import probabilistic_sharpe_ratio
    g5 = {"sr": 0.1, "sr0": 0.04, "skew": -0.3, "kurtosis": 6.0, "n_obs": 1200}
    z = G.dsr_z(g5)
    assert 0.5 * (1 + math.erf(z / math.sqrt(2))) == pytest.approx(
        probabilistic_sharpe_ratio(0.1, 1200, -0.3, 6.0, 0.04), abs=1e-12)
    # Val Sharpe ≤ 0 이면 결합 p 가 작아도 실패
    assert G.combined_val_evidence(5.0, 0.01, -0.1, 0.05)["passed"] is False
    assert G.combined_val_evidence(5.0, 0.01, 0.1, 0.05)["passed"] is True


def test_pipeline_val_evidence_modes():
    from btc_quant.research.pipeline import val_evidence
    vb = {"evidence": "holm", "fixed_p": 0.05, "combined_alpha": 0.05}
    g5 = {"sr": 0.12, "sr0": 0.03, "skew": 0.0, "kurtosis": 3.0, "n_obs": 1500}
    assert val_evidence(vb, {"holm_passed": False}, 0.001, 1.0, g5) == {"evidence": "holm", "evidence_passed": False}
    assert val_evidence({**vb, "evidence": "fixed"}, {"holm_passed": False}, 0.04, 1.0, g5)["evidence_passed"]
    assert not val_evidence({**vb, "evidence": "fixed"}, {"holm_passed": True}, 0.06, 1.0, g5)["evidence_passed"]
    e = val_evidence({**vb, "evidence": "combined"}, {"holm_passed": False}, 0.30, 0.2, g5)
    assert e["evidence"] == "combined" and e["evidence_passed"] is True and e["combined"]["z_dev"] > 3
    assert val_evidence({**vb, "evidence": "combined"}, {"holm_passed": True}, 0.01, 1.0, None)["evidence_passed"] \
        is False                                              # Dev 증거 없으면 실패


def test_offline_package_maps_config_modes():
    c = cfg_with(stress_mode="off", drawdown_mode="size", min_position_scale=0.5,
                 val_budget={"evidence": "combined", "combined_alpha": 0.04})
    pkg = GR.current_package(c)
    assert pkg["val_mode"] == "stouffer" and pkg["val_alpha"] == 0.04 and pkg["g6_min_scale"] == 0.5
    assert GR.current_package(cfg_with(val_budget={"evidence": "fixed", "fixed_p": 0.02}))["val_alpha"] == 0.02
    # size 모드 라벨: 오프라인 'size' 와 실제 candidate_outcome 'size'
    from btc_quant.research.calibration_study import candidate_outcome
    lab = candidate_outcome({"status": "failed G6", "dev_passed": True, "gates": {"G6": {
        "ok_sharpe": True, "ok_mdd": False, "ok_mc_mdd": False, "ok_stress_sharpe": False, "stress_mode": "off",
        "drawdown_mode": "size", "ok_position_scale": False, "val_holm_passed": False,
        "val_evidence_passed": True}}})
    assert lab == "G6(size)"
    lab = candidate_outcome({"status": "failed G6", "dev_passed": True, "gates": {"G6": {
        "ok_sharpe": False, "ok_mdd": False, "ok_stress_sharpe": False, "ok_mc_mdd": True,
        "val_holm_passed": False}}})
    assert lab == "G6(sharpe+mdd+stress_sr+holm)"            # 기본 모드 라벨은 이전과 같다


# ------------------------------------------------------------------ 파이프라인: 재사용 규칙 불변 + 기록
@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    from test_research_synth import make_root, template_dict

    from btc_quant.research.campaign import CampaignTemplate, ResearchEnv, run_campaign
    env = ResearchEnv.at(make_root(tmp_path_factory.mktemp("r1"), planted=True, seed=7))
    cr = run_campaign(CampaignTemplate.from_dict(template_dict(name="r1", filters=[[]])), env=env)
    return env, cr


R1_ALL = {"stress_mode": "off", "drawdown_mode": "size", "min_position_scale": 0.25,
          "val_budget": {"evidence": "combined", "combined_alpha": 0.05}}


def test_val_reuse_rule_unchanged_under_r1(planted):
    """이전 Val 사용이 실패였으면 R1(완화) 기준으로도 같은 Val 에서 통과할 수 없다 (camp-000013/17 유형)."""
    from btc_quant.research.pipeline import Pipeline
    env, cr = planted
    lh = cr.ranked()["logic_hash"].iloc[-1]
    spec = cr.specs[lh]
    p = Pipeline(cr, env, gate_cfg=cfg_with(**copy.deepcopy(R1_ALL)), register=False, reference_check=False,
                 robustness=False, lessons=False)
    m1v, vs, ve = p.load_val()
    vid = p.val_id(vs, ve)
    op = env.ledger.open_val_evaluation(lh, cr.campaign_id, vid, 20)
    env.ledger.record_val_result(op["id"], False, 0.001, {}, {"holm_passed": True})   # 옛 기록(evidence 키 없음)
    n0 = len(env.ledger.val_history(vid))
    g, *_ = p.g6(spec, m1v, vs, ve, cr.template.costs["primary"], cr.template.costs["stress"], record=False,
                 dev_g5={"sr": 0.5, "sr0": 0.0, "skew": 0.0, "kurtosis": 3.0, "n_obs": 1000})
    assert g["val_budget"]["reused"] and g["val_budget"]["prior_passed"] is False
    assert g["val_budget"]["evidence_passed"] is True              # 옛 기록: holm_passed 를 증거 결과로 읽음
    assert g["passed"] is False and len(env.ledger.val_history(vid)) == n0


def test_pipeline_records_r1_fields_and_scaled_risk(planted):
    from btc_quant.research.pipeline import Pipeline, public_pipeline, run_pipeline
    env, cr = planted
    cfg = cfg_with(**copy.deepcopy(R1_ALL))
    cfg["G2"]["null_runs"] = 100
    out = run_pipeline(cr, env=env, top_k=2, n_null=100, gate_cfg=cfg, register=False, reference_check=False,
                       robustness=False, lessons=False)
    evald = [c for c in out["candidates"] if c["gates"].get("G6")]
    fresh = [c for c in evald if not c["gates"]["G6"].get("val_budget", {}).get("reused")]
    assert fresh, "planted market: at least one Dev survivor evaluated on Val for the first time"
    for c in fresh:
        g6 = c["gates"]["G6"]
        assert g6["stress_mode"] == "off" and g6["drawdown_mode"] == "size" and "position_scale" in g6
        assert g6["val_evidence"] == "combined" and "combined" in g6["val_budget"]
        assert g6["val_budget"]["combined"]["z_dev"] == pytest.approx(G.dsr_z(c["gates"]["G5"]))
        if g6["passed"]:
            assert c["position_scale"] == pytest.approx(g6["position_scale"])
    pub = public_pipeline(out)
    for c in pub["candidates"]:
        assert c.get("position_scale") in (None, "withheld (Val-derived)")
        g = c["gates"].get("G6")
        assert g is None or set(g) <= {"passed", "outcome", "val_budget", "withheld"}
    # 위험 사이드카: position_scale 기록 (가짜 후보로 직접 확인)
    p = Pipeline(cr, env, gate_cfg=cfg, register=False, reference_check=False, robustness=False, lessons=False)
    spec = next(iter(cr.specs.values()))
    rl = {"daily_loss_limit": -0.03, "weekly_loss_limit": -0.06, "drawdown_limit": -0.12, "max_trades_per_day": 5}
    path = p.write_risk_yaml(spec, {"risk_limits": rl, "grade": "B", "position_scale": 0.4})
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert doc["position_scale"] == 0.4
    from btc_quant.paper.service import load_position_scale
    assert load_position_scale(Path(path)) == 0.4 and load_position_scale(None) == 1.0
    Path(path).unlink()


def test_scaled_risk_limits_follow_scaled_returns():
    import pandas as pd
    r = pd.Series(np.random.default_rng(1).normal(0.001, 0.03, 180),
                  index=pd.date_range("2026-01-01", periods=180, freq="D", tz="UTC"))
    full = G.risk_limits_from_validation(r, 0.4, 2.0)
    half = G.risk_limits_from_validation(r * 0.5, 0.4 * 0.5, 2.0)
    assert half["inputs"]["daily_p1"] == pytest.approx(full["inputs"]["daily_p1"] * 0.5)
    assert half["inputs"]["mc_mdd95"] == pytest.approx(0.2)


# ------------------------------------------------------------------ 페이퍼: notional·실현 기여에 배수
def test_paper_trader_applies_position_scale(tmp_path):
    """같은 체결 흐름: 거래(진입·청산 시점)는 같고, 진입 명목은 배수만큼, 거래당 자산 변화도 배수만큼."""
    import pandas as pd
    from test_paper_trader import (SPECS, T0, _setup, book_at, make_trader, paper_trades, run_live)

    from btc_quant.paper.bars import M1History
    from btc_quant.paper.bars import ts_minute
    spec = SPECS[0]
    raw_seed, trades = _setup(11)
    out = {}
    for sc in (1.0, 0.5):
        hist = M1History(max_rows=10 ** 6)
        hist.seed(raw_seed)
        tr = make_trader(spec, tmp_path / str(sc), position_scale=sc)
        tr.attach(hist, book_at(hist.last_close))
        run_live(tr, hist, trades, ts_minute(T0), (T0 + pd.Timedelta(minutes=2 * 1440 + 1)).timestamp())
        out[sc] = (tr, paper_trades(tr))
    (t1, p1), (t5, p5) = out[1.0], out[0.5]
    assert len(p1) >= 5 and len(p1) == len(p5)
    assert (p1["entry_time"].to_numpy() == p5["entry_time"].to_numpy()).all()
    assert t5.state_dict()["position_scale"] == 0.5
    r1 = t1.equity / t1.initial_equity - 1
    r5 = t5.equity / t5.initial_equity - 1
    assert abs(r5) < abs(r1) and np.sign(r5) == np.sign(r1)
    with pytest.raises(ValueError):
        make_trader(spec, tmp_path / "bad", position_scale=0.0)


def test_forward_reads_sidecar_position_scale(planted):
    from btc_quant.research.forward import ForwardStrategy, _sidecar_scale
    env, _ = planted
    assert _sidecar_scale(env, "validated", "nope", 1) == 1.0
    p = env.registry.sidecar_path("validated", "fwdscale", 1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"grade": "B", "position_scale": 0.3}), encoding="utf-8")
    try:
        assert _sidecar_scale(env, "validated", "fwdscale", 1) == 0.3
    finally:
        p.unlink()
    assert ForwardStrategy.__dataclass_fields__["position_scale"].default == 1.0
