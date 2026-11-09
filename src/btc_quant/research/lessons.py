"""오답노트 (lessons notebook, PLAN §10b-9).

목적: 실패(와 성공)에서 배운 것을 **다음 가설의 입력**으로 남기되, 노트 자체가 새로운 편향·누수 경로가
되지 않게 한다. 비판적 검토에서 나온 설계 요구를 그대로 구현한다.

1. **사실(fact)과 해석(interpretation)의 분리**
   - 사실: 파이프라인/관문 결과(pipeline.json)에서 *자동* 추출 — 캠페인·가설·계열, 깔때기 수, 실패 관문,
     핵심 수치, 자동 실패 유형. AI 가 쓰거나 고칠 API 가 없다(append-only + 내용 해시, `verify()`).
   - 해석: AI 가 쓴 "왜 실패했나". 항상 `status: unverified` 로 생성, 신뢰도·인용 근거 필수.
     `mark()` 로 verified / refuted 가 되려면 **후속 캠페인 id**(사실이 기록된)가 필요하다.
2. **자동 실패 분류** — 관문·견고성 결과 → 고정 코드 (`TAXONOMY`, `GATE_TAXONOMY`, `ROBUSTNESS_TAXONOMY`).
   LLM 문장이 아니라 결정적 규칙. 모르는 결과는 UNCLASSIFIED.
3. **파생 가설은 시도 비용을 물려받는다** — `derive_hypothesis()` 는 부모 계열(family)을 그대로 쓰게
   강제한다(Ledger.trials_count(family) 누적 → DSR). 새 계열 이름은 사람의 명시적 override 만
   (연구 모드 `BTCQ_RESEARCH_MODE=1` 에서는 override 거부).
4. **Val/잠금 정보는 결과 코드만** — 기록기(`sanitize`, `check_text`)가 세부 필드를 제거(자동 사실)
   하거나 거부(AI 입력)한다. 허용: `val_outcome`, `lockbox_outcome`, `val_aggregate`(정수 집계).
5. **범위·만료** — 사실마다 scope(데이터 기간·블록·거래소·시간축)와 review_after·reexamine_if.
   교훈이 영구 금지 규칙이 되지 않게 한다.
6. **과정 사후분석(postmortem)** — 버그·데이터·절차 위반을 별도 레코드로(근본 원인·재발 방지).
   `seed_postmortems()` 가 지금까지의 실제 사건 4건을 기록한다.
7. **중복·반복 방지** — `find_similar()` : 같은 블록 + 겹치는 격자 → exact, 텍스트 유사 → fuzzy.

저장소: research/lessons/ 의 JSONL (facts, interpretations, marks, postmortems, derivations),
research/lessons/.lock 에 대한 fcntl 배타 잠금 (원장과 같은 방식). LESSONS.md 는 `render_markdown()` 이
생성한다(직접 편집 금지 — 연구 모드 guard hook 이 이 디렉터리의 JSONL·LESSONS.md 쓰기를 막는다).

통합: 파이프라인 끝에서 `record_facts_from_pipeline(out, root=…)` → `render_markdown()`.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from btc_quant.registry.ledger import Ledger, _clean, repo_root, utcnow

# ============================================================== 실패 유형
TAXONOMY: dict[str, str] = {
    "NO_SIGNAL": "진입 신호에 정보가 없음 — 신호 선별 실패 또는 G2(양의 가중 Sharpe·무작위 진입 귀무 상위 5%) 실패",
    "COST_EATEN": "비용 전 수익은 양(+)인데 비용 후 음(−) — 비용 바닥을 못 넘음 (비용 +50% 견고성 실패, 업비트 비용 G2/G4 실패 포함)",
    "LOW_SAMPLE": "G1 표본 부족 (거래 수 / 최근 12개월 거래 수)",
    "PARAM_SPIKE": "G3 고원 실패 — 선택 조합만 튀고 인접 조합은 약함",
    "UNSTABLE": "G4 안정성 실패 — 롤링 6개월 양수 비율 또는 최근 12개월 Sharpe",
    "REGIME_DEPENDENT": "특정 국면·연도·시간대·거래소에서만 작동 (견고성: 연도 제외, 국면별, 거래소 재현)",
    "OUTLIER_DEPENDENT": "소수 거래·소수 일자에 의존 (견고성: 상위 거래 제거)",
    "LATENCY_SENSITIVE": "1봉 지연에 무너짐 (견고성: 진입 지연)",
    "SELECTION_BIAS": "G5 선택편향 — DSR < 0.95 또는 PBO ≥ 0.3",
    "VAL_FAIL": "Dev 는 통과했지만 G6(Val) 실패 — 결과 코드만 기록",
    "IMPL_OR_DATA_ISSUE": "구현·데이터 문제 — G0 실패(검증·인과성·live_ready), 엔진 불일치, 파이프라인 오류",
    "UNCLASSIFIED": "규칙으로 분류되지 않은 결과",
}
GATE_ORDER = ("G0", "G1", "G2", "G3", "G4", "G5", "G6")
GATE_TAXONOMY: dict[str, str] = {
    "G0": "IMPL_OR_DATA_ISSUE",
    "G1": "LOW_SAMPLE",
    "G2": "NO_SIGNAL",            # 비용 전 수익 양(+)·비용 후 음(−)이면 COST_EATEN
    "G3": "PARAM_SPIKE",
    "G4": "UNSTABLE",
    "G5": "SELECTION_BIAS",
    "G6": "VAL_FAIL",
}
# 견고성 검사 이름(소문자) → 코드. 위에서부터 첫 일치. 일치 없음 → UNCLASSIFIED.
ROBUSTNESS_TAXONOMY: list[tuple[str, str]] = [
    (r"top.?trade|outlier|drop.?top|top.?day|concentration", "OUTLIER_DEPENDENT"),
    (r"delay|lag|latency|shift", "LATENCY_SENSITIVE"),
    (r"cost|fee|slippage|spread", "COST_EATEN"),
    (r"year|regime|vol.?tercile|session|venue|exchange|replicat|cross", "REGIME_DEPENDENT"),
    (r"lookahead|causal|engine|reference|data.?quality|integrity", "IMPL_OR_DATA_ISSUE"),
    (r"plateau|neighbo|param", "PARAM_SPIKE"),
    (r"rolling|stabil", "UNSTABLE"),
    (r"alpha|beta", "NO_SIGNAL"),     # 수익이 BTC 베타로 설명됨 = 진입 정보 없음
]
VAL_OUTCOMES = {"pass", "fail", "not_run", "engine_mismatch", "budget_exhausted"}
LOCKBOX_OUTCOMES = {"not_accessed", "pending", "pass", "fail"}
CONFIDENCE_LEVELS = ("low", "medium", "high")
MARK_STATUSES = ("verified", "refuted")
POSTMORTEM_CATEGORIES = ("bug", "data", "procedure", "design")
DEFAULT_REVIEW_DAYS = 180

# 주간 시도 예산 (자문용; docs/AI_RESEARCH.md §7, research-campaign skill)
GLOBAL_WEEKLY_TRIALS = 2000
FAMILY_WEEKLY_TRIALS = 600
NEW_FAMILY_WEEKLY_TRIALS = 300
PROMISING_FAMILY_WEEKLY_TRIALS = 900
FREEZE_CODES = {"NO_SIGNAL", "COST_EATEN"}

TABLES = {"facts": "fact", "interpretations": "int", "marks": "mrk", "postmortems": "pm",
          "derivations": "drv"}
DERIVED_TAG_RE = re.compile(r"\[derived-from (drv-\d{6})")


class LessonsError(RuntimeError):
    pass


class FamilyInheritanceError(LessonsError):
    pass


class RestrictedInfoError(LessonsError):
    """Val/잠금 세부 정보를 노트에 쓰려 함."""


class ImmutableFactError(LessonsError):
    pass


# ============================================================== Val/잠금 정보 차단
_ALLOWED_RESTRICTED_KEYS = {"val_outcome", "lockbox_outcome", "val_aggregate", "lockbox_aggregate"}
_FORBIDDEN_KEY_RE = re.compile(
    r"^(g6|g7|val|validation|lockbox|grade_checks|risk_limits)$|(^|_)(val|lockbox|g6|g7)(_|$)", re.I)
_VAL_WORDS = r"(?:\bval(?:idation)?\b|검증\s*구간|\bG6\b|\bG7\b|lock\s*-?box|잠금\s*구간)"
_METRIC_WORDS = (r"(?:sharpe|샤프|returns?|수익률?|mdd|draw\s*down|낙폭|pnl|손익|p[-_ ]?value|win\s*rate|승률|"
                 r"trades?|거래\s*수|cagr|profit|dsr|pbo)")
_LOCKBOX_DATE = r"2026-(?:0[5-9]|1[01])"
_TEXT_RES = [
    re.compile(_VAL_WORDS + r"[^\n]{0,40}?" + _METRIC_WORDS + r"[^\n\d]{0,15}[-+]?\d", re.I),
    re.compile(_METRIC_WORDS + r"[^\n]{0,40}?" + _VAL_WORDS + r"[^\n\d]{0,15}[-+]?\d", re.I),
    re.compile(_LOCKBOX_DATE + r"[^\n]{0,40}?" + _METRIC_WORDS + r"[^\n\d]{0,15}[-+]?\d", re.I),
]
REDACTED = "[redacted: Val/lockbox detail]"


def restricted_text(text: str) -> str | None:
    """Val/잠금 구간의 수치 세부를 담은 문장이면 일치 부분, 아니면 None (휴리스틱)."""
    if not isinstance(text, str):
        return None
    for rx in _TEXT_RES:
        m = rx.search(text)
        if m:
            return m.group(0)
    return None


def check_text(text: str, where: str = "text") -> None:
    hit = restricted_text(text)
    if hit:
        raise RestrictedInfoError(
            f"{where}: Val/lockbox details are not allowed in the lessons notebook (outcome codes only): "
            f"{hit!r}")


def sanitize(obj: Any, mode: str = "strip", path: str = "") -> Any:
    """Val/잠금 세부 제거(strip) 또는 거부(refuse). 허용 키: val_outcome·lockbox_outcome(코드),
    val_aggregate·lockbox_aggregate(정수·불리언만)."""
    if mode not in ("strip", "refuse"):
        raise ValueError(mode)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            p = f"{path}.{ks}" if path else ks
            if ks in _ALLOWED_RESTRICTED_KEYS:
                out[ks] = _restricted_value(ks, v, mode, p)
                continue
            if _FORBIDDEN_KEY_RE.search(ks):
                if mode == "refuse":
                    raise RestrictedInfoError(f"field {p!r} carries Val/lockbox details; record only "
                                              "val_outcome / lockbox_outcome codes")
                continue
            out[ks] = sanitize(v, mode, p)
        return out
    if isinstance(obj, (list, tuple)):
        return [sanitize(v, mode, f"{path}[{i}]") for i, v in enumerate(obj)]
    if isinstance(obj, str) and restricted_text(obj):
        if mode == "refuse":
            check_text(obj, path or "text")
        return REDACTED
    return obj


def _restricted_value(key: str, v: Any, mode: str, path: str) -> Any:
    ok = True
    if key == "val_outcome":
        ok = v is None or v in VAL_OUTCOMES
    elif key == "lockbox_outcome":
        ok = v is None or v in LOCKBOX_OUTCOMES
    else:   # *_aggregate: {str: int|bool}
        ok = isinstance(v, dict) and all(isinstance(x, (int, bool)) and not isinstance(x, float)
                                         for x in v.values())
    if ok:
        return copy.deepcopy(v)
    if mode == "refuse":
        raise RestrictedInfoError(f"{path}: only outcome codes {sorted(VAL_OUTCOMES | LOCKBOX_OUTCOMES)} "
                                  "or integer aggregates are allowed")
    return None


# ============================================================== 분류
def _gate_passed(g) -> bool | None:
    if not isinstance(g, dict) or g.get("passed") is None:
        return None             # 없음 / 판정 불가(skipped)
    return bool(g["passed"])


def _robustness_items(rob) -> list[tuple[str, bool | None, dict]]:
    """견고성 결과 정규화: dict{name: {passed…}} | {"checks": [...]} | list[{name, passed}]."""
    if not rob:
        return []
    if isinstance(rob, dict) and isinstance(rob.get("checks"), (list, dict)):
        rob = rob["checks"]       # btc_quant.research.robustness.run_suite 형식
    items = []
    if isinstance(rob, dict):
        for name, v in rob.items():
            if isinstance(v, dict):
                items.append((str(name), _gate_passed(v), v))
            elif isinstance(v, bool):
                items.append((str(name), v, {}))
    elif isinstance(rob, list):
        for v in rob:
            if isinstance(v, dict) and "name" in v:
                items.append((str(v["name"]), _gate_passed(v), v))
    return items


def robustness_code(name: str) -> str:
    n = name.lower()
    for pat, code in ROBUSTNESS_TAXONOMY:
        if re.search(pat, n):
            return code
    return "UNCLASSIFIED"


def _gross_positive(c: dict) -> bool:
    """비용 전 수익이 양(+)이라는 증거가 있나 (지표 키 또는 gross/zero-cost 견고성 결과)."""
    m = c.get("metrics") or {}
    for k in ("sharpe_w_gross", "gross_sharpe_w", "gross_sharpe", "gross_mean_bp", "mean_gross_bp"):
        v = m.get(k, c.get(k))
        if isinstance(v, (int, float)) and v == v:
            return v > 0
    for name, passed, d in _robustness_items(c.get("robustness")):
        if re.search(r"gross|zero.?cost|no.?cost", name.lower()):
            for k in ("sharpe_w", "sharpe", "mean_bp"):
                if isinstance(d.get(k), (int, float)):
                    return d[k] > 0
            if passed is not None:
                return passed
    return False


def classify_candidate(c: dict) -> dict:
    """후보 하나의 관문·견고성 결과 → {primary, codes, reasons}. 결정적 규칙."""
    gates = c.get("gates") or {}
    codes: list[str] = []
    reasons: dict[str, list[str]] = {}

    def add(code: str, why: str):
        if code not in codes:
            codes.append(code)
        reasons.setdefault(code, []).append(why)

    for g in GATE_ORDER:
        passed = _gate_passed(gates.get(g))
        if passed is None or passed:
            continue
        code = GATE_TAXONOMY[g]
        d = gates[g]
        if g == "G2":
            sr = d.get("sharpe_w")
            if d.get("ok_positive") is False and _gross_positive(c):
                code = "COST_EATEN"
            add(code, f"G2 failed (sharpe_w={sr}, p_value={d.get('p_value')})")
        elif g == "G6":
            if d.get("val_status") == "exhausted" or (d.get("val_budget") or {}).get("exhausted"):
                add("UNCLASSIFIED", "Val budget exhausted — not evaluated (roll Val: human)")
                continue
            if d.get("reference_engine_match") is False:
                add("IMPL_OR_DATA_ISSUE", "fast/reference engine mismatch on Val")
            add("VAL_FAIL", "G6 failed (details withheld: outcome code only)")
        else:
            add(code, f"{g} failed")
    for name, passed, _ in _robustness_items(c.get("robustness")):
        if passed is False:
            add(robustness_code(name), f"robustness '{name}' failed")
    gc = c.get("grade_checks") or {}
    for g in ("G2", "G4"):        # Dev 부분만 (G6 grade 는 Val)
        if _gate_passed(gc.get(g)) is False:
            add("COST_EATEN", f"{g} fails under the grade (Upbit) cost model → cost grade B")
    first = c.get("first_failed")
    if first in GATE_TAXONOMY and first != "G2":
        primary = GATE_TAXONOMY[first]
    elif first == "G2":
        primary = "COST_EATEN" if "COST_EATEN" in codes and "NO_SIGNAL" not in codes else "NO_SIGNAL"
    elif "VAL_FAIL" in codes:
        primary = "VAL_FAIL"
    elif any("Val budget exhausted" in r for r in reasons.get("UNCLASSIFIED", [])):
        primary = "UNCLASSIFIED"
    elif codes and c.get("dev_passed") is False:
        primary = codes[0]
    else:
        primary = None    # Dev/Val 통과 (실패 아님) — 견고성 경고는 codes 에 남는다
    if c.get("dev_passed") is False and primary is None:
        primary = "UNCLASSIFIED"
        add("UNCLASSIFIED", "dev_passed is False but no failed gate recognised")
    if str(c.get("status") or "").startswith("failed") and primary is None:
        primary = "UNCLASSIFIED"
        add("UNCLASSIFIED", f"status {c.get('status')!r} not recognised")
    return {"primary": primary, "codes": codes, "reasons": reasons}


def classify_pipeline(p: dict, candidate_tax: list[dict] | None = None) -> dict:
    """캠페인 수준 분류: {primary, counts, outcome}."""
    cands = p.get("candidates") or []
    tax = candidate_tax if candidate_tax is not None else [classify_candidate(c) for c in cands]
    counts = Counter(t["primary"] for t in tax if t["primary"])
    n_val = int(p.get("n_validated") or 0)
    if p.get("error"):
        return {"primary": "IMPL_OR_DATA_ISSUE", "counts": dict(counts), "outcome": "error"}
    ss = p.get("signal_study")
    if isinstance(ss, dict) and ss.get("passed") is False:
        return {"primary": "NO_SIGNAL", "counts": dict(counts), "outcome": "failed"}
    if n_val > 0:
        return {"primary": None, "counts": dict(counts), "outcome": "validated"}
    fa = p.get("funnel_all") or {}
    if fa.get("n_specs") and fa.get("sharpe_w_positive") == 0:
        prim = "COST_EATEN" if any(_gross_positive(c) for c in cands) else "NO_SIGNAL"
        return {"primary": prim, "counts": dict(counts), "outcome": "failed"}
    if not counts:
        return {"primary": "UNCLASSIFIED", "counts": {}, "outcome": "failed"}
    order = {code: i for i, code in enumerate(GATE_TAXONOMY[g] for g in GATE_ORDER)}
    prim = sorted(counts, key=lambda k: (-counts[k], order.get(k, 99), k))[0]
    return {"primary": prim, "counts": dict(counts), "outcome": "failed"}


# ============================================================== 사실 추출
_GATE_KEYS = {
    "G0": ("validate", "n_filters", "n_params", "not_live_ready", "requires_aux", "causality_cuts"),
    "G1": ("n_trades", "recent_n_trades"),
    "G2": ("sharpe_w", "p_value", "percentile", "n_null", "calibration", "candidate_n_trades",
           "n_trades_median", "sharpe_w_median", "sharpe_w_p95", "ok_positive", "ok_null", "cost"),
    "G3": ("selected", "neighbor_median", "required", "n_neighbors"),
    "G4": ("rolling_frac_positive", "rolling_n_windows", "recent_sharpe", "ok_rolling", "ok_recent"),
    "G5": ("dsr", "pbo", "sr", "sr0", "n_trials", "ok_dsr", "ok_pbo"),
}
_METRIC_KEYS = ("sharpe_w", "sharpe", "n_trades", "recent_n_trades", "recent_sharpe",
                "rolling_frac_positive", "max_dd", "total_return", "trades_per_day", "n_days",
                "sharpe_w_gross", "gross_sharpe_w")
_STATS_KEYS = ("n_campaign_specs", "k_eff_clusters", "n_family_trials", "n_global_trials", "n_trials_dsr",
               "pbo", "cluster_threshold")


def _scalar_subset(d: dict, keys: Iterable[str]) -> dict:
    out = {}
    for k in keys:
        if k in (d or {}):
            v = d[k]
            if k == "causality_violations":
                v = len(v or [])
            if isinstance(v, (str, int, float, bool)) or v is None:
                out[k] = v
            elif isinstance(v, list) and len(v) <= 10 and all(isinstance(x, (str, int, float)) for x in v):
                out[k] = list(v)
    return out


def _candidate_fact(c: dict, tax: dict) -> dict:
    gates = c.get("gates") or {}
    spec = c.get("spec") or {}
    g6 = gates.get("G6")
    if not isinstance(g6, dict):
        val_outcome = "not_run"
    elif g6.get("val_status") == "exhausted" or (g6.get("val_budget") or {}).get("exhausted"):
        val_outcome = "budget_exhausted"
    elif g6.get("reference_engine_match") is False:
        val_outcome = "engine_mismatch"
    else:
        val_outcome = "pass" if g6.get("passed") else "fail"
    g_fact = {}
    for g in ("G0", "G1", "G2", "G3", "G4", "G5"):
        if isinstance(gates.get(g), dict):
            d = gates[g]
            g_fact[g] = {"passed": bool(d.get("passed")), **_scalar_subset(d, _GATE_KEYS[g])}
            if g == "G0":
                g_fact[g]["causality_violations"] = len(d.get("causality_violations") or [])
    abl = c.get("ablation") or {}
    abl_fact = None
    if abl:
        abl_fact = {"filters": [{"filter": r.get("filter"), "removable": r.get("removable"),
                                 "delta": r.get("delta"), "se": r.get("se")} for r in abl.get("filters", [])],
                    "simplified_logic_hash": (abl.get("simplified") or {}).get("logic_hash")}
    rob = {name: passed for name, passed, _ in _robustness_items(c.get("robustness"))}
    return {
        "rank": c.get("rank"), "spec_id": c.get("spec_id"), "logic_hash": c.get("logic_hash"),
        "entry": spec.get("entry"), "filters": spec.get("filters"), "exit": spec.get("exit"),
        "timeframe": spec.get("timeframe"),
        "metrics_dev": _scalar_subset(c.get("metrics") or {}, _METRIC_KEYS),
        "gates_dev": g_fact, "first_failed": c.get("first_failed"), "dev_passed": c.get("dev_passed"),
        "ablation": abl_fact, "robustness_dev": rob or None,
        "val_outcome": val_outcome, "grade": c.get("grade"),
        "taxonomy": {"primary": tax["primary"], "codes": tax["codes"]},
        "taxonomy_reasons": tax["reasons"],
    }


def _blocks_of_grid(grid: dict) -> dict:
    entry = (grid.get("entry") or {}).get("block")
    fsets = [[f.get("block") for f in fs] for fs in (grid.get("filters") or [])]
    return {"entry_block": entry, "filter_blocks": fsets}


# ============================================================== 텍스트 유사도
_STOP = {"the", "a", "an", "of", "on", "in", "for", "and", "or", "to", "is", "are", "with", "after", "at",
         "by", "btc", "krw", "btc-krw", "후", "및", "에서", "있다", "동안", "the", "that", "this"}


def _tokens(text: str) -> set[str]:
    toks = set()
    for w in re.findall(r"[0-9a-zA-Z_]+|[가-힣]+", (text or "").lower()):
        if w in _STOP or len(w) < 2:
            continue
        toks.add(w)
        if re.fullmatch(r"[가-힣]+", w) and len(w) > 2:
            toks.update(w[i:i + 2] for i in range(len(w) - 1))
    return toks


def text_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _norm_vals(vals) -> set:
    if not isinstance(vals, (list, tuple)):
        vals = [vals]
    out = set()
    for v in vals:
        out.add(repr(float(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else repr(v))
    return out


def grids_overlap(a: dict, b: dict) -> bool:
    """공통 파라미터마다 값 집합이 겹치면 True (공통 파라미터가 없으면 True — 블록 기본 격자)."""
    a, b = a or {}, b or {}
    return all(_norm_vals(a[k]) & _norm_vals(b[k]) for k in set(a) & set(b))


def _exits_overlap(a: dict, b: dict) -> bool:
    a, b = a or {}, b or {}
    shared = set(a) & set(b)
    if not shared:
        return not a and not b
    return all(_norm_vals(a[k]) & _norm_vals(b[k]) for k in shared)


def _fset_key(fs: list) -> tuple:
    return tuple(sorted(f.get("block") for f in fs or []))


def compare_grids(a: dict, b: dict) -> str | None:
    """두 캠페인 격자(CampaignTemplate.to_record 형식) 비교 → 'exact' | 'same_blocks' | 'same_entry' | None."""
    ea, eb = (a.get("entry") or {}), (b.get("entry") or {})
    if not ea.get("block") or ea.get("block") != eb.get("block"):
        return None
    fa = {_fset_key(fs): fs for fs in (a.get("filters") or [[]])}
    fb = {_fset_key(fs): fs for fs in (b.get("filters") or [[]])}
    common = set(fa) & set(fb)
    if not common:
        return "same_entry"
    entry_ok = grids_overlap(ea.get("grid"), eb.get("grid"))
    exits_ok = _exits_overlap(a.get("exits"), b.get("exits"))
    for key in common:
        ga = {f["block"]: f.get("grid") for f in fa[key]}
        gb = {f["block"]: f.get("grid") for f in fb[key]}
        if entry_ok and exits_ok and all(grids_overlap(ga[k], gb[k]) for k in ga):
            return "exact"
    return "same_blocks"


def _as_grid_record(template) -> dict | None:
    if template is None:
        return None
    if hasattr(template, "to_record"):
        return template.to_record()
    if isinstance(template, (str, Path)):
        import yaml
        template = yaml.safe_load(Path(template).read_text(encoding="utf-8"))
    if isinstance(template, dict):
        d = dict(template)
        if "family" not in d or not d.get("family"):
            d["family"] = (d.get("entry") or {}).get("block")
        return d
    raise TypeError(f"unsupported template {type(template)}")


# ============================================================== 저장소
def _content_hash(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k not in ("id", "seq", "ts", "content_hash")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _today() -> date:
    return datetime.now(timezone.utc).date()


class LessonBook:
    """오답노트 저장소. `LessonBook.at(repo_root)` 또는 `LessonBook()`(저장소 기본 경로)."""

    def __init__(self, root: str | Path | None = None, ledger: Ledger | None = None,
                 campaigns_dir: str | Path | None = None):
        base = repo_root()
        self.root = Path(root) if root is not None else base / "research" / "lessons"
        self.root.mkdir(parents=True, exist_ok=True)
        self._ledger = ledger
        self.campaigns_dir = (Path(campaigns_dir) if campaigns_dir is not None
                              else self.root.parent / "campaigns")

    @classmethod
    def at(cls, root: str | Path | None = None) -> "LessonBook":
        r = Path(root) if root is not None else repo_root()
        return cls(r / "research" / "lessons", Ledger(r / "research" / "ledger"), r / "research" / "campaigns")

    @property
    def ledger(self) -> Ledger:
        if self._ledger is None:
            self._ledger = Ledger(self.root.parent / "ledger")
        return self._ledger

    # ---------------------------------------------------------- 저수준 (append-only)
    def path(self, table: str) -> Path:
        if table not in TABLES:
            raise KeyError(table)
        return self.root / f"{table}.jsonl"

    @contextmanager
    def lock(self) -> Iterator[None]:
        with open(self.root / ".lock", "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _read(self, table: str) -> list[dict]:
        p = self.path(table)
        if not p.exists():
            return []
        with open(p, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def records(self, table: str) -> list[dict]:
        """레코드 사본 (수정해도 저장소에는 영향 없음)."""
        return copy.deepcopy(self._read(table))

    def get(self, id_: str) -> dict | None:
        prefix = id_.split("-", 1)[0]
        for t, pfx in TABLES.items():
            if pfx == prefix:
                for r in self._read(t):
                    if r["id"] == id_:
                        return copy.deepcopy(r)
        return None

    def _next_id(self, table: str) -> tuple[str, int]:
        seq = len(self._read(table)) + 1
        return f"{TABLES[table]}-{seq:06d}", seq

    def _append_locked(self, table: str, record: dict) -> dict:
        rid, seq = self._next_id(table)
        rec = {"id": rid, "seq": seq, "ts": utcnow(), **_clean(record)}
        rec["content_hash"] = _content_hash(rec)
        line = json.dumps(rec, ensure_ascii=False, allow_nan=False)
        p = self.path(table)
        if p.exists() and p.stat().st_size > 0:
            with open(p, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    line = "\n" + line
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return copy.deepcopy(rec)

    def verify(self) -> list[str]:
        """무결성 점검: 내용 해시 불일치(직접 편집), id 순서, 캠페인당 사실 1건. 문제 목록(없으면 [])."""
        problems = []
        for t, pfx in TABLES.items():
            for i, r in enumerate(self._read(t), start=1):
                if r.get("id") != f"{pfx}-{i:06d}":
                    problems.append(f"{t}: line {i} has id {r.get('id')!r} (expected {pfx}-{i:06d})")
                if r.get("content_hash") != _content_hash(r):
                    problems.append(f"{t}: {r.get('id')} content hash mismatch (edited outside the API)")
        seen = Counter((f.get("kind"), f.get("campaign_id") or f.get("study_id"), f.get("family"))
                       for f in self._read("facts"))
        problems += [f"facts: duplicate fact for {k}" for k, n in seen.items() if n > 1]
        return problems

    # ---------------------------------------------------------- 사실
    def facts(self, campaign_id: str | None = None, family: str | None = None) -> list[dict]:
        return [f for f in self.records("facts")
                if (campaign_id is None or f.get("campaign_id") == campaign_id)
                and (family is None or f.get("family") == family)]

    def _derivation_for_hypothesis(self, hyp: dict | None) -> dict | None:
        if not hyp:
            return None
        m = DERIVED_TAG_RE.search(str(hyp.get("rationale") or ""))
        if not m:
            return None
        return self.get(m.group(1))

    def record_facts_from_pipeline(self, pipeline_json: dict | str | Path, family: str | None = None,
                                   scope: dict | None = None) -> dict:
        """pipeline.json(경로·dict) → 불변 사실 레코드 1건 (캠페인당 1건, 재호출 시 기존 레코드 반환).

        family·격자·기간·스냅샷은 원장 캠페인 레코드에서(없으면 같은 디렉터리의 manifest.json).
        Val 은 val_outcome 코드와 정수 집계만 남긴다(sanitize strip).
        """
        cdir = None
        if isinstance(pipeline_json, (str, Path)):
            pth = Path(pipeline_json)
            if pth.is_dir():
                pth = pth / "pipeline.json"
            cdir = pth.parent
            p = json.loads(pth.read_text(encoding="utf-8"))
        else:
            p = pipeline_json
        cid = p.get("campaign_id")
        if not cid:
            raise LessonsError("pipeline result has no campaign_id")
        existing = self.facts(campaign_id=cid)
        existing = [f for f in existing if f.get("kind") == "campaign"]
        if existing:
            return existing[0]
        camp = self.ledger.get("campaigns", cid) or {}
        hyp_id = p.get("hypothesis_id") or camp.get("hypothesis_id")
        hyp = self.ledger.get("hypotheses", hyp_id) if hyp_id else None
        manifest = {}
        cdir = cdir or (self.campaigns_dir / cid)
        if (cdir / "manifest.json").exists():
            manifest = json.loads((cdir / "manifest.json").read_text(encoding="utf-8"))
        grid = camp.get("grid") if isinstance(camp.get("grid"), dict) and "entry" in camp.get("grid", {}) else {}
        fam = family or camp.get("family") or manifest.get("family") or grid.get("family")
        if not fam:
            raise LessonsError(f"cannot resolve family for {cid}; pass family=")
        period = camp.get("period") or manifest.get("period") or {}
        data = grid.get("data") or manifest.get("data") or {}
        cands = p.get("candidates") or []
        ctax = [classify_candidate(c) for c in cands]
        ptax = classify_pipeline(p, ctax)
        drv = self._derivation_for_hypothesis(hyp)
        if drv is None:
            family_check = "n/a"
        elif drv["family"] == fam:
            family_check = "ok"
        else:
            family_check = "violation"
        review_after = (_today() + timedelta(days=DEFAULT_REVIEW_DAYS)).isoformat()
        sc = {
            "exchange": data.get("exchange"), "symbol": data.get("symbol"), "timeframe": data.get("timeframe"),
            "segment": period.get("segment", "dev"), "period": {"start": period.get("start"),
                                                                "end": period.get("end")},
            "data_snapshot": period.get("data_snapshot") or manifest.get("data_snapshot"),
            "code_commit": p.get("code_commit") or manifest.get("code_commit"),
            "cost_primary": (grid.get("costs") or manifest.get("costs") or {}).get("primary"),
            **_blocks_of_grid(grid),
            "review_after": review_after,
            "reexamine_if": [
                "Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val)",
                "venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes)",
                "timeframe / decision interval changes (5m ↔ 1m)",
                "a block used here is re-implemented (new block version) or an engine bug is fixed",
                f"review date {review_after} passes",
            ],
            **(scope or {}),
        }
        dev_pass_counts = {g: sum(1 for c in cands if _gate_passed((c.get("gates") or {}).get(g)))
                           for g in ("G0", "G1", "G2", "G3", "G4", "G5")}
        n_val_eval = sum(1 for c in cands if isinstance((c.get("gates") or {}).get("G6"), dict))
        rec = {
            "kind": "campaign", "campaign_id": cid, "hypothesis_id": hyp_id,
            "hypothesis_text": (hyp or {}).get("text"), "family": fam,
            "derived_from": drv["id"] if drv else None, "family_check": family_check,
            "scope": sc,
            "grid": {k: grid.get(k) for k in ("entry", "filters", "exits")} if grid else None,
            "grid_size": camp.get("grid_size") or manifest.get("n_specs"),
            "funnel": {**(p.get("funnel_all") or {}), "top_k": p.get("top_k"),
                       "top_k_dev_gate_pass": dev_pass_counts,
                       "n_dev_survivors": p.get("n_dev_survivors")},
            "campaign_stats": _scalar_subset(p.get("campaign_stats") or {}, _STATS_KEYS),
            "candidates": [_candidate_fact(c, t) for c, t in zip(cands, ctax)],
            "val_aggregate": {"n_evaluated": int(n_val_eval), "n_passed": int(p.get("n_validated") or 0)},
            "lockbox_outcome": "not_accessed",
            "outcome": ptax["outcome"], "taxonomy": {"primary": ptax["primary"], "counts": ptax["counts"]},
            "signal_study": ({"passed": bool(p["signal_study"].get("passed"))}
                             if isinstance(p.get("signal_study"), dict) else None),
            "pipeline_error": str(p["error"])[:300] if p.get("error") else None,
            "source": str(cdir / "pipeline.json"),
            "author": "auto",
        }
        rec = sanitize(rec, "strip")
        with self.lock():
            again = [f for f in self._read("facts") if f.get("campaign_id") == cid and f.get("kind") == "campaign"]
            if again:
                return copy.deepcopy(again[0])
            return self._append_locked("facts", rec)

    def record_facts_from_signal_study(self, study: dict, family: str, study_id: str | None = None,
                                       hypothesis_id: str | None = None, scope: dict | None = None) -> dict:
        """신호 선별(btc_quant.research.signal_study) 결과 → 사실. passed=False → NO_SIGNAL
        (비용 전 평균 > 0 이고 비용 후 ≤ 0 이면 COST_EATEN). Dev 결과만 넣을 것."""
        sid = study_id or study.get("study_id") or study.get("id")
        if not sid:
            raise LessonsError("signal study needs a study_id")
        if "passed" not in study:
            raise LessonsError("signal study result needs 'passed'")
        for f in self._read("facts"):
            if f.get("kind") == "signal_study" and f.get("study_id") == sid:
                return copy.deepcopy(f)
        gross = next((study[k] for k in ("gross_mean_bp", "mean_gross_bp") if k in study), None)
        net = next((study[k] for k in ("net_mean_bp", "mean_net_bp") if k in study), None)
        if study["passed"]:
            primary = None
        elif isinstance(gross, (int, float)) and isinstance(net, (int, float)) and gross > 0 >= net:
            primary = "COST_EATEN"
        else:
            primary = "NO_SIGNAL"
        summary = {k: v for k, v in study.items()
                   if isinstance(v, (int, float, str, bool)) and k not in ("study_id", "id")}
        rec = sanitize({
            "kind": "signal_study", "study_id": sid, "campaign_id": study.get("campaign_id"),
            "hypothesis_id": hypothesis_id or study.get("hypothesis_id"), "family": family,
            "scope": {"review_after": (_today() + timedelta(days=DEFAULT_REVIEW_DAYS)).isoformat(),
                      "segment": "dev", **(scope or {})},
            "summary": summary, "outcome": "passed" if study["passed"] else "failed",
            "taxonomy": {"primary": primary, "counts": {primary: 1} if primary else {}},
            "lockbox_outcome": "not_accessed", "author": "auto",
        }, "strip")
        with self.lock():
            return self._append_locked("facts", rec)

    def record_facts_from_screen(self, screen, scope: dict | None = None) -> list[dict]:
        """신호 선별 캠페인(`signal_study.screen` 의 ScreenResult 또는 signal_screen.json 경로) → 계열별 사실 1건.
        구성별 결과 코드: 통과 → 없음, 보정 후 유의하지 않음 → NO_SIGNAL, 이벤트 부족 → LOW_SAMPLE,
        유의하지만 primary 비용 순평균 ≤ 0 → COST_EATEN. Dev 전용 결과."""
        if isinstance(screen, (str, Path)):
            pth = Path(screen)
            if pth.is_dir():
                pth = pth / "signal_screen.json"
            d = json.loads(pth.read_text(encoding="utf-8"))
            cid, hid, decision = d["campaign_id"], d.get("hypothesis_id"), d.get("decision") or {}
            rows = []
            for st in d.get("studies") or []:
                dec = st.get("decision") or {}
                rows.append({"label": st.get("label"), "family": st.get("family"),
                             "entry": (st.get("entry") or {}).get("block"),
                             "filters": "+".join(f.get("block") for f in st.get("filters") or []) or "-",
                             "n_events": st.get("n_events"), "p_signal": st.get("p_signal"),
                             "best_h": st.get("best_primary_horizon"), "info_h": st.get("info_horizon"),
                             "significant": dec.get("significant"), "passed": dec.get("passed"),
                             "fail_reason": dec.get("fail_reason")})
            period, snap, commit, src = d.get("period") or {}, d.get("data_snapshot"), d.get("code_commit"), str(pth)
            tpl_rec = None
        else:
            cid, hid, decision = screen.campaign_id, screen.hypothesis_id, dict(screen.decision)
            rows = screen.table.to_dict("records")
            period, snap, commit, src = {}, None, None, str(getattr(screen, "dir", "") or "")
            tpl_rec = screen.template
        camp = self.ledger.get("campaigns", cid) or {}
        period = period or camp.get("period") or {}
        out = []
        by_fam: dict[str, list] = {}
        for r in rows:
            by_fam.setdefault(r.get("family") or camp.get("family") or "?", []).append(r)
        for fam, rs in by_fam.items():
            cfgs = []
            for r in rs:
                reason = str(r.get("fail_reason") or "")
                if r.get("passed"):
                    code = None
                elif "few events" in reason:
                    code = "LOW_SAMPLE"
                elif "net mean" in reason:
                    code = "COST_EATEN"
                elif "not significant" in reason or r.get("significant") is False:
                    code = "NO_SIGNAL"
                else:
                    code = "UNCLASSIFIED"
                cfgs.append({**_scalar_subset(r, ("label", "entry", "filters", "n_events", "p_signal", "best_h",
                                                  "info_h", "excess_bp", "z", "significant", "passed",
                                                  "fail_reason", "profile")),
                             **{k: v for k, v in r.items() if k.startswith("net_bp[") and isinstance(v, (int, float))},
                             "code": code})
            counts = Counter(c["code"] for c in cfgs if c["code"])
            n_pass = sum(1 for c in cfgs if c.get("passed"))
            prim = None if n_pass else (sorted(counts, key=lambda k: (-counts[k], k))[0] if counts else "UNCLASSIFIED")
            with self.lock():
                again = [f for f in self._read("facts") if f.get("kind") == "signal_screen"
                         and f.get("campaign_id") == cid and f.get("family") == fam]
                if again:
                    out.append(copy.deepcopy(again[0]))
                    continue
                hyp = self.ledger.get("hypotheses", hid) if hid else None
                rec = sanitize({
                    "kind": "signal_screen", "campaign_id": cid, "hypothesis_id": hid,
                    "hypothesis_text": (hyp or {}).get("text"), "family": fam,
                    "scope": {"exchange": getattr(tpl_rec, "exchange", None), "timeframe": getattr(tpl_rec, "timeframe", None),
                              "bar_minutes": getattr(tpl_rec, "bar_minutes", None), "segment": "dev",
                              "period": {"start": period.get("start"), "end": period.get("end")},
                              "data_snapshot": snap or period.get("data_snapshot"), "code_commit": commit,
                              "entry_block": sorted({c.get("entry") for c in cfgs if c.get("entry")}),
                              "review_after": (_today() + timedelta(days=DEFAULT_REVIEW_DAYS)).isoformat(),
                              "reexamine_if": ["Dev data period changes", "venue/cost model changes",
                                               "decision interval changes (bar_minutes)"],
                              **(scope or {})},
                    "configs": cfgs, "decision": _scalar_subset(decision, ("method", "alpha", "m_current", "m_prior",
                                                                           "m_total", "n_passed")),
                    "funnel": {"n_configs": len(cfgs), "n_passed": n_pass},
                    "outcome": "passed" if n_pass else "failed",
                    "taxonomy": {"primary": prim, "counts": dict(counts)},
                    "lockbox_outcome": "not_accessed", "source": src, "author": "auto"}, "strip")
                out.append(self._append_locked("facts", rec))
        return out

    # ---------------------------------------------------------- 해석
    def add_interpretation(self, fact_id: str, text: str, confidence: str | float,
                           evidence: list, proposed_test: str | None = None,
                           reexamine_if: Iterable[str] = (), author: str = "ai") -> dict:
        """AI 해석 추가. 항상 status=unverified. 근거(evidence) 1개 이상 필수, Val/잠금 세부는 거부."""
        fact = self.get(fact_id)
        if fact is None or not fact_id.startswith("fact-"):
            raise LessonsError(f"unknown fact {fact_id!r} (interpretations attach to fact records)")
        if not text or not str(text).strip():
            raise ValueError("interpretation text required")
        if isinstance(confidence, str):
            if confidence not in CONFIDENCE_LEVELS:
                raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS} or a float in [0, 1]")
        elif not (isinstance(confidence, (int, float)) and 0 <= confidence <= 1):
            raise ValueError("confidence must be in [0, 1]")
        if not evidence:
            raise ValueError("cite at least one piece of evidence (fact field, report path, number)")
        payload = sanitize({"text": str(text), "evidence": list(evidence), "proposed_test": proposed_test,
                            "reexamine_if": list(reexamine_if)}, "refuse")
        with self.lock():
            return self._append_locked("interpretations", {
                "fact_id": fact_id, "campaign_id": fact.get("campaign_id"), "family": fact.get("family"),
                **payload, "confidence": confidence, "status": "unverified", "author": author})

    def mark(self, interpretation_id: str, status: str, by_campaign: str, note: str = "",
             actor: str = "ai") -> dict:
        """해석을 verified/refuted 로 표시. 근거 = 후속 캠페인(사실이 기록되어 있어야 하고, 해석보다 나중,
        해석의 원 캠페인과 달라야 함)."""
        if status not in MARK_STATUSES:
            raise ValueError(f"status must be one of {MARK_STATUSES} (interpretations start unverified)")
        it = self.get(interpretation_id)
        if it is None or not interpretation_id.startswith("int-"):
            raise LessonsError(f"unknown interpretation {interpretation_id!r} (facts cannot be marked)")
        if not by_campaign or by_campaign == it.get("campaign_id"):
            raise LessonsError("a follow-up campaign different from the interpreted one is required")
        fu = [f for f in self._read("facts") if f.get("campaign_id") == by_campaign]
        if not fu:
            raise LessonsError(f"no facts recorded for follow-up campaign {by_campaign}; run the pipeline and "
                               "record_facts_from_pipeline first")
        if all(f["ts"] < it["ts"] for f in fu):
            raise LessonsError(f"campaign {by_campaign} was recorded before the interpretation; "
                               "verification needs a follow-up campaign")
        payload = sanitize({"note": note}, "refuse")
        with self.lock():
            return self._append_locked("marks", {
                "interpretation_id": interpretation_id, "status": status, "by_campaign": by_campaign,
                "by_fact": fu[0]["id"], **payload, "actor": actor})

    def interpretation_status(self, interpretation_id: str) -> str:
        st = "unverified"
        for m in self._read("marks"):
            if m["interpretation_id"] == interpretation_id:
                st = m["status"]
        return st

    def interpretations(self, fact_id: str | None = None) -> list[dict]:
        out = []
        for r in self.records("interpretations"):
            if fact_id is None or r["fact_id"] == fact_id:
                r["current_status"] = self.interpretation_status(r["id"])
                out.append(r)
        return out

    # ---------------------------------------------------------- 파생 가설 (계열 상속)
    def parent_family(self, parent: str) -> tuple[str, dict]:
        """부모(fact-/int-/camp…/hyp-/drv-) → (family, 해석 정보)."""
        if parent.startswith(("fact-", "int-", "drv-")):
            rec = self.get(parent)
            if rec is None:
                raise LessonsError(f"unknown parent {parent!r}")
            if parent.startswith("int-"):
                rec = self.get(rec["fact_id"]) or rec
            return rec["family"], {"campaign_id": rec.get("campaign_id"), "hypothesis_id": rec.get("hypothesis_id")}
        if parent.startswith("hyp-"):
            fams = {c.get("family") for c in self.ledger.records("campaigns")
                    if c.get("hypothesis_id") == parent and c.get("family")}
            if len(fams) != 1:
                raise LessonsError(f"hypothesis {parent} maps to families {sorted(fams)}; pick a campaign/fact")
            return fams.pop(), {"hypothesis_id": parent}
        facts = [f for f in self._read("facts") if f.get("campaign_id") == parent]
        if facts:
            return facts[0]["family"], {"campaign_id": parent, "hypothesis_id": facts[0].get("hypothesis_id")}
        camp = self.ledger.get("campaigns", parent)
        if camp and camp.get("family"):
            return camp["family"], {"campaign_id": parent, "hypothesis_id": camp.get("hypothesis_id")}
        raise LessonsError(f"cannot resolve family of parent {parent!r}")

    def derive_hypothesis(self, parent: str, text: str, rationale: str, success_criteria: str,
                          family: str | None = None, human_override: bool = False, override_reason: str = "",
                          register_in_ledger: bool = False) -> dict:
        """실패 캠페인/교훈에서 파생한 가설. 부모 계열을 그대로 쓴다(시도 수 누적).

        family 를 다른 이름으로 주면 FamilyInheritanceError — 사람의 명시적 override
        (human_override=True + override_reason, 연구 모드 밖에서만)가 있어야 한다.
        반환 레코드의 `template_hypothesis`·`family` 를 캠페인 템플릿에 넣는다(`apply_derivation`).
        register_in_ledger=True 면 원장 hypotheses 에도 등록(직접 Ledger.create_campaign 경로용).
        """
        pfam, pinfo = self.parent_family(parent)
        fam = family or pfam
        override = False
        if fam != pfam:
            if not human_override:
                raise FamilyInheritanceError(
                    f"derived hypothesis must stay in the parent family {pfam!r} (got {fam!r}) so that "
                    "Ledger.trials_count(family) keeps accumulating; a new family needs an explicit human "
                    "override (human_override=True, override_reason=…) outside research mode")
            if not str(override_reason).strip():
                raise FamilyInheritanceError("human_override requires override_reason")
            if os.environ.get("BTCQ_RESEARCH_MODE") == "1":
                raise FamilyInheritanceError("family override is human-only and refused in research mode "
                                             "(BTCQ_RESEARCH_MODE=1)")
            override = True
        check_text(text, "text")
        check_text(rationale, "rationale")
        similar = self.find_similar(text=text, limit=5)
        with self.lock():
            drv_id, _ = self._next_id("derivations")
            tagged = f"{rationale} [derived-from {drv_id} ← {parent}; family {fam}]"
            hyp_id = None
            if register_in_ledger:
                hyp_id = self.ledger.register_hypothesis(text, tagged, success_criteria)
            rec = self._append_locked("derivations", {
                "parent": parent, "parent_family": pfam, "family": fam, **pinfo,
                "parent_campaign_id": pinfo.get("campaign_id"), "text": text, "rationale": rationale,
                "success_criteria": success_criteria, "human_override": override,
                "override_reason": override_reason if override else "", "hypothesis_id": hyp_id,
                "similar_prior": [{k: s.get(k) for k in ("match", "score", "campaign_id", "hypothesis_id",
                                                         "family", "outcome")} for s in similar]})
        rec["template_hypothesis"] = {"text": text, "rationale": tagged, "success_criteria": success_criteria}
        return rec

    def apply_derivation(self, template: dict, derivation_id: str) -> dict:
        """캠페인 템플릿 dict 에 파생 가설(태그 포함)과 상속 계열을 넣은 사본. 다른 family 가 이미
        지정되어 있으면 FamilyInheritanceError."""
        drv = self.get(derivation_id)
        if drv is None or not derivation_id.startswith("drv-"):
            raise LessonsError(f"unknown derivation {derivation_id!r}")
        t = copy.deepcopy(template)
        cur = t.get("family")
        if cur and cur != drv["family"]:
            raise FamilyInheritanceError(f"template family {cur!r} != inherited family {drv['family']!r}")
        t["family"] = drv["family"]
        t["hypothesis"] = {"text": drv["text"],
                           "rationale": f"{drv['rationale']} [derived-from {drv['id']} ← {drv['parent']}; "
                                        f"family {drv['family']}]",
                           "success_criteria": drv["success_criteria"]}
        return t

    def check_template(self, template) -> dict:
        """실행 전 점검: 파생 태그가 있으면 계열 일치 강제, 그리고 유사 선행 캠페인 목록."""
        rec = _as_grid_record(template)
        raw = template.raw if hasattr(template, "raw") else (rec or {})
        hyp = raw.get("hypothesis") or (template.hypothesis if hasattr(template, "hypothesis") else {})
        fam = rec.get("family") or (rec.get("entry") or {}).get("block")
        out = {"family": fam, "derivation": None, "warnings": []}
        m = DERIVED_TAG_RE.search(str((hyp or {}).get("rationale") or ""))
        if m:
            drv = self.get(m.group(1))
            if drv is None:
                raise LessonsError(f"template references unknown derivation {m.group(1)}")
            if drv["family"] != fam:
                raise FamilyInheritanceError(
                    f"template family {fam!r} != family inherited by {drv['id']} ({drv['family']!r})")
            out["derivation"] = drv["id"]
        sim = self.find_similar(template=rec, text=(hyp or {}).get("text"))
        out["similar"] = sim
        for s in sim:
            if s["match"] == "exact":
                out["warnings"].append(f"exact overlap with {s['campaign_id']} ({s['family']}, outcome "
                                       f"{s['outcome']}) — re-running an overlapping grid is a repeat trial")
            elif s["match"] == "fuzzy" and s.get("family") and s["family"] != fam and not m:
                out["warnings"].append(f"text similar to {s.get('hypothesis_id')} in family {s['family']!r}; "
                                       "if this derives from it, use derive_hypothesis (family inheritance)")
        return out

    # ---------------------------------------------------------- 유사 탐색
    def _outcome_of(self, campaign_id: str | None) -> str:
        if not campaign_id:
            return "no campaign"
        for f in self._read("facts"):
            if f.get("campaign_id") == campaign_id:
                prim = (f.get("taxonomy") or {}).get("primary")
                return f"{f.get('outcome')}" + (f" ({prim})" if prim else "")
        pj = self.campaigns_dir / campaign_id / "pipeline.json"
        if pj.exists():
            try:
                return "pipeline: " + str(json.loads(pj.read_text(encoding="utf-8")).get("verdict"))
            except (OSError, ValueError):
                pass
        return "no facts recorded"

    def find_similar(self, template=None, text: str | None = None, entry_block: str | None = None,
                     limit: int = 10, fuzzy_threshold: float = 0.3) -> list[dict]:
        """선행 가설·캠페인 중 유사한 것 + 결과.

        match: exact (같은 진입·필터 블록 + 모든 격자 겹침) > same_blocks (블록 같고 격자 겹치지 않음)
        > same_entry (진입 블록만 같음) > fuzzy (가설 텍스트 토큰 Jaccard ≥ fuzzy_threshold).
        """
        rec = _as_grid_record(template) if template is not None else None
        if rec is None and entry_block:
            rec = {"entry": {"block": entry_block, "grid": {}}, "filters": [[]], "exits": {}}
        hyps = {h["id"]: h for h in self.ledger.records("hypotheses")}
        rank = {"exact": 0, "same_blocks": 1, "same_entry": 2, "fuzzy": 3}
        out: dict[str, dict] = {}
        covered_hyps = set()
        for c in self.ledger.records("campaigns"):
            grid = c.get("grid") if isinstance(c.get("grid"), dict) else {}
            hyp = hyps.get(c.get("hypothesis_id"), {})
            covered_hyps.add(c.get("hypothesis_id"))
            match, score = None, 0.0
            if rec is not None and "entry" in grid:
                match = compare_grids(rec, grid)
                score = {"exact": 1.0, "same_blocks": 0.8, "same_entry": 0.5}.get(match, 0.0)
            if text:
                ts = text_similarity(text, hyp.get("text", ""))
                if match is None and ts >= fuzzy_threshold:
                    match, score = "fuzzy", ts
                elif match is not None:
                    score = max(score, ts)
            if match is None:
                continue
            out[c["id"]] = {"match": match, "score": round(score, 3), "campaign_id": c["id"],
                            "hypothesis_id": c.get("hypothesis_id"), "family": c.get("family"),
                            "text": hyp.get("text"), "ts": c.get("ts"), "outcome": self._outcome_of(c["id"]),
                            "trials_in_family": None}
        if text:   # 캠페인이 없는 가설 (등록만 된 것) / 파생 기록
            for h in hyps.values():
                if h["id"] in covered_hyps:
                    continue
                ts = text_similarity(text, h.get("text", ""))
                if ts >= fuzzy_threshold:
                    out[h["id"]] = {"match": "fuzzy", "score": round(ts, 3), "campaign_id": None,
                                    "hypothesis_id": h["id"], "family": None, "text": h.get("text"),
                                    "ts": h.get("ts"), "outcome": "no campaign", "trials_in_family": None}
            for d in self._read("derivations"):
                ts = text_similarity(text, d.get("text", ""))
                if ts >= fuzzy_threshold and d["id"] not in out:
                    out[d["id"]] = {"match": "fuzzy", "score": round(ts, 3), "campaign_id": None,
                                    "hypothesis_id": d.get("hypothesis_id"), "family": d.get("family"),
                                    "text": d.get("text"), "ts": d.get("ts"),
                                    "outcome": f"derivation {d['id']} of {d['parent']}", "trials_in_family": None}
        res = sorted(out.values(), key=lambda r: (rank[r["match"]], -r["score"], r.get("ts") or ""))[:limit]
        fams = {r["family"] for r in res if r["family"]}
        tc = {f: self.ledger.trials_count(family=f) for f in fams}
        for r in res:
            if r["family"]:
                r["trials_in_family"] = tc[r["family"]]
        return res

    # ---------------------------------------------------------- 사후분석
    def postmortem(self, title: str, category: str, what_happened: str, root_cause: str,
                   prevention: list[str] | str, impact: str = "", detected_by: str = "",
                   refs: Iterable[str] = (), incident_date: str | None = None, status: str = "mitigated",
                   key: str | None = None, author: str = "ai") -> dict:
        """과정 사후분석(버그·데이터·절차 위반·설계). key 를 주면 같은 key 재기록은 기존 레코드 반환."""
        if category not in POSTMORTEM_CATEGORIES:
            raise ValueError(f"category must be one of {POSTMORTEM_CATEGORIES}")
        if status not in ("open", "mitigated", "closed"):
            raise ValueError("status must be open | mitigated | closed")
        for k, v in (("title", title), ("what_happened", what_happened), ("root_cause", root_cause)):
            if not str(v or "").strip():
                raise ValueError(f"{k} required")
        prev = [prevention] if isinstance(prevention, str) else list(prevention)
        if not prev:
            raise ValueError("at least one prevention item required")
        payload = sanitize({"title": title, "what_happened": what_happened, "root_cause": root_cause,
                            "prevention": prev, "impact": impact, "detected_by": detected_by,
                            "refs": list(refs)}, "refuse")
        with self.lock():
            if key:
                for r in self._read("postmortems"):
                    if r.get("key") == key:
                        return copy.deepcopy(r)
            return self._append_locked("postmortems", {
                "key": key, "category": category, **payload,
                "incident_date": incident_date or _today().isoformat(), "status": status, "author": author})

    def seed_postmortems(self) -> list[dict]:
        """지금까지의 실제 사건 4건 (멱등)."""
        return [self.postmortem(**pm) for pm in SEED_POSTMORTEMS]

    # ---------------------------------------------------------- 주간 시도 예산 (자문)
    def trial_budget(self, family: str, now: datetime | None = None) -> dict:
        """주간 시도 예산 규칙 (docs/AI_RESEARCH.md §7):
        - 전역 상한 GLOBAL_WEEKLY_TRIALS(2,000) 새 시도/7일 (고유 campaign×logic_hash, 원장 runs ts 기준).
        - 계열 기본 600 / 사실 기록이 없는 새 계열 300 / 최근 캠페인에 Dev 생존자·Val 통과가 있으면 900.
        - 최근 2개 캠페인의 주 실패 유형이 모두 NO_SIGNAL·COST_EATEN 이면 0 (동결): 해석의 재검토 조건이
          충족되거나 사람이 해제할 때까지. 남은 예산 = min(계열 잔여, 전역 잔여)."""
        now = now or datetime.now(timezone.utc)
        since = (now - timedelta(days=7)).isoformat(timespec="seconds")
        fam_used, glob_used = set(), set()
        for r in self.ledger.records("runs"):
            if (r.get("ts") or "") >= since:
                glob_used.add((r["campaign_id"], r["logic_hash"]))
                if r.get("family") == family:
                    fam_used.add((r["campaign_id"], r["logic_hash"]))
        facts = sorted((f for f in self._read("facts") if f.get("family") == family and f.get("kind") == "campaign"),
                       key=lambda f: f["seq"])
        reason = "default"
        allowance = FAMILY_WEEKLY_TRIALS
        if not facts:
            allowance, reason = NEW_FAMILY_WEEKLY_TRIALS, "new family (no recorded facts): run signal study first"
        else:
            last = facts[-1]
            prims = [(f.get("taxonomy") or {}).get("primary") for f in facts[-2:]]
            if len(prims) == 2 and all(p in FREEZE_CODES for p in prims):
                allowance, reason = 0, (f"frozen: last two campaigns failed as {prims}; needs a met re-examine "
                                        "condition or human release")
            elif last.get("outcome") == "validated" or (last.get("funnel") or {}).get("n_dev_survivors"):
                allowance, reason = PROMISING_FAMILY_WEEKLY_TRIALS, "promising: recent Dev survivor"
        remaining = max(0, min(allowance - len(fam_used), GLOBAL_WEEKLY_TRIALS - len(glob_used)))
        return {"family": family, "used_7d": len(fam_used), "allowance": allowance,
                "global_used_7d": len(glob_used), "global_cap": GLOBAL_WEEKLY_TRIALS,
                "remaining": remaining, "rule": reason,
                "family_trials_total": self.ledger.trials_count(family=family)}

    # ---------------------------------------------------------- 렌더링
    def render_markdown(self, path: str | Path | None = None) -> Path:
        path = Path(path) if path is not None else self.root / "LESSONS.md"
        facts = self.records("facts")
        today = _today().isoformat()
        L = ["# 오답노트 (LESSONS) — 자동 생성",
             "",
             "> `btc_quant.research.lessons.render_markdown()` 생성물. **직접 편집 금지** (원본: `research/lessons/*.jsonl`).",
             "> 사실(Facts)은 파이프라인 결과에서 자동 추출되어 불변이다. 해석(Interpretations)은 AI 작성이며",
             "> 후속 캠페인으로 verified/refuted 되기 전까지 **unverified** 이다. Val/잠금은 결과 코드만.",
             f"> 생성: {utcnow()} · 사실 {len(facts)} · 해석 {len(self._read('interpretations'))} · "
             f"사후분석 {len(self._read('postmortems'))} · 파생 {len(self._read('derivations'))}",
             "", "## 실패 유형 (자동 분류)", "", "| 코드 | 의미 |", "|---|---|"]
        L += [f"| `{k}` | {v} |" for k, v in TAXONOMY.items()]
        # 계열 요약
        L += ["", "## 계열 요약", "", "| family | 캠페인 | 누적 시도 | 결과 | 주 실패 유형 |", "|---|---|---|---|---|"]
        by_fam: dict[str, list] = {}
        for f in facts:
            by_fam.setdefault(f.get("family") or "?", []).append(f)
        for fam, fs in sorted(by_fam.items()):
            outc = Counter(f.get("outcome") for f in fs)
            prim = Counter((f.get("taxonomy") or {}).get("primary") for f in fs if (f.get("taxonomy") or {}).get("primary"))
            try:
                trials = self.ledger.trials_count(family=fam)
            except Exception:  # noqa: BLE001 — 렌더링은 원장 문제로 실패하지 않는다
                trials = "?"
            L.append(f"| `{fam}` | {len(fs)} | {trials} | "
                     + ", ".join(f"{k} {v}" for k, v in outc.items()) + " | "
                     + ", ".join(f"`{k}`×{v}" for k, v in prim.most_common()) + " |")
        if not facts:
            L.append("| _(none yet)_ | | | | |")
        # 사실 + 해석
        L += ["", "## 교훈 (사실 → 해석)", ""]
        ints = self.interpretations()
        for f in facts:
            sc = f.get("scope") or {}
            stale = (sc.get("review_after") or "9999") < today
            title = f.get("campaign_id") or f.get("study_id")
            L.append(f"### {f['id']} · {f.get('kind')} `{title}` · family `{f.get('family')}`"
                     + (" · ⚠ 재검토 기한 경과" if stale else ""))
            L.append("")
            L.append("**사실 (자동, 불변)**")
            L.append("")
            if f.get("hypothesis_text"):
                L.append(f"- 가설 {f.get('hypothesis_id')}: {f['hypothesis_text']}")
            tax = f.get("taxonomy") or {}
            L.append(f"- 결과: **{f.get('outcome')}** · 주 실패 유형: `{tax.get('primary')}` · 분포: "
                     + (", ".join(f"`{k}`×{v}" for k, v in (tax.get('counts') or {}).items()) or "–"))
            if f.get("derived_from"):
                L.append(f"- 파생: {f['derived_from']} (계열 점검: {f.get('family_check')})")
            fn = f.get("funnel") or {}
            if fn:
                tk = fn.get("top_k_dev_gate_pass") or {}
                L.append(f"- 깔때기(전체 {fn.get('n_specs')} 스펙): G1 {fn.get('g1_pass')} · SR_w>0 "
                         f"{fn.get('sharpe_w_positive')} · G4 {fn.get('g4_pass')} | 상위 {fn.get('top_k')}: "
                         + " · ".join(f"{g} {n}" for g, n in tk.items())
                         + f" · Dev 생존 {fn.get('n_dev_survivors')}")
            cs = f.get("campaign_stats") or {}
            if cs:
                L.append("- 선택편향 입력: " + ", ".join(f"{k}={_fmt(v)}" for k, v in cs.items()))
            va = f.get("val_aggregate")
            if va:
                L.append(f"- Val(G6): 평가 {va.get('n_evaluated')} · 통과 {va.get('n_passed')} (결과 코드만) · "
                         f"잠금: {f.get('lockbox_outcome')}")
            for c in (f.get("candidates") or [])[:5]:
                m = c.get("metrics_dev") or {}
                t = c.get("taxonomy") or {}
                L.append(f"  - #{c.get('rank')} `{c.get('spec_id')}` SR_w={_fmt(m.get('sharpe_w'))} "
                         f"trades={_fmt(m.get('n_trades'))} first_failed={c.get('first_failed')} "
                         f"→ `{t.get('primary')}` {t.get('codes')} · val={c.get('val_outcome')}")
            for c in (f.get("configs") or [])[:8]:
                L.append(f"  - `{c.get('label')}` events={_fmt(c.get('n_events'))} p={_fmt(c.get('p_signal'))} "
                         f"best_h={_fmt(c.get('best_h'))} → {'PASS' if c.get('passed') else '`' + str(c.get('code')) + '`'}")
            if f.get("summary"):
                L.append("- 요약: " + ", ".join(f"{k}={_fmt(v)}" for k, v in list(f["summary"].items())[:12]))
            L.append(f"- 범위: {sc.get('exchange')} {sc.get('symbol')} {sc.get('timeframe')} · "
                     f"{sc.get('segment')} {(sc.get('period') or {}).get('start')}~{(sc.get('period') or {}).get('end')} · "
                     f"블록 {sc.get('entry_block')} / {sc.get('filter_blocks')} · 재검토 기한 {sc.get('review_after')}")
            if sc.get("reexamine_if"):
                L.append("- 재검토 조건: " + "; ".join(sc["reexamine_if"]))
            L.append("")
            fi = [i for i in ints if i["fact_id"] == f["id"]]
            if fi:
                L.append("**해석 (AI, 검증 상태 표시)**")
                L.append("")
                for i in fi:
                    L.append(f"- [{i['current_status'].upper()}] {i['id']} (신뢰도 {i['confidence']}): {i['text']}")
                    L.append(f"  - 근거: {'; '.join(map(_fmt_ev, i.get('evidence') or []))}")
                    if i.get("proposed_test"):
                        L.append(f"  - 검증 방법: {i['proposed_test']}")
                    for m in (x for x in self._read("marks") if x["interpretation_id"] == i["id"]):
                        L.append(f"  - {m['status']} by {m['by_campaign']} ({m['ts']}): {m.get('note', '')}")
                L.append("")
        if not facts:
            L += ["_(아직 기록된 사실 없음 — 파이프라인 종료 시 record_facts_from_pipeline)_", ""]
        # 파생
        drvs = self._read("derivations")
        L += ["## 파생 가설 (계열 상속)", ""]
        if drvs:
            L += ["| id | 부모 | family | override | 가설 |", "|---|---|---|---|---|"]
            L += [f"| {d['id']} | {d['parent']} | `{d['family']}` | {'YES: ' + d['override_reason'] if d.get('human_override') else '–'} | {d['text']} |"
                  for d in drvs]
        else:
            L.append("_(none)_")
        # 사후분석
        L += ["", "## 과정 사후분석 (버그·데이터·절차)", ""]
        for pm in self._read("postmortems"):
            L.append(f"### {pm['id']} · [{pm['category']}] {pm['title']} ({pm['incident_date']}, {pm['status']})")
            L.append("")
            L.append(f"- **무슨 일**: {pm['what_happened']}")
            L.append(f"- **근본 원인**: {pm['root_cause']}")
            if pm.get("impact"):
                L.append(f"- **영향**: {pm['impact']}")
            if pm.get("detected_by"):
                L.append(f"- **발견 경위**: {pm['detected_by']}")
            L.append("- **재발 방지**:")
            L += [f"  - {x}" for x in pm.get("prevention", [])]
            if pm.get("refs"):
                L.append("- 참고: " + ", ".join(pm["refs"]))
            L.append("")
        if not self._read("postmortems"):
            L.append("_(none)_")
        problems = self.verify()
        L += ["", "## 무결성", "", "- " + ("OK (내용 해시·id 순서 일치)" if not problems else
                                           "**문제 발견**: " + "; ".join(problems))]
        path.write_text("\n".join(L) + "\n", encoding="utf-8")
        return path


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.3f}" if v == v else "–"
    return "–" if v is None else str(v)


def _fmt_ev(e) -> str:
    return json.dumps(e, ensure_ascii=False) if isinstance(e, (dict, list)) else str(e)


# ============================================================== 실제 사건 (seed)
SEED_POSTMORTEMS: list[dict] = [
    {
        "key": "phase0-lockbox-exposure",
        "title": "Phase 0 분석이 분할 확정 전에 잠금 구간 기술통계를 봄",
        "category": "procedure",
        "incident_date": "2026-11-09",
        "what_happened": ("Phase 0 비용 바닥 분석이 업비트·빗썸·바이낸스 2025-11-01~2026-11-08 1분봉을 사용했다. "
                          "이 기간은 지금의 Val 전체와 잠금 구간 전체에 해당한다. 전략 수익률은 보지 않았지만 "
                          "시간대별 변동폭, 변동성 국면, 바이낸스 선행 상관 같은 기술통계를 보았고, session_kst·"
                          "vol_regime·binance_* 블록 가설이 이 관찰에서 나왔다."),
        "root_cause": ("분할·잠금 정책과 DataAccess 계층이 생기기 전에 분석을 먼저 했다. 분석 CLI 가 데이터 저장소를 "
                       "직접 읽어 분할을 강제하지 않았다."),
        "impact": ("이 블록 계열을 쓰는 후보의 잠금 평가가 약간 낙관 편향될 수 있다. Val 도 같은 관찰에 노출됨."),
        "detected_by": "자체 점검 후 PLAN §11 에 공개 (2026-11-09)",
        "prevention": [
            "모든 분석 CLI 를 DataAccess 경유로 변경(기본 Dev~Val, 잠금은 LockboxKey 필요) — 커밋 f8c80ae",
            "해당 블록 계열 후보 리포트에 노출 사실을 표기, 최종 판정은 노출 없는 Forward(페이퍼)",
            "research/knowledge/ 의 모든 항목에 '잠금/Val 노출 여부'를 표시",
            "새 탐색 분석은 Dev 구간에서만 (signal_study 도 Dev 전용)",
        ],
        "refs": ["docs/PLAN.md §11", "research/reports/SUMMARY_20261109_cost_floor.md"],
    },
    {
        "key": "g2-null-trade-count-bias",
        "title": "G2 귀무의 거래 수 편향 (신호 비율 보정)",
        "category": "bug",
        "incident_date": "2026-11-09",
        "what_happened": ("G2 귀무(random_entry, 같은 필터·청산)의 진입 확률을 후보의 '신호 비율'로 맞추자, "
                          "연속 True 인 상태형·군집형 진입은 쿨다운 때문에 실제 거래가 적고 귀무는 훨씬 자주 거래해 "
                          "비용을 더 냈다. 그 결과 무작위 보행 합성 시장에서도 후보가 '덜 거래한다'는 이유만으로 "
                          "귀무를 이겼다(p 매우 작음)."),
        "root_cause": ("귀무가 후보와 같은 '기회 집합·빈도'를 가정했지만 거래 수·노출이 달랐다. 비용이 큰 시장에서 "
                       "거래 수 차이가 곧 성과 차이가 된다."),
        "impact": "보정 전 G2 는 위양성을 냈다(합성 무작위 보행에서 확인). 실데이터 캠페인 판정 이전에 발견.",
        "detected_by": "합성 무작위 보행 시장 파이프라인 테스트",
        "prevention": [
            "기본 보정을 '거래 수 맞춤'으로 변경, 두 p 와 귀무 거래 수 분포를 모두 보고 (research/nulls.py)",
            "관문 전체를 합성 시장 다수로 검정력·오탐률 측정 (PLAN §10b-8)",
            "귀무 비교 시 거래 수·노출 시간이 맞는지 리포트에서 확인 (skeptic 체크리스트)",
        ],
        "refs": ["src/btc_quant/research/nulls.py", "tests/test_research_pipeline.py"],
    },
    {
        "key": "gitignore-data-pattern",
        "title": ".gitignore 의 data/ 패턴이 src/btc_quant/data 를 첫 커밋에서 제외",
        "category": "bug",
        "incident_date": "2026-11-09",
        "what_happened": ("데이터 디렉터리를 무시하려던 비고정 패턴 `data/` 가 모든 깊이의 data 디렉터리와 일치해 "
                          "패키지 코드 src/btc_quant/data 가 첫 커밋에서 빠졌다."),
        "root_cause": "루트에 고정되지 않은 .gitignore 패턴; 커밋 후 깨끗한 체크아웃에서 import 검사가 없었다.",
        "impact": "깨끗한 클론에서 데이터 계층 import 실패 가능 — 재현성 위협. 수정 전 커밋에 한함.",
        "detected_by": "커밋 내용 점검",
        "prevention": [
            "루트 고정 패턴 `/data/*` + `!/data/.gitkeep` 사용",
            "커밋 후 `git ls-files src | wc -l` 또는 깨끗한 클론에서 테스트 실행으로 누락 확인",
        ],
        "refs": [".gitignore"],
    },
    {
        "key": "dsr-power-exit-grid",
        "title": "DSR 검정력 문제: 청산 격자 ×80 이 통과를 거의 불가능하게 만듦",
        "category": "design",
        "incident_date": "2026-11-09",
        "what_happened": ("캠페인마다 청산 3요소 격자(최대 약 80 조합)를 곱해 시도 수가 폭증했다. 개발 구간 4.8년 기준 "
                          "DSR 통과에 필요한 연 Sharpe 는 실질 시도 10회 1.5~2.3, 200회 2.1~3.5, 1,000회 2.4~4.0 — "
                          "탐색할수록 아무것도 통과하지 못하는 구조였다."),
        "root_cause": ("청산을 데이터로 고르는 차원으로 취급했고, 캠페인 등록 전에 검정력(통과 가능한 최소 Sharpe)을 "
                       "계산하지 않았다."),
        "impact": "v3 캠페인 설계 전반. v4 연구 체계 개편의 직접 원인.",
        "detected_by": "2026-11-09 심층 검토 (PLAN §10b)",
        "prevention": [
            "신호 선별 단계(이벤트 스터디)를 전략 격자 전에 (btc_quant.research.signal_study)",
            "원칙적 청산: ATR 배수·신호 지속 시간 기반, 청산 변형 ≤ 6",
            "캠페인 등록 전 검정력 계산기 (btc_quant.research.power) 로 필요 Sharpe 확인",
            "계열별 주간 시도 예산 (LessonBook.trial_budget)",
        ],
        "refs": ["docs/PLAN.md §10b"],
    },
]


# ============================================================== 모듈 수준 편의 함수
def _book(root: str | Path | None = None, book: LessonBook | None = None) -> LessonBook:
    return book or LessonBook.at(root)


def record_facts_from_pipeline(pipeline_json: dict | str | Path, root: str | Path | None = None,
                               book: LessonBook | None = None, render: bool = True, **kw) -> dict:
    """파이프라인 끝에서 호출: 사실 기록 (+ LESSONS.md 재생성)."""
    b = _book(root, book)
    rec = b.record_facts_from_pipeline(pipeline_json, **kw)
    if render:
        b.render_markdown()
    return rec


def add_interpretation(fact_id: str, text: str, confidence, evidence: list, root=None, book=None, **kw) -> dict:
    return _book(root, book).add_interpretation(fact_id, text, confidence, evidence, **kw)


def mark(interpretation_id: str, status: str, by_campaign: str, root=None, book=None, **kw) -> dict:
    return _book(root, book).mark(interpretation_id, status, by_campaign, **kw)


def derive_hypothesis(parent: str, text: str, rationale: str, success_criteria: str, root=None, book=None,
                      **kw) -> dict:
    return _book(root, book).derive_hypothesis(parent, text, rationale, success_criteria, **kw)


def find_similar(template=None, text: str | None = None, root=None, book=None, **kw) -> list[dict]:
    return _book(root, book).find_similar(template=template, text=text, **kw)


def postmortem(title: str, category: str, what_happened: str, root_cause: str, prevention, root=None,
               book=None, **kw) -> dict:
    return _book(root, book).postmortem(title, category, what_happened, root_cause, prevention, **kw)


def render_markdown(root=None, book=None, path=None) -> Path:
    return _book(root, book).render_markdown(path)
