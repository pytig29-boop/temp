"""params.criteria — anti-goalpost 거부, 합성 보정 결과 → 제안 (가짜 runner, 네트워크·장시간 실행 없음)."""
from pathlib import Path

import pytest

from btc_quant.params import criteria as Q
from btc_quant.params import proposals as P

ROOT = Path(__file__).resolve().parents[1]
K_FOR = {2.0: 0.03}


def make_root(tmp_path: Path) -> Path:
    from _config_root import make_config_root
    return make_config_root(tmp_path)


def fake_runner(fpr_by_value: dict, power_by_value: dict):
    """값마다 정해진 비율로 validated 를 돌려주는 runner (seed 순서대로 결정적)."""
    def run(tasks):
        out, cnt = [], {}
        for t in tasks:
            v, kind = t["value"], t["kind"]
            i = cnt[(v, kind)] = cnt.get((v, kind), 0) + 1
            n = sum(1 for x in tasks if x["value"] == v and x["kind"] == kind)
            rate = fpr_by_value[v] if kind == "null" else power_by_value[v]
            hit = i <= round(rate * n)
            out.append({**t, "any_validated": hit if kind == "null" else True,
                        "star_validated": hit if kind == "planted" else False, "star_dev_passed": True})
        return out
    return run


@pytest.mark.parametrize("motivation,refs,match", [
    ("candidate from camp-000012 just missed DSR", [], "ledger"),
    ("loosen DSR", ["research/campaigns/camp_12/report.md"], "artefacts"),
    ("loosen DSR", ["strategies/review/brk_x.yaml"], "artefacts"),
    ("loosen DSR", ["https://my-notes/val-run"], "not synthetic"),
    ("", [], "motivation"),
])
def test_anti_goalpost_refusals(tmp_path, motivation, refs, match):
    root = make_root(tmp_path)
    called = []
    with pytest.raises(Q.GoalpostError, match=match):
        Q.propose_criterion("gates.G5.min_dsr", [0.9], Q.Targets(), motivation, refs, root=root,
                            runner=lambda t: called.append(t) or [], k_for=K_FOR)
    assert not called                                     # 거부면 아무것도 실행하지 않음
    assert not P.list_proposals(root)


def test_anti_goalpost_known_real_ids(tmp_path):
    root = make_root(tmp_path)
    from btc_quant.registry.ledger import Ledger
    from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec
    led = Ledger(root / "research" / "ledger")
    h = led.register_hypothesis("h", "r", "c")
    cid = led.create_campaign(h, {"a": [1]}, 5, ["coinone_conservative"], ("2021-01-01", "2025-11-01"))
    spec = StrategySpec("brk_failed_cand", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(1.0, 1.2, 60))
    led.record_run(cid, spec, "coinone_conservative", ("2021-01-01", "2025-11-01"), "snap", "abc", {"sharpe": 1})
    for text in ("brk_failed_cand failed G5 by a hair", f"see {spec.logic_hash()}"):
        with pytest.raises(Q.GoalpostError, match="anti-goalpost"):
            Q.check_anti_goalpost("gates.G5.min_dsr", text, [], root)
    Q.check_anti_goalpost("gates.G5.min_dsr", "synthetic FPR study of DSR threshold", ["synthetic:null-markets"], root)


@pytest.mark.parametrize("key,match", [
    ("gates.risk.drawdown_floor", "class 3"),            # class ③ 는 위험 선호
    ("costs.coinone_stress", "class 1"),
    ("calibration.n_boot", "only gates.yaml"),            # gates.yaml 밖의 값(보정 설정)은 대상 아님
])
def test_only_gates_class2(tmp_path, key, match):
    root = make_root(tmp_path)
    with pytest.raises(Q.GoalpostError, match=match):
        Q.check_anti_goalpost(key, "synthetic study", [], root)


def test_proposal_from_synthetic_evidence(tmp_path):
    root = make_root(tmp_path)
    runner = fake_runner({0.95: 0.0, 0.9: 0.05, 0.8: 0.3}, {0.95: 0.5, 0.9: 0.8, 0.8: 0.9})
    st = Q.propose_criterion("gates.G5.min_dsr", [0.9, 0.8], Q.Targets(0.05, 0.7, 2.0),
                             "DSR threshold power check on synthetic markets", ["synthetic:calibration_study"],
                             mode="bounded", n_null_markets=20, n_planted_markets=10, root=root, runner=runner,
                             k_for=K_FOR, progress=lambda *a: None)
    assert st.status == "proposed" and st.recommended == 0.9
    row = {r["value"]: r for r in st.rows}
    assert row[0.95]["power"] == 0.5 and row[0.8]["fpr"] == 0.3
    doc = P.load_proposal(st.proposal_id, root)
    assert doc["class"] == 2 and doc["kind"] == "criterion"
    assert doc["changes"] == [{"target": "yaml:config/gates.yaml#G5.min_dsr", "key": "gates.G5.min_dsr",
                               "from": 0.95, "to": 0.9}]
    assert "next campaign" in doc["applies_from"]
    assert (root / st.report_path).exists()
    # 같은 seed(공통 난수) 가 값마다 쓰였는지: build_tasks 확인
    tasks, _ = Q.build_tasks("G5.min_dsr", [0.95, 0.9], "bounded", 2.0, 3, 2, k_for=K_FOR)
    seeds = {v: [t["seed"] for t in tasks if t["value"] == v] for v in (0.95, 0.9)}
    assert seeds[0.95] == seeds[0.9]
    assert all(t["gate_overrides"] == {"G5.min_dsr": t["value"]} for t in tasks)


def test_no_proposal_when_current_best_or_none_meet(tmp_path):
    root = make_root(tmp_path)
    kw = dict(mode="bounded", n_null_markets=10, n_planted_markets=10, root=root, k_for=K_FOR,
              progress=lambda *a: None)
    st = Q.propose_criterion("gates.G5.min_dsr", [0.9], Q.Targets(), "synthetic check",
                             runner=fake_runner({0.95: 0.0, 0.9: 0.2}, {0.95: 0.8, 0.9: 0.9}), **kw)
    assert st.status == "no_change" and st.proposal_id is None
    st = Q.propose_criterion("gates.G5.min_dsr", [0.9], Q.Targets(), "synthetic check",
                             runner=fake_runner({0.95: 0.0, 0.9: 0.2}, {0.95: 0.3, 0.9: 0.9}), **kw)
    assert st.status == "no_candidate_meets_targets" and not P.list_proposals(root)


def test_quick_mode_is_smoke_only(tmp_path):
    root = make_root(tmp_path)
    st = Q.propose_criterion("gates.G5.min_dsr", [0.9], Q.Targets(), "synthetic check", mode="quick",
                             n_null_markets=2, n_planted_markets=2, root=root, k_for=K_FOR,
                             runner=fake_runner({0.95: 0.0, 0.9: 0.0}, {0.95: 0.0, 0.9: 1.0}),
                             progress=lambda *a: None)
    assert st.recommended == 0.9 and st.status == "smoke_only" and st.proposal_id is None


# ---- 저장된 k(FULL_K_FOR) 의 보정 조건 = 현재 비용 레지스트리·데이터 준비여야 함 -------------------------
def test_stale_k_requires_derivation_or_explicit_k(monkeypatch):
    monkeypatch.setattr(Q, "current_k_conditions", lambda: {**Q.FULL_K_CALIBRATED_UNDER})
    k, src = Q.full_k_for(2.0)                               # 보정 조건 그대로 → 저장값
    assert k == {2.0: Q.FULL_K_FOR[2.0]} and src == "stored"
    # 비용이 바뀌면(예: 승인된 v2) 저장 k 는 낡음 → 유도하지 않으면 거부
    changed = {**Q.FULL_K_CALIBRATED_UNDER, "cost": {"fee_rate": 0.0, "spread_bps": 2.0, "slip_bps": 1.0}}
    monkeypatch.setattr(Q, "current_k_conditions", lambda: changed)
    assert not Q.k_conditions_match()
    with pytest.raises(Q.StaleCalibrationError, match="derive"):
        Q.full_k_for(2.0)
    with pytest.raises(Q.StaleCalibrationError):
        Q.build_tasks("G5.min_dsr", [0.95], "bounded", 2.0, 1, 1)
    calls = []
    k, src = Q.full_k_for(2.0, derive=lambda s: calls.append(s) or {s: 0.0199})
    assert k == {2.0: 0.0199} and src == "derived" and calls == [2.0]
    tasks, meta = Q.build_tasks("G5.min_dsr", [0.95], "bounded", 2.0, 1, 1, derive_k=lambda s: {s: 0.0199})
    assert meta["k_source"] == "derived" and [t["k"] for t in tasks if t["kind"] == "planted"] == [0.0199]
    tasks, meta = Q.build_tasks("G5.min_dsr", [0.95], "bounded", 2.0, 1, 1, k_for={2.0: 0.05})
    assert meta["k_source"] == "explicit"                   # 명시 k 는 그대로
    # 꼬리 정리 폭이 바뀌어도 낡음
    monkeypatch.setattr(Q, "current_k_conditions", lambda: {**Q.FULL_K_CALIBRATED_UNDER, "coinone_wick_k": 0.01})
    assert not Q.k_conditions_match()


def test_current_k_conditions_read_cost_registry():
    from btc_quant.params.costs import cost_model
    cur = Q.current_k_conditions()
    m = cost_model("coinone_conservative")
    assert cur["cost"] == {"fee_rate": m.fee_rate, "spread_bps": m.spread_bps, "slip_bps": m.slip_bps}
    from btc_quant.data.quality import wick_params
    assert cur["coinone_wick_k"] == wick_params("coinone")[0]


# ------------------------------------------------------------------ study="regime" (노출 인식 관문 변형, PLAN D14 초안)
def test_regime_study_tasks_and_targets(tmp_path):
    tasks, meta = Q.build_regime_tasks("exposure_aware.enabled", [False, True], "bounded", 2.0, 6, 3,
                                       k_for={"null": 0.0, "regime_filter": 0.03, "regime_cond_f": 0.03})
    assert meta["study"] == "regime" and len(tasks) == 6 + 3 * 2
    assert {t["kind"] for t in tasks} == {"null", "regime_filter", "regime_cond_f"}
    from btc_quant.research import calibration_study as CS
    full = {t["seed"]: t["kind"] for t in CS.build_regime_tasks(CS.regime_full_config(),
                                                                 {"null": 0, "regime_filter": 0, "regime_cond_f": 0})}
    assert all(full[t["seed"]] == t["kind"] for t in tasks)          # 국면 연구와 같은 seed → 같은 시장
    assert sum(t["val_regime"] == "bear" for t in tasks if t["kind"] == "null") == 2
    assert all(t["variant_overrides"] == {"value=False": {"exposure_aware.enabled": False},
                                          "value=True": {"exposure_aware.enabled": True}} for t in tasks)
    assert all(t["star_filters"] for t in tasks if t["kind"] == "regime_filter")
    rows = Q.summarize([{"value": v, "kind": "null", "val_regime": r, "any_validated": False, "star_validated": False,
                         "star_dev_passed": False} for v in (False, True) for r in ("bear", "bull")]
                       + [{"value": False, "kind": "planted", "any_validated": False, "star_validated": False,
                           "star_dev_passed": True},
                          {"value": True, "kind": "planted", "any_validated": True, "star_validated": True,
                           "star_dev_passed": True}], [False, True])
    t = Q.Targets(0.05, 0.0, 2.0, max_fpr_bearish_val=0.05, min_power_gain=0.2)
    assert Q.recommend(rows, t, False) is True
    assert Q.recommend(rows, Q.Targets(0.05, 0.0, 2.0, 0.05, 2.0), False) is False   # 증가 목표 미달 → 현재 값


def test_regime_proposal_refuses_real_campaign_motivation(tmp_path):
    root = make_root(tmp_path)
    with pytest.raises(Q.GoalpostError):
        Q.propose_criterion("gates.exposure_aware.enabled", [True], Q.Targets(), "rescue camp-000013 after G6 fail",
                            ["synthetic:regime"], root=root, study="regime", runner=lambda t: [],
                            k_for={"null": 0.0, "regime_filter": 0.03})


# ------------------------------------------------------------------ 견고성 국면 검사 방식 (robustness.regime_mode, 기본 pnl_share)
def test_regime_mode_option_is_a_proposable_class2_criterion(tmp_path):
    root = make_root(tmp_path)
    from btc_quant.params.registry import load_registry
    reg = load_registry(root, validate_=False)
    for key, default in (("robustness.regime_mode", "pnl_share"), ("robustness.regime_dominant_min_excess", 0.0)):
        p = reg.get(key)
        assert p.cls == 2 and p.owner == "human" and reg.resolve(p) == default     # 기본 = 현재 동작
    Q.check_anti_goalpost("robustness.regime_mode", "long-only regime check calibration on synthetic markets",
                          ["synthetic:calibration_study --rcheck"], root)
    with pytest.raises(Q.GoalpostError):
        Q.check_anti_goalpost("robustness.regime_mode", "candidate in camp-000014 failed the regime check", [], root)

    def runner(gain: float):
        def run(tasks):
            out = []
            for t in tasks:
                for name, ov in t["variant_overrides"].items():
                    v = ov["robustness.regime_mode"]
                    hit = t["kind"] != "null" and (t["seed"] % 10) < (3 + (10 * gain if v == "informational" else 0))
                    out.append({**t, "variant": name, "any_validated": hit, "star_validated": hit,
                                "star_dev_passed": True})
            return Q.regime_runs_to_value_runs(out, ["pnl_share", "dominant_alpha", "informational"])
        return run
    kw = dict(root=root, study="regime", k_for={"null": 0.0, "regime_filter": 0.03, "regime_cond_f": 0.03},
              n_null_markets=6, n_planted_markets=5, progress=lambda *a: None)
    t = Q.Targets(0.05, 0.0, 2.0, max_fpr_bearish_val=0.05, min_power_gain=0.10)
    st = Q.propose_criterion("robustness.regime_mode", ["dominant_alpha", "informational"], t,
                             "synthetic regime-check calibration", ["synthetic:calibration_study --rcheck"],
                             runner=runner(0.0), **kw)
    assert st.current == "pnl_share" and st.status == "no_change" and st.proposal_id is None
    st = Q.propose_criterion("robustness.regime_mode", ["dominant_alpha", "informational"], t,
                             "synthetic regime-check calibration", ["synthetic:calibration_study --rcheck"],
                             runner=runner(0.3), **kw)
    assert st.status == "proposed" and st.recommended == "informational"
    doc = P.load_proposal(st.proposal_id, root)
    assert doc["changes"][0]["target"] == "yaml:config/gates.yaml#robustness.regime_mode"
    assert doc["changes"][0]["from"] == "pnl_share" and doc["changes"][0]["to"] == "informational"
