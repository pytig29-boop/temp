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
CUM_SEQUENCE8 = ("c1", "c2", "c3", "c4", "c6", "c7", "c9", "g12")      # 캠페인 8개 (마지막 g12 가 S* 포함)
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
            d = C.template_for(grid, f"calib_{grid}", fam)
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
