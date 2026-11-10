"""관문 재검토용 합성 보정 (docs/prompts/fable_gate_review.md §3.3·§4) — "원시 관문 수치를 한 번 모으고,
임의의 기준 묶음(패키지)을 오프라인으로 평가한다".

근거는 합성 시장뿐이다. 실제 캠페인·Val·잠금 구간 자료는 읽지 않는다. config/gates.yaml 을 바꾸지 않는다(읽기만).

1단계 — 원시 수집 (`run_task` → 작업마다 JSON 1개, 재실행하면 있는 것은 건너뜀)
   합성 시장(calibration_study 의 생성기·격자 재사용)마다 임시 저장소 → 캠페인 → 상위 K(군집 대표) 후보 **전부**에
   G0~G5, G6(Val), 견고성 묶음을 계산한다. 앞 관문에서 떨어져도 멈추지 않는다("허용 모드"). 구현은 통과선을 바꾸는
   대신 파이프라인의 관문 메서드(`Pipeline.g2/g3/g5/g6/robustness_check`, `gates.check_g1/g4`)를 후보마다 직접 부르는
   제어 흐름이다 — G3 는 설정만으로는 '절대 통과'로 만들 수 없고(선택 Sharpe ≤ 0 은 고정 실패), 현재 통과선에서의
   통과 표시도 같이 남겨야 하기 때문. 수치에 영향을 주는 값(귀무 보정, mc_sims, 부트스트랩, 군집, 회계)은 현재 설정
   그대로다. 예외: 귀무 run 수 n_null(기본 500, `G2.null_runs` 도 같은 값으로 맞춤 — "n_null < null_runs → 실패" 규칙을
   수집·대조 실행 모두에 같게 적용).
   - 임시 저장소에 현재 config/gates.yaml 을 복사한다 → 캠페인 매니페스트의 일간 수익 회계가 **새 캠페인 기준(현재 mtm)**
     이 된다(복사하지 않으면 임시 저장소의 캠페인 번호 < 승인 시점이라 승인 전 값 realized 로 등록된다).
   - 원장을 건드리지 않는다(Val 사용·기여도 재실행 없음) → 누적 프로그램에서도 원시 수치는 패키지와 무관하다.
     패키지에 따라 달라지는 것(누적 시도 수 → DSR, Val 사용 순서 → Holm)은 2단계가 다시 계산한다.
2단계 — 오프라인 평가 (`evaluate_market(record, package)`): 파이프라인의 순차 논리를 재현.
   후보를 순위대로 → Dev 관문(G0~G5; DSR 은 패키지·원장 상태의 시도 수로 재계산) → Dev 생존자만 Val 사용(k 증가;
   같은 원장의 이전 캠페인 사용 포함, 같은 logic_hash 는 재사용) → Val 증거 규칙(누적 Holm | 고정 p | Stouffer 결합)
   → G6 통과선 → 견고성 통과선 → validated.
   **일치 점검**(`consistency`): 패키지 = 현재 설정일 때 실제 `run_pipeline`(현재 설정) 결과와 후보 단위로 같아야 한다.
3단계 — 실험 (`build_tasks`, `run_tasks`): (a) 새 원장 귀무, (b) 새 원장 심은 엣지(회전율 × 세기), (c) 누적 원장
   프로그램, (d) 3자산 유니버스(선택).
4단계 — 분석 (`analyze`): 현재 패키지 오탐·검정력·첫 탈락, 관문 기여도(하나 빼기·하나만·쌍 중복), 통과선 훑기,
   패키지 탐색(TUNE 절반에서 고르고 HOLD-OUT 절반에서 보고), 단계별(Dev → Val → 견고성) 통과율.

CLI
  uv run python -m btc_quant.research.gate_review_study run --raw DIR --jobs 4 [--scale 1.0] [--groups a,b,c]
  uv run python -m btc_quant.research.gate_review_study analyze --raw DIR --out summary.json --tables tables.md
  uv run python -m btc_quant.research.gate_review_study quick     # 수 초~수십 초 구조 점검
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
import os
import shutil
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import pandas as pd

from btc_quant.evaluation import gates as G
from btc_quant.evaluation import inference

from . import calibration_study as C

TOP_K = 5
N_NULL = 500
DEV_GATES = ("G0", "G1", "G2", "G3", "G4", "G5")
ROB_CHECKS = ("latency", "outliers", "leave_year_out", "cost", "regime", "alpha")
# 누적 프로그램: 캠페인 8개, 격자 크기를 섞는다(4 / 12 / 48 스펙 — DSR 실질 시도 수 공식의 격자 크기 의존을 보려고).
# 앞 7개는 S* 를 포함하지 않는다(일부는 같은 돌파 신호의 다른 청산·필터 → 심은 시장에서 엣지와 상관). 마지막 g12 가 S* 포함.
EXTRA_GRIDS: dict[str, dict] = {
    "p4": {"entry": {"lookback": [12, 36]}, "filters": [[]],
           "exits": {"sl_pct": [1.0], "tp_pct": [1.2], "max_hold_min": [30, 90]}},
    "p48": {"entry": {"lookback": [18, 36, 72, 144]}, "filters": [[]],
            "exits": {"sl_pct": [0.6, 1.0, 1.5], "tp_pct": [0.8, 1.6], "max_hold_min": [30, 120]}},
}
CUM_SEQUENCE8 = ("c1", "p4", "c3", "c4", "p48", "c6", "c7", "g12")
SCENARIO_GRID = {"5m": "g12", "1m_atr": "a12", "5m_low": "l12", "5m_vlow": "v12"}


# ============================================================== 1단계: 원시 수집
def study_gate_cfg(n_null: int = N_NULL) -> dict:
    """현재 관문 설정(config/gates.yaml) + G2.null_runs = n_null (수집·대조 실행 공통). 그 밖의 값은 그대로."""
    cfg = copy.deepcopy(G.load_gate_config())
    cfg["G2"]["null_runs"] = int(n_null)
    return cfg


def _f(x):
    """JSON 용 float (nan/inf → None)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def flatten_robustness(rb: dict | None) -> dict:
    """견고성 묶음 결과 → 검사별 원시 수치 (통과선과 무관한 값) + 현재 통과선에서의 통과 표시."""
    if not rb:
        return {}
    ch = rb.get("checks") or {}
    out: dict = {"rob_failed": list(rb.get("failed") or []), "rob_skipped": list(rb.get("skipped") or []),
                 "rob_base_sharpe": _f((rb.get("base") or {}).get("sharpe"))}
    lat = ch.get("latency") or {}
    out["lat_min_retention"] = _f(lat.get("min_retention"))
    out["lat_base_sharpe"] = _f(lat.get("base_sharpe"))
    out["lat_scen"] = [[int(r["delay_min"]), _f(r["sharpe"]), _f(r["retention"])] for r in lat.get("scenarios") or []]
    ol = ch.get("outliers") or {}
    out["out_sharpes"] = [_f(r["sharpe"]) for r in ol.get("removals") or []]
    out["out_removed_share"] = [_f(r.get("removed_pnl_share")) for r in ol.get("removals") or []]
    ly = ch.get("leave_year_out") or {}
    out["loyo_min"] = _f(ly.get("min_sharpe"))
    out["loyo_skipped"] = ly.get("passed") is None
    co = ch.get("cost") or {}
    out["cost_sharpes"] = {f"{float(r['mult']):g}": _f(r["sharpe"]) for r in co.get("scenarios") or []}
    rg = ch.get("regime") or {}
    for dim in ("trend", "vol"):
        t = rg.get(dim) or {}
        out[f"reg_{dim}_share"] = _f(t.get("max_pnl_share"))
        out[f"reg_{dim}_total"] = _f(t.get("total_pnl_log"))
        out[f"reg_{dim}_dom"] = t.get("dominant")
        out[f"reg_{dim}_dom_excess"] = _f(t.get("dominant_excess_vs_matched_bh_annual"))
    al = ch.get("alpha") or {}
    out["alpha_t"] = _f(al.get("alpha_t"))
    out["alpha_beta"] = _f(al.get("beta"))
    out["alpha_annual"] = _f(al.get("alpha_annual"))
    out["alpha_skipped"] = al.get("passed") is None
    tl = ch.get("tail") or {}
    out["tail"] = {k: _f(tl.get(k)) for k in ("mtm_worst_day", "mtm_worst_week", "mtm_cvar5_daily", "mtm_max_dd",
                                              "max_inventory_minutes", "bear_total_return")}
    out["tail_flag"] = bool(tl.get("flag")) if tl else None
    out["rob_pass"] = {k: (ch[k].get("passed") if k in ch else None) for k in (*ROB_CHECKS, "cross_venue", "tail")}
    return out


def template_dict(grid: str, family: str | None = None) -> dict:
    """캠페인 템플릿 dict (calibration_study.template_for + 이 모듈의 EXTRA_GRIDS)."""
    if grid not in EXTRA_GRIDS:
        return C.template_for(grid, f"calib_{grid}", family)
    d = C.template_for("g12", f"calib_{grid}", family)
    g = EXTRA_GRIDS[grid]
    d["entry"] = {"block": "donchian_breakout", "grid": g["entry"]}
    d["filters"], d["exits"] = g["filters"], g["exits"]
    d["family"] = family or f"calib_{grid}"
    return d


def _star_of(cr, task: dict):
    sspec = C._star_spec(task)
    ex = sspec.exit
    star = cr.template.make_spec(sspec.entry, tuple(sspec.filters), ex.sl, ex.tp, ex.max_hold_min)
    return star.logic_hash()


def collect_campaign(env, cr, task: dict, grid: str) -> dict:
    """캠페인 결과 cr → 상위 K 후보 전부의 원시 관문 수치 (원장을 쓰지 않는다)."""
    from .pipeline import Pipeline, gate_g0

    t0 = time.time()
    cfg = study_gate_cfg(task["n_null"])
    p = Pipeline(cr, env, top_k=task["top_k"], n_null=task["n_null"], gate_cfg=cfg, register=False,
                 reference_check=bool(task.get("reference_check")), robustness=True, lessons=False, val_budget=False)
    tpl = cr.template
    top, sel = p.select_candidates()
    sel_by = {x["logic_hash"]: x for x in sel["selected"]}
    stats = p.campaign_stats()
    prim = p.prim
    lh_star = _star_of(cr, task)
    has_star = lh_star in prim.index
    ranked = cr.ranked()
    hit = ranked.index[ranked["logic_hash"] == lh_star]
    xstar = cr.returns[lh_star].to_numpy(float) if has_star else None
    m1v, vs, ve = p.load_val()
    vb = cfg["G6"]["val_budget"]
    cands = []
    for rank, lh in enumerate(top, start=1):
        spec = cr.specs[lh]
        row = prim.loc[lh].to_dict()
        row.update(p.exposure_metrics(p.dev_result(spec, p.costs["primary"])))
        g0 = gate_g0(spec, p.caches if p.multi else p.cache, allowed_aux=tuple(tpl.aux))
        ok1, d1 = G.check_g1(row, cfg)
        g2 = p.g2(spec, row, p.costs["primary"])
        g3 = p.g3(spec, float(row["sharpe_w"]))
        ok4, d4 = G.check_g4(row, cfg)
        g5 = p.g5(lh, stats)
        g6, vres, vmet, mc = p.g6(spec, m1v, vs, ve, p.costs["primary"], p.costs["stress"], record=False,
                                  budget=False, dev_metrics=row)
        bt = inference.bootstrap_mean_test(vres.daily_returns.to_numpy(float), n_boot=int(vb["n_boot"]),
                                           seed=vb.get("seed", 0))
        rb = p.robustness_check(spec)
        # 기여도 재실행이 원장에 더할 시도(격자 밖의 '필터 하나 제거' 스펙) — Dev 생존자에만 실제로 더해진다
        abl_new = []
        for i in range(len(spec.filters)):
            rest = spec.filters[:i] + spec.filters[i + 1:]
            ex = spec.exit
            dlh = tpl.make_spec(spec.entry, rest, ex.sl, ex.tp, ex.max_hold_min, execution=spec.execution,
                                sizing=spec.sizing, ladder=spec.ladder).logic_hash()
            if dlh not in prim.index:
                abl_new.append(dlh)
        corr = None
        if has_star:
            if lh == lh_star:
                corr = 1.0
            elif xstar.std() > 0:
                corr = _f(np.corrcoef(xstar, cr.returns[lh].to_numpy(float))[0, 1])
        ex = spec.exit
        c = {
            "rank": rank, "logic_hash": lh, "cluster_size": sel_by[lh].get("cluster_size"),
            "filtered": bool(spec.filters), "entry": dict(spec.entry.params),
            "exit": [ex.sl, ex.tp, ex.max_hold_min], "corr_star": corr, "is_star_exact": lh == lh_star,
            "abl_new": abl_new,
            # Dev 지표
            "n_trades": int(row["n_trades"]), "recent_n_trades": int(row["recent_n_trades"]),
            "trades_per_day": _f(row.get("trades_per_day")), "exposure_time": _f(row.get("exposure_time")),
            "exposure_days": _f(row.get("exposure_days")), "sharpe_w": _f(row["sharpe_w"]),
            "sharpe": _f(row.get("sharpe")), "gross_sharpe_w": _f(row.get("gross_sharpe_w")),
            "gross_sharpe": _f(row.get("gross_sharpe")), "dev_max_dd": _f(row.get("max_dd")),
            "dev_n_days": int(row["n_days"]) if row.get("n_days") is not None else None,
            # G0~G5 원시 수치
            "g0_pass": bool(g0["passed"]),
            "g1_n": int(d1["n_trades"]), "g1_recent_n": int(d1["recent_n_trades"]),
            "g2_p": _f(g2["p_value"]), "g2_pct": _f(g2.get("percentile")), "g2_null_p95": _f(g2.get("sharpe_w_p95")),
            "g2_null_median": _f(g2.get("sharpe_w_median")), "g2_ok_null_runs": bool(g2.get("ok_null_runs")),
            "g3_sel": _f(g3["selected"]), "g3_med": _f(g3["neighbor_median"]), "g3_n": int(g3["n_neighbors"]),
            "g3_med_gross": _f(g3.get("neighbor_median_gross")),
            "g4_roll": _f(d4["rolling_frac_positive"]), "g4_nwin": int(d4["rolling_n_windows"]),
            "g4_recent": _f(d4["recent_sharpe"]),
            "g5_dsr": _f(g5["dsr"]), "g5_pbo": _f(g5["pbo"]), "g5_n_trials": _f(g5["n_trials"]),
            "g5_sr": _f(g5["sr"]), "g5_sr0": _f(g5["sr0"]), "g5_skew": _f(g5["skew"]), "g5_kurt": _f(g5["kurtosis"]),
            "g5_n_obs": int(g5["n_obs"]), "g5_var": _f(g5["var_trial_sr"]),
            # G6 (Val)
            "val_sharpe": _f(g6["sharpe"]), "val_mdd": _f(g6.get("max_dd")), "val_stress_sharpe": _f(g6.get("stress_sharpe")),
            "val_stress_mdd": _f(g6.get("stress_mdd")), "val_mc_mdd95": _f(g6["mc_mdd95"]), "val_p": _f(bt["p_value"]),
            "val_n_days": int(g6.get("n_days") or 0), "val_n_trades": int(g6.get("n_trades") or 0),
            "val_total_return": _f(g6.get("total_return")), "val_ref_match": g6.get("reference_engine_match"),
            "val_bh_return": _f((g6.get("benchmark_val") or {}).get("bh_total_return")),
            "val_exposure_days": _f((g6.get("benchmark_val") or {}).get("exposure_days")),
            # 현재 통과선에서의 통과 표시 (참고·점검용; 오프라인 평가는 원시 수치로 다시 판정)
            "cur": {"G0": bool(g0["passed"]), "G1": bool(ok1), "G2": bool(g2["passed"]), "G3": bool(g3["passed"]),
                    "G4": bool(ok4), "G4_roll": bool(d4["ok_rolling"]), "G4_recent": bool(d4["ok_recent"]),
                    "G5": bool(g5["passed"]), "G5_dsr": bool(g5["ok_dsr"]), "G5_pbo": bool(g5["ok_pbo"]),
                    "G6_checks": bool(g6["passed"]), "G6_sharpe": bool(g6["ok_sharpe"]), "G6_mdd": bool(g6.get("ok_mdd")),
                    "G6_stress": bool(g6.get("ok_stress_sharpe")), "G6_mc": bool(g6["ok_mc_mdd"]),
                    "rob": bool(rb["passed"])},
            **flatten_robustness(rb),
        }
        cands.append(c)
    # S* 대표: S* 자체가 후보면 그것, 아니면 S* 와 일간 수익 상관이 가장 높은 후보(ρ ≥ 0.5) — calibration_study 와 같은 정의
    rep = None
    for i, c in enumerate(cands):
        if c["is_star_exact"]:
            rep = i
            break
    if rep is None and has_star:
        cors = [(-1.0 if c["corr_star"] is None else c["corr_star"]) for c in cands]
        if cors and max(cors) >= 0.5:
            rep = int(np.argmax(cors))
    for i, c in enumerate(cands):
        c["is_star"] = bool(rep is not None and i == rep)
    return {
        "grid": grid, "campaign_id": cr.campaign_id, "n_specs": int(len(prim)), "family": tpl.family,
        "k_eff": int(stats["k_eff_clusters"]), "var_trial_sr": _f(stats["var_trial_sr"]), "pbo": _f(stats.get("pbo")),
        "n_family_at_collect": int(stats["n_family_trials"]), "n_trials_dsr_at_collect": _f(stats["n_trials_dsr"]),
        "campaign_stats": {k: (stats.get(k) if isinstance(stats.get(k), str) else _f(stats.get(k)))
                           for k in ("n_campaign_specs", "k_eff_clusters", "entropy_trials", "n_family_trials",
                                     "n_global_trials", "n_trials_dsr", "var_trial_sr", "var_source",
                                     "cluster_threshold", "pbo")},
        "accounting": p.accounting, "n_null": int(task["n_null"]),
        "has_star": bool(has_star), "star_rank": int(hit[0]) + 1 if has_star else None,
        "star_sharpe_dev": _f(prim.at[lh_star, "sharpe"]) if has_star else None,
        "star_sharpe_w_dev": _f(prim.at[lh_star, "sharpe_w"]) if has_star else None,
        "star_trades_per_day": _f(prim.at[lh_star, "trades_per_day"]) if has_star else None,
        "star_selected": rep is not None,
        "cands": cands, "collect_sec": round(time.time() - t0, 1),
    }


def actual_outcomes(out: dict) -> dict:
    """실제 run_pipeline 결과 → 후보별 판정 요약 (일치 점검용)."""
    rows = []
    for c in out["candidates"]:
        g6 = (c.get("gates") or {}).get("G6") or {}
        lab = C.candidate_outcome(c)
        rows.append({"logic_hash": c["logic_hash"], "rank": c["rank"], "label": lab,
                     "dev_passed": bool(c.get("dev_passed")), "first_failed": c.get("first_failed"),
                     "validated": lab == "validated", "val_k": g6.get("val_k"), "val_p": _f(g6.get("val_p_value")),
                     "holm_threshold": _f((g6.get("val_budget") or {}).get("threshold")),
                     "holm_passed": g6.get("val_holm_passed"),
                     "n_trials": _f(c["gates"]["G5"].get("n_trials")), "dsr": _f(c["gates"]["G5"].get("dsr")),
                     "rob_failed": list((c.get("robustness") or {}).get("failed") or [])})
    return {"cands": rows, "n_validated": sum(r["validated"] for r in rows),
            "n_family_trials": out["campaign_stats"].get("n_family_trials"),
            "val_used": (out.get("val_status") or {}).get("used"), "accounting": out.get("daily_returns")}


def _make_root(tmp: Path, task: dict) -> dict:
    """임시 저장소 + 현재 gates.yaml 복사(새 캠페인 기준 회계). 반환 = 시장 정보(국면 시장이면 Val 국면 등)."""
    layout = C.Layout(**task["layout"])
    info: dict = {}
    if task.get("regime"):
        sched = C.make_regime_root(tmp, layout, task["seed"], task["k"], task["kind_gen"], task.get("val_regime"))
        vs = pd.Timestamp(layout.val_start, tz="UTC")
        dev_lab = sched[sched.index < vs]
        info = {"val_regime": str(sched.iloc[-1]),
                "dev_regime_share": {r: float((dev_lab == r).mean()) for r in C.REGIMES},
                "dev_last_year_regime_share": {r: float((dev_lab.iloc[-365:] == r).mean()) for r in C.REGIMES}}
    elif task.get("universe"):
        make_universe_root(tmp, layout, task)
    else:
        C.make_root(tmp, layout, task["seed"], task["k"], task.get("plant_lookback", C.PLANT_LOOKBACK),
                    task.get("scenario", "5m"))
    shutil.copy(G.GATES_PATH, tmp / "config" / "gates.yaml")
    return info


def _star_full_sharpe(env, task: dict) -> dict:
    """이 시장에서 심은 스펙의 실현 Sharpe (비용 후; Dev 와 Dev+Val 전 구간) — 메타데이터 전용."""
    from .campaign import load_segment
    if task.get("universe"):
        return {}
    try:
        layout = C.Layout(**task["layout"])
        spec = C._star_spec(task)
        raw = env.data.read_range(pd.Timestamp(layout.data_start, tz="UTC"), pd.Timestamp(layout.val_end, tz="UTC"),
                                  "coinone", "BTC-KRW", "1m")
        m1 = C._engine_m1(raw)
        s = pd.Timestamp(layout.dev_start, tz="UTC")
        st_all = C.planted_stats(m1, s, pd.Timestamp(layout.val_end, tz="UTC"), spec)
        st_dev = C.planted_stats(m1, s, pd.Timestamp(layout.val_start, tz="UTC"), spec)
        _ = load_segment
        return {"star_real_sharpe_all": _f(st_all["sharpe"]), "star_real_sharpe_dev": _f(st_dev["sharpe"]),
                "star_real_trades_per_day": _f(st_all["trades_per_day"])}
    except Exception as e:  # noqa: BLE001 — 메타데이터 실패로 시장을 버리지 않는다
        return {"star_real_error": f"{type(e).__name__}: {e}"}


def run_task(task: dict) -> dict:
    """작업 1개(시장 1개; 누적 프로그램이면 캠페인 여러 개를 한 원장에서) → 원시 레코드.
    task["actual"] 이면 캠페인마다 실제 run_pipeline(현재 설정, null_runs 만 n_null)도 돌려 판정을 같이 남긴다."""
    from .campaign import CampaignTemplate, ResearchEnv, run_campaign
    from .pipeline import run_pipeline

    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="gatereview_", dir=task.get("tmp_dir")))
    try:
        info = _make_root(tmp, task)
        env = ResearchEnv.at(tmp)
        seq = list(task.get("sequence") or [task["grid"]])
        fam = "calib_cum" if len(seq) > 1 else None
        camps = []
        for pos, grid in enumerate(seq, start=1):
            d = template_dict(grid, fam)
            if task.get("universe"):
                d["universe"] = list(task["universe"])
                d["data"].pop("symbol", None)
            tpl = CampaignTemplate.from_dict(d)
            tc = time.time()
            cr = run_campaign(tpl, env=env)
            camp = collect_campaign(env, cr, task, grid)
            camp["pos"] = pos
            camp["campaign_sec"] = round(time.time() - tc - camp["collect_sec"], 1)
            if task.get("actual"):
                ta = time.time()
                out = run_pipeline(cr, env=env, top_k=task["top_k"], n_null=task["n_null"],
                                   gate_cfg=study_gate_cfg(task["n_null"]), register=False,
                                   reference_check=bool(task.get("reference_check")), lessons=False)
                camp["actual"] = actual_outcomes(out)
                camp["actual_sec"] = round(time.time() - ta, 1)
            camps.append(camp)
        meta = {k: task.get(k) for k in ("id", "group", "kind", "scenario", "strength", "k", "seed", "grid",
                                         "val_regime", "sequence", "universe", "actual")}
        meta.update(info)
        meta.update(_star_full_sharpe(env, task))
        return {**meta, "layout": task["layout"], "top_k": task["top_k"], "n_null": task["n_null"],
                "campaigns": camps, "runtime_sec": round(time.time() - t0, 1),
                "created": pd.Timestamp.now("UTC").isoformat()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- 3자산 유니버스 시장 (선택): 공통 요인 + 개별 잡음, 세 자산 모두에 같은 돌파 규칙을 심음
UNIVERSE = ("BTC-KRW", "XRP-KRW", "ETH-KRW")
UNIVERSE_COMMON_SHARE = 0.6        # 공통 요인이 설명하는 로그수익 분산 비율 (자산 간 일간 상관 ≈ 0.6)


def gen_universe_markets(start: str, end: str, seed: int, k_edge: float, plant_lookback: int = C.PLANT_LOOKBACK
                         ) -> dict[str, pd.DataFrame]:
    """3자산 1분봉: r_i = √ρ·f + √(1−ρ)·e_i (f, e_i = 독립 GARCH·t 시장의 로그수익) + 자산별 돌파 드리프트.
    구현: 독립 시장 4개(공통 1 + 개별 3)를 만들고 로그수익을 섞은 뒤, 자산마다 섞인 가격에서 `_plant_drift` 로
    donchian 돌파 후 드리프트(k × 그 자산의 1분 σ 추정)를 더한다."""
    rho = UNIVERSE_COMMON_SHARE
    f = C.gen_market(start, end, seed * 10 + 9, 0.0)
    out = {}
    for j, sym in enumerate(UNIVERSE):
        e = C.gen_market(start, end, seed * 10 + j, 0.0)
        out[sym] = _mix_and_plant(f, e, rho, float(k_edge), int(plant_lookback), base_price=(50_000_000.0, 800.0,
                                                                                        3_000_000.0)[j])
    return out


def _mix_and_plant(f: pd.DataFrame, e: pd.DataFrame, rho: float, k_edge: float, lookback: int,
                   base_price: float) -> pd.DataFrame:
    lf = np.diff(np.log(f["close"].to_numpy(float)), prepend=np.log(f["open"].iloc[0]))
    le = np.diff(np.log(e["close"].to_numpy(float)), prepend=np.log(e["open"].iloc[0]))
    r = math.sqrt(rho) * lf + math.sqrt(1 - rho) * le
    # 봉 안 고저: 개별 시장의 (고가/종가, 저가/종가) 비율을 그대로 쓴다
    up = np.log(e["high"].to_numpy(float) / np.maximum(e["open"].to_numpy(float), e["close"].to_numpy(float)))
    dn = np.log(np.minimum(e["open"].to_numpy(float), e["close"].to_numpy(float)) / e["low"].to_numpy(float))
    sig = pd.Series(r).rolling(240, min_periods=30).std().shift(1).bfill().to_numpy(float)
    o, h, lo, c = _plant_drift(r, up, dn, sig, k_edge, lookback, C.PLANT_HOLD5, base_price)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": e["volume"].to_numpy(float),
                         "quote_volume": e["volume"].to_numpy(float) * c}, index=f.index)


def _plant_drift(r, up, dn, sig, k_edge, lookback, hold5, base_price):
    n = len(r)
    o = np.empty(n)
    h = np.empty(n)
    lo = np.empty(n)
    c = np.empty(n)
    hi5 = np.empty(n // 5 + 1)
    price = base_price
    drift_left = 0
    bar_hi = -1.0
    for k in range(n):
        d = k_edge * sig[k] if drift_left > 0 else 0.0
        op = price
        price = price * math.exp(d + r[k])
        o[k] = op
        c[k] = price
        h[k] = max(op, price) * math.exp(up[k])
        lo[k] = min(op, price) * math.exp(-dn[k])
        bar_hi = max(bar_hi, h[k])
        if k % 5 == 4:
            t = k // 5
            hi5[t] = bar_hi
            bar_hi = -1.0
            if drift_left > 0:
                drift_left -= 1
            if k_edge != 0.0 and t >= lookback and drift_left == 0:
                mx = -1.0
                for q in range(t - lookback, t):
                    mx = max(mx, hi5[q])
                if price > mx:
                    drift_left = hold5
    return o, h, lo, c


try:  # numba 가 있으면 JIT (없어도 동작)
    from numba import njit as _njit
    _plant_drift = _njit(cache=True)(_plant_drift)
except Exception:  # noqa: BLE001
    pass


def make_universe_root(tmp: Path, layout: C.Layout, task: dict) -> None:
    import yaml

    from btc_quant.data.store import write_ohlcv
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "splits.yaml").write_text(yaml.safe_dump(layout.splits()))
    for sym, df in gen_universe_markets(layout.data_start, layout.val_end, task["seed"], task["k"]).items():
        write_ohlcv(df, "coinone", sym, "1m", root=tmp / "data" / "raw")


# ---- MTM 엔진 0-나눗셈 가드 (연구 프로세스 안에서만): 고회전 스펙이 비싼 비용 모델(등급용 upbit_stress 등)에서 자산이
# 부동소수 0 까지 줄면 `fast._mtm_core` 가 ZeroDivisionError 를 낸다(1분·ATR 격자 캠페인이 통째로 죽음). 그 경우에만
# 같은 식을 '직전 자산 0 이면 수익 0' 으로 다시 계산한다. 오류가 나지 않는 경우의 수치는 그대로다.
def _mtm_core_safe(eg, w, r, ef, ed, xd, day_close, side_cost, fee_rate, n_days):
    realized = np.zeros(n_days)
    unreal = np.zeros(n_days)
    for i in range(eg.shape[0]):
        realized[xd[i]] += eg[i] * w[i] * r[i]
        den = ef[i] * (1 + fee_rate)
        if den <= 0:
            continue
        for d in range(ed[i], xd[i]):
            mark = (day_close[d] * (1 - side_cost) * (1 - fee_rate)) / den - 1
            if np.isfinite(mark):
                unreal[d] += eg[i] * w[i] * mark
    out = np.empty(n_days)
    cum, prev = 0.0, 1.0
    for d in range(n_days):
        cum += realized[d]
        eq = 1.0 + cum + unreal[d]
        out[d] = (eq / prev - 1.0) if prev > 1e-12 else 0.0
        prev = eq
    return out


def install_mtm_guard() -> None:
    from btc_quant.backtest import fast
    if getattr(fast._mtm_core, "_gate_review_guard", False):
        return
    orig = fast._mtm_core

    def guarded(*a):
        try:
            return orig(*a)
        except ZeroDivisionError:
            return _mtm_core_safe(*a)
    guarded._gate_review_guard = True
    fast._mtm_core = guarded


# ============================================================== 2단계: 오프라인 평가
# 패키지 = 통과선 묶음. 값 None = 그 조건 끔. 키:
#   g1_min_trades, g1_min_recent | g2_frac(귀무 상위 비율), g2_min_sharpe | g3_ratio | g4_roll, g4_recent
#   g5_dsr, g5_pbo, dsr_n("current" = max(K, N_family·K/N_campaign) | "sum_keff" = Σ 캠페인별 K)
#   g6_sharpe, g6_mdd, g6_stress, g6_mc | val_mode("holm" | "fixed" | "stouffer" | "stouffer_n" | "none"),
#   val_alpha(holm 의 α 또는 fixed/stouffer 의 p 문턱), val_cap(Val 사용 상한)
#   rob_latency, rob_outliers(제거 후 Sharpe 하한), rob_loyo, rob_cost_mult, rob_regime, rob_alpha_t
ATOMS = ("G1", "G2", "G3", "G4_roll", "G4_recent", "G5_dsr", "G5_pbo", "G6_sharpe", "G6_mdd", "G6_stress", "G6_mc",
         "G6_holm", "R_latency", "R_outliers", "R_loyo", "R_cost", "R_regime", "R_alpha")
ATOM_KEYS = {"G1": ("g1_min_trades", "g1_min_recent"), "G2": ("g2_frac", "g2_min_sharpe"), "G3": ("g3_ratio",),
             "G4_roll": ("g4_roll",), "G4_recent": ("g4_recent",), "G5_dsr": ("g5_dsr",), "G5_pbo": ("g5_pbo",),
             "G6_sharpe": ("g6_sharpe",), "G6_mdd": ("g6_mdd",), "G6_stress": ("g6_stress",), "G6_mc": ("g6_mc",),
             "G6_holm": ("val_mode",), "R_latency": ("rob_latency",), "R_outliers": ("rob_outliers",),
             "R_loyo": ("rob_loyo",), "R_cost": ("rob_cost_mult",), "R_regime": ("rob_regime",),
             "R_alpha": ("rob_alpha_t",)}
DEV_ATOMS = ("G1", "G2", "G3", "G4_roll", "G4_recent", "G5_dsr", "G5_pbo")
G6_ATOMS = ("G6_sharpe", "G6_mdd", "G6_stress", "G6_mc")
ROB_ATOMS = ("R_latency", "R_outliers", "R_loyo", "R_cost", "R_regime", "R_alpha")
ATOM_LABEL = {"G6_sharpe": "sharpe", "G6_mdd": "mdd", "G6_stress": "stress_sr", "G6_mc": "mc", "G6_holm": "holm",
              "R_latency": "latency", "R_outliers": "outliers", "R_loyo": "leave_year_out", "R_cost": "cost",
              "R_regime": "regime", "R_alpha": "alpha"}
ATOM_GATE = {"G1": "G1", "G2": "G2", "G3": "G3", "G4_roll": "G4", "G4_recent": "G4", "G5_dsr": "G5", "G5_pbo": "G5"}
BIG_CAP = 10 ** 9


def current_package(cfg: dict | None = None) -> dict:
    """config/gates.yaml(또는 cfg) → 패키지 (현재 기준)."""
    c = cfg or G.load_gate_config()
    r = c.get("robustness") or {}
    vb = c["G6"]["val_budget"]
    if vb["holm_mode"] != "cumulative":
        raise ValueError("offline evaluator implements holm_mode=cumulative only")
    return {
        "g1_min_trades": c["G1"]["min_trades"], "g1_min_recent": c["G1"]["min_recent_trades"],
        "g2_frac": c["G2"]["null_top_fraction"], "g2_min_sharpe": c["G2"]["min_weighted_sharpe"],
        "g3_ratio": c["G3"]["neighbor_median_ratio"],
        "g4_roll": c["G4"]["min_rolling_positive_frac"], "g4_recent": c["G4"]["min_recent_sharpe"],
        "g5_dsr": c["G5"]["min_dsr"], "g5_pbo": c["G5"]["max_pbo"], "dsr_n": "current",
        "g6_sharpe": c["G6"]["min_sharpe"], "g6_mdd": c["G6"]["max_mdd"], "g6_stress": c["G6"]["min_stress_sharpe"],
        "g6_mc": c["G6"]["max_mc_mdd95"],
        "val_mode": "holm", "val_alpha": float(vb["alpha"]), "val_cap": int(vb["cap"]),
        "rob_latency": r.get("latency_min_retention", 0.5), "rob_outliers": 0.0, "rob_loyo": 0.0,
        "rob_cost_mult": r.get("cost_pass_mult", 1.5), "rob_regime": r.get("regime_max_pnl_share", 0.70),
        "rob_alpha_t": r.get("alpha_min_tstat", 2.0),
    }


def with_changes(pkg: dict, **changes) -> dict:
    out = dict(pkg)
    out.update(changes)
    return out


def without_atoms(pkg: dict, atoms) -> dict:
    out = dict(pkg)
    for a in atoms:
        for k in ATOM_KEYS[a]:
            out[k] = "none" if k == "val_mode" else None
    return out


def only_atom(pkg: dict, atom: str) -> dict:
    return without_atoms(pkg, [a for a in ATOMS if a != atom])


_ND = None


def _norm():
    global _ND
    if _ND is None:
        from statistics import NormalDist
        _ND = NormalDist()
    return _ND


_SR0_CACHE: dict = {}


def expected_max_sr(n_trials: float, var: float) -> float:
    key = (round(float(n_trials), 9), float(var))
    v = _SR0_CACHE.get(key)
    if v is None:
        from btc_quant.evaluation.selection import expected_max_sharpe
        v = _SR0_CACHE[key] = float(expected_max_sharpe(n_trials, var))
    return v


def dsr_z(c: dict, n_trials: float) -> float:
    """DSR 의 z (DSR = Φ(z)) — selection.probabilistic_sharpe_ratio 와 같은 식, 시도 수 n_trials 로 재계산."""
    n = c["g5_n_obs"]
    if n < 2:
        return 0.0
    sr = c["g5_sr"]
    sr0 = expected_max_sr(n_trials, c["g5_var"] or 0.0)
    vt = 1.0 - (c["g5_skew"] or 0.0) * sr + ((c["g5_kurt"] or 3.0) - 1.0) / 4.0 * sr * sr
    if vt <= 0:
        vt = 1e-12
    return (sr - sr0) * math.sqrt(n - 1) / math.sqrt(vt)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def n_trials_for(pkg: dict, k_eff: int, n_camp: int, n_family: int, sum_keff: int) -> float:
    if pkg.get("dsr_n", "current") == "sum_keff":
        return float(max(k_eff, sum_keff))
    return max(float(k_eff), n_family * k_eff / max(n_camp, 1))


def atom_flags(c: dict, pkg: dict, n_trials: float, pbo: float | None) -> dict:
    """후보 하나의 원자 관문별 통과 여부 (G6_holm 은 순차 논리라 여기서는 넣지 않는다)."""
    f = {}
    t = pkg["g1_min_trades"]
    f["G1"] = (t is None or c["g1_n"] >= t) and (pkg["g1_min_recent"] is None or c["g1_recent_n"] >= pkg["g1_min_recent"])
    ok = True
    if pkg["g2_min_sharpe"] is not None:
        ok = c["sharpe_w"] is not None and c["sharpe_w"] > pkg["g2_min_sharpe"]
    if ok and pkg["g2_frac"] is not None:
        ok = c["g2_p"] <= pkg["g2_frac"] and c.get("g2_ok_null_runs", True)
    f["G2"] = ok
    r = pkg["g3_ratio"]
    f["G3"] = r is None or (c["g3_sel"] is not None and c["g3_sel"] > 0 and c["g3_n"] > 0 and c["g3_med"] is not None
                            and c["g3_med"] >= r * c["g3_sel"])
    f["G4_roll"] = pkg["g4_roll"] is None or (c["g4_roll"] >= pkg["g4_roll"] and c["g4_nwin"] > 0)
    f["G4_recent"] = pkg["g4_recent"] is None or c["g4_recent"] > pkg["g4_recent"]
    f["G5_dsr"] = pkg["g5_dsr"] is None or _phi(dsr_z(c, n_trials)) >= pkg["g5_dsr"]
    f["G5_pbo"] = pkg["g5_pbo"] is None or (1.0 if pbo is None else pbo) < pkg["g5_pbo"]
    f["G6_sharpe"] = pkg["g6_sharpe"] is None or c["val_sharpe"] > pkg["g6_sharpe"]
    f["G6_mdd"] = pkg["g6_mdd"] is None or abs(c["val_mdd"]) <= pkg["g6_mdd"]
    f["G6_stress"] = pkg["g6_stress"] is None or c["val_stress_sharpe"] > pkg["g6_stress"]
    f["G6_mc"] = pkg["g6_mc"] is None or abs(c["val_mc_mdd95"]) <= pkg["g6_mc"]
    t = pkg["rob_latency"]
    f["R_latency"] = t is None or ((c.get("lat_base_sharpe") or 0.0) > 0 and c.get("lat_min_retention") is not None
                                   and c["lat_min_retention"] >= t)
    t = pkg["rob_outliers"]
    f["R_outliers"] = t is None or (bool(c.get("out_sharpes")) and all(s is not None and s > t for s in c["out_sharpes"]))
    t = pkg["rob_loyo"]
    f["R_loyo"] = t is None or c.get("loyo_skipped") or (c.get("loyo_min") is not None and c["loyo_min"] > t)
    t = pkg["rob_cost_mult"]
    if t is None:
        f["R_cost"] = True
    else:
        s = (c.get("cost_sharpes") or {}).get(f"{float(t):g}")
        f["R_cost"] = s is not None and s > 0
    t = pkg["rob_regime"]
    if t is None:
        f["R_regime"] = True
    else:
        ok = True
        for dim in ("trend", "vol"):
            tot, sh = c.get(f"reg_{dim}_total"), c.get(f"reg_{dim}_share")
            if tot is None and sh is None:
                continue
            if not (tot is not None and tot > 0) or (sh is not None and sh > t):
                ok = False
        f["R_regime"] = ok
    t = pkg["rob_alpha_t"]
    f["R_alpha"] = t is None or c.get("alpha_skipped") or (c.get("alpha_t") is not None and c["alpha_t"] >= t)
    return f


def val_evidence(c: dict, pkg: dict, prior: list, z_dev: float) -> tuple[bool, dict]:
    """Val 증거 규칙. holm: 이 Val 의 이전 사용 p 들과 누적 Holm(α). fixed: p ≤ 문턱(다중성 보정 없음).
    stouffer(_n): Dev 의 DSR z 와 Val 의 z = Φ⁻¹(1 − p) 를 합쳐(같은 가중 | √일수 가중) p ≤ 문턱 그리고 Val Sharpe > 0."""
    mode, a = pkg["val_mode"], pkg.get("val_alpha")
    p = c["val_p"]
    if mode == "none":
        return True, {}
    if mode == "holm":
        dec = inference.lockbox_holm_decision(p, prior, a, BIG_CAP, "cumulative")
        return bool(dec["passed"]), {"threshold": dec["threshold"], "k": dec["k"]}
    if mode == "fixed":
        return p <= a, {"threshold": a}
    if mode in ("stouffer", "stouffer_n"):
        nd = _norm()
        zv = nd.inv_cdf(min(max(1.0 - p, 1e-9), 1 - 1e-9))
        if mode == "stouffer":
            z = (z_dev + zv) / math.sqrt(2.0)
        else:
            w1, w2 = math.sqrt(max(c["g5_n_obs"], 1)), math.sqrt(max(c["val_n_days"], 1))
            z = (w1 * z_dev + w2 * zv) / math.sqrt(w1 * w1 + w2 * w2)
        return (1.0 - _phi(z)) <= a and c["val_sharpe"] > 0, {"z": z}
    raise ValueError(f"unknown val_mode {mode!r}")


def evaluate_market(rec: dict, pkg: dict, detail: bool = True) -> dict:
    """원시 레코드(시장 1개; 누적 프로그램이면 캠페인 여러 개) + 패키지 → 파이프라인 순차 논리의 결과.
    반환: any_validated, star_validated(마지막 캠페인의 S* 대표), n_validated, any_dev, star_dev, star_g6, val_used,
    cands(detail=True: 후보별 dev/val/rob/validated, first_failed 라벨(calibration_study.candidate_outcome 형식), flags)."""
    prior: list[float] = []            # 이 Val 의 사용 p (사용 순서)
    used: dict[str, tuple] = {}        # logic_hash → (holm_passed, final_passed)
    n_family = 0
    extra = 0                          # 기여도 재실행이 더한 시도 수 (이전 캠페인들의 Dev 생존자)
    sum_keff = 0
    cap = pkg.get("val_cap") or BIG_CAP
    out_c = []
    any_val, any_dev, n_val = False, False, 0
    star = {"validated": False, "dev": False, "g6": False, "label": "no_star"}
    camps = rec["campaigns"]
    for ci, camp in enumerate(camps):
        n_family += camp["n_specs"]
        sum_keff += camp["k_eff"]
        ntr = n_trials_for(pkg, camp["k_eff"], camp["n_specs"], n_family + extra, sum_keff)
        last = ci == len(camps) - 1
        if last and camp.get("has_star"):
            star["label"] = "not_selected"
        rows = []
        abl = set()
        for c in camp["cands"]:
            f = atom_flags(c, pkg, ntr, camp.get("pbo"))
            dev = c["g0_pass"] and all(f[a] for a in DEV_ATOMS)
            rows.append((c, f, dev))
            if dev:
                abl.update(c.get("abl_new") or [])
        for c, f, dev in rows:
            row = {"pos": camp.get("pos", ci + 1), "rank": c["rank"], "is_star": bool(c.get("is_star")) and last,
                   "dev": dev, "g6": False, "validated": False}
            if not dev:
                lab = "G0" if not c["g0_pass"] else next(ATOM_GATE[a] for a in DEV_ATOMS if not f[a])
            else:
                any_dev = True
                checks = all(f[a] for a in G6_ATOMS) and c.get("val_ref_match") is not False
                why = [ATOM_LABEL[a] for a in G6_ATOMS if not f[a]]
                lh = c["logic_hash"]
                if lh in used:                       # 같은 스펙·같은 Val: 이전 결정 재사용 (새 사용 아님)
                    holm_ok, prev_final = used[lh]
                    g6 = checks and holm_ok and prev_final
                    row["val_reused"] = True
                elif len(prior) + 1 > cap:           # Val 사용 상한 → 평가하지 않고 실패
                    holm_ok, g6 = False, False
                    row["val_exhausted"] = True
                    why = []
                else:
                    holm_ok, info = val_evidence(c, pkg, prior, dsr_z(c, ntr))
                    row["val_k"] = len(prior) + 1
                    row["val_threshold"] = info.get("threshold")
                    prior.append(c["val_p"])
                    g6 = checks and holm_ok
                    used[lh] = (holm_ok, g6)
                if not holm_ok:
                    why.append("holm")
                row["g6"] = g6
                if not g6:
                    lab = f"G6({'+'.join(why) or '?'})"
                else:
                    rfail = [ATOM_LABEL[a] for a in ROB_ATOMS if not f[a]]
                    if rfail:
                        lab = f"R({'+'.join(rfail)})"
                    else:
                        lab = "validated"
                        row["validated"] = True
                        any_val = True
                        n_val += 1
            row["label"] = lab
            if row["is_star"]:
                star = {"validated": row["validated"], "dev": dev, "g6": row["g6"], "label": lab}
            if detail:
                row["flags"] = f
                row["n_trials"] = ntr
            out_c.append(row)
        extra += len(abl)
    return {"any_validated": any_val, "n_validated": n_val, "any_dev": any_dev, "star_validated": star["validated"],
            "star_dev": star["dev"], "star_g6": star["g6"], "star_label": star["label"], "val_used": len(prior),
            "cands": out_c}


def consistency(records: list[dict], pkg: dict | None = None) -> dict:
    """일치 점검: 패키지 = 현재 설정의 오프라인 결과 vs 실제 run_pipeline (actual 이 있는 캠페인만).
    후보 단위(라벨: validated | G1..G5 | G6(…) | R(…))와 시장 단위(validated 수)로 비교."""
    pkg = pkg or current_package()
    n_c = ok_c = n_m = ok_m = n_camp = 0
    mism = []
    groups: dict[str, int] = {}
    for rec in records:
        if not all("actual" in c for c in rec["campaigns"]):
            continue
        ev = evaluate_market(rec, pkg)
        n_m += 1
        good = True
        groups[rec.get("group", "?")] = groups.get(rec.get("group", "?"), 0) + 1
        for ci, camp in enumerate(rec["campaigns"]):
            n_camp += 1
            pos = camp.get("pos", ci + 1)
            off = {(r["pos"], r["rank"]): r for r in ev["cands"]}
            for a in camp["actual"]["cands"]:
                o = off.get((pos, a["rank"]))
                n_c += 1
                if o is not None and o["label"] == a["label"]:
                    ok_c += 1
                else:
                    good = False
                    mism.append({"id": rec.get("id"), "pos": pos, "rank": a["rank"], "actual": a["label"],
                                 "offline": None if o is None else o["label"], "actual_k": a.get("val_k"),
                                 "offline_k": None if o is None else o.get("val_k"),
                                 "actual_n_trials": a.get("n_trials"),
                                 "offline_n_trials": None if o is None else o.get("n_trials")})
        ok_m += good
    return {"n_markets": n_m, "markets_matching": ok_m, "n_campaigns": n_camp, "n_candidates": n_c,
            "candidates_matching": ok_c, "by_group": groups, "mismatches": mism}


# ============================================================== 3단계: 실험 (작업 목록·실행)
STRENGTHS = (1.0, 1.5, 2.0, 3.0)
SEED0 = {"a_std_null": 21_000, "a_breakeven": 22_000, "a_low_null": 23_000, "a_vlow_null": 24_000,
         "a_1m_null": 25_000, "a_regime_null": 26_001, "b_5m": 31_000, "b_1m_atr": 33_000, "b_5m_low": 35_000,
         "b_5m_vlow": 37_000, "b_regime": 39_001, "c_null": 41_000, "c_planted": 42_000, "d_uni_null": 51_000,
         "d_uni_planted": 52_000}
BASE_COUNTS = {"a_std_null": 40, "a_breakeven": 20, "a_low_null": 15, "a_vlow_null": 15, "a_1m_null": 20,
               "a_regime_null": 45, "b_planted": 14, "b_regime": 20, "c_null": 12, "c_planted": 12,
               "d_uni_null": 0, "d_uni_planted": 0}
# 일치 점검(실제 파이프라인도 같이 실행)할 시장 수 — 그룹별 앞에서부터
ACTUAL_COUNTS = {"a_std_null": 4, "a_breakeven": 2, "a_low_null": 1, "a_vlow_null": 1, "a_1m_null": 1,
                 "a_regime_null": 3, "b_planted@2.0": 2, "b_planted@3.0": 2, "b_planted@1.0": 0, "b_planted@1.5": 0,
                 "b_regime": 2, "c_null": 1, "c_planted": 1, "d_uni_null": 1, "d_uni_planted": 1}


def edge_configs(layout: C.Layout, quick: bool = False) -> dict:
    """시나리오별 파일럿 설정 (현재 비용 레지스트리로 k ↦ S* 참 Sharpe)."""
    out = {}
    for sc, cfg in (("5m", C.full_config(1)), ("1m_atr", C.m1_config(1)), ("5m_low", C.low_config(1, "5m_low")),
                    ("5m_vlow", C.low_config(1, "5m_vlow"))):
        cfg.strengths = STRENGTHS
        if quick:
            cfg.pilot_seeds, cfg.pilot_years = 1, 1.0
            cfg.pilot_ks = (cfg.pilot_ks[0], cfg.pilot_ks[len(cfg.pilot_ks) // 2], cfg.pilot_ks[-1])
        out[sc] = cfg
    return out


def edge_maps(raw_dir: Path, pool=None, quick: bool = False, progress=print, scenarios=None,
              regime: bool = True) -> dict:
    """엣지 세기 보정표 (시나리오별 k_for_strength, 5m 은 손익분기 k 도). raw_dir/_edge_*.json 에 저장·재사용."""
    raw_dir = Path(raw_dir)
    out = {}
    for sc, cfg in edge_configs(C.FULL_LAYOUT, quick).items():
        if scenarios is not None and sc not in scenarios:
            continue
        fp = raw_dir / f"_edge_{sc}.json"
        if fp.exists():
            out[sc] = json.loads(fp.read_text())
            continue
        progress(f"[edge] pilot {sc}: {len(cfg.pilot_ks)} k × {cfg.pilot_seeds} seeds × {cfg.pilot_years}y")
        e = C.pilot_edge_map(cfg, pool)
        e["k_for_strength"] = {f"{float(s):g}": v for s, v in e["k_for_strength"].items()}
        _write_json(fp, e)
        out[sc] = e
    if regime:
        fp = raw_dir / "_edge_regime.json"
        if fp.exists():
            out["regime"] = json.loads(fp.read_text())
        else:
            rc = C.RegimeStudyConfig("gate-review", C.FULL_LAYOUT, strength=2.0, n_markets={"regime_cond_f": 1}, n_null=N_NULL)
            if quick:
                rc.pilot_ks, rc.pilot_seeds, rc.pilot_years = (0.0, 0.03, 0.06), 1, 1.0
            progress(f"[edge] pilot regime_cond_f: {len(rc.pilot_ks)} k × {rc.pilot_seeds} seeds × {rc.pilot_years}y")
            e = C.pilot_regime_edge_map(rc, pool)
            _write_json(fp, e)
            out["regime"] = e
    return out


def _write_json(fp: Path, obj) -> None:
    fp = Path(fp)
    tmp = fp.with_suffix(fp.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False))
    os.replace(tmp, fp)


def build_tasks(edges: dict, scale: float = 1.0, layout: C.Layout = C.FULL_LAYOUT, n_null: int = N_NULL,
                top_k: int = TOP_K, counts: dict | None = None, actual_counts: dict | None = None,
                sequence=CUM_SEQUENCE8) -> list[dict]:
    """우선순위 순서의 작업 목록: 일치 점검 시장(actual) → (a) 귀무 → (b) 심은 엣지 → (c) 누적 프로그램 → (d) 유니버스.
    seed 는 그룹별 고정 범위(같은 scale 을 다시 주면 같은 작업 id → 이어서 실행). TUNE/HOLD-OUT = seed 짝/홀."""
    cnt = {k: int(round(v * scale)) for k, v in BASE_COUNTS.items()}
    cnt.update(counts or {})
    act = dict(ACTUAL_COUNTS)
    act.update(actual_counts or {})
    base = {"layout": asdict(layout), "n_null": int(n_null), "top_k": int(top_k)}
    tasks = []

    def add(group, i, **kw):
        t = {**base, "group": group, "seed": SEED0[group] + i, **kw}
        key = f"b_planted@{kw['strength']:g}" if group.startswith("b_") and group != "b_regime" else group
        t["actual"] = i < act.get(key, 0)
        t["id"] = f"{group}_s{kw.get('strength', 0):g}_{t['seed']}"
        tasks.append(t)

    kf5 = edges.get("5m") or {}
    for i in range(cnt["a_std_null"]):
        add("a_std_null", i, kind="null", scenario="5m", strength=0.0, k=0.0, grid="g12")
    for i in range(cnt["a_breakeven"]):
        add("a_breakeven", i, kind="breakeven", scenario="5m", strength=0.0, k=float(kf5.get("k_breakeven", 0.0)),
            grid="g12")
    for i in range(cnt["a_low_null"]):
        add("a_low_null", i, kind="null", scenario="5m_low", strength=0.0, k=0.0, grid="l12")
    for i in range(cnt["a_vlow_null"]):
        add("a_vlow_null", i, kind="null", scenario="5m_vlow", strength=0.0, k=0.0, grid="v12")
    for i in range(cnt["a_1m_null"]):
        add("a_1m_null", i, kind="null", scenario="1m_atr", strength=0.0, k=0.0, grid="a12")
    for i in range(cnt["a_regime_null"]):
        s = SEED0["a_regime_null"] + i
        add("a_regime_null", i, kind="null", kind_gen="null", scenario="regime", strength=0.0, k=0.0, grid="r12",
            regime=True, val_regime=C.REGIMES[s % 3])
    for sc in ("5m", "1m_atr", "5m_low", "5m_vlow"):
        kf = (edges.get(sc) or {}).get("k_for_strength") or {}
        for si, s in enumerate(STRENGTHS):
            if f"{s:g}" not in kf:
                continue
            for i in range(cnt["b_planted"]):
                add(f"b_{sc}", si * 1000 + i, kind="planted", scenario=sc, strength=float(s), k=float(kf[f"{s:g}"]),
                    grid=SCENARIO_GRID[sc])
    kr = ((edges.get("regime") or {}).get("k_for") or {}).get("regime_cond_f")
    if kr is not None:
        for i in range(cnt["b_regime"]):
            s = SEED0["b_regime"] + i
            add("b_regime", i, kind="planted", kind_gen="regime_cond_f", scenario="regime", strength=2.0, k=float(kr),
                grid="r12", regime=True, star_filters=True, val_regime=C.REGIMES[s % 3])
    for i in range(cnt["c_null"]):
        add("c_null", i, kind="null", scenario="5m", strength=0.0, k=0.0, grid="cum", sequence=list(sequence))
    if "2" in ((edges.get("5m") or {}).get("k_for_strength") or {}):
        for i in range(cnt["c_planted"]):
            add("c_planted", i, kind="planted", scenario="5m", strength=2.0,
                k=float(edges["5m"]["k_for_strength"]["2"]), grid="cum", sequence=list(sequence))
    ku = (edges.get("universe") or {}).get("k_for_strength") or {}
    for i in range(cnt.get("d_uni_null", 0)):
        add("d_uni_null", i, kind="null", scenario="universe", strength=0.0, k=0.0, grid="g12", universe=list(UNIVERSE))
    if "2" in ku:
        for i in range(cnt.get("d_uni_planted", 0)):
            add("d_uni_planted", i, kind="planted", scenario="universe", strength=2.0, k=float(ku["2"]), grid="g12",
                universe=list(UNIVERSE))
    prio = {"a": 1, "b": 2, "c": 3, "d": 4}
    order = sorted(range(len(tasks)), key=lambda j: (0 if tasks[j]["actual"] else prio[tasks[j]["group"][0]], j))
    return [tasks[j] for j in order]


def _run_and_save(task: dict, raw_dir: str) -> tuple[str, float, str | None]:
    fp = Path(raw_dir) / f"{task['id']}.json"
    if fp.exists():
        return task["id"], 0.0, None
    t0 = time.time()
    try:
        install_mtm_guard()
        rec = run_task(task)
        _write_json(fp, rec)
        return task["id"], time.time() - t0, None
    except Exception as e:  # noqa: BLE001 — 작업 하나의 실패로 전체를 멈추지 않는다 (오류는 파일로 남김)
        import traceback
        err = Path(raw_dir) / "_errors"
        err.mkdir(exist_ok=True)
        (err / f"{task['id']}.txt").write_text(traceback.format_exc())
        return task["id"], time.time() - t0, f"{type(e).__name__}: {e}"


def run_tasks(tasks: list[dict], raw_dir: str | Path, jobs: int = 4, progress=print, deadline: float | None = None
              ) -> dict:
    """작업마다 raw_dir/<id>.json. 이미 있으면 건너뜀(재개 가능). deadline(epoch 초) 이후에는 새 작업을 내지 않는다."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    todo = [t for t in tasks if not (raw_dir / f"{t['id']}.json").exists()]
    progress(f"[run] {len(tasks)} tasks, {len(tasks) - len(todo)} already done, {len(todo)} to run, jobs={jobs}")
    t0 = time.time()
    done = fail = 0
    if jobs <= 1:
        for t in todo:
            if deadline and time.time() > deadline:
                break
            tid, sec, err = _run_and_save(t, str(raw_dir))
            done += 1
            fail += err is not None
            progress(f"[run] {done}/{len(todo)} {tid} {sec:.0f}s" + (f" ERROR {err}" if err else ""))
        return {"ran": done, "failed": fail, "sec": time.time() - t0}
    with ProcessPoolExecutor(max_workers=jobs, mp_context=get_context("spawn")) as pool:
        it = iter(todo)
        pending = set()

        def submit_next():
            if deadline and time.time() > deadline:
                return
            t = next(it, None)
            if t is not None:
                pending.add(pool.submit(_run_and_save, t, str(raw_dir)))
        for _ in range(jobs):
            submit_next()
        while pending:
            fut = next(as_completed(pending))
            pending.discard(fut)
            tid, sec, err = fut.result()
            done += 1
            fail += err is not None
            progress(f"[run] {done}/{len(todo)} {tid} {sec:.0f}s elapsed {time.time() - t0:.0f}s"
                     + (f" ERROR {err}" if err else ""))
            submit_next()
    return {"ran": done, "failed": fail, "sec": time.time() - t0}


def load_records(raw_dir: str | Path) -> list[dict]:
    out = []
    for fp in sorted(Path(raw_dir).glob("*.json")):
        if fp.name.startswith("_"):
            continue
        try:
            out.append(json.loads(fp.read_text()))
        except json.JSONDecodeError:
            continue
    return out


# ============================================================== CLI
def _cmd_run(a) -> int:
    raw = Path(a.raw)
    raw.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    pool = ProcessPoolExecutor(max_workers=a.jobs, mp_context=get_context("spawn")) if a.jobs > 1 else None
    try:
        edges = edge_maps(raw, pool, progress=lambda s: print(s, flush=True))
    finally:
        if pool:
            pool.shutdown()
    print(f"[edge] done {time.time() - t0:.0f}s: " + json.dumps(
        {k: (v.get("k_for_strength") or v.get("k_for")) for k, v in edges.items()}), flush=True)
    counts = json.loads(a.counts) if a.counts else None
    tasks = build_tasks(edges, scale=a.scale, n_null=a.n_null, counts=counts)
    for t in tasks:
        t["reference_check"] = not a.no_reference
        if a.tmp:
            t["tmp_dir"] = a.tmp
    if a.groups:
        gs = tuple(a.groups.split(","))
        tasks = [t for t in tasks if t["group"].startswith(gs)]
    if a.tmp:
        Path(a.tmp).mkdir(parents=True, exist_ok=True)
    deadline = time.time() + a.max_minutes * 60 if a.max_minutes else None
    res = run_tasks(tasks, raw, a.jobs, progress=lambda s: print(s, flush=True), deadline=deadline)
    print(f"[run] finished: {res}", flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="원시 수집 (재개 가능)")
    r.add_argument("--raw", required=True)
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--scale", type=float, default=1.0)
    r.add_argument("--n-null", type=int, default=N_NULL)
    r.add_argument("--groups", default="")
    r.add_argument("--counts", default="", help='JSON, 예: {"d_uni_null": 6}')
    r.add_argument("--tmp", default="")
    r.add_argument("--max-minutes", type=float, default=0.0)
    r.add_argument("--no-reference", action="store_true")
    r.set_defaults(fn=_cmd_run)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
