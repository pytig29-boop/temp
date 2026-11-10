"""관문 G1~G7 판정과 §7 리스크 한도 공식. 입력은 이미 계산된 지표(dict)뿐.

모든 check_* 는 (passed: bool, details: dict) 를 반환. 수치는 config/gates.yaml.
지표 dict 키는 metrics.summarize() 출력과 같다. 낙폭은 양수 크기.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import inference

GATES_PATH = Path(__file__).resolve().parents[3] / "config" / "gates.yaml"


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_gate_config(path: str | Path | None = None) -> dict:
    return _load(str(path or GATES_PATH))


def _cfg(cfg, gate):
    return (cfg or load_gate_config())[gate]


def check_g1(metrics: dict, cfg: dict | None = None) -> tuple[bool, dict]:
    """G1 표본: 거래 ≥ 300, Dev 최근 12개월 거래 ≥ 60.
    거래 = 독립 포지션(`n_positions`/`recent_n_positions` 가 있으면 — 분할 매수 사다리는 사다리 하나가 1 거래; 같은
    사다리의 분할들은 한 결정의 상관된 조각이라 표본을 부풀리지 않는다). 기본 전략은 n_positions == n_trades (불변)."""
    c = _cfg(cfg, "G1")
    n = int(metrics["n_positions"] if metrics.get("n_positions") is not None else metrics["n_trades"])
    nr = int(metrics["recent_n_positions"] if metrics.get("recent_n_positions") is not None
             else metrics["recent_n_trades"])
    d = {"n_trades": n, "recent_n_trades": nr,
         "ok_trades": n >= c["min_trades"], "ok_recent": nr >= c["min_recent_trades"]}
    if metrics.get("n_positions") is not None and int(metrics["n_positions"]) != int(metrics["n_trades"]):
        d["n_tranche_rows"] = int(metrics["n_trades"])
        d["unit"] = "positions (ladders)"
    return d["ok_trades"] and d["ok_recent"], d


def check_g2(metrics: dict, null_sharpes, cfg: dict | None = None) -> tuple[bool, dict]:
    """G2 엣지: 가중 Sharpe > 0 그리고 무작위 진입 귀무(가중 Sharpe 분포) 대비 상위 5%."""
    c = _cfg(cfg, "G2")
    sr = float(metrics["sharpe_w"])
    pos = inference.null_percentile(sr, null_sharpes)
    d = {"sharpe_w": sr, **pos, "ok_positive": sr > c["min_weighted_sharpe"],
         "ok_null": pos["p_value"] <= c["null_top_fraction"],
         "null_runs_expected": c["null_runs"]}
    return d["ok_positive"] and d["ok_null"], d


def check_g3(selected_sharpe: float, neighbor_sharpes, cfg: dict | None = None
             ) -> tuple[bool, dict]:
    """G3 고원: median(인접 조합 Sharpe) ≥ 0.5 × 선택 조합 Sharpe. 선택 Sharpe ≤ 0 또는
    인접 조합이 없으면 실패."""
    c = _cfg(cfg, "G3")
    nb = np.asarray(list(neighbor_sharpes), dtype=float)
    nb = nb[np.isfinite(nb)]
    med = float(np.median(nb)) if nb.size else float("nan")
    need = c["neighbor_median_ratio"] * float(selected_sharpe)
    ok = bool(selected_sharpe > 0 and nb.size > 0 and med >= need)
    return ok, {"selected": float(selected_sharpe), "neighbor_median": med,
                "required": need, "n_neighbors": int(nb.size)}


DAILY_RETURN_MODES = ("realized", "mtm")


def daily_returns_mode(cfg: dict | None = None) -> str:
    """관문용 일간 수익 회계 (gates.yaml `evaluation.daily_returns`, class ②, PLAN D18).
    'realized' = 청산일 귀속 실현 기준(기존), 'mtm' = 보유 분할을 매 UTC 일 종가에 청산 평가(평가 기준).
    항목이 없으면 'realized' (D18 이전 설정·임시 저장소). 캠페인은 등록 시점 설정(`gate_config_for_campaign`)으로
    정하고 매니페스트 `daily_returns` 에 남긴다 — 파이프라인은 매니페스트 값을 따른다(재현성)."""
    c = cfg if cfg is not None else load_gate_config()
    v = str(((c or {}).get("evaluation") or {}).get("daily_returns") or "realized")
    if v not in DAILY_RETURN_MODES:
        raise ValueError(f"evaluation.daily_returns must be one of {DAILY_RETURN_MODES}, got {v!r}")
    return v


def exposure_aware_cfg(cfg: dict | None = None) -> dict:
    """gates.yaml exposure_aware (없으면 꺼짐). PLAN D14 초안 — 기본 OFF."""
    c = (cfg or load_gate_config()).get("exposure_aware") or {}
    return c


def exposure_aware_active(cfg: dict | None, gate: str) -> bool:
    c = exposure_aware_cfg(cfg)
    return bool(c.get("enabled")) and bool((c.get(gate) or {}).get("enabled", True))


def g4_exposure_variant(metrics: dict, cfg: dict | None = None) -> dict:
    """G4 노출 인식 변형 판정(정보·shadow 겸용). 롤링 창 중 exposure_days < neutral_max_exposure 인 '중립' 창을
    분모에서 빼고 양수 비율 ≥ min_rolling_positive_frac. 조건: 비중립 창 ≥ min_active_windows, Dev 전체
    exposure_days ≥ min_dev_exposure (거의 거래하지 않는 전략이 중립 창으로 G4 를 피하지 못하게). 기존 규칙의
    상위 집합: rolling_ok = 기존 rolling 통과 OR 변형 통과. 최근 12개월 Sharpe > 0 은 그대로."""
    c = _cfg(cfg, "G4")
    ea = exposure_aware_cfg(cfg)
    e4 = ea.get("G4") or {}
    thr = float(e4.get("neutral_max_exposure", 0.10))
    base_roll = (float(metrics["rolling_frac_positive"]) >= c["min_rolling_positive_frac"]
                 and int(metrics.get("rolling_n_windows", 0)) > 0)
    ok_recent = float(metrics["recent_sharpe"]) > c["min_recent_sharpe"]
    d: dict = {"neutral_max_exposure": thr, "min_active_windows": int(e4.get("min_active_windows", 0)),
               "min_dev_exposure": float(ea.get("min_dev_exposure", 0.0))}
    have = all(k in metrics and metrics[k] is not None
               for k in ("rolling_n_active", "rolling_frac_positive_active", "exposure_days"))
    m_thr = metrics.get("rolling_neutral_exposure")
    if not have or m_thr is None or abs(float(m_thr) - thr) > 1e-12:
        d.update(applicable=False, reason="exposure metrics missing or computed at another neutral threshold",
                 passed=bool(base_roll and ok_recent))
        return d
    n_act, n_neu = int(metrics["rolling_n_active"]), int(metrics.get("rolling_n_neutral", 0))
    fr = float(metrics["rolling_frac_positive_active"])
    ex = float(metrics["exposure_days"])
    d.update(applicable=True, rolling_n_active=n_act, rolling_n_neutral=n_neu, rolling_frac_positive_active=fr,
             exposure_days=ex, ok_active_windows=n_act >= d["min_active_windows"],
             ok_dev_exposure=ex >= d["min_dev_exposure"], ok_rolling_active=fr >= c["min_rolling_positive_frac"])
    var_roll = d["ok_active_windows"] and d["ok_dev_exposure"] and d["ok_rolling_active"] and n_act > 0
    d["ok_rolling"] = bool(base_roll or var_roll)
    d["via"] = "base" if base_roll else ("neutral_windows" if var_roll else None)
    d["passed"] = bool(d["ok_rolling"] and ok_recent)
    return d


def check_g4(metrics: dict, cfg: dict | None = None) -> tuple[bool, dict]:
    """G4 안정: 6개월 롤링 창 ≥ 60% 양수, 최근 12개월 Sharpe > 0.
    ("고정 파라미터 그대로" 는 절차 요건 — 호출 측이 동일 스펙임을 보장.)
    exposure_aware.enabled(기본 false) 이면 `g4_exposure_variant` 로 판정한다. 꺼져 있으면 변형 결과는
    details["exposure_aware"] 에 정보(shadow)로만 남는다."""
    c = _cfg(cfg, "G4")
    fr, rs = float(metrics["rolling_frac_positive"]), float(metrics["recent_sharpe"])
    d = {"rolling_frac_positive": fr, "rolling_n_windows": int(metrics.get("rolling_n_windows", 0)),
         "recent_sharpe": rs, "ok_rolling": fr >= c["min_rolling_positive_frac"],
         "ok_recent": rs > c["min_recent_sharpe"]}
    d["ok_rolling"] = d["ok_rolling"] and d["rolling_n_windows"] > 0
    ok = d["ok_rolling"] and d["ok_recent"]
    if "exposure_aware" in (cfg or load_gate_config()) and "rolling_frac_positive_active" in metrics:
        v = g4_exposure_variant(metrics, cfg)
        active = exposure_aware_active(cfg, "G4")
        d["exposure_aware"] = {"enabled": active, **v}
        if active:
            d["base_passed"] = bool(ok)
            ok = bool(v["passed"])
    return ok, d


def check_g5(dsr: float, pbo: float, cfg: dict | None = None) -> tuple[bool, dict]:
    """G5 선택편향: DSR ≥ 0.95 (실질 시도 수 사용), 캠페인 PBO < 0.3."""
    c = _cfg(cfg, "G5")
    d = {"dsr": float(dsr), "pbo": float(pbo), "ok_dsr": dsr >= c["min_dsr"],
         "ok_pbo": pbo < c["max_pbo"]}
    return d["ok_dsr"] and d["ok_pbo"], d


def g6_flat_variant(val_metrics: dict, stress: dict, mc_mdd95: float, cfg: dict | None = None,
                    dev_metrics: dict | None = None) -> dict:
    """G6 '쉬어서 하락장을 피함' 경로(정보·shadow 겸용, 규칙 b 의 dict stress 전용).

    통과(path='flat'): Val exposure_days ≤ flat_max_exposure, 같은 기간 B&H 총수익 < 0(flat_require_bh_down),
    Val 총수익 ≥ flat_min_return, stress 총수익 ≥ flat_min_stress_return, Val MDD ≤ flat_max_mdd_vs_bh × B&H MDD,
    기존 MDD ≤ max_mdd 와 MC MDD95 ≤ max_mc_mdd95 그대로, Dev exposure_days ≥ min_dev_exposure, 그리고
    Val 거래 ≥ flat_min_trades. 거래 수만 모자라면 'insufficient_evidence'(통과 아님). Sharpe>0·stress Sharpe>0
    (와 파이프라인의 Val Holm)은 '평균 > 0' 증거를 요구하므로 이 경로에서는 위 '손실 없음' 조건이 대신한다 —
    양(+)의 증거는 Dev(G2·G5)와 잠금(G7)·전진(G8)이 맡는다."""
    c = _cfg(cfg, "G6")
    ea = exposure_aware_cfg(cfg)
    e6 = ea.get("G6") or {}
    need = ("exposure_days", "total_return", "max_dd", "bh_total_return", "bh_max_dd", "n_trades")
    if not isinstance(stress, dict) or any(val_metrics.get(k) is None for k in need):
        return {"applicable": False, "reason": "Val exposure/B&H metrics missing (or legacy stress rule)",
                "passed": False, "status": "not_applicable"}
    ex = float(val_metrics["exposure_days"])
    ret = float(val_metrics["total_return"])
    sret = float(stress.get("total_return", float("nan")))
    mdd = abs(float(val_metrics["max_dd"]))
    bh_ret, bh_mdd = float(val_metrics["bh_total_return"]), abs(float(val_metrics["bh_max_dd"]))
    ntr = int(val_metrics["n_trades"])
    dev_ex = None if not dev_metrics or dev_metrics.get("exposure_days") is None else float(dev_metrics["exposure_days"])
    d = {"applicable": True, "exposure_days": ex, "total_return": ret, "stress_total_return": sret, "max_dd": mdd,
         "bh_total_return": bh_ret, "bh_max_dd": bh_mdd, "n_trades": ntr, "dev_exposure_days": dev_ex,
         "flat_max_exposure": float(e6.get("flat_max_exposure", 0.25)),
         "ok_flat": ex <= float(e6.get("flat_max_exposure", 0.25)),
         "ok_bh_down": (bh_ret < 0) if bool(e6.get("flat_require_bh_down", True)) else True,
         "ok_return": ret >= float(e6.get("flat_min_return", -0.02)),
         "ok_stress_return": sret >= float(e6.get("flat_min_stress_return", -0.04)),
         "ok_mdd_vs_bh": mdd <= float(e6.get("flat_max_mdd_vs_bh", 0.5)) * bh_mdd,
         "ok_mdd": mdd <= c["max_mdd"], "ok_mc_mdd": abs(float(mc_mdd95)) <= c["max_mc_mdd95"],
         "ok_dev_exposure": dev_ex is not None and dev_ex >= float(ea.get("min_dev_exposure", 0.0)),
         "ok_min_trades": ntr >= int(e6.get("flat_min_trades", 10))}
    no_harm = all(d[k] for k in ("ok_flat", "ok_bh_down", "ok_return", "ok_stress_return", "ok_mdd_vs_bh",
                                 "ok_mdd", "ok_mc_mdd", "ok_dev_exposure"))
    d["passed"] = bool(no_harm and d["ok_min_trades"])
    d["status"] = ("flat_pass" if d["passed"] else "insufficient_evidence" if no_harm else
                   "not_flat" if not d["ok_flat"] else "fail")
    return d


def check_g6(val_metrics: dict, stress, mc_mdd95: float,
             cfg: dict | None = None, dev_metrics: dict | None = None) -> tuple[bool, dict]:
    """G6 검증(Val) — 2026-11-09 사용자 결정 (b):
    - Sharpe(primary=conservative 비용) > min_sharpe
    - 최대낙폭은 **primary 비용** 기준: val_metrics["max_dd"] ≤ max_mdd
    - stress 비용에서는 Sharpe > min_stress_sharpe 만 요구 (낙폭 크기는 판정하지 않음)
    - 거래당 MC MDD95 ≤ max_mc_mdd95
    `stress` 는 stress 비용 summarize dict(권장). 하위 호환: float 이면 구 규칙(stress MDD ≤ max_stress_mdd)으로 판정.
    낙폭 값은 크기(부호 무관, abs).
    exposure_aware(기본 OFF): 켜져 있으면 기존 조건 OR `g6_flat_variant`(path='flat'). 꺼져 있으면 변형 결과는
    details["exposure_aware"] 에 정보로만. details["path"] = 'sharpe' | 'flat' | None. dev_metrics = 같은 스펙의
    Dev summarize (변형의 Dev 노출 조건)."""
    c = _cfg(cfg, "G6")
    sr = float(val_metrics["sharpe"])
    mc = abs(float(mc_mdd95))
    d = {"sharpe": sr, "mc_mdd95": mc, "ok_sharpe": sr > c["min_sharpe"],
         "ok_mc_mdd": mc <= c["max_mc_mdd95"]}
    if isinstance(stress, dict):
        mdd = abs(float(val_metrics.get("max_dd", float("nan"))))
        ssr = float(stress["sharpe"])
        d.update(max_dd=mdd, stress_sharpe=ssr, stress_mdd=abs(float(stress.get("max_dd", float("nan")))),
                 ok_mdd=mdd <= c["max_mdd"], ok_stress_sharpe=ssr > c["min_stress_sharpe"], rule="b")
        ok = d["ok_sharpe"] and d["ok_mdd"] and d["ok_stress_sharpe"] and d["ok_mc_mdd"]
    else:
        smdd = abs(float(stress))
        d.update(stress_mdd=smdd, ok_stress_mdd=smdd <= c.get("max_stress_mdd", c["max_mdd"]), rule="legacy")
        ok = d["ok_sharpe"] and d["ok_stress_mdd"] and d["ok_mc_mdd"]
    d["path"] = "sharpe" if ok else None
    if "exposure_aware" in (cfg or load_gate_config()) and isinstance(stress, dict):
        v = g6_flat_variant(val_metrics, stress, mc_mdd95, cfg, dev_metrics)
        active = exposure_aware_active(cfg, "G6")
        d["exposure_aware"] = {"enabled": active, **v}
        if active and not ok and v["passed"]:
            d["base_passed"] = False
            ok = True
            d["path"] = "flat"
        elif active:
            d["base_passed"] = bool(ok)
    return bool(ok), d


def check_g7(p_value: float, prior_p_values=(), cfg: dict | None = None) -> tuple[bool, dict]:
    """G7 잠금: 잠금 구간 일평균 수익 > 0 의 p 값이 순차 Holm(α=0.10, 상한 10) 통과.
    p_value 는 inference.bootstrap_mean_test 결과. 정의는 inference.lockbox_holm_decision."""
    c = _cfg(cfg, "G7")
    d = inference.lockbox_holm_decision(p_value, prior_p_values, c["alpha"], c["cap"],
                                        c["holm_mode"])
    return d["passed"], d


def _weekly(daily: pd.Series) -> pd.Series:
    s = pd.Series(daily).dropna().astype(float)
    if isinstance(s.index, pd.DatetimeIndex):
        return (1 + s).resample("W-SUN").prod() - 1   # 월~일 주, 복리
    g = np.arange(s.size) // 7
    return (1 + s).groupby(g).prod() - 1


def risk_limits_from_validation(val_daily_returns: pd.Series, mc_mdd95: float,
                                avg_trades_per_day: float, cfg: dict | None = None) -> dict:
    """PLAN §7 공식 (음수 = 손실 수익률 한도; 'min' 은 더 큰 손실 쪽 = 더 느슨한 쪽).

    daily_loss  = min(−3%, Val 일간 수익 1백분위)
    weekly_loss = min(−6%, Val 주간(월~일, 복리) 수익 1백분위)
    max_drawdown= min(−12%, −|mc_mdd95| × 1.2)     (mc_mdd95 는 크기, 부호 무관)
    max_trades_per_day = max(5, 3 × 백테스트 일평균 거래)
    """
    c = _cfg(cfg, "risk")
    s = pd.Series(val_daily_returns).dropna().astype(float)
    q = c["loss_percentile"]
    d1 = float(np.percentile(s, q)) if s.size else 0.0
    wk = _weekly(s)
    w1 = float(np.percentile(wk, q)) if wk.size else 0.0
    dd = -abs(float(mc_mdd95)) * c["drawdown_mc_mult"]
    return {
        "daily_loss_limit": min(c["daily_loss_floor"], d1),
        "weekly_loss_limit": min(c["weekly_loss_floor"], w1),
        "drawdown_limit": min(c["drawdown_floor"], dd),
        "max_trades_per_day": max(float(c["min_max_trades_per_day"]),
                                  c["trades_per_day_mult"] * float(avg_trades_per_day)),
        "inputs": {"daily_p1": d1, "weekly_p1": w1, "mc_mdd95": abs(float(mc_mdd95)),
                   "avg_trades_per_day": float(avg_trades_per_day)},
    }
