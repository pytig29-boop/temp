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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ._io import repo_root, today, utcnow
from .registry import load_registry

REPORTS_REL = Path("research") / "reports"
ALLOWED_REF_PREFIXES = ("synthetic:", "docs/", "literature:", "research/reports/pipeline_calibration")
LEDGER_ID_RE = re.compile(r"\b(camp|run|hyp|val|lbx|fwd|trn)-\d{3,}\b", re.I)
REAL_PATH_RE = re.compile(r"(research/(campaigns|ledger|lessons|lockbox_approvals)|strategies/(active|review|retired|"
                          r"candidates)|runtime/paper)", re.I)
# pipeline_calibration 에서 측정한 full 레이아웃 k (재보정 비용 절약; mode=bounded 기본값)
FULL_K_FOR = {0.5: 0.0239, 1.0: 0.0257, 2.0: 0.0292, 3.0: 0.0327}


class GoalpostError(ValueError):
    """실제 캠페인·후보 결과를 근거로 한 기준 변경 시도 (거부)."""


@dataclass
class Targets:
    max_fpr: float = 0.05
    min_power: float = 0.70
    power_sharpe: float = 2.0


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
def build_tasks(dotted: str, values: list, mode: str, power_sharpe: float, n_null_markets: int,
                n_planted_markets: int, k_for: dict | None = None, seed0: int = 9000,
                n_null_runs: int | None = None, grid: str = "g12") -> tuple[list[dict], dict]:
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
        if k_for is None:
            if power_sharpe not in FULL_K_FOR:
                raise ValueError(f"no stored k for Sharpe {power_sharpe}; pass k_for "
                                 f"(calibration_study.pilot_edge_map) — known {sorted(FULL_K_FOR)}")
            k_for = {power_sharpe: FULL_K_FOR[power_sharpe]}
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
    return tasks, {"layout": asdict(layout), "n_null_runs": n_null, "top_k": top_k, "k_for": k_for, "grid": grid}


def default_runner(tasks: list[dict], jobs: int = 1, progress: Callable = print) -> list[dict]:
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    from btc_quant.research.calibration_study import run_market
    out = []
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
        rows.append({"value": v, "n_null": len(nul), "fpr": fp / len(nul) if nul else float("nan"),
                     "fpr_ci": wilson(fp, len(nul)), "n_planted": len(pla),
                     "power": pw / len(pla) if pla else float("nan"), "power_ci": wilson(pw, len(pla)),
                     "dev_survival": (sum(bool(r["star_dev_passed"]) for r in pla) / len(pla)) if pla else float("nan")})
    return rows


def recommend(rows: list[dict], targets: Targets, current: Any) -> Any:
    ok = [r for r in rows if r["n_null"] and r["n_planted"] and r["fpr"] <= targets.max_fpr
          and r["power"] >= targets.min_power]
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
                      progress: Callable = print) -> CriterionStudy:
    root = repo_root(root)
    check_anti_goalpost(key, motivation, references, root)           # 먼저: 거부면 아무것도 돌리지 않음
    reg = load_registry(root, validate_=False)
    p = reg.get(key)
    dotted = p.source.split("#", 1)[1]
    current = reg.resolve(p)
    values = list(dict.fromkeys([current, *candidates]))
    st = CriterionStudy(key, dotted, current, values, targets, mode)
    tasks, meta = build_tasks(dotted, values, mode, targets.power_sharpe, n_null_markets, n_planted_markets,
                              k_for=k_for, n_null_runs=n_null_runs)
    progress(f"[criteria] {key}: {len(values)} values × {n_null_markets + n_planted_markets} markets "
             f"= {len(tasks)} runs ({mode})")
    runs = (runner or (lambda t: default_runner(t, jobs, progress)))(tasks)
    st.rows = summarize(runs, values)
    st.recommended = recommend(st.rows, targets, current)
    rep_rel = REPORTS_REL / f"criteria_{re.sub(r'[^A-Za-z0-9]+', '_', key)}_{today().replace('-', '')}.md"
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
         f"검정력 ≥ {t.min_power:.0%} (참 연 Sharpe {t.power_sharpe:g} 의 심은 스펙 S* validated)",
         f"- 합성 설정: {meta.get('layout')}, 격자 {meta.get('grid')}, G2 귀무 {meta.get('n_null_runs')}회, "
         f"top_k {meta.get('top_k')}, k {meta.get('k_for')}. 후보 값마다 같은 seed(공통 난수).",
         "- 실제 캠페인·후보 결과는 쓰지 않았다(anti-goalpost 검사 통과). 승인 시 **다음 캠페인부터** 적용.", "",
         "| 값 | 귀무 시장 | 오탐률 [95%] | 심은 시장 | 검정력 [95%] | S* Dev 생존 | 목표 충족 |",
         "|---|---|---|---|---|---|---|"]
    for r in st.rows:
        ok = r["fpr"] <= t.max_fpr and r["power"] >= t.min_power
        L.append(f"| `{r['value']}`{' (현재)' if r['value'] == st.current else ''} | {r['n_null']} | "
                 f"{r['fpr']:.0%} [{r['fpr_ci'][0]:.0%}–{r['fpr_ci'][1]:.0%}] | {r['n_planted']} | "
                 f"{r['power']:.0%} [{r['power_ci'][0]:.0%}–{r['power_ci'][1]:.0%}] | {r['dev_survival']:.0%} | "
                 f"{'예' if ok else '아니오'} |")
    L += ["", f"결과: **{st.status}**" + (f" — 추천 값 `{st.recommended}`" if st.recommended is not None else ""), ""]
    if st.proposal_id:
        L += [f"제안 `{st.proposal_id}` — 사람 승인: `python -m btc_quant.params approve {st.proposal_id}`", ""]
    L += ["주의: 시장 수가 적으면 구간이 넓다. 오탐률 점추정 0% 라도 Wilson 상한이 목표보다 클 수 있다.", ""]
    return "\n".join(L)
