"""Class ② 증거 기준 제안 도구 — 합성 보정으로만, 한 번에 한 기준, 다음 캠페인부터, 사람 승인.

    res = propose_criterion("gates.G5.min_dsr", [0.90, 0.95, 0.975],
                            Targets(max_fpr=0.05, min_power=0.70, power_sharpe=2.0),
                            motivation="DSR 문턱의 합성 시장 오탐/검정력 점검", mode="bounded",
                            n_null_markets=20, n_planted_markets=10, jobs=4)

절차
1. 골대 이동 방지(anti-goalpost) 검사 — 실패 시 GoalpostError, 아무것도 실행하지 않는다:
   - 대상은 class ② 이고 값이 config/gates.yaml 에 있는 항목만 (코드 상수는 사람이 코드로 바꾼다).
   - motivation / references 에 실제 연구 결과를 가리키는 것이 있으면 거부: 원장 id(camp-/run-/hyp-/val-/lbx-/
     fwd-/trn-NNN), 원장에 있는 spec_id·logic_hash, 레지스트리 전략 id, research/campaigns|ledger|lessons 경로,
     strategies/ 경로. references 는 허용 접두사(synthetic:, docs/, literature:, research/reports/pipeline_calibration)만.
2. calibration_study 의 합성 시장(귀무 = 엣지 없음, 심은 엣지 = 목표 참 Sharpe)을 후보 값마다 **같은 seed** 로
   실행(공통 난수) → 값마다 오탐률(귀무 시장에서 validated ≥ 1) · 검정력(S* validated) + Wilson 95% 구간.
3. 목표(오탐 ≤ max_fpr 이면서 검정력 ≥ min_power)를 만족하는 값 중 검정력이 가장 높은 값(동률이면 현재 값에
   가까운 값)을 추천. 현재 값이면 제안 없음. 만족하는 값이 없으면 제안 없음(근거 리포트만).
4. 제안 = research/param_proposals/<id>.yaml (class 2, kind criterion, applies_from = 승인 시점의 다음 캠페인).
   quick 모드(짧은 합성 구간)는 구조 점검용이라 allow_smoke=True 가 아니면 제안을 쓰지 않는다.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ._io import repo_root, today, utcnow
from .registry import load_registry

REPORTS_REL = Path("research") / "reports"
ALLOWED_REF_PREFIXES = ("synthetic:", "docs/", "literature:", "research/reports/pipeline_calibration")
LEDGER_ID_RE = re.compile(r"\b(camp|run|hyp|val|lbx|fwd|trn)-\d{3,}\b", re.IGNORECASE)
REAL_PATH_RE = re.compile(r"(research/(campaigns|ledger|lessons|lockbox_approvals)|strategies/(active|review|retired|"
                          r"candidates)|runtime/paper)", re.IGNORECASE)
# pipeline_calibration 에서 측정한 full 레이아웃 k (재보정 비용 절약; mode=bounded 기본값).
# k(엣지 세기) ↦ 심은 스펙의 비용후 Sharpe 는 primary 비용 모델과 합성 시장의 1분봉 준비(코인원 꼬리 정리 폭)에
# 의존한다. 저장된 k 는 아래 FULL_K_CALIBRATED_UNDER 조건에서만 유효하고, 현재 조건이 다르면(예: 비용 v2 승인,
# 코인원 꼬리 k 0.03→0.01) **현재 비용 레지스트리로 다시 유도**하거나(derive_full_k: pilot_edge_map, 같은 seed)
# 호출자가 k_for 를 명시해야 한다(`full_k_for`). 오래된 상수가 조용히 쓰이면 검정력 표가 틀린 참 Sharpe 를 가리킨다.
FULL_K_FOR = {0.5: 0.0239, 1.0: 0.0257, 2.0: 0.0292, 3.0: 0.0327}
FULL_K_CALIBRATED_UNDER = {
    "cost_model": "coinone_conservative",
    "cost": {"fee_rate": 0.0, "spread_bps": 2.0, "slip_bps": 3.0},     # costs.yaml coinone_conservative v1
    "coinone_wick_k": 0.03,                                            # 2026-11-09 데이터 점검 이전 기본값
    "scenario": "5m",
}


class StaleCalibrationError(ValueError):
    """저장된 k 가 현재 비용·데이터 준비 조건과 다르게 보정됨 → 유도하거나 k_for 를 명시해야 한다."""


def current_k_conditions() -> dict:
    """합성 보정 실행이 실제로 쓰는 조건: primary 비용(현재 버전, research.campaign.get_cost — 코드 저장소
    config/costs.yaml) + 코인원 꼬리 정리 폭 + 시나리오."""
    from btc_quant.data.quality import wick_params
    from btc_quant.research import calibration_study as CS
    m = CS.primary_cost()
    return {"cost_model": CS.PRIMARY_COST,
            "cost": {"fee_rate": float(m.fee_rate), "spread_bps": float(m.spread_bps), "slip_bps": float(m.slip_bps)},
            "coinone_wick_k": float(wick_params("coinone")[0]), "scenario": "5m"}


def k_conditions_match(cur: dict | None = None) -> bool:
    cur = cur or current_k_conditions()
    ref = FULL_K_CALIBRATED_UNDER
    return (cur["cost_model"] == ref["cost_model"] and cur["scenario"] == ref["scenario"]
            and abs(cur["coinone_wick_k"] - ref["coinone_wick_k"]) < 1e-12
            and all(abs(float(cur["cost"][k]) - float(v)) < 1e-9 for k, v in ref["cost"].items()))


def derive_full_k(power_sharpe: float, jobs: int = 1, progress: Callable = print) -> dict:
    """현재 비용 레지스트리·데이터 준비로 full 레이아웃 k 를 다시 유도 (calibration_study.pilot_edge_map, 같은
    seed·파일럿 격자). 수 분~수십 분. 반환 {power_sharpe: k}."""
    from dataclasses import replace

    from btc_quant.research import calibration_study as CS
    cfg = replace(CS.full_config(jobs=jobs), strengths=(float(power_sharpe),))
    progress(f"[criteria] deriving k for Sharpe {power_sharpe} under current costs "
             f"({current_k_conditions()['cost']}) — pilot {len(cfg.pilot_ks)} ks × {cfg.pilot_seeds} seeds")
    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor
        from multiprocessing import get_context
        with ProcessPoolExecutor(max_workers=jobs, mp_context=get_context("spawn")) as pool:
            res = CS.pilot_edge_map(cfg, pool)
    else:
        res = CS.pilot_edge_map(cfg)
    return {float(power_sharpe): float(res["k_for_strength"][float(power_sharpe)])}


def full_k_for(power_sharpe: float, derive: bool | Callable = False, jobs: int = 1,
               progress: Callable = print) -> tuple[dict, str]:
    """full 레이아웃 k 결정 → ({power_sharpe: k}, 출처). 저장 조건 = 현재 조건이면 저장값('stored'),
    아니면 derive(True 또는 유도 함수) 로 다시 유도('derived'), derive=False 면 StaleCalibrationError."""
    cur = current_k_conditions()
    if k_conditions_match(cur):
        if power_sharpe not in FULL_K_FOR:
            if not derive:
                raise ValueError(f"no stored k for Sharpe {power_sharpe}; pass k_for "
                                 f"(calibration_study.pilot_edge_map) — known {sorted(FULL_K_FOR)}")
        else:
            return {power_sharpe: FULL_K_FOR[power_sharpe]}, "stored"
    elif not derive:
        raise StaleCalibrationError(
            f"FULL_K_FOR was calibrated under {FULL_K_CALIBRATED_UNDER} but the current conditions are {cur}; "
            "derive k under the current cost registry (derive=True / derive_full_k) or pass k_for explicitly")
    fn = derive if callable(derive) else (lambda s: derive_full_k(s, jobs=jobs, progress=progress))
    return {float(k): float(v) for k, v in fn(power_sharpe).items()}, "derived"


class GoalpostError(ValueError):
    """실제 캠페인·후보 결과를 근거로 한 기준 변경 시도 (거부)."""


@dataclass
class Targets:
    max_fpr: float = 0.05
    min_power: float = 0.70
    power_sharpe: float = 2.0
    # study="regime" 전용 (국면 전환 합성 시장, calibration_study.run_regime_market):
    max_fpr_bearish_val: float | None = None   # 하락 Val 귀무 시장에서의 오탐률 상한 (None = 검사 안 함)
    min_power_gain: float | None = None        # 현재 값 대비 검정력 증가 하한 (None = 검사 안 함)


@dataclass
class CriterionStudy:
    key: str
    dotted: str
    current: Any
    candidates: list
    targets: Targets
    mode: str
    rows: list[dict] = field(default_factory=list)
    recommended: Any = None
    status: str = "pending"            # proposed | no_change | no_candidate_meets_targets | smoke_only
    proposal_id: str | None = None
    report_path: str | None = None
    created: str = field(default_factory=utcnow)


# ============================================================== 골대 이동 방지
def _known_real_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    try:
        from btc_quant.registry.ledger import Ledger
        led = Ledger(root / "research" / "ledger")
        for r in led.records("runs"):
            for k in ("spec_id", "logic_hash", "campaign_id"):
                if r.get(k):
                    ids.add(str(r[k]))
        for t in ("campaigns", "hypotheses", "val_evaluations", "lockbox"):
            for r in led.records(t):
                ids.add(str(r["id"]))
                if r.get("logic_hash"):
                    ids.add(str(r["logic_hash"]))
                if r.get("candidate_id"):
                    ids.add(str(r["candidate_id"]))
    except Exception:  # noqa: BLE001 — 원장이 없으면 패턴 검사만
        pass
    sdir = root / "strategies"
    if sdir.exists():
        for p in sdir.rglob("*.yaml"):
            ids.add(p.stem)
    return {i for i in ids if len(i) >= 4}


def check_anti_goalpost(key: str, motivation: str, references: list[str] | tuple = (), root=None) -> None:
    root = repo_root(root)
    reg = load_registry(root, validate_=False)
    p = reg.get(key)
    if p.cls != 2:
        raise GoalpostError(f"{key} is class {p.cls}; criteria proposals are for class ② only")
    if not p.source.startswith("yaml:config/gates.yaml#"):
        raise GoalpostError(f"{key} lives in code ({p.source}); only gates.yaml criteria can be proposed here")
    if not motivation or not motivation.strip():
        raise GoalpostError("state a motivation that does not depend on real research results")
    texts = [motivation, *map(str, references)]
    for t in texts:
        if LEDGER_ID_RE.search(t):
            raise GoalpostError(f"refused (anti-goalpost): references a real ledger record "
                                f"({LEDGER_ID_RE.search(t).group(0)}); criteria may not be tuned on real results")
        if REAL_PATH_RE.search(t):
            raise GoalpostError(f"refused (anti-goalpost): references real research artefacts "
                                f"({REAL_PATH_RE.search(t).group(0)})")
    for r in references:
        if not str(r).startswith(ALLOWED_REF_PREFIXES):
            raise GoalpostError(f"refused: reference {r!r} is not synthetic/literature/docs "
                                f"(allowed prefixes {ALLOWED_REF_PREFIXES})")
    known = _known_real_ids(root)
    blob = " ".join(texts)
    for i in known:
        if re.search(rf"(?<![\w-]){re.escape(i)}(?![\w-])", blob):
            raise GoalpostError(f"refused (anti-goalpost): mentions real candidate/campaign/strategy id {i!r}")


# ============================================================== 실행
def _vname(v) -> str:
    return f"value={v!r}"


def build_regime_tasks(dotted: str, values: list, mode: str, power_sharpe: float, n_null_markets: int,
                       n_planted_markets: int, k_for: dict | None = None, seed0: int | None = None,
                       n_null_runs: int | None = None, progress: Callable = print) -> tuple[list[dict], dict]:
    """study="regime": 국면 전환 합성 시장(calibration_study.regime_full_config 와 **같은 seed 순서·같은 시장**).
    시장 하나에 캠페인 1회, 후보 값마다 *복사한 원장*에서 파이프라인 1회(공통 난수). 귀무 = 앞에서부터 n_null_markets 개
    (Val 국면 seed % 3 순환 → 1/3 하락 Val), 심은 시장 = 국면 필터 스펙 S*_f 를 심은 종류(FILTERED_KINDS)마다 앞에서부터
    n_planted_markets 개. 무필터 S* 시장(regime_cond)은 노출 인식 변형과 무관(늘 시장에 있음)해 제외한다."""
    from btc_quant.research import calibration_study as CS
    cfg = CS.regime_quick_config() if mode == "quick" else CS.regime_full_config()
    cfg.strength = float(power_sharpe)
    if n_null_runs is not None:
        cfg.n_null, cfg.override_null_runs = int(n_null_runs), True
    if seed0 is not None:
        cfg.seed0 = int(seed0)
    k_source = "explicit (stored regime-study pilot)"
    if k_for is None:
        progress(f"[criteria] regime pilot for Sharpe {power_sharpe} ({len(cfg.pilot_ks)} k × {cfg.pilot_seeds} seeds)")
        k_for = CS.pilot_regime_edge_map(cfg)["k_for"]
        k_source = "regime pilot (calibration_study.pilot_regime_edge_map, current cost registry)"
    names = [_vname(v) for v in values]
    tasks, used = [], {}
    for t in CS.build_regime_tasks(cfg, k_for):
        kind = t["kind"]
        cap = n_null_markets if kind == "null" else n_planted_markets if kind in CS.FILTERED_KINDS else 0
        if used.get(kind, 0) >= cap:
            continue
        used[kind] = used.get(kind, 0) + 1
        t["variants"] = names
        t["variant_overrides"] = {_vname(v): {dotted: v} for v in values}
        tasks.append(t)
    meta = {"study": "regime", "layout": asdict(cfg.layout), "n_null_runs": cfg.n_null, "top_k": cfg.top_k,
            "k_for": k_for, "k_source": k_source, "grid": cfg.grid, "seed0": cfg.seed0, "markets": used,
            "regime_design": {"drift": CS.REGIME_DRIFT, "days": CS.REGIME_DAYS, "edge": CS.REGIME_EDGE,
                              "filter_span_h": CS.REGIME_FILTER_SPAN}}
    return tasks, meta


def regime_runner_from_results(runs: list[dict], value_to_variant: dict) -> Callable[[list[dict]], list[dict]]:
    """이미 실행한 국면 연구(calibration_study --regime 의 json "runs", 같은 코드·같은 seed)의 레코드를 runner 로.
    value_to_variant = {후보 값: 연구의 변형 이름} (예: {False: "current", True: "variant"}). 연구의 변형 설정이
    {dotted: 값} 과 같아야 한다 — 호출자가 보장(REGIME_VARIANTS). 없는 (seed, 변형) 이 있으면 KeyError."""
    idx = {(int(r["seed"]), r["variant"]): r for r in runs}

    def run(tasks: list[dict]) -> list[dict]:
        out = []
        for t in tasks:
            for v, var in value_to_variant.items():
                r = idx[(int(t["seed"]), var)]
                if r["kind"] != t["kind"]:
                    raise KeyError(f"seed {t['seed']}: study kind {r['kind']} != task kind {t['kind']}")
                out.append({**r, "variant": _vname(v)})
        return regime_runs_to_value_runs(out, list(value_to_variant))
    return run


def regime_runs_to_value_runs(recs: list[dict], values: list) -> list[dict]:
    """run_regime_market 레코드(variant = _vname(값)) → summarize 가 읽는 {value, kind(null|planted), …}."""
    by = {_vname(v): v for v in values}
    out = []
    for r in recs:
        if r.get("variant") not in by:
            continue
        out.append({**r, "value": by[r["variant"]], "kind": "null" if r["kind"] == "null" else "planted",
                    "market_kind": r["kind"]})
    return out


def build_tasks(dotted: str, values: list, mode: str, power_sharpe: float, n_null_markets: int,
                n_planted_markets: int, k_for: dict | None = None, seed0: int = 9000,
                n_null_runs: int | None = None, grid: str = "g12", derive_k: bool | Callable = False,
                jobs: int = 1, progress: Callable = print) -> tuple[list[dict], dict]:
    from btc_quant.research import calibration_study as CS
    if mode == "quick":
        cfg = CS.quick_config()
        layout, n_null, top_k, override = cfg.layout, n_null_runs or cfg.n_null, cfg.top_k, True
        grid = "g4"
        if k_for is None:
            from dataclasses import replace
            k_for = {power_sharpe: CS.pilot_edge_map(replace(cfg, strengths=(power_sharpe,)))
                     ["k_for_strength"][power_sharpe]}
    else:
        layout = CS.FULL_LAYOUT
        n_null, top_k, override = n_null_runs or 1000, CS.TOP_K, n_null_runs is not None
        k_source = "explicit"
        if k_for is None:
            k_for, k_source = full_k_for(power_sharpe, derive=derive_k, jobs=jobs, progress=progress)
    base = {"layout": asdict(layout), "n_null": n_null, "top_k": top_k, "override_null_runs": override,
            "plant_lookback": CS.PLANT_LOOKBACK, "grid": grid}
    tasks = []
    for v in values:
        seed = seed0                                  # 값마다 같은 seed → 공통 난수
        for _ in range(n_null_markets):
            tasks.append({**base, "kind": "null", "strength": 0.0, "k": 0.0, "seed": seed, "study": "criteria",
                          "gate_overrides": {dotted: v}, "value": v})
            seed += 1
        for _ in range(n_planted_markets):
            tasks.append({**base, "kind": "planted", "strength": float(power_sharpe), "k": float(k_for[power_sharpe]),
                          "seed": seed, "study": "criteria", "gate_overrides": {dotted: v}, "value": v})
            seed += 1
    meta = {"layout": asdict(layout), "n_null_runs": n_null, "top_k": top_k, "k_for": k_for, "grid": grid}
    if mode != "quick":
        meta["k_source"] = k_source
        meta["k_conditions"] = current_k_conditions()
    return tasks, meta


def default_runner(tasks: list[dict], jobs: int = 1, progress: Callable = print) -> list[dict]:
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    from btc_quant.research.calibration_study import run_market, run_regime_market
    out = []
    if tasks and tasks[0].get("regime"):           # study="regime": 시장마다 값별 레코드 목록
        values = list({repr(v): v for t in tasks for v in [o for ov in t["variant_overrides"].values()
                                                          for o in ov.values()]}.values())
        if jobs > 1:
            with ProcessPoolExecutor(max_workers=jobs, mp_context=get_context("spawn")) as pool:
                recs = [r for rs in pool.map(run_regime_market, tasks) for r in rs]
        else:
            recs = [r for t in tasks for r in run_regime_market(t)]
        return regime_runs_to_value_runs(recs, values)
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs, mp_context=get_context("spawn")) as pool:
            for t, r in zip(tasks, pool.map(run_market, tasks)):
                out.append({**r, "value": t["value"]})
                progress(f"[criteria] value={t['value']} {r['kind']} seed={r['seed']}: star={r['star_outcome']} "
                         f"validated={r['n_validated']}")
    else:
        for t in tasks:
            r = run_market(t)
            out.append({**r, "value": t["value"]})
            progress(f"[criteria] value={t['value']} {r['kind']} seed={r['seed']}: star={r['star_outcome']}")
    return out


def summarize(runs: list[dict], values: list) -> list[dict]:
    from btc_quant.research.calibration_study import wilson
    rows = []
    for v in values:
        nul = [r for r in runs if r["value"] == v and r["kind"] == "null"]
        pla = [r for r in runs if r["value"] == v and r["kind"] == "planted"]
        fp = sum(bool(r["any_validated"]) for r in nul)
        pw = sum(bool(r["star_validated"]) for r in pla)
        bear = [r for r in nul if r.get("val_regime") == "bear"]
        fpb = sum(bool(r["any_validated"]) for r in bear)
        rows.append({"value": v, "n_null": len(nul), "fpr": fp / len(nul) if nul else float("nan"),
                     "n_null_bear_val": len(bear), "fpr_bear_val": fpb / len(bear) if bear else float("nan"),
                     "fpr_bear_val_ci": wilson(fpb, len(bear)),
                     "fpr_ci": wilson(fp, len(nul)), "n_planted": len(pla),
                     "power": pw / len(pla) if pla else float("nan"), "power_ci": wilson(pw, len(pla)),
                     "dev_survival": (sum(bool(r["star_dev_passed"]) for r in pla) / len(pla)) if pla else float("nan")})
    return rows


def meets_targets(r: dict, targets: Targets, cur_row: dict | None = None) -> bool:
    if not (r["n_null"] and r["n_planted"] and r["fpr"] <= targets.max_fpr and r["power"] >= targets.min_power):
        return False
    if targets.max_fpr_bearish_val is not None:
        fb = r.get("fpr_bear_val", float("nan"))
        if not (r.get("n_null_bear_val") and fb <= targets.max_fpr_bearish_val):
            return False
    if targets.min_power_gain is not None and cur_row is not None and r is not cur_row:
        if not r["power"] - cur_row["power"] >= targets.min_power_gain:
            return False
    return True


def recommend(rows: list[dict], targets: Targets, current: Any) -> Any:
    cur_row = next((r for r in rows if r["value"] == current), None)
    ok = [r for r in rows if meets_targets(r, targets, cur_row)]
    if not ok:
        return None

    def dist(v):
        try:
            return abs(float(v) - float(current))
        except (TypeError, ValueError):
            return 0.0 if v == current else 1.0
    return max(ok, key=lambda r: (r["power"], -r["fpr"], -dist(r["value"])))["value"]


def propose_criterion(key: str, candidates: list, targets: Targets, motivation: str,
                      references: list[str] | tuple = (), mode: str = "bounded", n_null_markets: int = 20,
                      n_planted_markets: int = 10, jobs: int = 1, root=None,
                      runner: Callable[[list[dict]], list[dict]] | None = None, k_for: dict | None = None,
                      n_null_runs: int | None = None, allow_smoke: bool = False,
                      progress: Callable = print, derive_k: bool | Callable = True,
                      study: str = "standard", seed0: int | None = None) -> CriterionStudy:
    """derive_k: 저장된 k 의 보정 조건(비용·꼬리 정리)이 현재와 다르면 현재 비용 레지스트리로 k 를 다시 유도
    (True = derive_full_k, 함수 = 그 함수, False = StaleCalibrationError). k_for 를 주면 그대로 쓴다."""
    root = repo_root(root)
    check_anti_goalpost(key, motivation, references, root)           # 먼저: 거부면 아무것도 돌리지 않음
    reg = load_registry(root, validate_=False)
    p = reg.get(key)
    dotted = p.source.split("#", 1)[1]
    current = reg.resolve(p)
    values = list(dict.fromkeys([current, *candidates]))
    st = CriterionStudy(key, dotted, current, values, targets, mode)
    if study == "regime":
        tasks, meta = build_regime_tasks(dotted, values, mode, targets.power_sharpe, n_null_markets,
                                         n_planted_markets, k_for=k_for, seed0=seed0, n_null_runs=n_null_runs,
                                         progress=progress)
    elif study == "standard":
        tasks, meta = build_tasks(dotted, values, mode, targets.power_sharpe, n_null_markets, n_planted_markets,
                                  k_for=k_for, n_null_runs=n_null_runs, derive_k=derive_k, jobs=jobs,
                                  progress=progress)
    else:
        raise ValueError("study must be 'standard' or 'regime'")
    if study == "regime":
        progress(f"[criteria] {key}: {len(values)} values × {len(tasks)} regime markets "
                 f"= {len(values) * len(tasks)} pipeline runs ({mode}, study regime)")
    else:
        progress(f"[criteria] {key}: {len(values)} values × {n_null_markets + n_planted_markets} markets "
                 f"= {len(tasks)} runs ({mode})")
    meta["runner"] = ("default (runs the synthetic markets now)" if runner is None else
                      "supplied runner (e.g. stored results of the same synthetic study, same seeds)")
    runs = (runner or (lambda t: default_runner(t, jobs, progress)))(tasks)
    st.rows = summarize(runs, values)
    st.recommended = recommend(st.rows, targets, current)
    rep_rel = REPORTS_REL / (f"criteria_{re.sub(r'[^A-Za-z0-9]+', '_', key)}_{today().replace('-', '')}"
                             + ("_regime" if study == "regime" else "") + ".md")
    if st.recommended is None:
        st.status = "no_candidate_meets_targets"
    elif st.recommended == current:
        st.status = "no_change"
    elif mode == "quick" and not allow_smoke:
        st.status = "smoke_only"
    else:
        from .proposals import write_proposal
        st.proposal_id = write_proposal({
            "class": 2, "kind": "criterion", "title": f"criterion {key} {current} -> {st.recommended}",
            "changes": [{"target": p.source, "key": key, "from": current, "to": st.recommended}],
            "applies_from": "next campaign (campaign seq fixed at approval; earlier campaigns keep the old value)",
            "targets": asdict(targets), "mode": mode, "motivation": motivation, "references": list(references),
            "evidence": str(rep_rel), "study": meta,
            "table": [{k: (list(v) if isinstance(v, tuple) else v) for k, v in r.items()} for r in st.rows],
            "rationale": "synthetic calibration only (anti-goalpost); human approval required"}, root)
        st.status = "proposed"
    st.report_path = str(rep_rel)
    out = root / rep_rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report_markdown(st, meta), encoding="utf-8")
    return st


def report_markdown(st: CriterionStudy, meta: dict) -> str:
    t = st.targets
    L = [f"# 증거 기준 합성 보정 (class ②) — `{st.key}`", "",
         f"생성 {st.created} · 모드 `{st.mode}` · 코드 `src/btc_quant/params/criteria.py` (calibration_study 시장 재사용)", "",
         f"- 현재 값: `{st.current}` (config/gates.yaml `{st.dotted}`)",
         f"- 목표: 오탐률 ≤ {t.max_fpr:.0%} (귀무 시장에서 validated 후보 ≥ 1), "
         f"검정력 ≥ {t.min_power:.0%} (참 연 Sharpe {t.power_sharpe:g} 의 심은 스펙 S* validated)"
         + (f", 하락 Val 귀무 오탐률 ≤ {t.max_fpr_bearish_val:.0%}" if t.max_fpr_bearish_val is not None else "")
         + (f", 현재 값 대비 검정력 +{t.min_power_gain:.0%}p 이상" if t.min_power_gain is not None else ""),
         f"- 합성 연구: `{meta.get('study', 'standard')}`"
         + (" — 국면 전환 시장(상승·하락·횡보 수개월씩, Val 국면 seed%3 순환), 심은 시장 = 국면 필터 엣지 S*_f "
            f"(설계 {meta.get('regime_design')})" if meta.get("study") == "regime" else ""),
         f"- 합성 설정: {meta.get('layout')}, 격자 {meta.get('grid')}, G2 귀무 {meta.get('n_null_runs')}회, "
         f"top_k {meta.get('top_k')}, k {meta.get('k_for')} ({meta.get('k_source', 'quick pilot')}; 조건 "
         f"{meta.get('k_conditions', '-')}). 후보 값마다 같은 seed(공통 난수).",
         f"- 실행: {meta.get('runner', 'default')}; 시장 수 {meta.get('markets', '-')}",
         "- 실제 캠페인·후보 결과는 쓰지 않았다(anti-goalpost 검사 통과). 승인 시 **다음 캠페인부터** 적용.", "",
         "| 값 | 귀무 시장 | 오탐률 [95%] | 하락 Val 귀무 | 하락 Val 오탐률 [95%] | 심은 시장 | 검정력 [95%] | "
         "S* Dev 생존 | 목표 충족 |",
         "|---|---|---|---|---|---|---|---|---|"]
    cur_row = next((r for r in st.rows if r["value"] == st.current), None)
    for r in st.rows:
        ok = meets_targets(r, t, cur_row)
        fb = r.get("fpr_bear_val", float("nan"))
        fbci = r.get("fpr_bear_val_ci") or (float("nan"), float("nan"))
        L.append(f"| `{r['value']}`{' (현재)' if r['value'] == st.current else ''} | {r['n_null']} | "
                 f"{r['fpr']:.0%} [{r['fpr_ci'][0]:.0%}–{r['fpr_ci'][1]:.0%}] | {r.get('n_null_bear_val', 0)} | "
                 + (f"{fb:.0%} [{fbci[0]:.0%}–{fbci[1]:.0%}]" if fb == fb else "–") + f" | {r['n_planted']} | "
                 f"{r['power']:.0%} [{r['power_ci'][0]:.0%}–{r['power_ci'][1]:.0%}] | {r['dev_survival']:.0%} | "
                 f"{'예' if ok else '아니오'} |")
    L += ["", f"결과: **{st.status}**" + (f" — 추천 값 `{st.recommended}`" if st.recommended is not None else ""), ""]
    if st.proposal_id:
        L += [f"제안 `{st.proposal_id}` — 사람 승인: `python -m btc_quant.params approve {st.proposal_id}`", ""]
    L += ["주의: 시장 수가 적으면 구간이 넓다. 오탐률 점추정 0% 라도 Wilson 상한이 목표보다 클 수 있다.", ""]
    return "\n".join(L)
