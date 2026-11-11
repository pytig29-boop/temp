"""페이퍼 판정 (PLAN D28, autonomous_research_plan §5.4) — "백테스트와 같게 움직이는가"만 본다(수익 증명 아님).

입력은 `paper.service.build_report` 결과(슬리브별, 같은 기간 기준 엔진 재현과의 비교)와 체결 비용 통계, 가동 비율.
판정 = PASS | FAIL | EXTEND(7일 → 14일, 1회). 손익이 플러스인지는 보지 않는다 — 예상 범위 안인지만.

    P1 체결 비용: 자산별 실측 편도 비용 중앙값 ≤ conservative 편도, p90 ≤ stress 편도 (체결 < 10 이면 판정 보류 → EXTEND)
    P2 재현 일치: 진입 시각 일치율 ≥ 95%, 일치 거래 중 청산 동일 ≥ 90%, 한쪽만 있는 거래(리스크 차단 제외) ≤ max(1, 5%)
    P3 손익: |페이퍼 누적 − 재현 누적| ≤ max(거래당 10bp × 거래 수, 0.3%p), 누적 손익이 n일 부트스트랩 2.5~97.5% 안
    P4 운영: 실시간 가동 ≥ 70%, 사람-해제 리스크 정지 0
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from btc_quant.evaluation import inference

DEFAULT = {"min_days": 7, "max_days": 14, "min_fills": 10, "min_round_trips": 5, "min_4h_bars": 42,
           "entry_match": 0.95, "exit_match": 0.90, "one_sided_frac": 0.05, "pnl_bp_per_trade": 10.0,
           "pnl_abs": 0.003, "band": (0.025, 0.975), "monitor_low": 0.01, "monitor_high": 0.995,
           "min_uptime": 0.70}


def expectation_band(daily: pd.Series, n_days: int, n_boot: int = 5000, seed: int = 0,
                     qs=(0.01, 0.025, 0.975, 0.995)) -> dict:
    """개발+검증 일간 수익(운용 크기)의 정상 블록 부트스트랩으로 만든 n일 누적 수익 분위수."""
    x = np.asarray(pd.Series(daily).dropna(), float)
    n_days = max(int(n_days), 1)
    if x.size < 10:
        return {f"q{q:g}": float("nan") for q in qs}
    L = inference.default_block_length(x.size)
    rng = np.random.default_rng(seed)
    idx = inference.stationary_bootstrap_indices(x.size, n_boot, L, rng)[:, :n_days] if x.size >= n_days else \
        rng.integers(0, x.size, (n_boot, n_days))
    cum = np.prod(1 + x[idx], axis=1) - 1
    return {f"q{q:g}": float(np.quantile(cum, q)) for q in qs}


def judge(reports: list[dict], *, n_days: int, uptime: float, human_halts: int, band: dict,
          paper_cum: float, cost_limits: dict, fills_by_asset: dict, extended: bool = False,
          four_h: bool = False, n_4h_bars: int = 0, cfg: dict | None = None) -> dict:
    """reports = build_report 결과들(슬리브별). cost_limits = {자산: (conservative 편도 bp, stress 편도 bp)},
    fills_by_asset = {자산: [실측 편도 bp, ...]} (실시간 체결만). paper_cum = 운용 크기 기준 누적 손익."""
    c = {**DEFAULT, **(cfg or {})}
    reasons, out = [], {"n_days": n_days}
    n_fills = sum(len(v) for v in fills_by_asset.values())
    n_rt = sum(int((r.get("trades") or {}).get("n_paper", 0)) for r in reports)
    active = (n_4h_bars >= c["min_4h_bars"]) if four_h else (n_fills >= c["min_fills"] or n_rt >= c["min_round_trips"])
    out["activity"] = {"fills": n_fills, "round_trips": n_rt, "enough": bool(active)}
    if n_days < c["min_days"] or (not active and n_days < c["max_days"]):
        if n_days >= c["min_days"] and not extended:
            return {**out, "verdict": "EXTEND", "reasons": ["activity below minimum after 7 days"]}
        if n_days < c["min_days"]:
            return {**out, "verdict": "PENDING", "reasons": [f"{n_days} < {c['min_days']} days"]}
    # P1
    p1 = {}
    for a, xs in fills_by_asset.items():
        if not xs:
            continue
        cons, stress = cost_limits[a]
        med, p90 = float(np.median(xs)), float(np.percentile(xs, 90))
        p1[a] = {"n": len(xs), "median": med, "p90": p90, "ok": med <= cons + 1e-9 and p90 <= stress + 1e-9}
    out["P1"] = p1
    if n_fills >= c["min_fills"] and not all(v["ok"] for v in p1.values()):
        reasons.append("P1 fill cost above model")
    # P2
    nb = sum(int((r.get("trades") or {}).get("n_backtest", 0)) for r in reports)
    nm = sum(int((r.get("trades") or {}).get("n_matched_entry", 0)) for r in reports)
    ni = sum(int((r.get("trades") or {}).get("n_identical", 0)) for r in reports)
    blocked = sum(len(r.get("blocked_by_risk") or []) for r in reports)
    one = sum(len((r.get("trades") or {}).get("paper_only") or []) + len((r.get("trades") or {}).get("backtest_only")
                                                                         or []) for r in reports) - blocked
    denom = max(nb, n_rt, 1)
    out["P2"] = {"entry_match": nm / denom, "exit_match": (ni / nm) if nm else 1.0, "one_sided": max(one, 0)}
    if nm / denom < c["entry_match"] and denom > 0 and (nb or n_rt):
        reasons.append("P2 entry times differ from replay")
    if nm and ni / nm < c["exit_match"]:
        reasons.append("P2 exits differ from replay")
    if max(one, 0) > max(1, c["one_sided_frac"] * denom):
        reasons.append("P2 unexplained one-sided trades")
    # P3
    net_p = sum(float((r.get("trades") or {}).get("net_paper") or 0) for r in reports) / max(len(reports), 1)
    net_b = sum(float((r.get("trades") or {}).get("net_backtest") or 0) for r in reports) / max(len(reports), 1)
    tol = max(c["pnl_bp_per_trade"] * 1e-4 * max(n_rt, 1), c["pnl_abs"])
    lo, hi = band.get(f"q{c['band'][0]:g}"), band.get(f"q{c['band'][1]:g}")
    out["P3"] = {"paper_vs_replay": net_p - net_b, "tol": tol, "paper_cum": paper_cum, "band": [lo, hi]}
    if abs(net_p - net_b) > tol:
        reasons.append("P3 paper PnL differs from replay")
    if lo is not None and np.isfinite(lo) and paper_cum < lo:
        reasons.append("P3 below expected band")
    out["P3"]["above_band_check"] = bool(hi is not None and np.isfinite(hi) and paper_cum > band.get("q0.995", hi))
    # P4
    out["P4"] = {"uptime": uptime, "human_halts": human_halts}
    if human_halts > 0:
        reasons.append("P4 human-level risk halt")
    if uptime < c["min_uptime"]:
        if not extended and n_days < c["max_days"]:
            return {**out, "verdict": "EXTEND", "reasons": ["uptime below 70%"]}
        reasons.append("P4 uptime below 70% (environment) — undecidable")
        out["undecidable_environment"] = True
    return {**out, "verdict": "FAIL" if reasons else "PASS", "reasons": reasons}


def monitor(paper_cum: float, band: dict, mismatch_frac: float, human_halts: int, cfg: dict | None = None) -> dict:
    """규칙 잠금 이후 매일: 누적 손익이 99% 하한 아래·재현 불일치 > 5%·사람-해제 정지 → 강등. 99.5% 초과 = 버그 점검."""
    c = {**DEFAULT, **(cfg or {})}
    lo = band.get(f"q{c['monitor_low']:g}")
    hi = band.get(f"q{c['monitor_high']:g}")
    demote = []
    if lo is not None and np.isfinite(lo) and paper_cum < lo:
        demote.append("below 99% band")
    if mismatch_frac > 0.05:
        demote.append("replay mismatch > 5%")
    if human_halts > 0:
        demote.append("human-level risk halt")
    return {"demote": bool(demote), "reasons": demote,
            "check_bug": bool(hi is not None and np.isfinite(hi) and paper_cum > hi)}
