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
   - 임시 저장소에 현재 config/gates.yaml·costs.yaml 을 복사한다 → 캠페인 매니페스트의 일간 수익 회계가 **새 캠페인
     기준(현재 mtm)** 이 되고 캠페인 비용도 현재 레지스트리다 (gates.yaml 을 복사하지 않으면 임시 저장소의 캠페인 번호 <
     승인 시점이라 승인 전 값 realized 로 등록된다; calibration_study 의 기존 실행이 그 상태였다).
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
def study_gate_cfg(n_null: int = N_NULL, path: str | None = None) -> dict:
    """현재 관문 설정(config/gates.yaml, 또는 path 의 관문 설정 파일) + G2.null_runs = n_null (수집·대조 실행 공통).
    그 밖의 값은 그대로."""
    cfg = copy.deepcopy(G.load_gate_config(path) if path else G.load_gate_config())
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
        g6, vres, _vmet, _mc = p.g6(spec, m1v, vs, ve, p.costs["primary"], p.costs["stress"], record=False,
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
            "g3_nb": [_f(x.get("sharpe_w")) for x in g3.get("neighbors") or []],
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
        "grid": grid, "campaign_id": cr.campaign_id, "n_specs": len(prim), "family": tpl.family,
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
    # 현재 gates.yaml + costs.yaml 을 임시 저장소에 둔다: (1) 캠페인 회계 = 새 캠페인 기준(mtm), (2) 캠페인 비용 = 현재
    # 비용 레지스트리(임시 저장소에 gates.yaml 만 있으면 run_campaign 이 costs.yaml 을 못 찾아 v1 프리셋으로 돈다).
    # params.yaml 은 두지 않는다(두면 변경 이력의 applies_from_campaign 때문에 승인 전 값으로 되돌아간다).
    for name in ("gates.yaml", "costs.yaml"):
        shutil.copy(G.GATES_PATH.parent / name, tmp / "config" / name)
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
                                   gate_cfg=study_gate_cfg(task["n_null"], task.get("gate_config")), register=False,
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
except Exception:  # noqa: BLE001, S110 — numba 가 없으면 순수 파이썬으로 동작
    pass


def make_universe_root(tmp: Path, layout: C.Layout, task: dict) -> None:
    import yaml

    from btc_quant.data.store import write_ohlcv
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "splits.yaml").write_text(yaml.safe_dump(layout.splits()))
    for sym, df in gen_universe_markets(layout.data_start, layout.val_end, task["seed"], task["k"]).items():
        write_ohlcv(df, "coinone", sym, "1m", root=tmp / "data" / "raw")


# ---- (예전 MTM 0-나눗셈 가드는 `fast._mtm_core` 의 파산 규칙(그날 −100%, 이후 0)으로 대체됨 — 호환용 무해 함수)
def install_mtm_guard() -> None:
    return None


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


def _val_mode_of(vb: dict) -> dict:
    """gates.yaml G6.val_budget.evidence → 패키지 val_mode/val_alpha (holm → holm(alpha), fixed → fixed(fixed_p),
    combined → stouffer(combined_alpha))."""
    m = G.val_evidence_mode(vb)
    if m == "holm":
        return {"val_mode": "holm", "val_alpha": float(vb["alpha"])}
    if m == "fixed":
        return {"val_mode": "fixed", "val_alpha": float(vb["fixed_p"])}
    return {"val_mode": "stouffer", "val_alpha": float(vb["combined_alpha"])}


def size_scale(c: dict, pkg: dict) -> float:
    """크기 조절 배수 (`gates.position_scale` 와 같은 식): min(1, g6_mdd/Val MDD, g6_mc/MC MDD95). 한도 None = 그 항 없음."""
    out = 1.0
    for lim, x in ((pkg["g6_mdd"], c["val_mdd"]), (pkg["g6_mc"], c["val_mc_mdd95"])):
        if lim is None:
            continue
        if x is None or not math.isfinite(float(x)):
            return 0.0
        x = abs(float(x))
        if x > 0:
            out = min(out, float(lim) / x)
    return out


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
        "g6_sharpe": c["G6"]["min_sharpe"], "g6_mdd": c["G6"]["max_mdd"],
        "g6_stress": None if G.g6_stress_mode(c["G6"]) == "off" else c["G6"]["min_stress_sharpe"],
        "g6_mc": c["G6"]["max_mc_mdd95"],
        "g6_dd_mode": G.g6_drawdown_mode(c["G6"]), "g6_min_scale": float(c["G6"].get("min_position_scale", 0.25)),
        **_val_mode_of(vb),
        "val_cap": int(vb["cap"]),
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
    m = float(pkg.get("dsr_mult", 1.0))          # 자율 체제 벌점 (1 + L_dev)
    if pkg.get("dsr_n", "current") == "sum_keff":
        return m * float(max(k_eff, sum_keff))
    return m * max(float(k_eff), n_family * k_eff / max(n_camp, 1))


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
    if pkg.get("g6_dd_mode", "reject") == "size":     # 크기 조절: 낙폭 두 조건 → 크기 하한 하나 (G6_mdd 자리, 라벨 'size')
        f["G6_mdd"] = size_scale(c, pkg) >= float(pkg.get("g6_min_scale", 0.25))
        f["G6_mc"] = True
    else:
        f["G6_mdd"] = pkg["g6_mdd"] is None or abs(c["val_mdd"]) <= pkg["g6_mdd"]
        f["G6_mc"] = pkg["g6_mc"] is None or abs(c["val_mc_mdd95"]) <= pkg["g6_mc"]
    f["G6_stress"] = pkg["g6_stress"] is None or c["val_stress_sharpe"] > pkg["g6_stress"]
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
                why = [("size" if a == "G6_mdd" and pkg.get("g6_dd_mode") == "size" else ATOM_LABEL[a])
                       for a in G6_ATOMS if not f[a]]
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
         "a_1m_null": 25_000, "a_regime_null": 26_001, "b_5m": 31_000, "b_1m_atr": 131_000, "b_5m_low": 231_000,
         "b_5m_vlow": 331_000, "b_regime": 39_001, "c_null": 41_000, "c_planted": 42_000, "d_uni_null": 51_000,
         "d_uni_planted": 52_000}
BASE_COUNTS = {"a_std_null": 40, "a_breakeven": 20, "a_low_null": 15, "a_vlow_null": 15, "a_1m_null": 20,
               "a_regime_null": 45, "b_planted": 14, "b_regime": 20, "c_null": 12, "c_planted": 12,
               "d_uni_null": 0, "d_uni_planted": 0}
# 일치 점검(실제 파이프라인도 같이 실행)할 시장 수 — 그룹별 앞에서부터
ACTUAL_COUNTS = {"a_std_null": 4, "a_breakeven": 2, "a_low_null": 1, "a_vlow_null": 1, "a_1m_null": 1,
                 "a_regime_null": 3, "b_planted@2": 2, "b_planted@3": 2, "b_planted@1": 0, "b_planted@1.5": 1,
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


UNIVERSE_PILOT_KS = (0.0, 0.015, 0.02, 0.025, 0.03, 0.04)


def _pilot_universe_one(args) -> tuple[float, int, float, float]:
    """3자산 시장에서 S*(세 자산 모두 같은 스펙, 자산별 비용, 1/N 슬리브)의 포트폴리오 연 Sharpe 와 거래/일(합계)."""
    from btc_quant.backtest import fast
    from btc_quant.evaluation import metrics as M

    from .campaign import get_sleeve_cost
    k, seed, years = args
    start = pd.Timestamp("2010-01-01", tz="UTC")
    end = start + pd.Timedelta(days=round(365.25 * years))
    dailies, n_tr = [], 0
    for sym, df in gen_universe_markets(str(start.date()), str(end.date()), seed, k).items():
        res = fast.run_fast(C.PLANTED_SPEC, C._engine_m1(df), get_sleeve_cost(C.PRIMARY_COST, sym),
                            start + pd.Timedelta(days=3), end)
        dailies.append(res.daily_returns)
        n_tr += len(res.trades)
    port = pd.concat(dailies, axis=1).fillna(0.0).mean(axis=1)
    return k, seed, float(M.sharpe(port)), n_tr / max(1.0, (end - start).days)


def universe_edge_map(raw_dir: Path, pool=None, seeds: int = 3, years: float = 4.0, ks=UNIVERSE_PILOT_KS,
                      progress=print) -> dict:
    """3자산 유니버스 시장의 k ↦ 포트폴리오 S* 참 Sharpe (자산별 현재 비용). raw_dir/_edge_universe.json."""
    fp = Path(raw_dir) / "_edge_universe.json"
    if fp.exists():
        return json.loads(fp.read_text())
    tasks = [(k, 90_000 + s_, years) for k in ks for s_ in range(seeds)]
    progress(f"[edge] pilot universe: {len(ks)} k × {seeds} seeds × {years}y")
    rows = list(pool.map(_pilot_universe_one, tasks)) if pool else [_pilot_universe_one(t) for t in tasks]
    kk = np.array(ks, float)
    sr = np.array([np.mean([r[2] for r in rows if r[0] == k]) for k in kk])
    mono = np.maximum.accumulate(sr)
    k_for = {}
    for s_ in STRENGTHS:
        if s_ > mono[-1]:
            k_for[f"{s_:g}"] = float(kk[-1] + (s_ - mono[-1]) / ((mono[-1] - mono[-2]) / (kk[-1] - kk[-2])))
        else:
            k_for[f"{s_:g}"] = float(np.interp(s_, mono, kk))
    e = {"ks": kk.tolist(), "sharpe_mean": sr.tolist(),
         "trades_per_day": [float(np.mean([r[3] for r in rows if r[0] == k])) for k in kk],
         "per_seed": [list(r) for r in rows], "pilot_years": years, "pilot_seeds": seeds, "k_for_strength": k_for,
         "common_share": UNIVERSE_COMMON_SHARE}
    _write_json(fp, e)
    return e


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
    cnt = {k: round(v * scale) for k, v in BASE_COUNTS.items()}
    cnt.update(counts or {})
    act = dict(ACTUAL_COUNTS)
    act.update(actual_counts or {})
    base = {"layout": asdict(layout), "n_null": int(n_null), "top_k": int(top_k)}
    tasks = []

    def add(group, i, off=0, **kw):
        t = {**base, "group": group, "seed": SEED0[group] + off + i, **kw}
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
                add(f"b_{sc}", i, si * 1000, kind="planted", scenario=sc, strength=float(s), k=float(kf[f"{s:g}"]),
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


# ============================================================== 4단계: 분석 (원시 JSON 에서 다시 실행 가능)
SCENARIOS = ("5m", "1m_atr", "5m_low", "5m_vlow")
SCEN_LABEL = {"5m": "5분 (≈5회/일)", "1m_atr": "1분·ATR (≈12회/일)", "5m_low": "저회전 (≈0.45회/일)",
              "5m_vlow": "초저회전 (≈0.23회/일)", "regime": "국면 전환 (필터 엣지)", "universe": "3자산"}
NULL_GROUPS = ("a_std_null", "a_breakeven", "a_low_null", "a_vlow_null", "a_1m_null", "a_regime_null")
NULL_LABEL = {"a_std_null": "표준 GARCH 귀무", "a_breakeven": "손익분기 귀무", "a_low_null": "저회전 격자 귀무",
              "a_vlow_null": "초저회전 격자 귀무", "a_1m_null": "1분·ATR 귀무", "a_regime_null": "국면 전환 귀무"}
TARGET_STRENGTHS = (1.5, 2.0)
# '어려운 귀무': 비용이 후보를 저절로 죽이지 않는 귀무(저회전·초저회전 격자, 손익분기). 표준·1분·국면 귀무의 상위 후보는
# 비용 후 Sharpe 가 뚜렷이 음수라(하루 5~12회 × 왕복 4bp) 거의 모든 관문에 동시에 걸린다 → 합산 오탐은 쉬운 귀무가 지배한다.
# 패키지 탐색의 오탐 제약은 '합산'과 '어려운 귀무' 둘 다에 건다.
HARD_NULL_GROUPS = ("a_low_null", "a_vlow_null", "a_breakeven")


def is_program(rec: dict) -> bool:
    return len(rec["campaigns"]) > 1


def is_null(rec: dict) -> bool:
    return rec.get("kind") in ("null", "breakeven")


def half(rec: dict) -> str:
    return "tune" if int(rec["seed"]) % 2 == 0 else "hold"


def rate(x: int, n: int) -> dict:
    lo, hi = C.wilson(int(x), int(n))
    return {"x": int(x), "n": int(n), "p": (x / n if n else None), "lo": _f(lo), "hi": _f(hi)}


def fmt_rate(r: dict | None, ci: bool = True) -> str:
    if not r or not r["n"]:
        return "—"
    s = f"{100 * r['p']:.0f}% ({r['x']}/{r['n']})"
    return s + (f" [{100 * r['lo']:.0f}–{100 * r['hi']:.0f}]" if ci else "")


class Data:
    """레코드 묶음: 새 원장 귀무/심은 엣지, 누적 프로그램."""

    def __init__(self, records: list[dict]):
        self.all = [r for r in records if r.get("scenario") != "universe"]
        self.universe = [r for r in records if r.get("scenario") == "universe"]
        self.fresh = [r for r in self.all if not is_program(r)]
        self.null = [r for r in self.fresh if is_null(r)]
        self.planted = [r for r in self.fresh if not is_null(r)]
        self.prog = [r for r in self.all if is_program(r)]

    def subset(self, which: str) -> Data:
        return self if which == "all" else Data([r for r in self.all + self.universe if half(r) == which])


def package_metrics(d: Data, pkg: dict, cells: bool = True) -> dict:
    """패키지 하나의 오탐(새 원장 귀무; 종류별·하락 Val)과 검정력(시나리오 × 세기; 목표 = Sharpe 1.5–2 칸 평균)."""
    fp = fp_bear = n_bear = fp_hard = n_hard = 0
    by_g: dict[str, list[int]] = {}
    for r in d.null:
        v = evaluate_market(r, pkg, detail=False)["any_validated"]
        fp += v
        if r["group"] in HARD_NULL_GROUPS:
            fp_hard += v
            n_hard += 1
        g = by_g.setdefault(r["group"], [0, 0])
        g[0] += v
        g[1] += 1
        if r.get("val_regime") == "bear":
            fp_bear += v
            n_bear += 1
    pw: dict[tuple, list[int]] = {}
    for r in d.planted:
        ev = evaluate_market(r, pkg, detail=False)
        c = pw.setdefault((r["scenario"], float(r["strength"])), [0, 0, 0])
        c[0] += ev["star_validated"]
        c[1] += 1
        c[2] += ev["any_validated"]
    tgt = [pw[(sc, s)][0] / pw[(sc, s)][1] for sc in SCENARIOS for s in TARGET_STRENGTHS if pw.get((sc, s), [0, 0])[1]]
    if not tgt:                        # 목표 칸이 없는 자료(quick 점검 등): 있는 칸 전부의 평균
        tgt = [v[0] / v[1] for v in pw.values() if v[1]]
    out = {"fpr": rate(fp, len(d.null)), "fpr_bear": rate(fp_bear, n_bear), "fpr_hard": rate(fp_hard, n_hard),
           "power_target": float(np.mean(tgt)) if tgt else 0.0,
           "power_target_pooled": rate(sum(pw[(sc, s)][0] for sc in SCENARIOS for s in TARGET_STRENGTHS if (sc, s) in pw),
                                       sum(pw[(sc, s)][1] for sc in SCENARIOS for s in TARGET_STRENGTHS if (sc, s) in pw))}
    if cells:
        out["fpr_by_group"] = {g: rate(*v) for g, v in sorted(by_g.items())}
        out["power"] = {f"{sc}@{s:g}": rate(v[0], v[1]) for (sc, s), v in sorted(pw.items())}
        out["power_any"] = {f"{sc}@{s:g}": rate(v[2], v[1]) for (sc, s), v in sorted(pw.items())}
        for s in STRENGTHS:
            ks = [(sc, s) for sc in SCENARIOS if (sc, s) in pw]
            out[f"power_s{s:g}"] = rate(sum(pw[k][0] for k in ks), sum(pw[k][1] for k in ks))
    return out


def program_metrics(d: Data, pkg: dict) -> dict:
    """누적 원장 프로그램: 귀무 프로그램 오탐(어느 캠페인에서든 validated), 심은 프로그램의 마지막 캠페인 S* validated·
    프로그램 전체(어느 후보든 validated), Val 사용 수."""
    n0 = n1 = fp = star = anyv = 0
    used0, used1 = [], []
    for r in d.prog:
        ev = evaluate_market(r, pkg, detail=False)
        if is_null(r):
            n0 += 1
            fp += ev["any_validated"]
            used0.append(ev["val_used"])
        else:
            n1 += 1
            star += ev["star_validated"]
            anyv += ev["any_validated"]
            used1.append(ev["val_used"])
    return {"null_program_fpr": rate(fp, n0), "planted_last_star": rate(star, n1), "planted_program_any": rate(anyv, n1),
            "val_used_null_mean": float(np.mean(used0)) if used0 else None,
            "val_used_planted_mean": float(np.mean(used1)) if used1 else None}


def first_failed_tables(d: Data, pkg: dict) -> dict:
    """첫 탈락 관문 분포: 귀무 시장의 모든 후보 vs 심은 시장의 S* 대표(시나리오 × 세기)."""
    null_lab: dict[str, int] = {}
    for r in d.null:
        for c in evaluate_market(r, pkg, detail=False)["cands"]:
            null_lab[c["label"]] = null_lab.get(c["label"], 0) + 1
    star: dict[str, dict[str, int]] = {}
    for r in d.planted:
        ev = evaluate_market(r, pkg, detail=False)
        t = star.setdefault(f"{r['scenario']}@{float(r['strength']):g}", {})
        t[ev["star_label"]] = t.get(ev["star_label"], 0) + 1
    return {"null_candidates": dict(sorted(null_lab.items(), key=lambda kv: -kv[1])),
            "star": {k: dict(sorted(v.items(), key=lambda kv: -kv[1])) for k, v in sorted(star.items())}}


def _standalone_flags(c: dict, camp: dict, pkg: dict) -> dict:
    """후보 하나의 원자 관문별 단독 판정 (새 원장: 시도 수 = K; G6_holm = 첫 사용(k=1) 문턱으로 근사)."""
    f = atom_flags(c, pkg, float(camp["k_eff"]), camp.get("pbo"))
    ok, _ = val_evidence(c, pkg, [], dsr_z(c, float(camp["k_eff"])))
    f["G6_holm"] = ok
    return f


def gate_contribution(d: Data, pkg: dict) -> dict:
    """관문 기여도. (i) 하나 빼기: 원자 관문을 하나씩 껐을 때 오탐·검정력 변화. (ii) 하나만: 그 관문만 켰을 때 시장 단위
    오탐, 후보 단위로 귀무 후보·참 엣지(S* 대표, 참 Sharpe ≥ 1.5)를 막는 비율, 쌍 중복 P(B 도 막음 | A 가 막음),
    귀무 후보를 '혼자' 막은 관문(다른 원자 관문은 모두 통과)."""
    base = package_metrics(d, pkg, cells=True)
    logo, only = {}, {}
    for a in ATOMS:
        m = package_metrics(d, without_atoms(pkg, [a]), cells=True)
        logo[a] = {"fpr": m["fpr"], "fpr_bear": m["fpr_bear"], "fpr_hard": m["fpr_hard"], "power_target": m["power_target"],
                   "d_fpr": m["fpr"]["p"] - base["fpr"]["p"], "d_power_target": m["power_target"] - base["power_target"],
                   **{f"power_s{s:g}": m[f"power_s{s:g}"] for s in STRENGTHS}}
        mo = package_metrics(d, only_atom(pkg, a), cells=False)
        only[a] = {"fpr_market": mo["fpr"], "fpr_hard": mo["fpr_hard"], "power_target": mo["power_target"]}
    nullf, starf = [], []
    for r in d.null:
        for c in r["campaigns"][0]["cands"]:
            nullf.append(_standalone_flags(c, r["campaigns"][0], pkg))
    for r in d.planted:
        if float(r["strength"]) < 1.5:
            continue
        for c in r["campaigns"][0]["cands"]:
            if c.get("is_star"):
                starf.append(_standalone_flags(c, r["campaigns"][0], pkg))
    stop_null = {a: rate(sum(not f[a] for f in nullf), len(nullf)) for a in ATOMS}
    stop_star = {a: rate(sum(not f[a] for f in starf), len(starf)) for a in ATOMS}
    red = {}
    for a in ATOMS:
        sa = [f for f in nullf if not f[a]]
        red[a] = {b: (sum(not f[b] for f in sa) / len(sa) if sa else None) for b in ATOMS}
    sole = {a: 0 for a in ATOMS}
    nfail: dict[int, int] = {}
    for f in nullf:
        bad = [a for a in ATOMS if not f[a]]
        nfail[len(bad)] = nfail.get(len(bad), 0) + 1
        if len(bad) == 1:
            sole[bad[0]] += 1
    sole_star = {a: 0 for a in ATOMS}
    nfail_star: dict[int, int] = {}
    for f in starf:
        bad = [a for a in ATOMS if not f[a]]
        nfail_star[len(bad)] = nfail_star.get(len(bad), 0) + 1
        if len(bad) == 1:
            sole_star[bad[0]] += 1
    return {"base": {"fpr": base["fpr"], "fpr_hard": base["fpr_hard"], "power_target": base["power_target"]},
            "leave_one_out": logo, "only": only,
            "stop_null_candidates": stop_null, "stop_true_edge_star": stop_star, "redundancy_null": red,
            "sole_stopper_null": sole, "n_failed_atoms_null": dict(sorted(nfail.items())),
            "sole_stopper_true_edge": sole_star, "n_failed_atoms_true_edge": dict(sorted(nfail_star.items())),
            "n_null_candidates": len(nullf), "n_true_edge_star": len(starf)}


# ---- 통과선 훑기 (한 번에 하나씩, 현재 기준에서)
VAL_VARIANTS = (("holm α=0.10 (현재)", {"val_mode": "holm", "val_alpha": 0.10}),
                ("holm α=0.20", {"val_mode": "holm", "val_alpha": 0.20}),
                ("holm α=0.30", {"val_mode": "holm", "val_alpha": 0.30}),
                ("고정 p≤0.05", {"val_mode": "fixed", "val_alpha": 0.05}),
                ("고정 p≤0.10", {"val_mode": "fixed", "val_alpha": 0.10}),
                ("고정 p≤0.20", {"val_mode": "fixed", "val_alpha": 0.20}),
                ("고정 p≤0.30", {"val_mode": "fixed", "val_alpha": 0.30}),
                ("고정 p≤0.50", {"val_mode": "fixed", "val_alpha": 0.50}),
                ("Stouffer(같은 가중) p≤0.05", {"val_mode": "stouffer", "val_alpha": 0.05}),
                ("Stouffer(√일수 가중) p≤0.05", {"val_mode": "stouffer_n", "val_alpha": 0.05}),
                ("Val 증거 없음(Sharpe>0 만)", {"val_mode": "none"}))
SWEEPS: list[tuple[str, str, dict]] = [
    *[("G1 최소 거래", f"{v}", {"g1_min_trades": v}) for v in (100, 150, 200, 300)],
    *[("G2 귀무 상위 비율", f"{v:g}", {"g2_frac": v}) for v in (0.05, 0.10, 0.20)],
    *[("G3 이웃 중앙값 비", f"{v:g}", {"g3_ratio": v}) for v in (0.0, 0.25, 0.5)],
    *[("G4 롤링 양수 비율", f"{v:g}", {"g4_roll": v}) for v in (0.4, 0.5, 0.6)],
    *[("G4 최근 12개월 Sharpe", lab, {"g4_recent": v}) for lab, v in ((">0", 0.0), (">-0.5", -0.5), ("끔", None))],
    *[("G5 DSR", f"{v:g}", {"g5_dsr": v}) for v in (0.80, 0.90, 0.95)],
    *[("G5 PBO", lab, {"g5_pbo": v}) for lab, v in (("<0.3", 0.3), ("<0.5", 0.5), ("끔", None))],
    *[("G6 stress Sharpe", lab, {"g6_stress": v}) for lab, v in ((">0", 0.0), (">-1", -1.0), ("끔(견고성 비용×1.5 만)", None))],
    *[("Val 증거", lab, ch) for lab, ch in VAL_VARIANTS],
    *[("견고성 알파 t", f"{v:g}", {"rob_alpha_t": v}) for v in (1.5, 2.0)],
    *[("견고성 지연 유지율", f"{v:g}", {"rob_latency": v}) for v in (0.3, 0.5)],
    *[("견고성 국면 비중", f"{v:g}", {"rob_regime": v}) for v in (0.7, 0.8)],
]
CLASS3_SWEEPS: list[tuple[str, str, dict]] = [
    *[("G6 Val 낙폭 상한", lab, {"g6_mdd": v}) for lab, v in (("20%", 0.20), ("30%", 0.30), ("40%", 0.40), ("끔", None))],
    *[("G6 MC 낙폭95 상한", lab, {"g6_mc": v}) for lab, v in (("20%", 0.20), ("30%", 0.30), ("40%", 0.40), ("끔", None))],
    *[("둘 다", lab, {"g6_mdd": v, "g6_mc": v}) for lab, v in (("30%", 0.30), ("40%", 0.40), ("끔", None))],
]


def run_sweeps(d: Data, pkg: dict, sweeps=None) -> list[dict]:
    out = []
    for name, lab, ch in (sweeps or SWEEPS):
        m = package_metrics(d, with_changes(pkg, **ch), cells=True)
        out.append({"gate": name, "value": lab, "changes": ch, "is_current": all(pkg.get(k) == v for k, v in ch.items()),
                    "fpr": m["fpr"], "fpr_bear": m["fpr_bear"], "fpr_hard": m["fpr_hard"],
                    "power_target": m["power_target"],
                    **{f"power_s{s:g}": m[f"power_s{s:g}"] for s in STRENGTHS},
                    "power_by_scenario_target": {sc: _pool(m["power"], sc) for sc in SCENARIOS}})
    return out


def _pool(power: dict, sc: str) -> dict:
    xs = [power[f"{sc}@{s:g}"] for s in TARGET_STRENGTHS if f"{sc}@{s:g}" in power]
    return rate(sum(r["x"] for r in xs), sum(r["n"] for r in xs))


# ---- 단계별 통과율: Dev 생존 → Val (점검 + 증거 규칙) → 견고성
def stage_rates(d: Data, dev_pkgs: dict[str, dict], base: dict) -> dict:
    """귀무·참 엣지(S* 대표, 세기별) 후보의 단계별 통과율. Dev 패키지마다: Dev 생존 비율, 그 생존자 중 G6 점검(Sharpe·낙폭·
    stress·MC) 통과, 각 Val 증거 규칙 통과(첫 사용 k=1 기준), 점검+증거 통과, 그리고 견고성까지 통과.
    '전체 후보' 행 = Dev 를 무시한 Val 단계 단독 통과율(귀무에서 Val 단계가 혼자 얼마나 거르는지)."""
    groups: dict[str, list[tuple[dict, dict]]] = {"귀무(전체)": []}
    for r in d.null:
        for c in r["campaigns"][0]["cands"]:
            groups["귀무(전체)"].append((c, r["campaigns"][0]))
            if r.get("val_regime") == "bear":
                groups.setdefault("귀무(하락 Val)", []).append((c, r["campaigns"][0]))
    for r in d.planted:
        for c in r["campaigns"][0]["cands"]:
            if c.get("is_star"):
                key = "참 엣지 국면(S2)" if r["scenario"] == "regime" else f"참 엣지 S{float(r['strength']):g}"
                groups.setdefault(key, []).append((c, r["campaigns"][0]))
    out = {}
    dev_all = {"(Dev 무시 — 전체 후보)": None, **dev_pkgs}
    for gname, items in groups.items():
        res = {}
        for dname, dp in dev_all.items():
            surv = []
            for c, camp in items:
                if dp is None:
                    surv.append((c, camp))
                else:
                    f = atom_flags(c, dp, float(camp["k_eff"]), camp.get("pbo"))
                    if c["g0_pass"] and all(f[a] for a in DEV_ATOMS):
                        surv.append((c, camp))
            row = {"n": len(items), "dev_survivors": rate(len(surv), len(items)), "val": {}}
            chk = rob = 0
            fl = []
            for c, camp in surv:
                f = atom_flags(c, base, float(camp["k_eff"]), camp.get("pbo"))
                ok_chk = all(f[a] for a in G6_ATOMS)
                ok_rob = all(f[a] for a in ROB_ATOMS)
                chk += ok_chk
                rob += ok_rob
                fl.append((c, camp, ok_chk, ok_rob, f["G6_sharpe"]))
            row["g6_checks"] = rate(chk, len(surv))
            row["robustness"] = rate(rob, len(surv))
            for lab, ch in VAL_VARIANTS:
                vp = with_changes(base, **ch)
                ev = evonly = full = 0
                for c, camp, ok_chk, ok_rob, ok_sr in fl:
                    ok, _ = val_evidence(c, vp, [], dsr_z(c, float(camp["k_eff"])))
                    evonly += ok and ok_sr
                    ev += ok and ok_chk
                    full += ok and ok_chk and ok_rob
                row["val"][lab] = {"evidence_and_sharpe": rate(evonly, len(surv)), "g6": rate(ev, len(surv)),
                                   "g6_and_robustness": rate(full, len(surv))}
            res[dname] = row
        out[gname] = res
    return out


# ---- 패키지 탐색 (TUNE 절반에서 고르고 HOLD-OUT 에서 보고). class ② 통과선만 (낙폭 한도 = class ③ 은 고정).
SEARCH_OPTIONS: dict[str, list[dict]] = {
    "G1": [{"g1_min_trades": v} for v in (300, 200, 150, 100)],
    "G2": [{"g2_frac": v} for v in (0.05, 0.10, 0.20)],
    "G3": [{"g3_ratio": v} for v in (0.5, 0.25, 0.0)],
    "G4_roll": [{"g4_roll": v} for v in (0.6, 0.5, 0.4)],
    "G4_recent": [{"g4_recent": v} for v in (0.0, -0.5, None)],
    "G5_dsr": [{"g5_dsr": v} for v in (0.95, 0.90, 0.80)],
    "G5_pbo": [{"g5_pbo": v} for v in (0.3, 0.5, None)],
    "G6_stress": [{"g6_stress": v} for v in (0.0, -1.0, None)],
    "Val": [ch for _, ch in VAL_VARIANTS[:10]],
    "R_alpha": [{"rob_alpha_t": v} for v in (2.0, 1.5)],
    "R_latency": [{"rob_latency": v} for v in (0.5, 0.3)],
    "R_regime": [{"rob_regime": v} for v in (0.7, 0.8)],
}


# 확장 탐색 공간: 훑기 범위 + '끔'(관문 제거)까지. 훑기 범위 안에서는 귀무 오탐이 0 에서 움직이지 않아(귀무 후보가 관문
# 여러 개에 동시에 걸림) 오탐 제약이 묶이지 않는다 → 오탐 5/10/20% 를 실제로 '쓰는' 패키지는 이 공간에서 찾는다.
SEARCH_OPTIONS_EXT: dict[str, list[dict]] = {
    "G1": [{"g1_min_trades": v} for v in (300, 200, 150, 100)],
    "G2": [{"g2_frac": v} for v in (0.05, 0.10, 0.20, None)],
    "G3": [{"g3_ratio": v} for v in (0.5, 0.25, 0.0, None)],
    "G4_roll": [{"g4_roll": v} for v in (0.6, 0.5, 0.4, None)],
    "G4_recent": [{"g4_recent": v} for v in (0.0, -0.5, None)],
    "G5_dsr": [{"g5_dsr": v} for v in (0.95, 0.90, 0.80, 0.50, None)],
    "G5_pbo": [{"g5_pbo": v} for v in (0.3, 0.5, None)],
    "G6_stress": [{"g6_stress": v} for v in (0.0, -1.0, -2.0, None)],
    "Val": [ch for _, ch in VAL_VARIANTS],
    "R_alpha": [{"rob_alpha_t": v} for v in (2.0, 1.5, 1.0, None)],
    "R_latency": [{"rob_latency": v} for v in (0.5, 0.3, None)],
    "R_regime": [{"rob_regime": v} for v in (0.7, 0.8, None)],
    "R_cost": [{"rob_cost_mult": v} for v in (1.5, None)],
    "R_outliers": [{"rob_outliers": v} for v in (0.0, None)],
    "R_loyo": [{"rob_loyo": v} for v in (0.0, None)],
}
LOOSEST = {"Val": 7}                # 가장 느슨한 출발점의 Val 선택 = 고정 p ≤ 0.50 (확장 공간에서는 '증거 없음')


def _choice_pkg(base: dict, choice: dict[str, int], options: dict | None = None) -> dict:
    options = options or SEARCH_OPTIONS
    pkg = dict(base)
    for key, i in choice.items():
        pkg.update(options[key][i])
    return pkg


def _choice_changes(choice: dict[str, int], options: dict) -> dict:
    ch = {}
    for k, i in choice.items():
        if i:
            ch.update(options[k][i])
    return ch


def _n_changes(choice: dict[str, int]) -> int:
    return sum(1 for i in choice.values() if i != 0)


def package_search(d: Data, base: dict, fpr_target: float, eps: float = 0.02, progress=None,
                   options: dict | None = None, cache: dict | None = None) -> dict:
    """목표: Sharpe 1.5–2 칸(회전율 4종) 평균 검정력 최대, 제약: 새 원장 귀무 오탐 ≤ fpr_target — 합산 오탐과
    '어려운 귀무'(HARD_NULL_GROUPS) 오탐 중 큰 쪽 기준. 단순한 패키지 선호.
    방법: (A) 현재에서 출발해 한 키 또는 두 키를 동시에 바꾸는 이동 중 가장 좋은 것을 반복(좌표 상승; 두 키 이동은 여러
    관문이 함께 막는 경우를 위해). (B) 가장 느슨한 선택에서 출발해 오탐 제약을 만족할 때까지 '검정력 손실이 가장 작은'
    조이기를 반복. 둘 중 목표값이 큰 쪽(같으면 변경 수가 적은 쪽) → (C) 가지치기: 현재값으로 되돌려도 목표값이 eps 넘게
    줄지 않는 변경은 되돌린다. 평가 기록(모든 패키지의 오탐·검정력)은 trade-off 곡선용으로 남긴다."""
    SEARCH = options or SEARCH_OPTIONS
    keys = list(SEARCH)
    cache = {} if cache is None else cache            # 같은 자료·같은 공간이면 목표 간에 공유
    log = progress or (lambda s: None)

    def ev(choice: dict[str, int]) -> tuple[float, float]:
        k = tuple(choice[x] for x in keys)
        if k not in cache:
            m = package_metrics(d, _choice_pkg(base, choice, SEARCH), cells=False)
            cache[k] = (max(m["fpr"]["p"] or 0.0, m["fpr_hard"]["p"] or 0.0), m["power_target"] or 0.0)
        return cache[k]

    def better(a, b, na, nb) -> bool:      # a 가 b 보다 나은가 (목표값 → 변경 수 → 오탐)
        if a[1] > b[1] + eps:
            return True
        if a[1] < b[1] - eps:
            return False
        return (na, a[0]) < (nb, b[0])

    path: list[dict] = []

    def ascend(cur: dict[str, int], record: bool = False) -> dict[str, int]:
        cur = dict(cur)
        while True:
            best, best_v = None, ev(cur)
            moves = [((k1, i1),) for k1 in keys for i1 in range(len(SEARCH[k1])) if i1 != cur[k1]]
            moves += [((k1, i1), (k2, i2)) for a_, k1 in enumerate(keys) for k2 in keys[a_ + 1:]
                      for i1 in range(len(SEARCH[k1])) if i1 != cur[k1]
                      for i2 in range(len(SEARCH[k2])) if i2 != cur[k2]]
            for mv in moves:
                c2 = dict(cur)
                for k_, i_ in mv:
                    c2[k_] = i_
                v = ev(c2)
                if (v[0] <= fpr_target + 1e-12 and v[1] > best_v[1] + eps
                        and (best is None or better(v, ev(best), _n_changes(c2), _n_changes(best)))):
                    best = c2
            if best is None:
                return cur
            if record:
                step = {k_: best[k_] for k_ in keys if best[k_] != cur[k_]}
                path.append({"step_choice": step, "choice": dict(best), "tune_fpr": ev(best)[0],
                             "tune_power": ev(best)[1]})
            cur = best
            log(f"  ascend → {ev(cur)} {_n_changes(cur)} changes")

    def tighten(cur: dict[str, int]) -> dict[str, int]:
        cur = dict(cur)
        while ev(cur)[0] > fpr_target + 1e-12:
            v0 = ev(cur)
            best, score = None, None
            for k1 in keys:
                for i1 in range(len(SEARCH[k1])):
                    if i1 == cur[k1]:
                        continue
                    c2 = dict(cur)
                    c2[k1] = i1
                    v = ev(c2)
                    if v[0] >= v0[0] - 1e-12:
                        continue
                    sc = ((v0[1] - v[1]) / (v0[0] - v[0]), -(v0[0] - v[0]))     # 오탐 1 줄이는 데 드는 검정력
                    if score is None or sc < score:
                        best, score = c2, sc
            if best is None:
                return {k: 0 for k in keys}
            cur = best
        return cur

    def prune(cur: dict[str, int]) -> dict[str, int]:
        cur = dict(cur)
        changed = True
        while changed:
            changed = False
            v0 = ev(cur)
            for k1 in keys:
                if cur[k1] == 0:
                    continue
                c2 = dict(cur)
                c2[k1] = 0
                v = ev(c2)
                if v[0] <= fpr_target + 1e-12 and v[1] >= v0[1] - eps:
                    cur, changed = c2, True
                    break
        return cur

    start = {k: 0 for k in keys}
    path.append({"step_choice": {}, "choice": dict(start), "tune_fpr": ev(start)[0], "tune_power": ev(start)[1]})
    a = ascend(start, record=True)
    loosest = {k: len(SEARCH[k]) - 1 for k in keys}
    if SEARCH is SEARCH_OPTIONS:
        loosest.update(LOOSEST)
    b = ascend(tighten(loosest))
    pick = a if not better(ev(b), ev(a), _n_changes(b), _n_changes(a)) else b
    pick = prune(pick)
    changes = _choice_changes(pick, SEARCH)
    evals = [{"choice": dict(zip(keys, k)), "fpr": v[0], "power_target": v[1],
              "n_changes": sum(1 for i in k if i != 0)} for k, v in cache.items()]
    return {"fpr_target": fpr_target, "choice": pick, "changes": changes, "n_changes": _n_changes(pick),
            "tune": {"fpr": ev(pick)[0], "power_target": ev(pick)[1]}, "start_ascend": {"choice": a, "value": ev(a)},
            "start_loosest": {"choice": b, "value": ev(b)}, "n_evaluated": len(cache), "evaluations": evals,
            "path": path}


def pareto(evals: list[dict]) -> list[dict]:
    """(오탐 ↓, 검정력 ↑) 비지배 점 — 같은 값이면 변경 수가 적은 것."""
    pts = sorted(evals, key=lambda e: (e["fpr"], -e["power_target"], e["n_changes"]))
    out, best = [], -1.0
    for e in pts:
        if e["power_target"] > best + 1e-12:
            out.append(e)
            best = e["power_target"]
    return out


# ---- DSR 실질 시도 수 공식 비교 (누적 프로그램)
def dsr_formula_table(d: Data, pkg: dict) -> dict:
    """현재 N = max(K, N_family·K/N_campaign) vs 대안 N_alt = Σ(이 family 의 전략 캠페인별 K, 현재 포함).
    캠페인 위치(격자 크기)별 N 중앙값, DSR 통과율(귀무 후보 / 심은 프로그램 후보 / 마지막 캠페인 S* 대표)."""
    rows: dict[tuple, dict] = {}
    for r in d.prog:
        n_family = sum_keff = 0
        for ci, camp in enumerate(r["campaigns"]):
            n_family += camp["n_specs"]
            sum_keff += camp["k_eff"]
            n_cur = max(float(camp["k_eff"]), n_family * camp["k_eff"] / max(camp["n_specs"], 1))
            n_alt = float(max(camp["k_eff"], sum_keff))
            key = (ci + 1, camp["grid"], camp["n_specs"])
            t = rows.setdefault(key, {"n_cur": [], "n_alt": [], "k_eff": [],
                                      "null": [0, 0, 0], "planted": [0, 0, 0], "star": [0, 0, 0]})
            t["n_cur"].append(n_cur)
            t["n_alt"].append(n_alt)
            t["k_eff"].append(camp["k_eff"])
            for c in camp["cands"]:
                p_cur = _phi(dsr_z(c, n_cur)) >= pkg["g5_dsr"]
                p_alt = _phi(dsr_z(c, n_alt)) >= pkg["g5_dsr"]
                for grp in (["null"] if is_null(r) else ["planted"] + (["star"] if c.get("is_star") else [])):
                    t[grp][0] += p_cur
                    t[grp][1] += p_alt
                    t[grp][2] += 1
    out = []
    for (pos, grid, ns), t in sorted(rows.items()):
        out.append({"pos": pos, "grid": grid, "n_specs": ns, "k_eff_median": float(np.median(t["k_eff"])),
                    "n_current_median": float(np.median(t["n_cur"])), "n_alt_median": float(np.median(t["n_alt"])),
                    **{f"{g}_dsr_pass_current": rate(t[g][0], t[g][2]) for g in ("null", "planted", "star")},
                    **{f"{g}_dsr_pass_alt": rate(t[g][1], t[g][2]) for g in ("null", "planted", "star")}})
    cur = program_metrics(d, pkg)
    alt = program_metrics(d, with_changes(pkg, dsr_n="sum_keff"))
    return {"by_position": out, "program_current": cur, "program_alt": alt}


def describe_data(d: Data) -> dict:
    """시장 수, TUNE/HOLD, 심은 스펙의 실현 Sharpe·거래 빈도, 후보 수."""
    out: dict = {"n_fresh_null": len(d.null), "n_fresh_planted": len(d.planted), "n_programs": len(d.prog),
                 "n_universe": len(d.universe), "groups": {}, "planted": {}}
    for r in d.fresh + d.prog:
        g = out["groups"].setdefault(r["group"], {"n": 0, "tune": 0, "hold": 0, "cands": 0, "runtime_sec": 0.0})
        g["n"] += 1
        g[half(r)] += 1
        g["cands"] += sum(len(c["cands"]) for c in r["campaigns"])
        g["runtime_sec"] += r.get("runtime_sec") or 0.0
    cells: dict[str, dict] = {}
    for r in d.planted:
        t = cells.setdefault(f"{r['scenario']}@{float(r['strength']):g}", {"all": [], "dev": [], "tpd": [], "k": r["k"],
                                                                           "sel": 0, "n": 0})
        t["n"] += 1
        t["sel"] += bool(r["campaigns"][0].get("star_selected"))
        for key, src in (("all", "star_real_sharpe_all"), ("dev", "star_real_sharpe_dev"),
                         ("tpd", "star_real_trades_per_day")):
            if r.get(src) is not None:
                t[key].append(r[src])
    for k, t in sorted(cells.items()):
        out["planted"][k] = {"n": t["n"], "k": t["k"], "star_selected": t["sel"],
                             "real_sharpe_all_mean": float(np.mean(t["all"])) if t["all"] else None,
                             "real_sharpe_all_sd": float(np.std(t["all"])) if t["all"] else None,
                             "real_sharpe_dev_mean": float(np.mean(t["dev"])) if t["dev"] else None,
                             "trades_per_day": float(np.mean(t["tpd"])) if t["tpd"] else None}
    return out


# 낙폭 한도(class ③: G6 Val 낙폭·MC 낙폭95) 설정 — 탐색의 '조건'. 20% = 현재.
C3_SETTINGS: dict[str, dict] = {"20%": {}, "30%": {"g6_mdd": 0.30, "g6_mc": 0.30}, "40%": {"g6_mdd": 0.40, "g6_mc": 0.40},
                                "끔": {"g6_mdd": None, "g6_mc": None}}


def analyze(records: list[dict], fpr_targets=(0.05, 0.10, 0.20), progress=print, search: bool = True) -> dict:
    """4단계 전체. 반환 dict 는 작다(원시 레코드 없음). 패키지는 TUNE(짝수 seed)에서 고르고 HOLD-OUT(홀수 seed)·전체에서 보고."""
    cur = current_package()
    d = Data(records)
    tune, hold = d.subset("tune"), d.subset("hold")
    res: dict = {"created": pd.Timestamp.now("UTC").isoformat(), "current_package": cur, "data": describe_data(d),
                 "gate_config_version": G.load_gate_config().get("version")}
    progress("[analyze] consistency")
    res["consistency"] = consistency(records, cur)
    progress("[analyze] current package")
    res["current"] = {w: package_metrics(x, cur) for w, x in (("all", d), ("tune", tune), ("hold", hold))}
    res["current"]["first_failed"] = first_failed_tables(d, cur)
    res["current"]["programs"] = program_metrics(d, cur)
    progress("[analyze] gate contribution")
    res["contribution"] = gate_contribution(d, cur)
    c3off = with_changes(cur, g6_mdd=None, g6_mc=None)
    res["contribution_c3off"] = gate_contribution(d, c3off)
    progress("[analyze] sweeps")
    res["sweeps_c3off"] = run_sweeps(d, c3off)
    res["sweeps"] = run_sweeps(d, cur)
    res["sweeps_class3"] = run_sweeps(d, cur, CLASS3_SWEEPS)
    res["dsr_formula"] = dsr_formula_table(d, cur)
    packages: dict[str, dict] = {"current": {"changes": {}, "n_changes": 0}}
    for lab, ch in C3_SETTINGS.items():
        if ch:
            packages[f"current@{lab}"] = {"changes": dict(ch), "n_changes": 0, "class3": lab}
    if search:
        # (1) 훑기 범위 안에서의 최선 (낙폭 한도 = 현재; 오탐 제약 5% — 이 범위에서는 보통 묶이지 않는다)
        progress("[analyze] package search (TUNE), sweep-range space")
        s0 = package_search(tune, cur, min(fpr_targets), progress=progress)
        ev0 = s0.pop("evaluations")
        s0.pop("path", None)
        packages["sweep_range_best"] = s0
        # (2) 확장 공간('끔' 포함): 낙폭 한도(class ③) 설정별 × 오탐 목표별. class ③ 은 사람의 위험 선호이므로 탐색
        # 대상이 아니라 '조건'이다 — 현재(20%)가 본 결과, 나머지는 정보.
        names = {0.05: "strict_optimised", 0.10: "moderate", 0.20: "aggressive"}
        curve = []
        for e in pareto(ev0):
            pkg = _choice_pkg(cur, e["choice"], SEARCH_OPTIONS)
            mh, ma = package_metrics(hold, pkg, cells=False), package_metrics(d, pkg, cells=False)
            curve.append({"space": "sweep", "class3": "20%", "changes": _choice_changes(e["choice"], SEARCH_OPTIONS),
                          "n_changes": e["n_changes"], "tune_fpr": e["fpr"], "tune_power": e["power_target"],
                          "hold_fpr": mh["fpr"], "hold_power": mh["power_target"], "all_fpr": ma["fpr"],
                          "all_power": ma["power_target"]})
        for lab, ch in C3_SETTINGS.items():
            base = with_changes(cur, **ch)
            cache: dict = {}
            evals_ext = []
            for t in fpr_targets:
                progress(f"[analyze] package search (TUNE), extended space, class3={lab}, FPR ≤ {t:g}")
                sx = package_search(tune, base, t, progress=None, options=SEARCH_OPTIONS_EXT, cache=cache)
                evals_ext = sx.pop("evaluations")
                pth = sx.pop("path")
                if t == max(fpr_targets):       # 단계별 경로 (한 번에 한두 키씩 — 가장 값진 변경부터)
                    steps = []
                    for st in pth:
                        pkg = _choice_pkg(base, st["choice"], SEARCH_OPTIONS_EXT)
                        mh, ma = package_metrics(hold, pkg, cells=False), package_metrics(d, pkg, cells=True)
                        steps.append({"added": _choice_changes(st["step_choice"], SEARCH_OPTIONS_EXT),
                                      "tune_fpr": st["tune_fpr"], "tune_power": st["tune_power"],
                                      "hold_fpr": mh["fpr"], "hold_fpr_hard": mh["fpr_hard"],
                                      "hold_power": mh["power_target_pooled"], "all_fpr": ma["fpr"],
                                      "all_fpr_hard": ma["fpr_hard"], "all_power": ma["power_target_pooled"],
                                      **{f"all_power_s{s_:g}": ma[f"power_s{s_:g}"] for s_ in STRENGTHS}})
                    res.setdefault("greedy_paths", {})[lab] = steps
                sx["changes"] = {**ch, **sx["changes"]}
                sx["class3"] = lab
                packages[names.get(t, f"fpr{t:g}") + ("" if not ch else f"@{lab}")] = sx
            for e in pareto(evals_ext):
                pkg = _choice_pkg(base, e["choice"], SEARCH_OPTIONS_EXT)
                mh, ma = package_metrics(hold, pkg, cells=False), package_metrics(d, pkg, cells=False)
                curve.append({"space": "extended", "class3": lab,
                              "changes": _choice_changes(e["choice"], SEARCH_OPTIONS_EXT), "n_changes": e["n_changes"],
                              "tune_fpr": e["fpr"], "tune_power": e["power_target"], "hold_fpr": mh["fpr"],
                              "hold_power": mh["power_target"], "all_fpr": ma["fpr"], "all_power": ma["power_target"]})
        res["tradeoff_curve"] = curve
    for name, p_ in packages.items():
        pkg = with_changes(cur, **p_["changes"])
        p_["metrics"] = {w: package_metrics(x, pkg) for w, x in (("all", d), ("tune", tune), ("hold", hold))}
        p_["programs"] = program_metrics(d, pkg)
        p_["class3_info"] = ({} if p_.get("class3", "20%") != "20%" else
                             {lab: {k: v for k, v in package_metrics(d, with_changes(pkg, **ch), cells=True).items()
                                    if k in ("fpr", "fpr_bear", "power_target", "power_s1", "power_s1.5", "power_s2",
                                             "power_s3")}
                              for _, lab, ch in CLASS3_SWEEPS[8:]})
        p_["first_failed"] = {"star": first_failed_tables(d, pkg)["star"]}
        for k_ in ("start_ascend", "start_loosest", "choice"):
            p_.pop(k_, None)
    res["packages"] = packages
    progress("[analyze] stage rates")
    dev_pkgs = {"현재 Dev 관문": cur}
    for name, p_ in packages.items():
        if name in ("sweep_range_best", "strict_optimised", "aggressive@끔"):
            dev_pkgs[f"{PKG_KO.get(name, name)} 의 Dev 관문"] = with_changes(cur, **p_["changes"])
    res["stage_rates"] = stage_rates(d, dev_pkgs, cur)
    if d.universe:
        du = Data([dict(r, scenario="5m") for r in d.universe])
        res["universe"] = {name: {"null_fpr": rate(sum(evaluate_market(r, with_changes(cur, **p_["changes"]), False)["any_validated"]
                                                       for r in du.null), len(du.null)),
                                  "planted_star": rate(sum(evaluate_market(r, with_changes(cur, **p_["changes"]), False)["star_validated"]
                                                           for r in du.planted), len(du.planted))}
                           for name, p_ in packages.items()}
    return res


KEY_PACKAGES = ("current", "sweep_range_best", "strict_optimised", "moderate", "aggressive", "current@끔",
                "strict_optimised@끔", "moderate@끔", "aggressive@끔")


def compact_summary(res: dict) -> dict:
    """요약 JSON 을 작게: 실수 4자리 반올림, 핵심 패키지 밖은 오탐·검정력 요약만, 큰 부속 표 제거."""
    def rnd(o):
        if isinstance(o, float):
            return round(o, 4)
        if isinstance(o, dict):
            if set(o) == {"x", "n", "p", "lo", "hi"}:      # 비율 = [x, n] (신뢰구간은 Wilson 으로 다시 계산)
                return [o["x"], o["n"]]
            return {k: rnd(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [rnd(v) for v in o]
        return o

    def slim_metrics(m: dict, cells: bool) -> dict:
        keep = ("fpr", "fpr_hard", "fpr_bear", "power_target", "power_target_pooled", "power_s1", "power_s1.5",
                "power_s2", "power_s3") + (("fpr_by_group", "power") if cells else ())
        return {k: m[k] for k in keep if k in m}

    out = copy.deepcopy(res)
    for name, p_ in (out.get("packages") or {}).items():
        key = name in KEY_PACKAGES
        p_["metrics"] = {w: slim_metrics(m, key and w != "tune") for w, m in p_["metrics"].items()}
        if not key:
            p_.pop("first_failed", None)
            p_.pop("class3_info", None)
        p_.pop("n_evaluated", None)
    for w in ("tune", "hold"):
        if w in out.get("current", {}):
            out["current"][w] = slim_metrics(out["current"][w], True)
    for key in ("contribution", "contribution_c3off"):
        g = out.get(key)
        if g:
            g["redundancy_null"] = {a: {b: (None if v is None else round(v, 2)) for b, v in row.items()}
                                    for a, row in g["redundancy_null"].items()}
    for key in ("sweeps", "sweeps_class3", "sweeps_c3off"):
        for r in out.get(key) or []:
            r.pop("changes", None)
    out.pop("sweeps_c3off", None)
    out["tradeoff_curve"] = [e for e in out.get("tradeoff_curve") or [] if e.get("class3") in ("20%", "끔")]
    out["stage_rates"] = {g: {d_: {"n": row["n"], "dev_survivors": row["dev_survivors"], "g6_checks": row["g6_checks"],
                                   "robustness": row["robustness"],
                                   "val": {lab: v["evidence_and_sharpe"] for lab, v in row["val"].items()},
                                   "val_g6_and_robustness": {lab: v["g6_and_robustness"] for lab, v in row["val"].items()}}
                              for d_, row in by.items()} for g, by in (out.get("stage_rates") or {}).items()}
    out["rate_format"] = "[x, n] = x of n markets/candidates (Wilson 95% CI: calibration_study.wilson)"
    return rnd(out)


# ============================================================== 표 (마크다운)
ATOM_KO = {"G1": "G1 표본(거래 수)", "G2": "G2 무작위 진입 귀무", "G3": "G3 이웃 고원", "G4_roll": "G4 롤링 양수 비율",
           "G4_recent": "G4 최근 12개월", "G5_dsr": "G5 DSR", "G5_pbo": "G5 PBO", "G6_sharpe": "G6 Val Sharpe>0",
           "G6_mdd": "G6 Val 낙폭 ≤20% (③)", "G6_stress": "G6 stress Sharpe>0", "G6_mc": "G6 MC 낙폭95 ≤20% (③)",
           "G6_holm": "G6 Val Holm", "R_latency": "견고성 지연", "R_outliers": "견고성 상위 거래 제거",
           "R_loyo": "견고성 연도 제외", "R_cost": "견고성 비용×1.5", "R_regime": "견고성 국면 비중",
           "R_alpha": "견고성 알파 t"}
PKG_KO = {"current": "현재", "sweep_range_best": "훑기 범위 최선", "strict_optimised": "엄격-최적 (오탐 ≤5%)",
          "moderate": "중도 (오탐 ≤10%)", "aggressive": "적극 (오탐 ≤20%)"}
for _lab in ("30%", "40%", "끔"):
    PKG_KO[f"current@{_lab}"] = f"현재 + 낙폭 한도 {_lab}"
    for _n, _k in (("strict_optimised", "엄격-최적 (오탐 ≤5%)"), ("moderate", "중도 (오탐 ≤10%)"), ("aggressive", "적극 (오탐 ≤20%)")):
        PKG_KO[f"{_n}@{_lab}"] = f"{_k} · 낙폭 한도 {_lab}"
KEY_KO = {"rob_cost_mult": "견고성 비용 배수", "rob_outliers": "견고성 상위 거래 제거", "rob_loyo": "견고성 연도 제외",
          "g1_min_trades": "G1 최소 거래", "g2_frac": "G2 귀무 상위 비율", "g3_ratio": "G3 이웃 비", "g4_roll": "G4 롤링",
          "g4_recent": "G4 최근 Sharpe 하한", "g5_dsr": "G5 DSR", "g5_pbo": "G5 PBO", "g6_stress": "G6 stress Sharpe 하한",
          "val_mode": "Val 증거 방식", "val_alpha": "Val α/p 문턱", "rob_alpha_t": "견고성 알파 t",
          "rob_latency": "견고성 지연 유지율", "rob_regime": "견고성 국면 비중", "g6_mdd": "G6 낙폭 상한",
          "g6_mc": "G6 MC 낙폭 상한", "dsr_n": "DSR 시도 수 공식"}


def _tbl(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


def _pc(x, nd: int = 0) -> str:
    return "—" if x is None else f"{100 * x:.{nd}f}%"


def _chg(ch: dict, cur: dict) -> str:
    if not ch:
        return "(변경 없음)"
    return "; ".join(f"{KEY_KO.get(k, k)} {cur.get(k)} → {'끔' if v is None else v}" for k, v in ch.items())


def tables_markdown(res: dict) -> dict[str, str]:
    """analyze() 결과 → 절별 마크다운 표 {이름: 표}."""
    T: dict[str, str] = {}
    cur = res["current_package"]
    dd = res["data"]
    T["data_groups"] = _tbl(["그룹", "시장(프로그램) 수", "TUNE", "HOLD-OUT", "후보 수", "실행 시간 합(분)"],
                            [[g, v["n"], v["tune"], v["hold"], v["cands"], f"{v['runtime_sec'] / 60:.0f}"]
                             for g, v in sorted(dd["groups"].items())])
    T["data_planted"] = _tbl(["시나리오@목표 Sharpe", "시장", "k", "S* 실현 Sharpe 전 구간(평균 ± sd)", "S* Dev Sharpe 평균",
                              "거래/일", "S*(대표)가 후보로 뽑힘"],
                             [[k, v["n"], f"{v['k']:.4f}", "—" if v["real_sharpe_all_mean"] is None else
                               f"{v['real_sharpe_all_mean']:.2f} ± {v['real_sharpe_all_sd']:.2f}",
                               "—" if v["real_sharpe_dev_mean"] is None else f"{v['real_sharpe_dev_mean']:.2f}",
                               "—" if v["trades_per_day"] is None else f"{v['trades_per_day']:.2f}",
                               f"{v['star_selected']}/{v['n']}"] for k, v in dd["planted"].items()])
    c = res["consistency"]
    T["consistency"] = (f"시장(프로그램) {c['markets_matching']}/{c['n_markets']} 일치, 캠페인 {c['n_campaigns']}개, "
                        f"후보 {c['candidates_matching']}/{c['n_candidates']} 일치. 그룹별 점검 시장 수: {c['by_group']}. "
                        f"불일치: {c['mismatches'] or '없음'}")
    ca = res["current"]["all"]
    T["current_fpr"] = _tbl(["귀무 종류", "오탐(시장 단위) 전체", "TUNE", "HOLD-OUT"],
                            [[NULL_LABEL.get(g, g), fmt_rate(r), fmt_rate(res["current"]["tune"]["fpr_by_group"].get(g), False),
                              fmt_rate(res["current"]["hold"]["fpr_by_group"].get(g), False)]
                             for g, r in ca["fpr_by_group"].items()]
                            + [["**합계**", fmt_rate(ca["fpr"]), fmt_rate(res["current"]["tune"]["fpr"], False),
                                fmt_rate(res["current"]["hold"]["fpr"], False)],
                               ["그중 하락 Val (국면 귀무)", fmt_rate(ca["fpr_bear"]),
                                fmt_rate(res["current"]["tune"]["fpr_bear"], False),
                                fmt_rate(res["current"]["hold"]["fpr_bear"], False)]])

    def power_grid(m: dict, key: str = "power") -> str:
        scs = [sc for sc in (*SCENARIOS, "regime") if any(k.startswith(sc + "@") for k in m[key])]
        return _tbl(["시나리오", *[f"참 Sharpe {s:g}" for s in STRENGTHS]],
                    [[SCEN_LABEL.get(sc, sc), *[fmt_rate(m[key].get(f"{sc}@{s:g}")) for s in STRENGTHS]] for sc in scs]
                    + [["4개 회전율 합계", *[fmt_rate(m.get(f"power_s{s:g}")) for s in STRENGTHS]]])
    T["current_power"] = power_grid(ca)
    T["current_power_any"] = power_grid(ca, "power_any")
    ff = res["current"]["first_failed"]
    T["current_first_failed_null"] = _tbl(["첫 탈락(귀무 후보)", "후보 수"], [[k, v] for k, v in ff["null_candidates"].items()])
    T["current_first_failed_star"] = _tbl(["시나리오@Sharpe", "S* 대표의 결과 (개수)"],
                                          [[k, ", ".join(f"{a}:{b}" for a, b in v.items())] for k, v in ff["star"].items()])
    for suffix, g, ca_ in (("", res["contribution"], ca), ("_c3off", res.get("contribution_c3off"), None)):
        if g is None:
            continue
        _contribution_tables(T, g, ca_, suffix)

    def sweep_tbl(rows: list[dict]) -> str:
        return _tbl(["기준", "값", "오탐", "어려운 귀무 오탐", "하락 Val 오탐", "검정력(1.5–2 칸 평균)", "S1", "S1.5", "S2", "S3",
                     *[f"{SCEN_LABEL[sc].split(' ')[0]} 1.5–2" for sc in SCENARIOS]],
                    [[r["gate"], r["value"] + (" **(현재)**" if r["is_current"] else ""), fmt_rate(r["fpr"], False),
                      fmt_rate(r.get("fpr_hard"), False), fmt_rate(r["fpr_bear"], False), _pc(r["power_target"]),
                      *[fmt_rate(r[f"power_s{s:g}"], False) for s in STRENGTHS],
                      *[fmt_rate(r["power_by_scenario_target"][sc], False) for sc in SCENARIOS]] for r in rows])
    T["sweeps"] = sweep_tbl(res["sweeps"])
    T["sweeps_class3"] = sweep_tbl(res["sweeps_class3"])
    if res.get("sweeps_c3off"):
        T["sweeps_c3off"] = sweep_tbl(res["sweeps_c3off"])
    _rest_tables(T, res, cur, power_grid)
    return T


def _contribution_tables(T: dict, g: dict, ca: dict | None, suffix: str) -> None:
    T["logo" + suffix] = _tbl(["뺀 관문", "오탐", "Δ오탐", "어려운 귀무 오탐", "검정력(1.5–2 칸 평균)", "Δ검정력", "S1", "S1.5", "S2", "S3"],
                     [["(기준 — 아무것도 안 뺌)", fmt_rate(g["base"]["fpr"], False), "", fmt_rate(g["base"].get("fpr_hard"), False),
                       _pc(g["base"]["power_target"]), "",
                       *[(fmt_rate(ca[f"power_s{s:g}"], False) if ca else "") for s in STRENGTHS]]]
                     + [[ATOM_KO[a], fmt_rate(v["fpr"], False), f"{100 * v['d_fpr']:+.1f}%p", fmt_rate(v.get("fpr_hard"), False),
                         _pc(v["power_target"]),
                         f"{100 * v['d_power_target']:+.1f}%p", *[fmt_rate(v[f"power_s{s:g}"], False) for s in STRENGTHS]]
                        for a, v in g["leave_one_out"].items()])
    T["only" + suffix] = _tbl(["관문", "이 관문만 켰을 때 오탐(시장)", "이 관문만: 어려운 귀무 오탐", "귀무 후보를 막는 비율",
                      "참 엣지(S*≥1.5) 대표를 막는 비율", "귀무 후보를 혼자 막음(건)", "참 엣지를 혼자 막음(건)"],
                     [[ATOM_KO[a], fmt_rate(g["only"][a]["fpr_market"], False), fmt_rate(g["only"][a].get("fpr_hard"), False),
                       fmt_rate(g["stop_null_candidates"][a], False),
                       fmt_rate(g["stop_true_edge_star"][a], False), g["sole_stopper_null"][a], g["sole_stopper_true_edge"][a]]
                      for a in ATOMS])
    T["n_failed" + suffix] = (f"귀무 후보 {g['n_null_candidates']}개가 실패한 원자 관문 수 분포: {g['n_failed_atoms_null']}. "
                     f"참 엣지(S* ≥ 1.5) 대표 {g['n_true_edge_star']}개: {g['n_failed_atoms_true_edge']}.")
    short = {a: a.replace("G6_", "V_") for a in ATOMS}
    T["redundancy" + suffix] = _tbl(["A가 막은 귀무 후보 중 B도 막은 비율 → B", *[short[b] for b in ATOMS]],
                                    [[short[a], *["·" if a == b else ("—" if g["redundancy_null"][a][b] is None
                                                                      else f"{100 * g['redundancy_null'][a][b]:.0f}")
                                                  for b in ATOMS]] for a in ATOMS])


def _rest_tables(T: dict, res: dict, cur: dict, power_grid) -> None:
    # 단계별 통과율
    sr = res["stage_rates"]
    vlabs = [lab for lab, _ in VAL_VARIANTS]
    rows = []
    for gname, by_dev in sr.items():
        for dname, row in by_dev.items():
            rows.append([gname, dname, fmt_rate(row["dev_survivors"], False), fmt_rate(row["g6_checks"], False),
                         fmt_rate(row["robustness"], False),
                         *[fmt_rate(row["val"][lab]["evidence_and_sharpe"], False) for lab in vlabs]])
    T["stage_val"] = _tbl(["후보 집단", "Dev 관문", "Dev 생존", "생존자 중 G6 점검 4종 통과", "생존자 중 견고성 통과",
                           *[f"증거: {lab}" for lab in vlabs]], rows)
    rows = []
    for gname, by_dev in sr.items():
        for dname, row in by_dev.items():
            rows.append([gname, dname, row["dev_survivors"]["x"],
                         *[fmt_rate(row["val"][lab]["g6_and_robustness"], False) for lab in vlabs]])
    T["stage_full"] = _tbl(["후보 집단", "Dev 관문", "Dev 생존자 수", *[f"G6 전체+견고성: {lab}" for lab in vlabs]], rows)
    # 패키지
    pk = res["packages"]
    T["packages"] = _tbl(["패키지", "TUNE 오탐 (합산 / 어려운 귀무)", "TUNE 검정력", "HOLD-OUT 오탐", "HOLD-OUT 어려운 귀무 오탐",
                          "HOLD-OUT 검정력", "전체 오탐", "전체 어려운 귀무 오탐", "전체 하락 Val 오탐", "전체 검정력(1.5–2)"],
                         [[PKG_KO.get(n, n), fmt_rate(v["metrics"]["tune"]["fpr"], False) + " / "
                           + fmt_rate(v["metrics"]["tune"]["fpr_hard"], False),
                           _pc(v["metrics"]["tune"]["power_target"]), fmt_rate(v["metrics"]["hold"]["fpr"]),
                           fmt_rate(v["metrics"]["hold"]["fpr_hard"]),
                           fmt_rate(v["metrics"]["hold"]["power_target_pooled"]),
                           fmt_rate(v["metrics"]["all"]["fpr"]), fmt_rate(v["metrics"]["all"]["fpr_hard"]),
                           fmt_rate(v["metrics"]["all"]["fpr_bear"], False),
                           fmt_rate(v["metrics"]["all"]["power_target_pooled"])]
                          for n, v in pk.items()])
    T["packages_changes"] = _tbl(["패키지", "변경 수", "현재에서 바꾼 것"],
                                 [[PKG_KO.get(n, n), len(v["changes"]), _chg(v["changes"], cur)] for n, v in pk.items()])
    for n, v in pk.items():
        T[f"package_power_{n}"] = power_grid(v["metrics"]["all"])
        T[f"package_power_hold_{n}"] = power_grid(v["metrics"]["hold"])
        T[f"package_fpr_{n}"] = _tbl(["귀무 종류", "전체", "HOLD-OUT"],
                                     [[NULL_LABEL.get(gk, gk), fmt_rate(r), fmt_rate(v["metrics"]["hold"]["fpr_by_group"].get(gk))]
                                      for gk, r in v["metrics"]["all"]["fpr_by_group"].items()])
        T[f"package_star_{n}"] = _tbl(["시나리오@Sharpe", "S* 대표의 결과 (개수)"],
                                      [[k, ", ".join(f"{a}:{b}" for a, b in x.items())]
                                       for k, x in v["first_failed"]["star"].items()])
    T["packages_programs"] = _tbl(["패키지", "귀무 프로그램 오탐(8 캠페인 중 하나라도)", "심은 S2 프로그램: 마지막 캠페인 S*",
                                   "심은 S2 프로그램: 어느 후보든", "Val 사용 수 평균(귀무/심은)"],
                                  [[PKG_KO.get(n, n), fmt_rate(v["programs"]["null_program_fpr"]),
                                    fmt_rate(v["programs"]["planted_last_star"]), fmt_rate(v["programs"]["planted_program_any"]),
                                    f"{v['programs']['val_used_null_mean'] or 0:.1f} / {v['programs']['val_used_planted_mean'] or 0:.1f}"]
                                   for n, v in pk.items()])
    rows = []
    for n, v in pk.items():
        if not v.get("class3_info"):
            continue
        rows.append([PKG_KO.get(n, n), "낙폭 한도 그대로(20%)", fmt_rate(v["metrics"]["all"]["fpr"], False),
                     _pc(v["metrics"]["all"]["power_target"]),
                     *[fmt_rate(v["metrics"]["all"][f"power_s{s:g}"], False) for s in STRENGTHS]])
        for lab, m in v["class3_info"].items():
            rows.append([PKG_KO.get(n, n), f"Val 낙폭·MC 낙폭 둘 다 {lab}", fmt_rate(m["fpr"], False), _pc(m["power_target"]),
                         *[fmt_rate(m[f"power_s{s:g}"], False) for s in STRENGTHS]])
    T["packages_class3"] = _tbl(["패키지", "낙폭 한도(③, 정보)", "오탐", "검정력(1.5–2 칸 평균)", "S1", "S1.5", "S2", "S3"], rows)
    if res.get("tradeoff_curve"):
        T["tradeoff"] = _tbl(["탐색 공간", "낙폭 한도(③)", "변경 수", "바꾼 것", "TUNE 오탐", "TUNE 검정력", "HOLD-OUT 오탐", "HOLD-OUT 검정력",
                              "전체 오탐", "전체 검정력"],
                             [[e["space"], e.get("class3", ""), e["n_changes"], _chg(e["changes"], cur), _pc(e["tune_fpr"], 1), _pc(e["tune_power"]),
                               fmt_rate(e["hold_fpr"], False), _pc(e["hold_power"]), fmt_rate(e["all_fpr"], False),
                               _pc(e["all_power"])] for e in res["tradeoff_curve"]])
    for lab, steps in (res.get("greedy_paths") or {}).items():
        T[f"greedy_path_{lab}"] = _tbl(
            ["단계", "이 단계에서 추가로 바꾼 것", "TUNE 오탐(합산·어려운 귀무 중 큰 쪽)", "TUNE 검정력", "HOLD-OUT 오탐",
             "HOLD-OUT 어려운 귀무", "HOLD-OUT 검정력(1.5–2)", "전체 오탐", "전체 어려운 귀무", "전체 검정력(1.5–2)", "S1", "S1.5",
             "S2", "S3"],
            [[i, _chg(st["added"], with_changes(cur, **C3_SETTINGS[lab])) if st["added"] else f"(출발: 현재, 낙폭 한도 {lab})",
              _pc(st["tune_fpr"], 1), _pc(st["tune_power"]), fmt_rate(st["hold_fpr"], False),
              fmt_rate(st["hold_fpr_hard"], False), fmt_rate(st["hold_power"], False), fmt_rate(st["all_fpr"], False),
              fmt_rate(st["all_fpr_hard"], False), fmt_rate(st["all_power"], False),
              *[fmt_rate(st[f"all_power_s{s_:g}"], False) for s_ in STRENGTHS]] for i, st in enumerate(steps)])
    df = res["dsr_formula"]
    T["dsr_formula"] = _tbl(["캠페인 순서", "격자(스펙 수)", "K 중앙값", "현재 N 중앙값", "대안 N(ΣK) 중앙값",
                             "귀무 후보 DSR 통과: 현재", "대안", "심은 프로그램 후보 DSR 통과: 현재", "대안",
                             "마지막 S* 대표: 현재", "대안"],
                            [[r["pos"], f"{r['grid']} ({r['n_specs']})", f"{r['k_eff_median']:g}", f"{r['n_current_median']:.2f}",
                              f"{r['n_alt_median']:.2f}", fmt_rate(r["null_dsr_pass_current"], False),
                              fmt_rate(r["null_dsr_pass_alt"], False), fmt_rate(r["planted_dsr_pass_current"], False),
                              fmt_rate(r["planted_dsr_pass_alt"], False), fmt_rate(r["star_dsr_pass_current"], False),
                              fmt_rate(r["star_dsr_pass_alt"], False)] for r in df["by_position"]])
    T["dsr_formula_programs"] = _tbl(["DSR 시도 수 공식", "귀무 프로그램 오탐", "심은 프로그램 마지막 S*", "심은 프로그램 어느 후보든"],
                                     [[lab, fmt_rate(m["null_program_fpr"]), fmt_rate(m["planted_last_star"]),
                                       fmt_rate(m["planted_program_any"])]
                                      for lab, m in (("현재 N = max(K, N_family·K/N_campaign)", df["program_current"]),
                                                     ("대안 N = Σ K", df["program_alt"]))])


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
    if a.universe or (raw / "_edge_universe.json").exists():
        pool2 = ProcessPoolExecutor(max_workers=a.jobs, mp_context=get_context("spawn")) if a.jobs > 1 else None
        try:
            edges["universe"] = universe_edge_map(raw, pool2, progress=lambda s: print(s, flush=True))
        finally:
            if pool2:
                pool2.shutdown()
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


def _cmd_consistency(a) -> int:
    """일치 점검 전용: 관문 설정 파일(--gate-config)로 실제 run_pipeline 을 돌린 시장(actual)들만 수집하고,
    같은 설정의 오프라인 패키지와 후보 단위로 비교한다. 엣지 지도는 --edges 디렉터리의 _edge_*.json 을 재사용."""
    import shutil as _sh
    raw = Path(a.raw)
    raw.mkdir(parents=True, exist_ok=True)
    if a.edges:
        for fp in Path(a.edges).glob("_edge_*.json"):
            if not (raw / fp.name).exists():
                _sh.copy(fp, raw / fp.name)
    cfg_path = str(Path(a.gate_config).resolve())
    cfg = G.load_gate_config(cfg_path)
    edges = edge_maps(raw, None, progress=lambda s: print(s, flush=True))
    if (raw / "_edge_universe.json").exists():
        edges["universe"] = universe_edge_map(raw, None, progress=lambda s: print(s, flush=True))
    tasks = [t for t in build_tasks(edges, scale=1.0, n_null=a.n_null) if t["actual"]]
    for t in tasks:
        t["gate_config"] = cfg_path
        t["reference_check"] = not a.no_reference
        if a.tmp:
            t["tmp_dir"] = a.tmp
    if a.tmp:
        Path(a.tmp).mkdir(parents=True, exist_ok=True)
    res = run_tasks(tasks, raw, a.jobs, progress=lambda s: print(s, flush=True))
    print(f"[run] finished: {res}", flush=True)
    recs = load_records(raw)
    cons = consistency(recs, current_package(cfg))
    _write_json(Path(a.out), {"gate_config": cfg_path, "package": current_package(cfg), "consistency": cons,
                              "n_records": len(recs)})
    print(json.dumps({k: v for k, v in cons.items() if k != "mismatches"}, ensure_ascii=False), flush=True)
    for m in (cons.get("mismatches") or [])[:20]:
        print("MISMATCH", m, flush=True)
    return 0 if not cons.get("mismatches") else 1


def _cmd_analyze(a) -> int:
    recs = load_records(a.raw)
    print(f"[analyze] {len(recs)} records from {a.raw}", flush=True)
    res = analyze(recs, progress=lambda s: print(s, flush=True), search=not a.no_search)
    _write_json(Path(a.out), compact_summary(res))
    if a.full:
        _write_json(Path(a.full), res)
    if a.tables:
        T = tables_markdown(res)
        Path(a.tables).write_text("\n\n".join(f"### {k}\n\n{v}" for k, v in T.items()), encoding="utf-8")
    print(f"[analyze] wrote {a.out}", flush=True)
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
    r.add_argument("--universe", action="store_true", help="3자산 유니버스 파일럿 (d 그룹에 필요)")
    r.set_defaults(fn=_cmd_run)
    cn = sub.add_parser("consistency", help="관문 설정 파일로 실제 파이프라인 vs 오프라인 평가기 일치 점검")
    cn.add_argument("--raw", required=True)
    cn.add_argument("--gate-config", required=True)
    cn.add_argument("--edges", default="", help="_edge_*.json 을 재사용할 이전 raw 디렉터리")
    cn.add_argument("--out", required=True)
    cn.add_argument("--jobs", type=int, default=4)
    cn.add_argument("--n-null", type=int, default=N_NULL)
    cn.add_argument("--tmp", default="")
    cn.add_argument("--no-reference", action="store_true")
    cn.set_defaults(fn=_cmd_consistency)
    an = sub.add_parser("analyze", help="원시 JSON → 요약 JSON + 표")
    an.add_argument("--raw", required=True)
    an.add_argument("--out", required=True)
    an.add_argument("--tables", default="")
    an.add_argument("--no-search", action="store_true")
    an.add_argument("--full", default="", help="압축하지 않은 전체 결과 JSON (선택)")
    an.set_defaults(fn=_cmd_analyze)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
