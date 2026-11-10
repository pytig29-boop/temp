"""파이프라인 자체 검증 (research/calibration_study.py): 합성 시장 성질 + quick 모드 구조."""
from __future__ import annotations

import numpy as np
import pandas as pd

from btc_quant.evaluation import gates as G
from btc_quant.research import calibration_study as C


def test_market_properties():
    df = C.gen_market("2023-01-01", "2023-07-01", seed=3)
    assert df.index.is_monotonic_increasing and len(df) == 181 * 1440
    assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()
    r = np.log(df["close"]).diff().dropna()
    assert r.kurt() > 3                                            # 두꺼운 꼬리(1분)
    hv = r.groupby(r.index.hour).std()
    assert hv.loc[12:16].mean() / hv.loc[[0, 1, 2, 3, 4, 5, 23]].mean() > 1.3   # 일중 계절성 (UTC 14시 정점)
    d = np.log(df["close"].resample("1D").last()).diff().dropna()
    assert 0.25 < d.std() * np.sqrt(365) < 1.2
    # 같은 seed → 같은 시장, 엣지는 드리프트만 바꾼다
    a = C.gen_market("2023-01-01", "2023-01-03", seed=9)
    b = C.gen_market("2023-01-01", "2023-01-03", seed=9)
    pd.testing.assert_frame_equal(a, b)


def test_planted_edge_raises_planted_sharpe():
    from btc_quant.research.campaign import prepare_m1
    s, e = pd.Timestamp("2023-01-02", tz="UTC"), pd.Timestamp("2023-07-01", tz="UTC")
    null = prepare_m1(C.gen_market("2023-01-01", "2023-07-01", seed=4, k_edge=0.0))
    plant = prepare_m1(C.gen_market("2023-01-01", "2023-07-01", seed=4, k_edge=0.08))
    assert C.planted_sharpe(plant, s, e) > C.planted_sharpe(null, s, e) + 3


def test_grids_contain_planted_spec():
    from btc_quant.research.campaign import CampaignTemplate
    sizes = {}
    for g in C.GRIDS:
        tpl = CampaignTemplate.from_dict(C.template_for(g, f"t_{g}"))
        sizes[g] = tpl.n_specs()
        ex = C.PLANTED_SPEC.exit
        star = tpl.make_spec(C.PLANTED_SPEC.entry, (), ex.stop_loss_pct, ex.take_profit_pct, ex.max_hold_min)
        assert star.logic_hash() in {s.logic_hash() for s in tpl.enumerate()}
    assert sizes == {"g4": 4, "g12": 12, "g48": 48, "g405": 405}


def test_wilson_and_summary():
    lo, hi = C.wilson(0, 20)
    assert lo == 0 and 0.1 < hi < 0.2
    runs = [{"grid": "g4", "n_specs": 4, "strength": 0.0, "kind": "null", "star_sharpe_dev": -1.0, "star_rank": 3,
             "any_validated": False, "star_validated": False, "any_dev_survivor": False,
             "star_dev_passed": False, "star_outcome": "G2", "star_is_candidate": True, "runtime_sec": 1.0},
            {"grid": "g4", "n_specs": 4, "strength": 0.0, "kind": "null", "star_sharpe_dev": -2.0, "star_rank": 1,
             "any_validated": True, "star_validated": False, "any_dev_survivor": True,
             "star_dev_passed": False, "star_outcome": "G5", "star_is_candidate": False, "runtime_sec": 1.0}]
    s = C.summarize_runs(runs)["by_strength"]
    assert len(s) == 1 and s[0]["any_validated"] == 0.5 and s[0]["star_outcome"] == {"G2": 1, "G5": 1}


def test_candidate_outcome_labels():
    assert C.candidate_outcome({"status": "validated (grade A)", "dev_passed": True}) == "validated"
    assert C.candidate_outcome({"dev_passed": False, "first_failed": "G2"}) == "G2"
    c = {"dev_passed": True, "status": "failed G6", "gates": {"G6": {"ok_sharpe": True, "ok_stress_mdd": False,
                                                                       "ok_mc_mdd": True, "val_holm_passed": False}}}
    assert C.candidate_outcome(c) == "G6(stress_mdd+holm)"


def test_quick_mode_runs(tmp_path):
    cfg = C.quick_config(jobs=1)
    res = C.run_study(cfg, progress=lambda s: None)
    assert set(res) >= {"config", "edge_map", "runs", "summary", "runtime_sec"}
    em = res["edge_map"]
    assert em["k_for_strength"][0.0] == 0.0 and em["k_for_strength"][3.0] > 0
    assert len(res["runs"]) == cfg.n_null_markets + cfg.n_planted_markets
    kinds = {r["kind"] for r in res["runs"]}
    assert kinds == {"null", "planted"}
    for r in res["runs"]:
        assert r["n_specs"] == 4 and 1 <= r["star_rank"] <= 4
        assert isinstance(r["any_validated"], bool) and r["n_candidates"] >= 1
        assert r["star_outcome"] in {"validated", "not_selected", "G0", "G1", "G2", "G3", "G4", "G5"} \
            or r["star_outcome"].startswith(("G6(", "R("))
    planted = next(r for r in res["runs"] if r["kind"] == "planted")
    null = next(r for r in res["runs"] if r["kind"] == "null")
    assert planted["star_sharpe_dev"] > null["star_sharpe_dev"]
    by = res["summary"]["by_strength"]
    assert {r["strength"] for r in by} == {0.0, 3.0}
    md = C.write_report(res, tmp_path / "calib.md", interpretation="## 해석\n테스트")
    text = md.read_text(encoding="utf-8")
    assert "파이프라인 자체 검증" in text and "| 3 |" in text and "## 해석" in text


def test_robustness_fail_is_not_validated():
    c = {"status": "validated (robustness FAIL) (grade A)", "dev_passed": True,
         "robustness": {"failed": ["cost", "alpha"]}}
    assert C.candidate_outcome(c) == "R(cost+alpha)"
    c = {"dev_passed": True, "status": "failed G6", "gates": {"G6": {"ok_sharpe": True, "ok_mdd": False,
                                                                       "ok_stress_sharpe": False, "ok_mc_mdd": True}}}
    assert C.candidate_outcome(c) == "G6(mdd+stress_sr)"


def test_pilot_uses_current_cost_registry():
    from btc_quant.research.campaign import get_cost
    assert C.primary_cost() == get_cost("coinone_conservative")


def test_1m_scenario_grid_and_plant():
    from btc_quant.research.campaign import CampaignTemplate, prepare_m1
    tpl = CampaignTemplate.from_dict(C.template_for("a12", "t_a12"))
    assert tpl.decision_tf == "1m" and tpl.exit_mode == "atr" and tpl.n_specs() == 12
    ex = C.PLANTED_SPEC_1M.exit
    star = tpl.make_spec(C.PLANTED_SPEC_1M.entry, (), ex.sl, ex.tp, ex.max_hold_min)
    assert star.logic_hash() in {s.logic_hash() for s in tpl.enumerate()}
    s, e = pd.Timestamp("2023-01-02", tz="UTC"), pd.Timestamp("2023-04-01", tz="UTC")
    pp = C.plant_params("1m_atr")
    null = prepare_m1(C.gen_market("2023-01-01", "2023-04-01", seed=4, k_edge=0.0, **pp))
    plant = prepare_m1(C.gen_market("2023-01-01", "2023-04-01", seed=4, k_edge=0.12, **pp))
    a = C.planted_stats(null, s, e, C.PLANTED_SPEC_1M)
    b = C.planted_stats(plant, s, e, C.PLANTED_SPEC_1M)
    assert b["sharpe"] > a["sharpe"] + 5 and a["trades_per_day"] > 3


def test_cumulative_tasks_pair_with_main_seeds():
    from btc_quant.research.campaign import CampaignTemplate
    cfg = C.cumulative_config(jobs=1)
    k_for = {s: 0.01 * s for s in cfg.strengths}
    tasks = C.build_tasks(cfg, k_for)
    main = C.build_tasks(C.full_config(jobs=1), k_for)
    main_seed = {(t["kind"], t["strength"], t["seed"]) for t in main if t["study"] == "main"}
    assert all((t["kind"], t["strength"], t["seed"]) in main_seed for t in tasks)
    assert len(tasks) == sum(cfg.cumulative.values()) and all(t["sequence"][-1] == "g12" for t in tasks)
    ex = C.PLANTED_SPEC.exit
    for g in C.CUM_SEQUENCE[:-1]:
        tpl = CampaignTemplate.from_dict(C.template_for(g, f"t_{g}", "calib_cum"))
        star = tpl.make_spec(C.PLANTED_SPEC.entry, (), ex.stop_loss_pct, ex.take_profit_pct, ex.max_hold_min)
        assert star.logic_hash() not in {s.logic_hash() for s in tpl.enumerate()}


# ---------------------------------------------------------------- 롱 온리 기준 2종 보정 (--rcheck)
def test_recent_flat_metrics_and_g4_variant():
    days = pd.date_range("2024-01-01", periods=730, freq="D", tz="UTC")
    daily = pd.Series(0.0, index=days)
    daily.iloc[100:110] = 0.01
    daily.iloc[-20:-15] = -0.002                              # 최근 1년: 5일만 시장에, 약 −1%
    trades = pd.DataFrame({"entry_time": days[[100, 715]], "exit_time": days[[109, 719]]})
    m = C.recent_flat_metrics(daily, trades, 365)
    assert abs(m["recent_exposure_days"] - 5 / 365) < 1e-9 and -0.011 < m["recent_total_return"] < -0.009
    base = {"rolling_frac_positive": 0.8, "rolling_n_windows": 20, "recent_sharpe": -0.5}
    cfg = G.load_gate_config()
    ok0, _ = G.check_g4(base, cfg)
    assert not ok0
    assert C.g4_recent_variant(base, cfg, None) == G.check_g4(base, cfg)          # opt=None → 현재 판정 그대로
    opt = C.G4_RECENT_VARIANTS["g4flat10"]
    ok, d = C.g4_recent_variant({**base, **m}, cfg, opt)
    assert ok and d["recent_flat"]["applied"]
    ok, d = C.g4_recent_variant({**base, **m, "recent_total_return": -0.05}, cfg, opt)
    assert not ok and not d["recent_flat"]["applied"]                             # 손실 허용치 초과
    ok, _ = C.g4_recent_variant({**base, **m, "recent_exposure_days": 0.3}, cfg, opt)
    assert not ok                                                                 # 쉬지 않았음
    ok, _ = C.g4_recent_variant({**base, **m, "rolling_frac_positive": 0.3}, cfg, opt)
    assert not ok                                                                 # 롤링 조건은 그대로
    ok, _ = C.g4_recent_variant(base, cfg, opt)
    assert not ok                                                                 # 최근 지표 없음 → 현재 판정


def test_g4_patch_is_scoped():
    from btc_quant.research import pipeline as PL
    g4, em = G.check_g4, PL.Pipeline.exposure_metrics
    with C._G4RecentPatch(None):
        assert G.check_g4 is g4
    with C._G4RecentPatch(C.G4_RECENT_VARIANTS["g4flat10"]):
        assert G.check_g4 is not g4 and PL.Pipeline.exposure_metrics is not em
    assert G.check_g4 is g4 and PL.Pipeline.exposure_metrics is em


def _cand(g6=True, failed=(), share=0.85, excess=0.1, rep=False):
    reg = {"trend": {"total_pnl_log": 0.5, "max_pnl_share": share, "dominant_excess_vs_matched_bh_annual": excess},
           "vol": {"total_pnl_log": 0.5, "max_pnl_share": 0.4, "dominant_excess_vs_matched_bh_annual": 0.0}}
    return {"g6_passed": g6, "rob_failed": list(failed), "regime": reg, "is_rep": rep}


def test_rcheck_mode_outcomes():
    cfg = G.load_gate_config()
    th = {m: C.rcheck_thresholds(cfg, m) for m in C.RCHECK_MODES}
    assert th["current"]["regime_mode"] == "pnl_share" and th["current"]["regime_max_pnl_share"] == 0.7
    assert th["share90"]["regime_max_pnl_share"] == 0.9 and th["dom_alpha"]["regime_mode"] == "dominant_alpha"
    c = _cand(failed=("regime",), rep=True)                  # 국면 검사만 실패한 S* 대표
    v = {m: C.rcheck_candidate_validated(c, th[m]) for m in C.RCHECK_MODES}
    assert v == {"current": False, "share80": False, "share90": True, "dom_alpha": True, "info": True}
    c2 = _cand(failed=("regime", "alpha"))                   # 알파도 실패 → 어떤 방식이든 validated 아님
    assert not any(C.rcheck_candidate_validated(c2, th[m]) for m in C.RCHECK_MODES)
    assert not any(C.rcheck_candidate_validated(_cand(g6=False), th[m]) for m in C.RCHECK_MODES)
    c3 = _cand(failed=("regime",), excess=-0.2)
    assert not C.rcheck_candidate_validated(c3, th["dom_alpha"]) and C.rcheck_candidate_validated(c3, th["info"])
    out = C.rcheck_mode_outcomes({"rcheck_cands": [c, c3]}, cfg)
    assert out["current"] == {"any_validated": False, "star_validated": False, "n_validated": 0}
    assert out["dom_alpha"] == {"any_validated": True, "star_validated": True, "n_validated": 1}
    assert out["info"]["n_validated"] == 2


def test_rcheck_tasks_share_regime_study_seeds():
    cfg = C.rcheck_full_config(jobs=1)
    k_for = {"null": 0.0, "regime_cond": 0.04, "regime_cond_f": 0.03, "regime_filter": 0.04}
    tasks = C.build_rcheck_tasks(cfg, k_for)
    reg = C.build_regime_tasks(C.regime_full_config(jobs=1), k_for)
    assert [(t["kind"], t["seed"], t["val_regime"]) for t in tasks[:len(reg)]] == \
        [(t["kind"], t["seed"], t["val_regime"]) for t in reg]                    # 같은 시장(공통 난수)
    extra = [t for t in tasks[len(reg):] if t["market"] == "regime"]
    std = [t for t in tasks if t["market"] == "standard"]
    assert len(extra) == cfg.n_extra_null and len(std) == cfg.n_standard_null
    assert {t["kind"] for t in std} == {"std_null"} and all(t["k"] == 0.0 for t in extra + std)
    seeds = [t["seed"] for t in tasks if t["market"] == "regime"]
    assert len(seeds) == len(set(seeds))
    assert all(t["g4_variants"] == list(C.G4_RECENT_VARIANTS) and t["rcheck"] for t in tasks)


def test_rcheck_quick_mode_runs():
    cfg = C.rcheck_quick_config(jobs=1)
    cfg.g4_variants = ("current", "g4flat10")
    res = C.run_rcheck_study(cfg, progress=lambda s: None)
    n_mk = sum(cfg.n_markets.values()) + cfg.n_extra_null + cfg.n_standard_null
    assert len(res["runs"]) == n_mk * 2
    for r in res["runs"]:
        assert set(r["rcheck_modes"]) == set(C.RCHECK_MODES) and r["rcheck_consistent"]
        assert r["kind"] in {"null", "regime_filter", "std_null"}
    diag = [c for r in res["runs"] if r["g4_variant"] == "current" for c in r["rcheck_cands"]]
    assert diag and all(set(c["diag"]["regime_pass"]) == set(C.RCHECK_MODES) for c in diag)
    assert all(c["diag"]["regime_pass"]["info"] for c in diag)
    rows = res["summary"]["rows"]
    assert len(rows) == 2 * len(C.RCHECK_MODES) and res["summary"]["inconsistent"] == []
    md = C.rcheck_tables_markdown(res)
    assert "G4 최근 조건" in md and "조건부 진단" in md
