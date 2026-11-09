"""오답노트 (btc_quant.research.lessons) — 분류·불변성·계열 상속·Val 차단·유사 탐색·사후분석."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

from btc_quant.research import lessons as LS
from btc_quant.research.lessons import (FamilyInheritanceError, LessonBook, LessonsError,
                                        RestrictedInfoError, classify_candidate, classify_pipeline)


# ------------------------------------------------------------------ 픽스처
def grid_record(entry="donchian_breakout", lookback=(48, 96), filters=None, sl=(0.5, 0.8), family=None):
    filters = filters if filters is not None else [[], [{"block": "vol_regime", "grid": {"min_pct": [0.5]}}]]
    return {"name": "t", "family": family or entry, "segment": "dev",
            "data": {"exchange": "coinone", "symbol": "BTC-KRW", "timeframe": "1m", "aux": []},
            "entry": {"block": entry, "grid": {"lookback": list(lookback)}},
            "filters": filters, "exits": {"sl_pct": list(sl), "tp_pct": [1.0], "max_hold_min": [60]},
            "costs": {"primary": "coinone_conservative"}, "budget": 100}


def gate(passed, **kw):
    return {"passed": passed, **kw}


def cand(rank=1, first_failed="G2", gates=None, **kw):
    g = {"G0": gate(True, validate="ok", causality_violations=[]),
         "G1": gate(True, n_trades=500, recent_n_trades=90),
         "G2": gate(False, sharpe_w=-0.3, p_value=0.4, ok_positive=False, ok_null=False),
         "G3": gate(True, selected=0.5, neighbor_median=0.4, required=0.25, n_neighbors=4),
         "G4": gate(True, rolling_frac_positive=0.7, recent_sharpe=0.2),
         "G5": gate(True, dsr=0.97, pbo=0.1, n_trials=12.0)}
    g.update(gates or {})
    c = {"rank": rank, "logic_hash": f"lh{rank}", "spec_id": f"t_lh{rank}",
         "spec": {"entry": {"block": "donchian_breakout", "params": {"lookback": 48}}, "filters": [],
                  "exit": {"sl_pct": 0.5, "tp_pct": 1.0, "max_hold_min": 60}},
         "metrics": {"sharpe_w": -0.3, "n_trades": 500, "sharpe_year_2023": 0.1},
         "gates": g, "first_failed": first_failed, "dev_passed": first_failed is None,
         "status": f"failed {first_failed} (ledger only)" if first_failed else "validated (grade A)"}
    c.update(kw)
    return c


@pytest.fixture
def env(tmp_path):
    book = LessonBook.at(tmp_path)
    led = book.ledger
    h = led.register_hypothesis("Donchian breakouts continue for an hour on Coinone BTC", "phase 0", ">=1 pass")
    c = led.create_campaign(h, grid=grid_record(), budget=100, cost_models=["coinone_conservative"],
                            period={"segment": "dev", "start": "2021-01-01", "end": "2025-11-01",
                                    "data_snapshot": "snap1"}, family="donchian_breakout")
    return book, h, c


def pipeline_out(cid, hid, cands, n_validated=0, **kw):
    p = {"campaign_id": cid, "hypothesis_id": hid, "top_k": len(cands),
         "funnel_all": {"n_specs": 8, "g1_pass": 6, "sharpe_w_positive": 2, "g4_pass": 3, "g1_g4_positive": 1},
         "campaign_stats": {"n_family_trials": 8, "n_trials_dsr": 4.0, "k_eff_clusters": 4, "pbo": 0.2,
                            "var_trial_sr": 0.01},
         "candidates": cands, "n_dev_survivors": sum(1 for c in cands if c["dev_passed"]),
         "n_validated": n_validated, "verdict": "no candidate passed", "code_commit": "abc",
         "lockbox": "not accessed"}
    p.update(kw)
    return p


# ------------------------------------------------------------------ 분류
@pytest.mark.parametrize("first,code", [("G0", "IMPL_OR_DATA_ISSUE"), ("G1", "LOW_SAMPLE"), ("G2", "NO_SIGNAL"),
                                        ("G3", "PARAM_SPIKE"), ("G4", "UNSTABLE"), ("G5", "SELECTION_BIAS")])
def test_gate_taxonomy(first, code):
    gates = {"G2": gate(True, sharpe_w=0.5, p_value=0.01)}
    gates[first] = gate(False, sharpe_w=-0.1, ok_positive=False) if first == "G2" else gate(False)
    t = classify_candidate(cand(first_failed=first, gates=gates))
    assert t["primary"] == code and code in t["codes"]


def test_cost_eaten_when_gross_positive():
    c = cand(metrics={"sharpe_w": -0.2, "sharpe_w_gross": 0.8, "n_trades": 900})
    assert classify_candidate(c)["primary"] == "COST_EATEN"
    # 양(+)이지만 귀무를 못 이김 → NO_SIGNAL (비용 탓 아님)
    c2 = cand(gates={"G2": gate(False, sharpe_w=0.2, ok_positive=True, ok_null=False)},
              metrics={"sharpe_w": 0.2, "sharpe_w_gross": 0.9})
    assert classify_candidate(c2)["primary"] == "NO_SIGNAL"


def test_val_fail_and_engine_mismatch():
    c = cand(first_failed=None, gates={"G2": gate(True), "G6": gate(False, sharpe=-0.4)})
    t = classify_candidate(c)
    assert t["primary"] == "VAL_FAIL"
    c = cand(first_failed=None, gates={"G2": gate(True), "G6": gate(False, reference_engine_match=False)})
    t = classify_candidate(c)
    assert {"VAL_FAIL", "IMPL_OR_DATA_ISSUE"} <= set(t["codes"])


def test_robustness_mapping_and_unknown():
    rob = {"drop_top_trades": {"passed": False}, "exclude_year": {"passed": False},
           "delay_1bar": {"passed": False}, "cost_plus_50pct": {"passed": False},
           "weird_check": {"passed": False}, "fine": {"passed": True}}
    c = cand(first_failed=None, gates={"G2": gate(True), "G6": gate(True)}, robustness=rob)
    t = classify_candidate(c)
    assert t["primary"] is None   # Dev/Val 통과 — 견고성 경고만
    assert {"OUTLIER_DEPENDENT", "REGIME_DEPENDENT", "LATENCY_SENSITIVE", "COST_EATEN",
            "UNCLASSIFIED"} <= set(t["codes"])
    assert LS.robustness_code("something_new") == "UNCLASSIFIED"
    # 리스트 형식도 같은 결과
    lst = [{"name": k, **v} for k, v in rob.items()]
    assert set(classify_candidate({**c, "robustness": lst})["codes"]) == set(t["codes"])


def test_campaign_level_taxonomy():
    p = pipeline_out("c", "h", [cand(1), cand(2), cand(3, first_failed="G5", gates={"G2": gate(True), "G5": gate(False)})])
    assert classify_pipeline(p)["primary"] == "NO_SIGNAL"
    p = pipeline_out("c", "h", [cand(1)], funnel_all={"n_specs": 8, "sharpe_w_positive": 0})
    assert classify_pipeline(p)["primary"] == "NO_SIGNAL"
    assert classify_pipeline(pipeline_out("c", "h", [], signal_study={"passed": False}))["primary"] == "NO_SIGNAL"
    assert classify_pipeline(pipeline_out("c", "h", [], error="boom"))["primary"] == "IMPL_OR_DATA_ISSUE"
    assert classify_pipeline(pipeline_out("c", "h", []))["primary"] == "UNCLASSIFIED"
    assert classify_pipeline(pipeline_out("c", "h", [cand(1)], n_validated=1))["outcome"] == "validated"


# ------------------------------------------------------------------ 사실: 기록·불변성
def test_record_facts_and_idempotent(env):
    book, h, c = env
    p = pipeline_out(c, h, [cand(1), cand(2, first_failed="G3", gates={"G2": gate(True), "G3": gate(False)})])
    f = book.record_facts_from_pipeline(p)
    assert f["id"] == "fact-000001" and f["family"] == "donchian_breakout"
    assert f["taxonomy"]["primary"] in ("NO_SIGNAL", "PARAM_SPIKE")
    assert f["scope"]["entry_block"] == "donchian_breakout" and f["scope"]["timeframe"] == "1m"
    assert f["scope"]["period"] == {"start": "2021-01-01", "end": "2025-11-01"}
    assert f["scope"]["review_after"] and f["scope"]["reexamine_if"]
    assert f["candidates"][0]["gates_dev"]["G2"]["p_value"] == 0.4
    # 재호출 → 같은 레코드, 줄 추가 없음
    f2 = book.record_facts_from_pipeline(p)
    assert f2["id"] == f["id"] and len(book.records("facts")) == 1
    assert book.verify() == []


def test_record_facts_from_path(env, tmp_path):
    book, h, c = env
    d = book.campaigns_dir / c
    d.mkdir(parents=True)
    (d / "pipeline.json").write_text(json.dumps(pipeline_out(c, h, [cand(1)])))
    f = LS.record_facts_from_pipeline(d / "pipeline.json", book=book)
    assert f["campaign_id"] == c and (book.root / "LESSONS.md").exists()


def test_facts_immutable_via_api(env):
    book, h, c = env
    f = book.record_facts_from_pipeline(pipeline_out(c, h, [cand(1)]))
    # 반환·조회 사본을 고쳐도 저장소는 그대로
    f["outcome"] = "validated"
    book.facts()[0]["taxonomy"]["primary"] = "X"
    assert book.facts()[0]["outcome"] == "failed"
    assert book.facts()[0]["taxonomy"]["primary"] == "NO_SIGNAL"
    # 사실은 mark 할 수 없다, 해석은 사실 필드를 바꾸지 않는다
    with pytest.raises(LessonsError):
        book.mark(f["id"], "refuted", by_campaign="x")
    book.add_interpretation(f["id"], "entry fires mostly in low-vol hours", "low", ["fact-000001.candidates[0]"])
    assert book.get(f["id"]) == book.facts()[0]
    assert not any(hasattr(book, n) for n in ("update_fact", "delete_fact", "edit_fact"))
    # 파일을 직접 고치면 verify 가 잡는다
    p = book.path("facts")
    rec = json.loads(p.read_text())
    rec["outcome"] = "validated"
    p.write_text(json.dumps(rec) + "\n")
    assert any("hash mismatch" in x for x in book.verify())


# ------------------------------------------------------------------ Val/잠금 세부 차단
def test_val_details_stripped_from_facts(env):
    book, h, c = env
    g6 = gate(True, sharpe=1.7, stress_mdd=0.08, mc_mdd95=0.12, n_trades=77, total_return=0.31,
              val_p_value=0.01, val_budget={"k": 2})
    cc = cand(1, first_failed=None, gates={"G2": gate(True), "G6": g6}, grade="A",
              grade_checks={"G2": gate(True), "G4": gate(True), "G6": gate(True, sharpe=1.1)},
              risk_limits={"daily_loss_limit": -0.02}, yearly=[{"year": 2026, "sharpe": 3.0}])
    f = book.record_facts_from_pipeline(pipeline_out(c, h, [cc], n_validated=1,
                                                      val_status={"used": 3, "cap": 10}))
    blob = json.dumps(f)
    for bad in ("1.7", "stress_mdd", "mc_mdd95", "0.31", "risk_limits", "grade_checks", "val_p_value",
                "val_status", "yearly"):
        assert bad not in blob, bad
    assert f["candidates"][0]["val_outcome"] == "pass"
    assert f["val_aggregate"] == {"n_evaluated": 1, "n_passed": 1}
    assert f["lockbox_outcome"] == "not_accessed" and f["outcome"] == "validated"


def test_sanitize_strip_and_refuse():
    obj = {"G6": {"sharpe": 1}, "val_sharpe": 2, "lockbox": {"p": 0.01}, "value": 3, "interval": 4,
           "val_outcome": "fail", "val_aggregate": {"n_passed": 0}, "note": "Val Sharpe was 1.4"}
    s = LS.sanitize(obj, "strip")
    assert s == {"value": 3, "interval": 4, "val_outcome": "fail", "val_aggregate": {"n_passed": 0},
                 "note": LS.REDACTED}
    with pytest.raises(RestrictedInfoError):
        LS.sanitize({"evidence": [{"val_sharpe": 1.2}]}, "refuse")
    with pytest.raises(RestrictedInfoError):
        LS.sanitize({"val_aggregate": {"mean": 0.5}}, "refuse")
    with pytest.raises(RestrictedInfoError):
        LS.sanitize({"val_outcome": "sharpe 1.3"}, "refuse")


@pytest.mark.parametrize("text", [
    "Val Sharpe was 1.4 so the edge is real",
    "검증 구간 수익률 12% 로 좋았다",
    "lockbox return: -3%",
    "the Sharpe on validation came out at 0.9",
    "2026-07 win rate 61%",
])
def test_interpretation_refuses_val_numbers(env, text):
    book, h, c = env
    f = book.record_facts_from_pipeline(pipeline_out(c, h, [cand(1)]))
    with pytest.raises(RestrictedInfoError):
        book.add_interpretation(f["id"], text, "low", ["x"])
    assert book.records("interpretations") == []


def test_interpretation_allows_outcome_mentions(env):
    book, h, c = env
    f = book.record_facts_from_pipeline(pipeline_out(c, h, [cand(1)]))
    i = book.add_interpretation(f["id"], "Dev Sharpe -0.3 with 500 trades; G6 not run. Probably no edge after "
                                "costs in quiet hours.", 0.3, ["fact-000001 G2 p_value=0.4"],
                                proposed_test="signal study restricted to KST 22-01")
    assert i["status"] == "unverified" and book.interpretation_status(i["id"]) == "unverified"


# ------------------------------------------------------------------ 해석: 검증 표시
def test_mark_requires_follow_up_campaign(env):
    book, h, c = env
    f = book.record_facts_from_pipeline(pipeline_out(c, h, [cand(1)]))
    i = book.add_interpretation(f["id"], "breakouts fail in low vol", "medium", ["fact-000001"])
    with pytest.raises(ValueError):
        book.mark(i["id"], "unverified", by_campaign=c)
    with pytest.raises(LessonsError):
        book.mark(i["id"], "verified", by_campaign=c)          # 같은 캠페인
    with pytest.raises(LessonsError):
        book.mark(i["id"], "verified", by_campaign="camp-999")  # 사실 없음
    with pytest.raises(ValueError):
        book.add_interpretation(f["id"], "x", "certain", ["e"])
    with pytest.raises(ValueError):
        book.add_interpretation(f["id"], "x", "low", [])
    c2 = book.ledger.create_campaign(h, grid=grid_record(), budget=10, cost_models=["coinone_conservative"],
                                     period="dev", family="donchian_breakout")
    book.record_facts_from_pipeline(pipeline_out(c2, h, [cand(1)]))
    m = book.mark(i["id"], "refuted", by_campaign=c2, note="vol filter did not help")
    assert m["by_fact"] == "fact-000002" and book.interpretation_status(i["id"]) == "refuted"
    assert book.interpretations(f["id"])[0]["current_status"] == "refuted"


# ------------------------------------------------------------------ 계열 상속
def test_derive_inherits_family(env, monkeypatch):
    book, h, c = env
    f = book.record_facts_from_pipeline(pipeline_out(c, h, [cand(1)]))
    d = book.derive_hypothesis(f["id"], "Donchian breakouts continue on Coinone with ATR exits", "lesson fact-000001", ">=1 pass")
    assert d["family"] == "donchian_breakout" and d["parent_family"] == "donchian_breakout"
    assert "[derived-from drv-000001" in d["template_hypothesis"]["rationale"]
    assert d["similar_prior"] and d["similar_prior"][0]["campaign_id"] == c
    # 새 계열 이름 → 거부
    monkeypatch.delenv("BTCQ_RESEARCH_MODE", raising=False)
    with pytest.raises(FamilyInheritanceError):
        book.derive_hypothesis(c, "same idea renamed", "r", "s", family="donchian_v2")
    with pytest.raises(FamilyInheritanceError):
        book.derive_hypothesis(c, "same idea renamed", "r", "s", family="donchian_v2", human_override=True)
    monkeypatch.setenv("BTCQ_RESEARCH_MODE", "1")
    with pytest.raises(FamilyInheritanceError):
        book.derive_hypothesis(c, "same idea renamed", "r", "s", family="donchian_v2", human_override=True,
                               override_reason="really new mechanism")
    monkeypatch.delenv("BTCQ_RESEARCH_MODE")
    d2 = book.derive_hypothesis(c, "same idea renamed", "r", "s", family="donchian_v2", human_override=True,
                                override_reason="human: genuinely different mechanism")
    assert d2["human_override"] and d2["family"] == "donchian_v2"


def test_derived_hypothesis_registered_and_trials_accumulate(env):
    book, h, c = env
    led = book.ledger
    from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec
    spec = StrategySpec("s1", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(0.5, 1.0, 60))
    led.record_run(c, spec, "coinone_conservative", "dev", "snap", "abc", {"sharpe_w": 0.1})
    d = book.derive_hypothesis(c, "follow-up", "r", "s", register_in_ledger=True)
    assert led.get("hypotheses", d["hypothesis_id"])["rationale"].count("derived-from drv-000001")
    c2 = led.create_campaign(d["hypothesis_id"], grid=grid_record(), budget=10,
                             cost_models=["coinone_conservative"], period="dev", family=d["family"])
    spec2 = StrategySpec("s2", BlockRef("donchian_breakout", {"lookback": 96}), ExitSpec(0.5, 1.0, 60))
    led.record_run(c2, spec2, "coinone_conservative", "dev", "snap", "abc", {"sharpe_w": 0.1})
    assert led.trials_count(family="donchian_breakout") == 2


def test_template_family_enforced(env):
    book, h, c = env
    d = book.derive_hypothesis(c, "follow-up breakout idea", "r", "s")
    tpl = {"name": "x", "entry": {"block": "donchian_breakout", "grid": {"lookback": [48]}},
           "filters": [[]], "exits": {"sl_pct": [0.5], "tp_pct": [1.0], "max_hold_min": [60]}}
    t = book.apply_derivation(tpl, d["id"])
    assert t["family"] == "donchian_breakout" and "[derived-from drv-000001" in t["hypothesis"]["rationale"]
    assert book.check_template(t)["derivation"] == d["id"]
    bad = copy.deepcopy(t)
    bad["family"] = "fresh_family"
    with pytest.raises(FamilyInheritanceError):
        book.check_template(bad)
    with pytest.raises(FamilyInheritanceError):
        book.apply_derivation({**tpl, "family": "other"}, d["id"])
    # 파생 템플릿이 실제 템플릿 파서도 통과
    from btc_quant.research.campaign import CampaignTemplate
    full = {**t, "budget": 10, "data": {"exchange": "coinone", "symbol": "BTC-KRW", "timeframe": "1m", "aux": []}}
    assert CampaignTemplate.from_dict(full).family == "donchian_breakout"


def test_family_violation_flagged_in_facts(env):
    book, h, c = env
    d = book.derive_hypothesis(c, "follow-up", "r", "s", register_in_ledger=True)
    c2 = book.ledger.create_campaign(d["hypothesis_id"], grid=grid_record(family="renamed"), budget=10,
                                     cost_models=["coinone_conservative"], period="dev", family="renamed")
    f = book.record_facts_from_pipeline(pipeline_out(c2, d["hypothesis_id"], [cand(1)]))
    assert f["derived_from"] == d["id"] and f["family_check"] == "violation"


# ------------------------------------------------------------------ 유사 탐색
def test_find_similar_exact_blocks_fuzzy(env):
    book, h, c = env
    led = book.ledger
    book.record_facts_from_pipeline(pipeline_out(c, h, [cand(1)]))
    r = book.find_similar(template=grid_record(lookback=(96, 144)))
    assert r[0]["match"] == "exact" and r[0]["campaign_id"] == c
    assert r[0]["outcome"].startswith("failed") and r[0]["trials_in_family"] == 0
    r = book.find_similar(template=grid_record(lookback=(288,)))
    assert r[0]["match"] == "same_blocks"
    r = book.find_similar(template=grid_record(filters=[[{"block": "session_kst", "grid": {}}]]))
    assert r[0]["match"] == "same_entry"
    assert book.find_similar(template=grid_record(entry="rsi_oversold_bounce")) == []
    h2 = led.register_hypothesis("RSI oversold bounce reverts within 2 hours", "x", "y")
    r = book.find_similar(text="Donchian breakout continues for an hour on Coinone")
    assert r and r[0]["match"] == "fuzzy" and r[0]["campaign_id"] == c
    r = book.find_similar(text="RSI oversold bounce reverts quickly")
    assert any(x["hypothesis_id"] == h2 and x["outcome"] == "no campaign" for x in r)
    assert book.find_similar(text="completely unrelated words here") == []


def test_check_template_warns_on_exact_repeat(env):
    book, h, c = env
    out = book.check_template({**grid_record(), "hypothesis": {"text": "t", "rationale": "r",
                                                              "success_criteria": "s"}})
    assert out["derivation"] is None and any("exact overlap" in w for w in out["warnings"])


def test_text_similarity_korean():
    a = "코인원 BTC-KRW 5분봉에서 Donchian 채널 상향 돌파 후 양의 기대수익"
    b = "Donchian 채널 돌파가 1시간 지속된다"
    assert LS.text_similarity(a, b) > LS.text_similarity(a, "RSI 과매도 반등")


# ------------------------------------------------------------------ 사후분석·렌더링·예산
def test_postmortems_seed_and_render(env):
    book, h, c = env
    pms = book.seed_postmortems()
    assert [p["key"] for p in pms] == [p["key"] for p in LS.SEED_POSTMORTEMS] and len(pms) == 4
    assert book.seed_postmortems()[0]["id"] == pms[0]["id"] and len(book.records("postmortems")) == 4
    with pytest.raises(ValueError):
        book.postmortem("t", "oops", "w", "r", ["p"])
    with pytest.raises(ValueError):
        book.postmortem("t", "bug", "w", "r", [])
    with pytest.raises(RestrictedInfoError):
        book.postmortem("t", "bug", "lockbox Sharpe 2.1 seen", "r", ["p"])
    f = book.record_facts_from_pipeline(pipeline_out(c, h, [cand(1)]))
    book.add_interpretation(f["id"], "no edge in quiet hours", "low", ["fact-000001"])
    book.derive_hypothesis(c, "follow-up", "r", "s")
    md = book.render_markdown().read_text()
    for s in ("과정 사후분석", "잠금 구간 기술통계", "거래 수 편향", ".gitignore", "DSR 검정력",
              "[UNVERIFIED]", "`NO_SIGNAL`", "drv-000001", "무결성", "OK", "재검토 조건"):
        assert s in md, s


def test_trial_budget_rule(env):
    book, h, c = env
    led = book.ledger
    assert book.trial_budget("new_fam")["allowance"] == LS.NEW_FAMILY_WEEKLY_TRIALS
    from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec
    for lb in (24, 48, 96):
        led.record_run(c, StrategySpec(f"s{lb}", BlockRef("donchian_breakout", {"lookback": lb}),
                                       ExitSpec(0.5, 1.0, 60)), "coinone_conservative", "dev", "s", "a", {})
    book.record_facts_from_pipeline(pipeline_out(c, h, [cand(1)]))
    b = book.trial_budget("donchian_breakout")
    assert b["allowance"] == LS.FAMILY_WEEKLY_TRIALS and b["used_7d"] == 3 and b["remaining"] == 597
    c2 = led.create_campaign(h, grid=grid_record(), budget=10, cost_models=["coinone_conservative"],
                             period="dev", family="donchian_breakout")
    book.record_facts_from_pipeline(pipeline_out(c2, h, [cand(1)]))
    b = book.trial_budget("donchian_breakout")
    assert b["allowance"] == 0 and b["remaining"] == 0 and "frozen" in b["rule"]
    later = datetime(2100, 1, 1, tzinfo=timezone.utc)
    assert book.trial_budget("donchian_breakout", now=later)["used_7d"] == 0


def test_signal_study_facts(env):
    book, h, c = env
    f = book.record_facts_from_signal_study({"study_id": "ss1", "passed": False, "gross_mean_bp": 3.0,
                                            "net_mean_bp": -2.0, "n_events": 800, "val_mean": 9.9},
                                           family="donchian_breakout")
    assert f["taxonomy"]["primary"] == "COST_EATEN" and "val_mean" not in f["summary"]
    assert book.record_facts_from_signal_study({"study_id": "ss1", "passed": False},
                                               family="donchian_breakout")["id"] == f["id"]
    g = book.record_facts_from_signal_study({"study_id": "ss2", "passed": False}, family="x")
    assert g["taxonomy"]["primary"] == "NO_SIGNAL"
    assert book.verify() == []


def test_val_budget_exhausted_is_not_a_hypothesis_failure():
    c = cand(first_failed=None, gates={"G2": gate(True),
                                       "G6": gate(False, val_status="exhausted", val_budget={"exhausted": True})})
    t = classify_candidate(c)
    assert t["primary"] == "UNCLASSIFIED" and "VAL_FAIL" not in t["codes"]
    assert LS._candidate_fact(c, t)["val_outcome"] == "budget_exhausted"


def test_robustness_suite_format():
    """btc_quant.research.robustness.run_suite 출력 형식 ({"checks": {name: {...}}})."""
    rob = {"checks": {"latency": {"passed": False, "min_retention": 0.2}, "outliers": {"passed": True},
                      "leave_year_out": {"passed": False}, "cost": {"passed": None},
                      "alpha": {"passed": False}, "cross_venue": {"passed": False}},
           "passed": False}
    c = cand(first_failed=None, gates={"G2": gate(True), "G6": gate(True)}, robustness=rob)
    t = classify_candidate(c)
    assert set(t["codes"]) == {"LATENCY_SENSITIVE", "REGIME_DEPENDENT", "NO_SIGNAL"}
    assert LS._candidate_fact(c, t)["robustness_dev"]["cost"] is None


def test_screen_facts_from_json(env):
    book, h, c = env
    d = book.campaigns_dir / "camp-000009"
    d.mkdir(parents=True)
    studies = [
        {"label": "donchian(48)", "family": "donchian_breakout", "entry": {"block": "donchian_breakout"},
         "filters": [], "n_events": 900, "p_signal": 0.4, "best_primary_horizon": 60,
         "decision": {"significant": False, "passed": False, "fail_reason": "not significant after correction"}},
        {"label": "donchian(96)", "family": "donchian_breakout", "entry": {"block": "donchian_breakout"},
         "filters": [{"block": "vol_regime"}], "n_events": 700, "p_signal": 0.001, "best_primary_horizon": 60,
         "decision": {"significant": True, "passed": False, "fail_reason": "net mean ≤ 0 under coinone_conservative"}},
        {"label": "rsi(14)", "family": "rsi_oversold_bounce", "entry": {"block": "rsi_oversold_bounce"},
         "filters": [], "n_events": 20, "p_signal": 0.3, "best_primary_horizon": 30,
         "decision": {"significant": False, "passed": False, "fail_reason": "too few events"}},
    ]
    (d / "signal_screen.json").write_text(json.dumps({"campaign_id": "camp-000009", "hypothesis_id": h,
                                                     "period": {"start": "2021-01-01", "end": "2025-11-01"},
                                                     "decision": {"method": "bh", "n_passed": 0},
                                                     "studies": studies}))
    fs = book.record_facts_from_screen(d)
    # 동률(NO_SIGNAL 1, COST_EATEN 1)은 코드 이름순 → 결정적
    assert {f["family"]: f["taxonomy"]["primary"] for f in fs} == {"donchian_breakout": "COST_EATEN",
                                                                    "rsi_oversold_bounce": "LOW_SAMPLE"}
    assert sorted(x["code"] for x in fs[0]["configs"]) == ["COST_EATEN", "NO_SIGNAL"]
    assert [f["id"] for f in book.record_facts_from_screen(d)] == [f["id"] for f in fs]
    assert book.verify() == []
    assert "donchian(96)" in book.render_markdown().read_text()
