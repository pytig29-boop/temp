"""관문 재검토 합성 보정 (research/gate_review_study.py): 오프라인 평가기의 순차 논리 + quick 모드 일치 점검."""
from __future__ import annotations

import copy
from dataclasses import asdict

import pytest

from btc_quant.evaluation import gates as G
from btc_quant.evaluation import selection
from btc_quant.research import calibration_study as C
from btc_quant.research import gate_review_study as S


def _cand(rank=1, lh=None, **kw):
    """모든 관문을 넉넉히 통과하는 후보 원시 레코드 (필요한 값만 덮어씀)."""
    c = {"rank": rank, "logic_hash": lh or f"h{rank}", "is_star": False, "abl_new": [], "g0_pass": True,
         "g1_n": 1000, "g1_recent_n": 200, "sharpe_w": 2.0, "g2_p": 0.002, "g2_ok_null_runs": True,
         "g3_sel": 2.0, "g3_med": 1.8, "g3_n": 4, "g4_roll": 0.9, "g4_nwin": 50, "g4_recent": 1.0,
         "g5_sr": 0.12, "g5_skew": 0.0, "g5_kurt": 3.0, "g5_n_obs": 1700, "g5_var": 0.0004,
         "val_sharpe": 2.5, "val_mdd": 0.05, "val_stress_sharpe": 1.0, "val_mc_mdd95": 0.10, "val_p": 0.01,
         "val_n_days": 181, "val_ref_match": True,
         "lat_base_sharpe": 2.0, "lat_min_retention": 0.9, "out_sharpes": [1.5, 1.6], "loyo_min": 1.0,
         "loyo_skipped": False, "cost_sharpes": {"1": 2.0, "1.5": 1.5, "2": 1.0},
         "reg_trend_share": 0.4, "reg_trend_total": 1.0, "reg_vol_share": 0.5, "reg_vol_total": 1.0,
         "alpha_t": 3.0, "alpha_skipped": False}
    c.update(kw)
    return c


def _rec(camps, **kw):
    rec = {"id": "x", "group": "t", "kind": "planted", "scenario": "5m", "strength": 2.0, "seed": 2,
           "campaigns": [{"grid": "g", "n_specs": 12, "k_eff": 1, "pbo": 0.1, "has_star": True, "cands": cs, "pos": i + 1}
                         for i, cs in enumerate(camps)]}
    rec.update(kw)
    return rec


def test_current_package_matches_gates_yaml():
    cfg = G.load_gate_config()
    p = S.current_package()
    assert p["g1_min_trades"] == cfg["G1"]["min_trades"] and p["g5_dsr"] == cfg["G5"]["min_dsr"]
    assert p["g6_mc"] == cfg["G6"]["max_mc_mdd95"] and p["val_alpha"] == cfg["G6"]["val_budget"]["alpha"]
    assert p["rob_regime"] == cfg["robustness"]["regime_max_pnl_share"] and p["val_mode"] == "holm"
    # 수집용 설정은 null_runs 만 바꾼다
    sc = S.study_gate_cfg(123)
    assert sc["G2"]["null_runs"] == 123 and cfg["G2"]["null_runs"] != 123
    sc["G2"]["null_runs"] = cfg["G2"]["null_runs"]
    assert sc == cfg


def test_dsr_recomputation_matches_selection():
    c = _cand(g5_sr=0.05, g5_skew=-0.3, g5_kurt=6.0, g5_n_obs=1765, g5_var=0.0009)
    for n in (1.0, 3.5, 40.0):
        want = selection.deflated_sharpe_ratio(0.05, 1765, -0.3, 6.0, n, 0.0009)
        assert S._phi(S.dsr_z(c, n)) == pytest.approx(want, abs=1e-9)
    assert S.n_trials_for({"dsr_n": "current"}, 2, 12, 60, 7) == 10.0
    assert S.n_trials_for({"dsr_n": "current"}, 3, 12, 6, 7) == 3.0
    assert S.n_trials_for({"dsr_n": "sum_keff"}, 2, 12, 60, 7) == 7.0


def test_sequential_logic_dev_then_val_holm_then_robustness():
    pkg = S.current_package()
    cands = [_cand(1, val_p=0.20, is_star=True),          # Dev 통과, Val k=1 문턱 0.10 → holm 실패
             _cand(2, g2_p=0.30),                          # Dev 실패(G2) → Val 을 쓰지 않는다
             _cand(3, val_p=0.04),                         # Dev 통과, k=2: Holm(0.20, 0.04; m=2) → 0.04 ≤ 0.05 통과
             _cand(4, val_p=0.04, alpha_t=1.0),            # k=3: 0.04 ≤ 0.10/3? 아니오 → (0.04,0.04,0.20) 첫 순위 0.033 실패
             _cand(5, val_p=0.001, val_stress_sharpe=-0.5, val_mc_mdd95=0.3)]
    ev = S.evaluate_market(_rec([cands]), pkg)
    lab = [c["label"] for c in ev["cands"]]
    assert lab == ["G6(holm)", "G2", "validated", "G6(holm)", "G6(stress_sr+mc)"]
    assert [c.get("val_k") for c in ev["cands"]] == [1, None, 2, 3, 4]
    assert ev["any_validated"] and not ev["star_validated"] and ev["val_used"] == 4
    # 견고성 실패 라벨, 하나 빼기
    ev2 = S.evaluate_market(_rec([[_cand(1, alpha_t=1.0, lat_min_retention=0.2)]]), pkg)
    assert ev2["cands"][0]["label"] == "R(latency+alpha)" and not ev2["any_validated"]
    assert S.evaluate_market(_rec([[_cand(1, alpha_t=1.0)]]), S.without_atoms(pkg, ["R_alpha"]))["any_validated"]
    # 고정 p 문턱(Holm 끔)이면 4번째도 통과, Stouffer 는 Dev z 가 크면 Val p 0.20 도 통과
    fixed = S.with_changes(pkg, val_mode="fixed", val_alpha=0.05)
    assert [c["label"] for c in S.evaluate_market(_rec([cands]), fixed)["cands"]][3] == "R(alpha)"
    st = S.with_changes(pkg, val_mode="stouffer", val_alpha=0.05)
    assert S.evaluate_market(_rec([cands]), st)["cands"][0]["label"] == "validated"
    weak = _cand(1, val_p=0.20, g5_sr=0.045)               # Dev z ≈ 1.85, Val z ≈ 0.84 → 결합 z ≈ 1.9 통과
    assert S.evaluate_market(_rec([[weak]]), st)["any_validated"]
    assert not S.evaluate_market(_rec([[_cand(1, val_p=0.20, g5_sr=0.045, val_sharpe=-0.1)]]),
                                 S.with_changes(st, g6_sharpe=None))["any_validated"]


def test_cumulative_program_trials_val_reuse_and_cap():
    pkg = S.current_package()
    # 캠페인 1: 필터 후보가 Dev 생존 → 기여도 재실행 시도 2개가 원장에 더해진다. 캠페인 2 의 DSR 시도 수에 반영.
    c1 = [_cand(1, lh="a", val_p=0.03, abl_new=["d1", "d2"])]
    c2 = [_cand(1, lh="a", val_p=0.03), _cand(2, lh="b", val_p=0.03, is_star=True)]
    rec = _rec([c1, c2], campaigns=None)
    rec["campaigns"] = [{"grid": "c7", "n_specs": 12, "k_eff": 1, "pbo": 0.1, "cands": c1, "pos": 1},
                        {"grid": "g12", "n_specs": 4, "k_eff": 2, "pbo": 0.1, "has_star": True, "cands": c2, "pos": 2}]
    ev = S.evaluate_market(rec, pkg)
    assert ev["cands"][0]["n_trials"] == 1.0
    assert ev["cands"][1]["n_trials"] == pytest.approx((12 + 2 + 4) * 2 / 4)       # N_family·K/N_campaign
    assert ev["cands"][1].get("val_reused") and ev["cands"][1]["label"] == "validated"   # 같은 스펙: 새 사용 아님
    assert ev["cands"][2]["val_k"] == 2 and ev["val_used"] == 2
    assert ev["cands"][2]["label"] == "validated" and ev["star_validated"]           # Holm(0.03, 0.03; m=2) 통과
    alt = S.evaluate_market(rec, S.with_changes(pkg, dsr_n="sum_keff"))
    assert alt["cands"][1]["n_trials"] == 3.0
    # 이전에 G6 를 떨어진 스펙은 같은 Val 에서 다시 통과할 수 없다
    rec2 = copy.deepcopy(rec)
    rec2["campaigns"][0]["cands"][0]["val_p"] = 0.5
    rec2["campaigns"][1]["cands"][0]["val_p"] = 0.001
    assert S.evaluate_market(rec2, pkg)["cands"][1]["label"] == "G6(holm)"
    # Val 사용 상한
    capped = S.evaluate_market(rec, S.with_changes(pkg, val_cap=1))
    assert capped["cands"][2].get("val_exhausted") and capped["cands"][2]["label"] == "G6(holm)"


def test_build_tasks_structure():
    edges = {sc: {"k_for_strength": {"1": 0.01, "1.5": 0.012, "2": 0.015, "3": 0.02}, "k_breakeven": 0.005}
             for sc in S.SCENARIOS}
    edges["regime"] = {"k_for": {"regime_cond_f": 0.03}}
    tasks = S.build_tasks(edges, scale=0.5)
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids))
    n_act = sum(t["actual"] for t in tasks)
    assert n_act >= 20 and all(t["actual"] for t in tasks[:n_act])       # 일치 점검 시장이 맨 앞
    assert {t["group"][0] for t in tasks} == {"a", "b", "c"}
    reg = [t for t in tasks if t["group"] == "a_regime_null"]
    assert {t["val_regime"] for t in reg} == set(C.REGIMES)
    assert all(t["val_regime"] == C.REGIMES[t["seed"] % 3] for t in reg)
    bigger = {t["id"] for t in S.build_tasks(edges, scale=1.0)}
    assert set(ids) <= bigger                                             # scale 을 키우면 같은 id 에 이어서 추가


def test_quick_mode_collect_evaluate_consistency_and_analyze(tmp_path):
    """quick 구간(Dev 8개월·Val 2개월)에서 심은 시장 1 + 2캠페인 프로그램 1: 허용 모드 원시 수집 + 실제 파이프라인 →
    오프라인 평가(현재 패키지)가 실제 판정과 같아야 한다. 이어서 분석·표 생성의 구조 점검 (수치 결론용 아님)."""
    base = {"layout": asdict(C.QUICK_LAYOUT), "n_null": 100, "top_k": 3, "actual": True, "reference_check": True,
            "tmp_dir": str(tmp_path)}
    tasks = [{**base, "id": "q_pl", "group": "b_5m", "kind": "planted", "scenario": "5m", "strength": 2.0, "k": 0.08,
              "seed": 8, "grid": "g12"},
             {**base, "id": "q_cum", "group": "c_null", "kind": "null", "scenario": "5m", "strength": 0.0, "k": 0.0,
              "seed": 7, "grid": "cum", "sequence": ["c7", "g4"]}]
    res = S.run_tasks(tasks, tmp_path / "raw", jobs=1, progress=lambda s: None)
    assert res["failed"] == 0 and res["ran"] == 2
    assert S.run_tasks(tasks, tmp_path / "raw", jobs=1, progress=lambda s: None)["ran"] == 0      # 재개: 건너뜀
    recs = S.load_records(tmp_path / "raw")
    assert len(recs) == 2
    pl = next(r for r in recs if r["id"] == "q_pl")
    camp = pl["campaigns"][0]
    assert camp["accounting"] == G.daily_returns_mode() and camp["has_star"]     # 새 캠페인 기준 회계
    c0 = camp["cands"][0]
    assert c0["sharpe"] == pytest.approx(c0["rob_base_sharpe"], rel=1e-9)        # 캠페인 비용 = 현재 레지스트리
    for k in ("g2_p", "g3_med", "g4_roll", "g5_sr", "g5_var", "val_sharpe", "val_p", "val_mc_mdd95", "alpha_t",
              "lat_min_retention", "cost_sharpes", "reg_trend_share", "val_ref_match", "cur"):
        assert c0.get(k) is not None, k
    con = S.consistency(recs)
    assert con["n_markets"] == 2 and con["n_candidates"] >= 3
    assert con["candidates_matching"] == con["n_candidates"], con["mismatches"]
    ev = S.evaluate_market(pl, S.current_package())
    assert ev["star_validated"] == any(c["validated"] for c in camp["actual"]["cands"])
    # 분석 구조: 복제해 TUNE/HOLD 양쪽에 놓는다
    many = []
    for i in range(4):
        for r in recs:
            r2 = copy.deepcopy(r)
            r2["seed"], r2["id"] = r["seed"] + i, f"{r['id']}_{i}"
            many.append(r2)
        nul = copy.deepcopy(pl)
        nul.update(kind="null", group="a_std_null", strength=0.0, seed=100 + i, id=f"n_{i}")
        many.append(nul)
    out = S.analyze(many, progress=lambda s: None)
    assert {"current", "sweep_range_best", "strict_optimised", "moderate", "aggressive", "current@끔",
            "aggressive@끔"} <= set(out["packages"])
    assert out["greedy_paths"] and S.compact_summary(out)["rate_format"]
    assert out["current"]["all"]["fpr"]["n"] == 4 and len(out["contribution"]["leave_one_out"]) == len(S.ATOMS)
    assert out["dsr_formula"]["by_position"] and out["stage_rates"]
    T = S.tables_markdown(out)
    assert "packages" in T and "logo" in T and "stage_val" in T and all(isinstance(v, str) for v in T.values())
