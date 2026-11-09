"""파이프라인 자체 검증 (research/calibration_study.py): 합성 시장 성질 + quick 모드 구조."""
from __future__ import annotations

import numpy as np
import pandas as pd

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
            or r["star_outcome"].startswith("G6(")
    planted = next(r for r in res["runs"] if r["kind"] == "planted")
    null = next(r for r in res["runs"] if r["kind"] == "null")
    assert planted["star_sharpe_dev"] > null["star_sharpe_dev"]
    by = res["summary"]["by_strength"]
    assert {r["strength"] for r in by} == {0.0, 3.0}
    md = C.write_report(res, tmp_path / "calib.md", interpretation="## 해석\n테스트")
    text = md.read_text(encoding="utf-8")
    assert "파이프라인 자체 검증" in text and "| 3 |" in text and "## 해석" in text
