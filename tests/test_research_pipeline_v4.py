"""파이프라인 v4: Val 사용 원장 + 누적 Holm·상한, 상관 군집별 대표 선택."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from btc_quant.evaluation import gates as G
from btc_quant.registry.ledger import Ledger
from btc_quant.registry.splits import overlaps_lockbox
from btc_quant.research.campaign import CampaignTemplate, ResearchEnv, run_campaign
from btc_quant.research.pipeline import Pipeline, run_pipeline, val_holm_decision

from test_research_pipeline import _spy_reads
from test_research_synth import make_root, template_dict

VB = {"alpha": 0.10, "holm_mode": "cumulative", "cap": 4, "n_boot": 500, "seed": 0}


# ------------------------------------------------------------------ Holm 순서 (원장 + 결정)
def _use(led, lh, p, vb=VB):
    op = led.open_val_evaluation(lh, "camp", "v1", vb["cap"])
    dec = val_holm_decision(led, "v1", op["k"], p, vb)
    led.record_val_result(op["id"], dec["holm_passed"], p, {}, dec)
    return dec


def test_val_holm_cumulative_sequence(tmp_path):
    led = Ledger(tmp_path)
    d1 = _use(led, "a", 0.08)                 # k=1: m=1, 문턱 α
    assert d1["holm_passed"] and d1["m"] == 1 and d1["threshold"] == pytest.approx(0.10)
    d2 = _use(led, "b", 0.04)                 # k=2: 가장 작은 p → 문턱 α/2 = 0.05
    assert d2["holm_passed"] and d2["rank"] == 1 and d2["threshold"] == pytest.approx(0.05)
    d3 = _use(led, "c", 0.06)                 # k=3: 순위 2 → α/2 = 0.05 < 0.06 → 실패 (문턱이 높아짐)
    assert not d3["holm_passed"] and d3["rank"] == 2 and d3["threshold"] == pytest.approx(0.05)
    assert d3["n_prior"] == 2
    d4 = _use(led, "d", 0.01)                 # k=4=cap: 순위 1 → α/4
    assert d4["holm_passed"] and d4["threshold"] == pytest.approx(0.025) and d4["retire_val"]
    from btc_quant.registry.ledger import ValBudgetExhaustedError
    with pytest.raises(ValBudgetExhaustedError):
        led.open_val_evaluation("e", "camp", "v1", VB["cap"])


def test_val_holm_cap_mode_and_aborted_use(tmp_path):
    led = Ledger(tmp_path)
    led.open_val_evaluation("a", "camp", "v1", 10)            # 결과 없는 사용 → p = 1 로 셈
    vb = {**VB, "cap": 10, "holm_mode": "cap"}
    op = led.open_val_evaluation("b", "camp", "v1", 10)
    dec = val_holm_decision(led, "v1", op["k"], 0.009, vb)
    assert dec["m"] == 10 and dec["n_prior"] == 1 and dec["holm_passed"]   # 0.009 <= 0.1/10
    dec = val_holm_decision(led, "v1", op["k"], 0.02, vb)
    assert not dec["holm_passed"]


# ------------------------------------------------------------------ 군집별 대표 선택
def _fake_pipeline(returns: pd.DataFrame, sharpe: dict, top_k: int, method: str) -> Pipeline:
    ranked = (pd.DataFrame({"logic_hash": list(sharpe), "sharpe_w": list(sharpe.values())})
              .sort_values(["sharpe_w", "logic_hash"], ascending=[False, True]).reset_index(drop=True))
    p = Pipeline.__new__(Pipeline)
    p.cr = SimpleNamespace(ranked=lambda: ranked, returns=returns)
    p.top_k, p.selection_method, p.selection_threshold, p._labels = top_k, method, 0.5, {}
    return p


def test_cluster_best_selection_picks_distinct_clusters():
    rng = np.random.default_rng(0)
    T = 400
    base = {g: rng.normal(0, 0.01, T) for g in "ABC"}
    cols, sharpe = {}, {}
    sizes = {"A": 5, "B": 3, "C": 2}
    score = {"A": 3.0, "B": 2.0, "C": 1.0}
    for g, n in sizes.items():
        for i in range(n):
            lh = f"{g}{i}"
            cols[lh] = base[g] + rng.normal(0, 0.002, T)      # 군집 내 상관 ~0.96
            sharpe[lh] = score[g] - 0.1 * i                   # A 군집 5개가 전체 상위 5
    cols["Z"] = np.zeros(T)                                   # 무거래 열 → 별도 군집
    sharpe["Z"] = 0.0
    R = pd.DataFrame(cols)

    top, info = _fake_pipeline(R, sharpe, 3, "cluster_best").select_candidates()
    assert top == ["A0", "B0", "C0"]
    assert info["n_clusters"] == 4 and info["cluster_sizes"] == [5, 3, 2, 1]
    assert [x["cluster_size"] for x in info["selected"]] == [5, 3, 2]
    assert len({x["cluster"] for x in info["selected"]}) == 3

    top_all, info_all = _fake_pipeline(R, sharpe, 10, "cluster_best").select_candidates()
    assert top_all == ["A0", "B0", "C0", "Z"]                 # 군집 수보다 많이 요청해도 군집당 1개

    old, _ = _fake_pipeline(R, sharpe, 3, "top_sharpe").select_candidates()
    assert old == ["A0", "A1", "A2"]                          # 구 방식: 한 군집에 몰림


# ------------------------------------------------------------------ end-to-end: Val 예산 (합성 시장)
@pytest.fixture(scope="module")
def planted_env(tmp_path_factory):
    root = make_root(tmp_path_factory.mktemp("v4"), planted=True, seed=7)
    return ResearchEnv.at(root)


def test_pipeline_val_budget_reuse_and_cap(planted_env):
    env = planted_env
    cfg = copy.deepcopy(G.load_gate_config())
    cfg["G6"]["val_budget"]["cap"] = 1
    calls = _spy_reads(env)
    cr = run_campaign(CampaignTemplate.from_dict(template_dict(name="v4a", filters=[[]])), env=env)
    out = run_pipeline(cr, env=env, top_k=3, gate_cfg=cfg)
    # 모든 스펙이 한 군집(같은 돌파 신호) → 후보 1개, 군집 크기 보고
    assert out["selection"]["method"] == "cluster_best"
    assert len(out["candidates"]) == out["selection"]["n_clusters"] == 1
    c = out["candidates"][0]
    assert c["cluster_size"] == len(cr.specs) and c["spec"]["entry"]["params"] == {"lookback": 48}
    assert c["dev_passed"]
    vb = c["gates"]["G6"]["val_budget"]
    assert vb["k"] == 1 and vb["holm_passed"] and vb["p_value"] <= 0.10 and vb["m"] == 1
    assert c["gates"]["G6"]["passed"] and c["status"].startswith("validated")
    assert "val_budget" not in c["grade_checks"]["G6"]           # 등급 재확인은 예산 미사용
    hist = env.ledger.val_history(vb["val_id"])
    assert len(hist) == 1 and hist[0]["result"]["passed"]
    summ = hist[0]["result"]["summary"]
    assert not any(k.startswith("sharpe_year_") or "month" in k for k in summ)
    val_runs = [r for r in env.ledger.records("runs") if r["period"].get("segment") == "val"]
    assert val_runs and not any(k.startswith("sharpe_year_") for r in val_runs for k in r["metrics"])
    assert out["val_status"]["used"] == 1 and out["val_status"]["remaining"] == 0

    # 같은 캠페인 재실행: 같은 logic_hash → 이전 결정 재사용, 새 사용으로 세지 않음
    out2 = run_pipeline(cr, env=env, top_k=3, gate_cfg=cfg, register=False)
    vb2 = out2["candidates"][0]["gates"]["G6"]["val_budget"]
    assert vb2["reused"] == hist[0]["id"] and vb2["holm_passed"]
    assert len(env.ledger.val_history(vb["val_id"])) == 1

    # 새 캠페인(다른 청산 → 새 logic_hash): 상한 도달 → Val 평가 없이 G6 실패
    cr3 = run_campaign(CampaignTemplate.from_dict(template_dict(
        name="v4b", filters=[[]], exits={"sl_pct": [0.7, 1.1], "tp_pct": [0.9, 1.3], "max_hold_min": [60]})),
        env=env)
    out3 = run_pipeline(cr3, env=env, top_k=3, gate_cfg=cfg, register=False)
    c3 = out3["candidates"][0]
    if c3["dev_passed"]:
        g6 = c3["gates"]["G6"]
        assert not g6["passed"] and g6["val_budget"]["exhausted"] and "Val budget exhausted" in c3["status"]
    # 결정적 확인: 새 스펙의 G6 직접 호출 → 백테스트 없이 실패
    p3 = Pipeline(cr3, env, gate_cfg=cfg, register=False)
    m1v, vs, ve = p3.load_val()
    new_lh = next(lh for lh in cr3.specs if env.ledger.val_prior(vb["val_id"], lh) is None)
    g, res, met, mc = p3.g6(cr3.specs[new_lh], m1v, vs, ve, cr3.template.costs["primary"],
                            cr3.template.costs["stress"])
    assert not g["passed"] and g["val_budget"]["exhausted"] and res is None
    assert len(env.ledger.val_history(vb["val_id"])) == 1
    # 잠금 구간은 읽지 않음
    cfg_s = env.data.config
    assert all(k is None and not overlaps_lockbox(s, e, cfg_s) for s, e, k in calls)
    assert env.ledger.records("lockbox") == []
