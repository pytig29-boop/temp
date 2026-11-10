"""검정력·예산 계산기 (PLAN §10b-3): 캠페인 등록 *전에* G5(DSR ≥ min_dsr) 통과에 필요한 최소 Sharpe.

G5 는 `evaluation.selection.deflated_sharpe_ratio` 로 판정한다 (pipeline.Pipeline.g5 와 같은 식):
    DSR = PSR(SR; SR* = E[max SR | N, V]),  SR·V 는 일간(비연율화), n_obs = Dev 일수.
여기서는 그 식을 거꾸로 푼다.
- `required_sharpe`: 주어진 (n_days, N, V, 왜도, 첨도) 에서 DSR = min_dsr 이 되는 연율 Sharpe.
- `max_trials`: 목표 연율 Sharpe 로 DSR ≥ min_dsr 을 유지할 수 있는 최대 실질 시도 수 N.
- `estimate_effective_trials`: 계획 격자의 실질 시도 수 추정 — pipeline 과 같은 식
      N_dsr = max(k_eff, n_family · k_eff / n_campaign)  ≈ (계열 기존 시도 + 격자) · 압축비
  압축비(k_eff / 격자 크기) 는 저장된 pipeline.json 들의 campaign_stats 중앙값, 없으면 기본값 — 단 하한 1/n_grid
  (군집은 최소 1개라 실현 K/N ≥ 1/N; 작은 단일 군집 격자에서 이력 압축비가 N_dsr 를 과소추정하던 문제, pm-000007).
- `power_table` / `budget_report`: CLI·리포트용 작은 표.

V(시도 SR 분산) 기본값 = 1/n_days: 진짜 SR 이 모두 0 인 시도들의 표본 SR 분산(최소값). 실제 캠페인은
시도 간 진짜 SR 차이가 더해져 더 크다 → 표는 V 배수(1, 2, 4)로 범위를 보여 준다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize

from btc_quant.evaluation import selection

ANN = 365
DEFAULT_MIN_DSR = 0.95                 # gates.yaml G5.min_dsr (load_gate_config 실패 시)
# 압축비 기본값 (k_eff / 격자 스펙 수): 이력이 없을 때의 대략값. 한 신호의 청산 변형(×80)은 대부분 상관 ≥ 0.5
# 로 한 군집에 묶이고(같은 진입 시점·같은 방향), 인접 진입 파라미터도 상당 부분 겹친다는 가정에서
# '신호 조합당 ~2 군집, 청산 80개 중' ≈ 2/80 ≈ 0.025 를 두 배로 잡아 보수적으로 0.05.
# 저장된 pipeline 결과가 MIN_HISTORY 개 이상 쌓이면 그 중앙값을 쓴다.
DEFAULT_COMPRESSION = 0.05
MIN_HISTORY = 3
DEFAULT_TRIALS = (1, 10, 30, 100, 200, 500, 1000)
DEFAULT_V_MULTS = (1.0, 2.0, 4.0)


def _min_dsr(min_dsr: float | None) -> float:
    if min_dsr is not None:
        return float(min_dsr)
    try:
        from btc_quant.evaluation.gates import load_gate_config
        return float(load_gate_config()["G5"]["min_dsr"])
    except Exception:          # noqa: BLE001 — 설정 없이도 동작
        return DEFAULT_MIN_DSR


def default_var_trial_sr(n_days: int) -> float:
    """진짜 SR=0 인 시도들의 일간 표본 SR 분산 ≈ 1/n (V 의 하한)."""
    return 1.0 / max(int(n_days), 2)


def dsr_at(sharpe_ann: float, n_days: int, n_trials: float, var_trial_sr: float | None = None,
           skew: float = 0.0, kurtosis: float = 3.0, ann: int = ANN) -> float:
    V = default_var_trial_sr(n_days) if var_trial_sr is None else float(var_trial_sr)
    return selection.deflated_sharpe_ratio(sharpe_ann / math.sqrt(ann), int(n_days), skew, kurtosis,
                                           n_trials, V)


def required_sharpe(n_days: int, n_trials: float, var_trial_sr: float | None = None, skew: float = 0.0,
                    kurtosis: float = 3.0, min_dsr: float | None = None, ann: int = ANN) -> float:
    """DSR ≥ min_dsr 에 필요한 최소 연율 Sharpe (일간 SR × √ann)."""
    target = _min_dsr(min_dsr)
    V = default_var_trial_sr(n_days) if var_trial_sr is None else float(var_trial_sr)
    sr0 = selection.expected_max_sharpe(n_trials, V)

    def f(sr):
        return selection.probabilistic_sharpe_ratio(sr, int(n_days), skew, kurtosis, sr0) - target

    lo, hi = sr0, sr0 + 0.05
    k = 0
    while f(hi) < 0:
        hi += 0.05 * 2 ** k
        k += 1
        if k > 40:
            return float("inf")
    if f(lo) >= 0:
        return float(lo * math.sqrt(ann))
    return float(optimize.brentq(f, lo, hi, xtol=1e-10) * math.sqrt(ann))


def max_trials(sharpe_ann: float, n_days: int, var_trial_sr: float | None = None, skew: float = 0.0,
               kurtosis: float = 3.0, min_dsr: float | None = None, ann: int = ANN,
               n_cap: float = 1e12) -> float:
    """목표 연율 Sharpe 로 DSR ≥ min_dsr 이 유지되는 최대 실질 시도 수 (연속값, 내림해서 쓴다).
    N=1 에서도 실패하면 0, n_cap 에서도 통과하면 inf."""
    target = _min_dsr(min_dsr)

    def g(logn):
        return dsr_at(sharpe_ann, n_days, math.exp(logn), var_trial_sr, skew, kurtosis, ann) - target

    if g(0.0) < 0:
        return 0.0
    if g(math.log(n_cap)) >= 0:
        return float("inf")
    return float(math.exp(optimize.brentq(g, 0.0, math.log(n_cap), xtol=1e-9)))


# ------------------------------------------------------------------ 실질 시도 수
def compression_history(campaigns_dir: str | Path) -> list[dict]:
    """저장된 pipeline.json 의 campaign_stats → [{campaign_id, n_specs, k_eff, ratio, var_trial_sr}]."""
    out = []
    d = Path(campaigns_dir)
    if not d.exists():
        return out
    for pj in sorted(d.glob("*/pipeline.json")):
        try:
            cs = json.loads(pj.read_text(encoding="utf-8")).get("campaign_stats") or {}
            n, k = int(cs["n_campaign_specs"]), int(cs["k_eff_clusters"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if n > 0 and k > 0:
            out.append({"campaign_id": pj.parent.name, "n_specs": n, "k_eff": k, "ratio": k / n,
                        "var_trial_sr": cs.get("var_trial_sr")})
    return out


def estimate_effective_trials(n_grid: int, family: str | None = None, root: str | Path | None = None,
                              ledger=None, campaigns_dir: str | Path | None = None,
                              ratio: float | None = None,
                              default_ratio: float = DEFAULT_COMPRESSION) -> dict:
    """계획 격자(n_grid 스펙)의 DSR 실질 시도 수 추정 — pipeline.campaign_stats 와 같은 식.

    k_eff ≈ max(1, ratio · n_grid);  N_dsr = max(k_eff, (n_family_prior + n_grid) · ratio).
    ratio = max(기준 압축비, 1/n_grid); 기준 압축비: 지정값 > 이력 중앙값(MIN_HISTORY 개 이상) > default_ratio.
    하한 1/n_grid (pm-000007): pipeline G5 는 캠페인의 실현 K/N 을 쓰고 K ≥ 1 이므로 K/N ≥ 1/n_grid 다. 스펙이 모두
    한 군집에 묶이는 작은 격자(예: 4 스펙 → K/N = 0.25)에 이력 중앙값(예: 0.125)을 쓰면 N_dsr·필요 Sharpe 를
    과소추정한다. 큰 격자(1/n_grid < 기준)에는 영향이 없다. 반환값의 ratio_base 는 하한 적용 전 값.
    """
    from btc_quant.registry.ledger import Ledger, repo_root

    r = Path(root) if root is not None else repo_root()
    if ledger is None:
        ledger = Ledger(r / "research" / "ledger")
    hist = compression_history(campaigns_dir if campaigns_dir is not None else r / "research" / "campaigns")
    if ratio is not None:
        rt, src = float(ratio), "given"
    elif len(hist) >= MIN_HISTORY:
        rt, src = float(np.median([h["ratio"] for h in hist])), f"history median of {len(hist)} campaigns"
    else:
        rt, src = float(default_ratio), (f"default {default_ratio} (history {len(hist)} < {MIN_HISTORY})")
    base = rt
    floor = 1.0 / max(int(n_grid), 1)
    if floor > rt:
        rt, src = floor, f"{src}; floored at 1/n_grid = {floor:.3g} (single-cluster small grid, pm-000007)"
    n_prior = int(ledger.trials_count(family=family)) if family else 0
    k_eff = max(1.0, rt * int(n_grid))
    n_dsr = max(k_eff, (n_prior + int(n_grid)) * rt)
    vs = [h["var_trial_sr"] for h in hist if h.get("var_trial_sr")]
    return {"n_grid": int(n_grid), "family": family, "n_family_prior": n_prior, "ratio": rt,
            "ratio_source": src, "ratio_base": base, "ratio_floor": floor, "k_eff_est": k_eff, "n_trials_dsr": float(n_dsr),
            "history": hist, "var_trial_sr_history_median": float(np.median(vs)) if vs else None}


# ------------------------------------------------------------------ 표
def power_table(n_days: int, trials=DEFAULT_TRIALS, v_mults=DEFAULT_V_MULTS, skew: float = 0.0,
                kurtosis: float = 3.0, min_dsr: float | None = None, ann: int = ANN) -> pd.DataFrame:
    """행 = 실질 시도 N, 열 = V 배수(× 1/n_days) → 필요한 최소 연율 Sharpe."""
    base = default_var_trial_sr(n_days)
    rows = []
    for n in trials:
        row = {"n_trials": n}
        for m in v_mults:
            row[f"V={m:g}/T"] = required_sharpe(n_days, n, base * m, skew, kurtosis, min_dsr, ann)
        rows.append(row)
    return pd.DataFrame(rows).set_index("n_trials")


def budget_report(n_grid: int, n_days: int, family: str | None = None, target_sharpe: float | None = None,
                  root: str | Path | None = None, skew: float = 0.0, kurtosis: float = 3.0,
                  var_trial_sr: float | None = None, min_dsr: float | None = None, ledger=None,
                  campaigns_dir: str | Path | None = None, ratio: float | None = None) -> dict:
    """캠페인 등록 전 점검: 계획 격자의 실질 시도 수 → 통과 최소 Sharpe (+ 목표 Sharpe 의 최대 시도 수).

    V: 지정값 > 이력 중앙값 > 1/n_days. 반환 dict 의 "markdown" 은 리포트에 그대로 붙일 수 있다.
    """
    est = estimate_effective_trials(n_grid, family, root, ledger, campaigns_dir, ratio)
    if var_trial_sr is not None:
        V, vsrc = float(var_trial_sr), "given"
    elif est["var_trial_sr_history_median"]:
        V, vsrc = float(est["var_trial_sr_history_median"]), "history median"
    else:
        V, vsrc = default_var_trial_sr(n_days), "1/n_days (null minimum)"
    need = required_sharpe(n_days, est["n_trials_dsr"], V, skew, kurtosis, min_dsr)
    need_single = required_sharpe(n_days, 1, V, skew, kurtosis, min_dsr)
    out = {**est, "n_days": int(n_days), "var_trial_sr": V, "var_source": vsrc, "skew": skew,
           "kurtosis": kurtosis, "min_dsr": _min_dsr(min_dsr), "required_sharpe_ann": need,
           "required_sharpe_ann_single_trial": need_single}
    if target_sharpe is not None:
        nmax = max_trials(target_sharpe, n_days, V, skew, kurtosis, min_dsr)
        out["target_sharpe_ann"] = float(target_sharpe)
        out["max_trials_for_target"] = nmax
        out["max_grid_for_target"] = (max(0.0, nmax / est["ratio"] - est["n_family_prior"])
                                      if np.isfinite(nmax) and est["ratio"] > 0 else nmax)
    lines = [(f"- 계획 격자 {n_grid} 스펙, 계열 `{family}` 기존 시도 {est['n_family_prior']}, "
              f"압축비 {est['ratio']:.3g} ({est['ratio_source']}) → 실질 시도 ≈ {est['n_trials_dsr']:.1f}"),
             f"- Dev {n_days}일, V={V:.3g} ({vsrc}), 왜도 {skew:g}, 첨도 {kurtosis:g}, DSR ≥ {out['min_dsr']}",
             f"- **G5 통과 최소 연 Sharpe ≈ {need:.2f}** (시도 1회였다면 {need_single:.2f})"]
    if target_sharpe is not None:
        lines.append(f"- 목표 연 Sharpe {target_sharpe:g} 로 감당 가능한 실질 시도 ≈ {out['max_trials_for_target']:.0f}"
                     f" (격자 ≈ {out['max_grid_for_target']:.0f} 스펙)")
    out["markdown"] = "\n".join(lines)
    return out


def table_markdown(df: pd.DataFrame, nd: int = 2) -> str:
    cols = [df.index.name or "", *df.columns]
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for i, r in df.iterrows():
        lines.append("| " + " | ".join([str(i)] + [f"{v:.{nd}f}" for v in r.to_numpy()]) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    """uv run python -m btc_quant.research.power --days 1764 [--grid 1280 --family X --target 2.0]"""
    import argparse

    ap = argparse.ArgumentParser(description="DSR power / budget calculator (PLAN §10b-3)")
    ap.add_argument("--days", type=int, required=True)
    ap.add_argument("--grid", type=int, default=None)
    ap.add_argument("--family", default=None)
    ap.add_argument("--target", type=float, default=None)
    ap.add_argument("--skew", type=float, default=0.0)
    ap.add_argument("--kurt", type=float, default=3.0)
    a = ap.parse_args(argv)
    print(table_markdown(power_table(a.days, skew=a.skew, kurtosis=a.kurt)))
    if a.grid:
        print()
        print(budget_report(a.grid, a.days, a.family, a.target, skew=a.skew, kurtosis=a.kurt)["markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_COMPRESSION",
    "budget_report",
    "compression_history",
    "default_var_trial_sr",
    "dsr_at",
    "estimate_effective_trials",
    "max_trials",
    "power_table",
    "required_sharpe",
    "table_markdown",
]
