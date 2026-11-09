"""연구 MCP 도구 구현 — 기존 연구 API 위의 얇은 검증 래퍼 (PLAN §8, docs/MCP.md).

원칙
- 모든 도구는 JSON 직렬화 가능한 **요약**만 반환한다(큰 프레임·수익 행렬 없음). 성공 = {"ok": true, …},
  실패 = {"ok": false, "error": {"code", "type", "message", …}} (예외를 던지지 않는다).
- 데이터는 `DataAccess`(분할 강제) 를 통해서만 읽는다. 잠금 구간은 어떤 도구로도 읽거나 요약하지 않는다.
- Val(G6)·잠금 정보는 **결과 코드만**(pass/fail, 사용 횟수) 반환한다. 원장 조회도 화이트리스트 열만.
- 연구 모드(BTCQ_RESEARCH_MODE=1) 규칙을 도구 수준에서 강제한다: 주간 시도 예산 초과·선별 미통과·검정력
  미달·정확 중복(사유 없음)이면 원장에 쓰기 전에 거부, 계열 override 인자는 아예 노출하지 않는다.
- 사람 전용 기능(승인·활성화·롤백·잠금 개봉·관문/비용/분할/원장 파일 편집·주문·키)은 **도구가 없다**.
  `request_lockbox_eval` 은 사람에게 보내는 요청 기록만 쓰고 잠금 구간을 열지 않는다.

서버 등록은 `server.py`, 사용법은 docs/MCP.md.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from btc_quant.registry.ledger import _clean, repo_root, utcnow

# ============================================================== 도구 목록 · 금지 기능
TOOL_NAMES = (
    "list_blocks", "get_data_summary", "register_hypothesis", "find_similar", "screen_signals", "power_check",
    "create_and_run_campaign", "list_campaigns", "get_campaign_report", "query_ledger", "lessons_view",
    "lessons_add_interpretation", "lessons_mark", "lessons_derive_hypothesis", "propose_block",
    "request_lockbox_eval", "registry_status", "params_status", "propose_criterion", "forward_status",
)

# 이 서버에 절대 있어서는 안 되는 기능 (테스트가 도구 이름·설명에 대해 강제한다).
FORBIDDEN_CAPABILITIES = {
    "approve": r"approv",                      # 잠금·활성화·파라미터 승인 (사람 전용)
    "activate": r"activat",
    "rollback": r"roll_?back",
    "open_lockbox": r"open_lockbox|lockbox_open|unlock|read_lockbox",
    "promote": r"promot",
    "edit_config": r"(edit|write|set|update)_(gates?|costs?|splits?|risk|params?|config)",
    "edit_ledger": r"(edit|write|delete|update|append)_ledger|ledger_(write|edit|delete|append)",
    "orders": r"order|trade_|place_|submit_|execute",
    "keys": r"(api_)?keys?$|secret|credential",
    "transition": r"transition|reject_strategy",
}

ALLOWED_LEDGER_TABLES = ("hypotheses", "campaigns", "runs", "transitions", "val_evaluations", "lockbox",
                         "forward_results")

# 원장 조회 화이트리스트 (점 표기 = 중첩 필드). Val·잠금 결과는 outcome 코드로만 투영한다.
LEDGER_COLUMNS: dict[str, tuple[str, ...]] = {
    "hypotheses": ("id", "ts", "text", "rationale", "success_criteria", "author"),
    "campaigns": ("id", "ts", "hypothesis_id", "family", "budget", "grid_size", "cost_models", "segment",
                  "period_start", "period_end", "data_snapshot", "kind", "note", "grid"),
    "runs": ("id", "ts", "campaign_id", "hypothesis_id", "family", "spec_id", "spec_version", "logic_hash",
             "cost_name", "segment", "data_snapshot", "code_commit", "params_version", "spec", "metrics"),
    "transitions": ("id", "ts", "strategy_id", "version", "from_state", "to_state", "reason", "actor",
                    "logic_hash"),
    "val_evaluations": ("id", "ts", "event", "val_id", "k", "logic_hash", "campaign_id", "spec_id",
                        "opening_id", "outcome"),
    "lockbox": ("id", "ts", "event", "candidate_id", "lockbox_id", "logic_hash", "opening_id", "outcome"),
    "forward_results": ("id", "ts", "strategy_key", "strategy_id", "version", "state", "logic_hash", "as_of",
                        "status", "days_until_eval", "n_days", "n_trades", "net_return", "sharpe", "max_dd",
                        "within_band", "campaign_id"),
}
LEDGER_DEFAULT_EXCLUDE = {"campaigns": ("grid",), "runs": ("spec",)}
# Dev 실행 지표 중 반환하는 것 (전략 실행 / 신호 선별 실행)
RUN_METRIC_KEYS = ("n_days", "n_trades", "recent_n_trades", "sharpe", "sharpe_w", "recent_sharpe", "max_dd",
                   "expectancy", "win_rate", "trades_per_day", "mean_daily", "std_daily", "total_return",
                   "rolling_frac_positive", "exposure")
SCREEN_METRIC_KEYS = ("kind", "n_events", "p_signal", "info_horizon", "best_primary_horizon",
                      "saturation_horizon", "profile", "bar_minutes")
DEV_METRIC_KEYS = RUN_METRIC_KEYS
MAX_ROWS = 200
GATES_DEV = ("G0", "G1", "G2", "G3", "G4", "G5")
REVIEW_VERDICT_RE = re.compile(r"VERDICT:\s*\**\s*SURVIVES\b")
BLOCK_FILE_RE = re.compile(r"^[a-z][a-z0-9_]*\.py$")
EXPERIMENTAL_REL = Path("src") / "btc_quant" / "strategy" / "blocks" / "experimental"
LOCKBOX_REQUESTS_REL = Path("research") / "lockbox_requests"


class ToolFailure(Exception):
    """도구가 의도적으로 거부·실패할 때 (구조화된 오류로 반환)."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details


def _error_code(e: BaseException) -> str:
    """예외 → 안정적인 오류 코드 (이름 매칭: 모듈을 미리 import 하지 않기 위해)."""
    names = [c.__name__ for c in type(e).__mro__]
    table = [("CampaignBudgetError", "grid_budget_exceeded"), ("ScreenGateError", "screen_gate"),
             ("PowerCheckError", "power_check_failed"), ("CampaignError", "invalid_template"),
             ("LockboxAccessError", "lockbox_denied"), ("LockboxApprovalError", "lockbox_denied"),
             ("RestrictedInfoError", "restricted_info"), ("FamilyInheritanceError", "family_inheritance"),
             ("ImmutableFactError", "immutable_fact"), ("LessonsError", "lessons_error"),
             ("GoalpostError", "anti_goalpost"), ("ValBudgetExhaustedError", "val_budget_exhausted"),
             ("BudgetExceededError", "budget_exceeded"), ("LedgerError", "ledger_error"),
             ("RegistryError", "registry_error"), ("ParamRegistryError", "params_error"),
             ("FileNotFoundError", "not_found"), ("KeyError", "not_found"), ("ValueError", "invalid_argument"),
             ("TypeError", "invalid_argument"), ("PermissionError", "permission_denied")]
    for n, code in table:
        if n in names:
            return code
    return "internal_error"


def tool(fn: Callable) -> Callable:
    """결과를 {"ok": true, …} 로 감싸고, 예외는 구조화된 오류로 바꾼다 (절대 던지지 않음)."""
    @functools.wraps(fn)
    def wrapper(self: "ResearchTools", *a, **kw) -> dict[str, Any]:
        try:
            out = fn(self, *a, **kw)
            return _clean({"ok": True, "tool": fn.__name__, **(out or {})})
        except ToolFailure as e:
            return _clean({"ok": False, "tool": fn.__name__,
                           "error": {"code": e.code, "type": "ToolFailure", "message": str(e), **e.details}})
        except Exception as e:  # noqa: BLE001 — 서버는 오류를 구조화해 돌려준다
            msg = str(e)
            return {"ok": False, "tool": fn.__name__,
                    "error": {"code": _error_code(e), "type": type(e).__name__,
                              "message": msg[:4000] + ("…" if len(msg) > 4000 else "")}}
    wrapper.__annotations__ = {**getattr(fn, "__annotations__", {}), "return": "dict[str, Any]"}
    wrapper.is_tool = True
    return wrapper


def _scalars(d: dict | None, maxlen: int = 300) -> dict[str, Any]:
    """스칼라 값만 (리스트·dict·긴 문자열 제외 — 큰 구조·분포를 반환하지 않는다)."""
    out = {}
    for k, v in (d or {}).items():
        if isinstance(v, str):
            out[k] = v if len(v) <= maxlen else v[:maxlen] + "…"
        elif isinstance(v, (bool, int, float)) or v is None:
            out[k] = v
        elif hasattr(v, "item") and not isinstance(v, (list, dict)):
            out[k] = v
    return out


def _outcome(passed) -> str | None:
    return None if passed is None else ("pass" if passed else "fail")


# ============================================================== 구현
class ResearchTools:
    """MCP 도구의 순수 파이썬 구현. 테스트는 이것을 직접(또는 MCPServer 를 통해 in-process) 부른다.

    root: 연구 저장소 루트 (기본: BTC_QUANT_ROOT 또는 저장소). 다음 인자는 **테스트 전용**(도구 인자로
    노출하지 않음): gate_cfg / n_null (합성 시장에서 빠르게), blocks_dir / docs_path (임시 실험 블록),
    load_accepted_blocks (수용 통과 블록을 이 프로세스 REGISTRY 에 적재할지), criteria_runner (가짜 합성 보정).
    """

    def __init__(self, root: str | Path | None = None, *, gate_cfg: dict | None = None, n_null: int | None = None,
                 blocks_dir: str | Path | None = None, docs_path: str | Path | None = None,
                 load_accepted_blocks: bool = True, progress: Callable[[str], None] | None = None,
                 criteria_runner: Callable[[list[dict]], list[dict]] | None = None):
        self.root = Path(root) if root is not None else repo_root()
        here = Path(__file__).resolve().parents[3]          # src/btc_quant/mcp/tools.py → 저장소
        self.code_root = here if (here / "pyproject.toml").exists() else repo_root()
        self._gate_cfg = gate_cfg
        self._n_null = n_null
        self.blocks_dir = Path(blocks_dir) if blocks_dir is not None else self.code_root / EXPERIMENTAL_REL
        self.docs_path = Path(docs_path) if docs_path is not None else None
        self.load_accepted_blocks = load_accepted_blocks
        self._criteria_runner = criteria_runner
        self.log = progress or (lambda s: print(s, file=sys.stderr, flush=True))

    # ---------------------------------------------------------- 공용
    def env(self):
        from btc_quant.research.campaign import ResearchEnv
        return ResearchEnv.at(self.root)

    def book(self):
        from btc_quant.research.lessons import LessonBook
        return LessonBook.at(self.root)

    def _safe_path(self, p: str, suffixes: tuple[str, ...], base: Path | None = None) -> Path:
        base = (base or self.root).resolve()
        q = Path(p).expanduser()
        q = (base / q if not q.is_absolute() else q).resolve()
        if base != q and base not in q.parents:
            raise ToolFailure("path_outside_root", f"path {p!r} is outside {base}")
        if suffixes and q.suffix not in suffixes:
            raise ToolFailure("invalid_argument", f"path {p!r} must end with one of {suffixes}")
        if not q.exists():
            raise ToolFailure("not_found", f"{q} does not exist")
        return q

    def _template_dict(self, template) -> dict[str, Any]:
        if isinstance(template, dict):
            return copy.deepcopy(template)
        if isinstance(template, str):
            d = yaml.safe_load(self._safe_path(template, (".yaml", ".yml")).read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                raise ToolFailure("invalid_template", "template file must contain a mapping")
            return d
        raise ToolFailure("invalid_argument", "template must be a mapping or a YAML path under the repository")

    def _require_budget(self, book, needs: dict[str, int]) -> dict[str, Any]:
        """주간 시도 예산 (docs/AI_RESEARCH.md §7) — MCP 경로에서는 **강제**한다."""
        out, glob_rem = {}, None
        for fam, n in needs.items():
            b = book.trial_budget(fam)
            out[fam] = {**b, "planned": int(n)}
            glob_rem = b["global_cap"] - b["global_used_7d"]
            if n > b["remaining"]:
                raise ToolFailure("weekly_budget_exceeded",
                                  f"family {fam!r}: planned {n} trials > weekly remaining {b['remaining']} "
                                  f"({b['rule']}). Shrink the grid or wait; a frozen family needs a met "
                                  "re-examine condition or a human release.", budget=out[fam])
        if glob_rem is not None and sum(needs.values()) > glob_rem:
            raise ToolFailure("weekly_budget_exceeded", f"planned {sum(needs.values())} trials > global weekly "
                              f"remaining {glob_rem}", budget=out)
        return out

    # ========================================================== 블록·데이터
    @tool
    def list_blocks(self, kind: str | None = None, include_experimental: bool = True) -> dict[str, Any]:
        from btc_quant.strategy.blocks import KINDS, REGISTRY
        if kind is not None and kind not in KINDS:
            raise ToolFailure("invalid_argument", f"kind must be one of {KINDS}")
        rows = []
        for d in REGISTRY.all(kind, include_experimental=include_experimental):
            try:
                defaults = {k: v.default for k, v in inspect.signature(d.fn).parameters.items()
                            if v.default is not inspect.Parameter.empty}
            except (TypeError, ValueError):
                defaults = {}
            rows.append({"name": d.name, "kind": d.kind, "status": d.status, "grid": d.grid,
                         "defaults": {k: v for k, v in defaults.items() if k in d.grid},
                         "requires": list(d.requires), "live_ready": d.live_ready, "description": d.description,
                         **({"author": d.author, "created": d.created, "rationale": d.rationale,
                             "note": "experimental: candidates using it need human block review before a "
                                     "lockbox request (D11)"} if d.experimental else {})})
        rows.sort(key=lambda r: (r["kind"], r["status"] != "official", r["name"]))
        return {"n": len(rows), "blocks": rows,
                "counts": {s: sum(1 for r in rows if r["status"] == s) for s in ("official", "experimental")}}

    @tool
    def get_data_summary(self, exchanges: list[str] | None = None, timeframe: str = "1m",
                         segments: list[str] | None = None) -> dict[str, Any]:
        """커버리지 요약 (dev/val/forward). 잠금 구간은 읽지 않고 상태(개봉 횟수)만."""
        import pandas as pd
        env = self.env()
        segs = list(segments or ("dev", "val", "forward"))
        bad = [s for s in segs if s not in ("dev", "val", "forward")]
        if bad:
            raise ToolFailure("lockbox_denied" if "lockbox" in bad else "invalid_argument",
                              f"segments {bad} not allowed; one of dev, val, forward (the lockbox is sealed)")
        data_root = env.data.data_root
        series = []
        if data_root.exists():
            for ex in sorted(data_root.glob("exchange=*")):
                for sy in sorted(ex.glob("symbol=*")):
                    for tf in sorted(sy.glob("timeframe=*")):
                        series.append((ex.name.split("=", 1)[1], sy.name.split("=", 1)[1],
                                       tf.name.split("=", 1)[1]))
        if exchanges:
            series = [s for s in series if s[0] in exchanges]
        series = [s for s in series if s[2] == timeframe]
        now = pd.Timestamp.now(tz="UTC")
        bounds = {s: env.data.segment_bounds(s, now=now) for s in segs}
        tf_min = {"1m": 1, "5m": 5, "1h": 60}.get(timeframe)
        out = []
        for ex, sym, tf in series:
            row = {"exchange": ex, "symbol": sym, "timeframe": tf, "segments": {}}
            for s in segs:
                start, end = bounds[s]
                end = min(end, now)
                rec = {"start": start.isoformat(), "end": end.isoformat()}
                try:
                    df = env.data.read_range(start, end, ex, sym, tf)
                except Exception as e:  # noqa: BLE001 — 해당 구간 데이터 없음
                    if _error_code(e) == "lockbox_denied":
                        raise
                    rec.update(rows=0, note="no data")
                    row["segments"][s] = rec
                    continue
                idx = pd.DatetimeIndex(df.index)
                rec.update(rows=int(len(df)), first=idx.min().isoformat() if len(idx) else None,
                           last=idx.max().isoformat() if len(idx) else None)
                if tf_min and len(idx):
                    expected = int((end - start) / pd.Timedelta(minutes=tf_min))
                    gaps = idx.to_series().diff().dropna() / pd.Timedelta(minutes=tf_min)
                    rec.update(expected_rows=expected, coverage=round(len(idx) / expected, 6) if expected else None,
                               max_gap_bars=float(gaps.max()) if len(gaps) else 0.0,
                               n_gaps_over_5_bars=int((gaps > 5).sum()),
                               duplicates=int(idx.duplicated().sum()))
                if "filled" in df.columns:
                    rec["filled_frac"] = float(df["filled"].mean())
                row["segments"][s] = rec
            out.append(row)
        lb = env.data.lockbox_status()
        cfg = env.data.config
        return {"series": out, "segments_reported": segs,
                "lockbox": {"lockbox_id": lb["lockbox_id"], "openings": lb["openings"], "cap": lb["cap"],
                            "data": "sealed — not read or summarised by any tool"},
                "val_id": cfg.get("val_id"),
                "note": "Read through DataAccess (split-enforcing). Coverage = rows / expected bars in "
                        "[segment start, min(segment end, now)). For anomalies ask the data-steward agent."}

    # ========================================================== 가설·유사 탐색·예산
    @tool
    def register_hypothesis(self, text: str, mechanism: str, counterparty: str, success_criteria: str,
                            rationale: str = "", scope: str = "", evidence_exposure: str = "") -> dict[str, Any]:
        from btc_quant.research.lessons import check_text
        for k, v in (("text", text), ("mechanism", mechanism), ("counterparty", counterparty),
                     ("success_criteria", success_criteria)):
            if not str(v or "").strip():
                raise ToolFailure("invalid_argument", f"{k} is required (no mechanism/counterparty ⇒ do not register)")
        for k, v in (("text", text), ("mechanism", mechanism), ("counterparty", counterparty),
                     ("rationale", rationale), ("scope", scope), ("success_criteria", success_criteria)):
            check_text(v, k)
        full = (f"Mechanism: {mechanism.strip()} | Counterparty: {counterparty.strip()}"
                + (f" | Scope: {scope.strip()}" if scope.strip() else "")
                + (f" | Evidence exposure: {evidence_exposure.strip()}" if evidence_exposure.strip() else "")
                + (f" | {rationale.strip()}" if rationale.strip() else ""))
        env = self.env()
        book = self.book()
        similar = book.find_similar(text=text, limit=5)
        hid = env.ledger.register_hypothesis(text.strip(), full, success_criteria.strip())
        return {"hypothesis_id": hid, "rationale_recorded": full, "similar": similar,
                "note": "Campaign/screen templates register their own hypothesis record when run; copy the "
                        "same text/mechanism/counterparty into the template's hypothesis block."}

    @tool
    def find_similar(self, template: dict | str | None = None, text: str | None = None,
                     entry_block: str | None = None, family: str | None = None, limit: int = 10) -> dict[str, Any]:
        book = self.book()
        tpl = self._template_dict(template) if template is not None else None
        if tpl is None and not text and not entry_block and not family:
            raise ToolFailure("invalid_argument", "give a template, text, entry_block or family")
        res = book.find_similar(template=tpl, text=text, entry_block=entry_block, limit=max(1, min(int(limit), 50)))
        out: dict[str, Any] = {"similar": res, "exact": [r for r in res if r["match"] == "exact"]}
        fam = family or (tpl or {}).get("family") or ((tpl or {}).get("entry") or {}).get("block")
        if fam:
            out["family"] = fam
            out["weekly_budget"] = book.trial_budget(fam)
        if tpl is not None and "entry" in tpl:
            out["template_check"] = {k: v for k, v in book.check_template(tpl).items() if k != "similar"}
        return out

    @tool
    def power_check(self, n_grid: int | None = None, template: dict | str | None = None,
                    family: str | None = None, target_sharpe: float | None = None,
                    n_days: int | None = None) -> dict[str, Any]:
        import pandas as pd
        from btc_quant.research.power import budget_report
        env = self.env()
        if template is not None:
            from btc_quant.research.campaign import CampaignTemplate
            t = CampaignTemplate.from_dict(self._template_dict(template))
            n_grid = t.n_specs()
            family = family or t.family
            target_sharpe = target_sharpe if target_sharpe is not None else t.power.get("target_sharpe")
        if not n_grid or int(n_grid) < 1:
            raise ToolFailure("invalid_argument", "give n_grid ≥ 1 or a campaign template")
        if n_days is None:
            s, e = env.data.segment_bounds("dev")
            n_days = int((e - s) / pd.Timedelta(days=1))
        rep = budget_report(int(n_grid), int(n_days), family, target_sharpe, root=env.root, ledger=env.ledger,
                            campaigns_dir=env.campaigns_dir)
        rep.pop("history", None)
        need = float(rep["required_sharpe_ann"])
        ok = target_sharpe is not None and need <= float(target_sharpe)
        return {**_scalars(rep, 2000), "ok": ok,
                "verdict": ("target ≥ required — power OK" if ok else
                            "no target_sharpe declared" if target_sharpe is None else
                            f"required {need:.2f} > target {float(target_sharpe):.2f} — shrink the grid or stop"),
                "note": "create_and_run_campaign refuses (before any ledger write) unless power.target_sharpe "
                        "is declared and ≥ the required Sharpe (strict power)."}

    # ========================================================== 선별·캠페인
    @tool
    def screen_signals(self, template: dict | str) -> dict[str, Any]:
        from btc_quant.research.campaign import load_screen_template, run_screen
        d = self._template_dict(template)
        tpl = load_screen_template(d)
        n = tpl.check_budget()
        needs: dict[str, int] = {}
        for e, _ in tpl.configs():
            f = tpl.family_of(e)
            needs[f] = needs.get(f, 0) + 1
        book = self.book()
        budget = self._require_budget(book, needs)
        res = run_screen(tpl, env=self.env(), progress=self.log)
        table = [_scalars(r) for r in res.table.to_dict("records")]
        return {"campaign_id": res.campaign_id, "hypothesis_id": res.hypothesis_id, "n_configs": n,
                "n_passed": res.decision.get("n_passed"), "decision": _scalars(res.decision, 1000),
                "configs": table, "passed": [r["label"] for r in table if r.get("passed")],
                "lessons_facts": getattr(res, "lesson_facts", None), "weekly_budget_before": budget,
                "report_path": str(res.dir / "signal_screen.md") if res.dir else None,
                "next": "Only passed configs may enter a strategy campaign: set from_screen: "
                        f"{res.campaign_id} in the campaign template (entry/filters fixed to the passed values)."}

    @tool
    def create_and_run_campaign(self, template: dict | str, max_candidates: int = 5,
                                repeat_justification: str = "") -> dict[str, Any]:
        from btc_quant.research.campaign import CampaignTemplate, run_campaign
        from btc_quant.research.pipeline import run_pipeline
        from btc_quant.research.report import write_report
        d = self._template_dict(template)
        tpl = CampaignTemplate.from_dict(d)
        n_specs = tpl.check_budget()                           # 격자 > 템플릿 예산 → 거부
        if not tpl.screen_gated:
            raise ToolFailure("screen_gate", "strategy campaigns via MCP must be screen-gated: set from_screen "
                              "(screen campaign id) or requires_screen_pass: true (v4: screen first)")
        if not 1 <= int(max_candidates) <= 5:
            raise ToolFailure("invalid_argument", "max_candidates must be 1..5")
        env = self.env()
        book = self.book()
        chk = book.check_template(tpl)                         # 파생 태그 ↔ 계열 일치 (불일치면 예외)
        exact = [s for s in chk.get("similar", []) if s["match"] == "exact"]
        if exact and not repeat_justification.strip():
            raise ToolFailure("repeat_trial", "exact grid overlap with prior campaign(s) "
                              f"{[s['campaign_id'] for s in exact]}; re-running is a repeat trial. State what is "
                              "materially new in repeat_justification or change the grid.",
                              similar=exact)
        budget = self._require_budget(book, {tpl.family: n_specs})
        # run_campaign 이 원장 쓰기 전에 선별 게이트(ScreenGateError)·엄격 검정력(PowerCheckError)을 확인한다
        cr = run_campaign(tpl, env=env, progress=self.log, strict_power=True)
        kw = {"top_k": int(max_candidates), "progress": self.log}
        if self._gate_cfg is not None:
            kw["gate_cfg"] = self._gate_cfg
        if self._n_null is not None:
            kw["n_null"] = self._n_null
        run_pipeline(cr, env=env, **kw)
        rp = write_report(cr.campaign_id, env=env)
        rep = self._campaign_summary(cr.campaign_id, env, book)
        rep.update(weekly_budget_before=budget,
                   repeat_justification=repeat_justification or None,
                   template_warnings=chk.get("warnings"), base_report=str(rp))
        return rep

    # ---------------------------------------------------------- 캠페인 요약 (Val = 결과 코드만)
    def _campaign_summary(self, campaign_id: str, env, book) -> dict[str, Any]:
        camp = env.ledger.get("campaigns", campaign_id)
        if camp is None:
            raise ToolFailure("not_found", f"unknown campaign {campaign_id!r}")
        hyp = env.ledger.get("hypotheses", camp.get("hypothesis_id")) or {}
        d = env.campaign_dir(campaign_id)
        grid = camp.get("grid") or {}
        out: dict[str, Any] = {
            "campaign_id": campaign_id, "hypothesis_id": camp.get("hypothesis_id"), "family": camp.get("family"),
            "kind": grid.get("kind", "strategy_campaign"), "registered": camp.get("ts"), "budget": camp.get("budget"),
            "hypothesis": {k: hyp.get(k) for k in ("text", "rationale", "success_criteria")},
            "facts": [{"id": f["id"], "outcome": f.get("outcome"), "taxonomy": f.get("taxonomy")}
                      for f in book.facts(campaign_id=campaign_id)],
        }
        if grid.get("kind") == "signal_screen":
            p = d / "signal_screen.json"
            if p.exists():
                import pandas as pd
                csv = d / "signal_screen.csv"
                rows = pd.read_csv(csv).to_dict("records") if csv.exists() else []
                out.update(configs=[_scalars(r) for r in rows],
                           decision=_scalars(json.loads(p.read_text(encoding="utf-8")).get("decision"), 1000),
                           report_path=str(d / "signal_screen.md"))
            return out
        man_p, pj = d / "manifest.json", d / "pipeline.json"
        if man_p.exists():
            man = json.loads(man_p.read_text(encoding="utf-8"))
            pw = man.get("power") or {}
            out["campaign"] = {"n_specs": man.get("n_specs"), "n_runs": man.get("n_runs"),
                               "data_snapshot": man.get("data_snapshot"), "code_commit": man.get("code_commit"),
                               "timeframe": man.get("timeframe"), "exits": man.get("exits"),
                               "period": man.get("period"), "params_version": man.get("params_version"),
                               "power": {k: pw.get(k) for k in ("required_sharpe_ann", "target_sharpe_ann",
                                                                "n_trials_dsr", "warning")},
                               "screen_gate": ({k: (man.get("screen") or {}).get(k)
                                                for k in ("from_screen", "requires_screen_pass", "n_configs")}
                                               if man.get("screen") else None)}
        if not pj.exists():
            out["pipeline"] = None
            return out
        p = json.loads(pj.read_text(encoding="utf-8"))
        st = p.get("campaign_stats") or {}
        out["pipeline"] = {
            "verdict": p.get("verdict"), "n_dev_survivors": p.get("n_dev_survivors"),
            "n_validated": p.get("n_validated"), "n_robustness_fail": p.get("n_robustness_fail"),
            "n_registry_validated": p.get("n_registry_validated"), "uses_experimental": p.get("uses_experimental"),
            "funnel_all": _scalars(p.get("funnel_all")),
            "selection_bias": {k: st.get(k) for k in ("n_campaign_specs", "k_eff_clusters", "n_family_trials",
                                                      "n_trials_dsr", "pbo")},
            "val_budget": {k: (p.get("val_status") or {}).get(k) for k in ("val_id", "cap", "used", "remaining")}
            if p.get("val_status") else None,
            "lockbox": "not accessed",
            "candidates": [self._candidate_summary(c) for c in p.get("candidates") or []],
        }
        rp = env.reports_dir / f"campaign_{campaign_id}.md"
        out["report_path"] = str(rp) if rp.exists() else None
        out["val_policy"] = ("Val (G6) is returned as an outcome code only. The human-facing report file also "
                             "contains Val detail lines: do not quote Val numbers in reports, lessons or hypotheses.")
        return out

    @staticmethod
    def _candidate_summary(c: dict) -> dict[str, Any]:
        gates = c.get("gates") or {}
        g6 = gates.get("G6")
        rb = c.get("robustness")
        abl = c.get("ablation")
        reg = c.get("registry") or {}
        return {
            "rank": c.get("rank"), "spec_id": c.get("spec_id"), "logic_hash": c.get("logic_hash"),
            "spec": c.get("spec"), "cluster_size": c.get("cluster_size"),
            "uses_experimental": c.get("uses_experimental") or [],
            "metrics_dev": {k: (c.get("metrics") or {}).get(k) for k in DEV_METRIC_KEYS
                            if k in (c.get("metrics") or {})},
            "gates_dev": {g: _scalars(gates[g]) for g in GATES_DEV if g in gates},
            "dev_passed": c.get("dev_passed"), "first_failed": c.get("first_failed"),
            "G6_val": _outcome(g6.get("passed")) if isinstance(g6, dict) else "not evaluated",
            "val_budget_exhausted": bool(((g6 or {}).get("val_budget") or {}).get("exhausted"))
            if isinstance(g6, dict) else False,
            "grade": c.get("grade"), "status": c.get("status"), "human_review": c.get("human_review"),
            "robustness_dev": None if not rb else {
                "passed": rb.get("passed"), "verdict": rb.get("verdict"), "failed": rb.get("failed"),
                "skipped": rb.get("skipped"),
                "checks": {k: _scalars(v) for k, v in (rb.get("checks") or {}).items() if isinstance(v, dict)}},
            "ablation_dev": None if not abl else {
                "criterion": abl.get("criterion"), "filters": [_scalars(f) for f in abl.get("filters") or []],
                "simplified_logic_hash": (abl.get("simplified") or {}).get("logic_hash")},
            "registry_state": reg.get("state") or reg.get("skipped"),
        }

    @tool
    def list_campaigns(self, family: str | None = None, limit: int = 30) -> dict[str, Any]:
        from btc_quant.research.campaign import list_campaigns
        env = self.env()
        rows = list_campaigns(env=env)
        if family:
            rows = [r for r in rows if r.get("family") == family]
        rows = list(reversed(rows))[: max(1, min(int(limit), MAX_ROWS))]
        kinds = {c["id"]: (c.get("grid") or {}).get("kind", "strategy_campaign") for c in env.ledger.records("campaigns")}
        for r in rows:
            r["kind"] = kinds.get(r["campaign_id"])
            r["note"] = (r.get("note") or "")[:300]
        return {"n": len(rows), "campaigns": rows}

    @tool
    def get_campaign_report(self, campaign_id: str) -> dict[str, Any]:
        return self._campaign_summary(campaign_id, self.env(), self.book())

    # ========================================================== 원장 조회 (읽기 전용, 화이트리스트)
    @staticmethod
    def _project(table: str, r: dict) -> dict[str, Any]:
        if table == "campaigns":
            per = r.get("period") or {}
            return {"id": r.get("id"), "ts": r.get("ts"), "hypothesis_id": r.get("hypothesis_id"),
                    "family": r.get("family"), "budget": r.get("budget"), "grid_size": r.get("grid_size"),
                    "cost_models": r.get("cost_models"), "segment": per.get("segment"),
                    "period_start": per.get("start"), "period_end": per.get("end"),
                    "data_snapshot": per.get("data_snapshot"),
                    "kind": (r.get("grid") or {}).get("kind", "strategy_campaign"), "note": r.get("note"),
                    "grid": r.get("grid")}
        if table == "runs":
            per = r.get("period") or {}
            seg = per.get("segment")
            m = r.get("metrics") or {}
            if seg != "dev":
                met = {"withheld": f"segment {seg!r}: only dev metrics are returned"}
            elif m.get("kind") == "signal_screen":
                met = {k: m.get(k) for k in SCREEN_METRIC_KEYS if k in m}
                met["passed"] = (m.get("decision") or {}).get("passed")
            else:
                met = {k: m.get(k) for k in RUN_METRIC_KEYS if k in m}
            return {"id": r.get("id"), "ts": r.get("ts"), "campaign_id": r.get("campaign_id"),
                    "hypothesis_id": r.get("hypothesis_id"), "family": r.get("family"), "spec_id": r.get("spec_id"),
                    "spec_version": r.get("spec_version"), "logic_hash": r.get("logic_hash"),
                    "cost_name": r.get("cost_name"), "segment": seg, "data_snapshot": r.get("data_snapshot"),
                    "code_commit": r.get("code_commit"), "params_version": r.get("params_version"),
                    "spec": r.get("spec"), "metrics": met}
        if table == "val_evaluations":
            base = {k: r.get(k) for k in ("id", "ts", "event", "val_id", "k", "logic_hash", "campaign_id",
                                          "spec_id", "opening_id")}
            base["outcome"] = _outcome(r.get("passed")) if r.get("event") == "result" else None
            return base
        if table == "lockbox":
            base = {k: r.get(k) for k in ("id", "ts", "event", "candidate_id", "lockbox_id", "logic_hash",
                                          "opening_id")}
            res = r.get("result")
            base["outcome"] = (_outcome(res.get("passed")) if isinstance(res, dict) and "passed" in res
                               else ("recorded" if r.get("event") == "result" else None))
            return base
        return {k: r.get(k) for k in LEDGER_COLUMNS[table]}

    @tool
    def query_ledger(self, table: str, filters: dict[str, Any] | None = None, columns: list[str] | None = None,
                     limit: int = 50, offset: int = 0, since: str | None = None, count_only: bool = False) -> dict[str, Any]:
        """table ∈ ALLOWED_LEDGER_TABLES | 'trials'. filters: 허용 열에 대한 동등 비교."""
        env = self.env()
        filters = dict(filters or {})
        if table == "trials":
            bad = set(filters) - {"family", "campaign_id"}
            if bad:
                raise ToolFailure("invalid_argument", f"trials filters: family, campaign_id (got {sorted(bad)})")
            out: dict[str, Any] = {"table": "trials", "filters": filters,
                                   "trials": env.ledger.trials_count(campaign_id=filters.get("campaign_id"),
                                                                    family=filters.get("family")),
                                   "definition": "unique (campaign_id, logic_hash) in runs"}
            if not filters:
                fams = sorted({r.get("family") for r in env.ledger.records("runs") if r.get("family")})
                out["by_family"] = {f: env.ledger.trials_count(family=f) for f in fams}
            return out
        if table not in ALLOWED_LEDGER_TABLES:
            raise ToolFailure("table_not_allowed", f"table {table!r} not queryable; one of "
                              f"{list(ALLOWED_LEDGER_TABLES) + ['trials']}")
        allowed = LEDGER_COLUMNS[table]
        bad = [k for k in filters if k not in allowed]
        if bad:
            raise ToolFailure("column_not_allowed", f"filter columns {bad} not allowed for {table}; allowed {list(allowed)}")
        if columns is not None:
            badc = [c for c in columns if c not in allowed]
            if badc:
                raise ToolFailure("column_not_allowed", f"columns {badc} not allowed for {table}; allowed {list(allowed)}")
            cols = list(columns)
        else:
            cols = [c for c in allowed if c not in LEDGER_DEFAULT_EXCLUDE.get(table, ())]
        rows = []
        for r in env.ledger.records(table):
            pr = self._project(table, r)
            if since and str(pr.get("ts") or "") < since:
                continue
            if any(str(pr.get(k)) != str(v) for k, v in filters.items()):
                continue
            rows.append({c: pr.get(c) for c in cols})
        rows.reverse()                                          # 최신 먼저
        n = len(rows)
        lim = max(1, min(int(limit), MAX_ROWS))
        return {"table": table, "n_matched": n, "columns": cols,
                "rows": [] if count_only else rows[int(offset): int(offset) + lim],
                "policy": "read-only; whitelisted columns; Val/lockbox as outcome codes; non-dev run metrics withheld"}

    # ========================================================== 오답노트
    @tool
    def lessons_view(self, family: str | None = None, campaign_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        book = self.book()
        facts = book.facts(campaign_id=campaign_id, family=family)[-max(1, min(int(limit), 100)):]
        fids = {f["id"] for f in facts}
        ints = [{k: i.get(k) for k in ("id", "ts", "fact_id", "campaign_id", "family", "text", "confidence",
                                       "evidence", "proposed_test", "current_status")}
                for i in book.interpretations() if i["fact_id"] in fids]
        out = {"facts": facts, "interpretations": ints, "integrity_problems": book.verify()}
        if family:
            out["weekly_budget"] = book.trial_budget(family)
        return out

    @tool
    def lessons_add_interpretation(self, fact_id: str, text: str, confidence: str | float, evidence: list[str],
                                   proposed_test: str | None = None, reexamine_if: list[str] | None = None) -> dict[str, Any]:
        book = self.book()
        rec = book.add_interpretation(fact_id, text, confidence, evidence, proposed_test=proposed_test,
                                      reexamine_if=reexamine_if or ())
        book.render_markdown()
        return {"interpretation": rec, "status": "unverified"}

    @tool
    def lessons_mark(self, interpretation_id: str, status: str, by_campaign: str, note: str = "") -> dict[str, Any]:
        book = self.book()
        rec = book.mark(interpretation_id, status, by_campaign, note=note)
        book.render_markdown()
        return {"mark": rec}

    @tool
    def lessons_derive_hypothesis(self, parent: str, text: str, rationale: str, success_criteria: str,
                                  template: dict | str | None = None) -> dict[str, Any]:
        """파생 가설 — 부모 계열을 상속(override 인자 없음). template 을 주면 적용·점검한 사본도 반환."""
        book = self.book()
        drv = book.derive_hypothesis(parent, text, rationale, success_criteria)
        out: dict[str, Any] = {"derivation": drv, "family": drv["family"]}
        if template is not None:
            t = book.apply_derivation(self._template_dict(template), drv["id"])
            chk = book.check_template(t) if "entry" in t else None
            out["template"] = t
            if chk is not None:
                out["template_check"] = {k: v for k, v in chk.items() if k != "similar"}
        return out

    # ========================================================== 실험 블록 수용 검사
    @tool
    def propose_block(self, path: str) -> dict[str, Any]:
        """에이전트가 이미 작성한 blocks/experimental/<name>.py 에 대해 acceptance_check 실행 (새 프로세스).
        코드를 쓰지 않는다. 통과하면 (설정 시) 이 서버 프로세스에 모듈을 적재해 캠페인에서 쓸 수 있게 한다."""
        base = self.blocks_dir.resolve()
        q = Path(path).expanduser()
        if not q.is_absolute():
            q = self.code_root / q if (self.code_root / q).exists() or len(q.parts) > 1 else base / q
        q = q.resolve()
        if q.parent != base:
            raise ToolFailure("path_not_allowed", f"block proposals must be a file directly under {base} "
                              f"(got {q})")
        if not BLOCK_FILE_RE.match(q.name):
            raise ToolFailure("invalid_argument", "module file name must match [a-z][a-z0-9_]*.py (no leading _)")
        if not q.exists():
            raise ToolFailure("not_found", f"{q} does not exist — write the module first (block-authoring skill)")
        modname = f"btc_quant.strategy.blocks.experimental.{q.stem}"
        docs = str(self.docs_path) if self.docs_path else ""
        proc = subprocess.run([sys.executable, "-c", _ACCEPT_SCRIPT, str(q), modname, docs],
                              capture_output=True, text=True, timeout=900, cwd=str(self.code_root),
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        marker = "\x00ACCEPT_JSON"
        if proc.returncode != 0 or marker not in proc.stdout:
            raise ToolFailure("module_error", "importing/checking the module failed",
                              stderr_tail=(proc.stderr or "")[-3000:], returncode=proc.returncode)
        res = json.loads(proc.stdout.split(marker, 1)[1])
        if not res["blocks"]:
            raise ToolFailure("no_blocks", f"{q.name} registers no blocks (use REGISTRY.register_experimental)")
        not_exp = [b["block"] for b in res["blocks"] if b["status"] != "experimental"]
        reports = res["blocks"]
        passed = all(b["passed"] for b in reports) and not not_exp
        loaded = None
        if passed and self.load_accepted_blocks:
            loaded = _load_block_module(q, modname)
        digest = hashlib.sha256(q.read_bytes()).hexdigest()[:16]
        return {"module": modname, "file": str(q), "sha256_16": digest, "passed": passed,
                "blocks": reports, "not_experimental": not_exp,
                "loaded_in_server": loaded,
                "policy": "Experimental blocks may be used in campaigns once accepted; candidates using them "
                          "need human block review before any lockbox request; promotion to official is a "
                          "human code change. A passing check is not evidence of an edge."}

    # ========================================================== 잠금 요청 (열지 않는다)
    @tool
    def request_lockbox_eval(self, strategy_id: str, version: int, justification: str,
                             review_report: str) -> dict[str, Any]:
        from btc_quant.registry.strategies import StrategyRegistry, key_of
        from btc_quant.research.lessons import check_text
        from btc_quant.strategy.blocks import spec_uses_experimental
        env = self.env()
        reg = StrategyRegistry(root=self.root, ledger=env.ledger)
        key = key_of(strategy_id, int(version))
        state = reg.state_of(strategy_id, int(version))
        if state not in ("validated", "challenger"):
            raise ToolFailure("not_eligible", f"{key} is in state {state!r}; only validated/challenger strategies "
                              "(passed G0–G6 and the robustness suite) can be proposed for a lockbox evaluation")
        if not str(justification or "").strip():
            raise ToolFailure("invalid_argument", "justification required")
        check_text(justification, "justification")
        rp = self._safe_path(review_report, (".md",))
        if (self.root / "research" / "reports").resolve() not in rp.parents:
            raise ToolFailure("invalid_argument", "review_report must be a skeptic review under research/reports/")
        if not REVIEW_VERDICT_RE.search(rp.read_text(encoding="utf-8")):
            raise ToolFailure("not_eligible", "the skeptic review does not say 'VERDICT: SURVIVES' — no lockbox request")
        lb = env.data.lockbox_status()
        if any(h["candidate_id"] == key for h in env.data.openings()):
            raise ToolFailure("not_eligible", f"{key} already used its single lockbox opening ({lb['lockbox_id']})")
        if lb["remaining"] <= 0:
            raise ToolFailure("not_eligible", f"lockbox {lb['lockbox_id']} has no openings left (human must roll it)")
        spec = reg.load(strategy_id, int(version))
        exp = spec_uses_experimental(spec)
        d = self.root / LOCKBOX_REQUESTS_REL
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{key}.yaml"
        if p.exists():
            prev = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            return {"request": str(p), "status": prev.get("status", "pending_human_approval"), "duplicate": True,
                    "lockbox_opened": False}
        rec = {"candidate_id": key, "strategy_id": strategy_id, "version": int(version),
               "logic_hash": spec.logic_hash(), "state": state, "requested_at": utcnow(), "requested_by": "ai (mcp)",
               "lockbox_id": lb["lockbox_id"], "openings_used": lb["openings"], "cap": lb["cap"],
               "justification": justification.strip(), "review_report": str(rp.relative_to(self.root.resolve())),
               "uses_experimental": exp,
               "human_block_review_required": bool(exp),
               "status": "pending_human_approval",
               "human_next_step": (f"python -m btc_quant.registry.admin_cli approve-lockbox {key} "
                                   f"--logic-hash {spec.logic_hash()}  (interactive, human only)")}
        p.write_text(yaml.safe_dump(rec, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return {"request": str(p), "status": "pending_human_approval", "duplicate": False, "lockbox_opened": False,
                "human_block_review_required": bool(exp), "uses_experimental": exp,
                "note": "This only records a request for a human. The lockbox was NOT opened; no approval was "
                        "written. Results of an approved opening reach you only as an outcome code."}

    # ========================================================== 상태 조회
    @tool
    def registry_status(self) -> dict[str, Any]:
        from btc_quant.params import gate_config_for_campaign
        from btc_quant.registry.strategies import StrategyRegistry
        from btc_quant.research.campaign import config_root
        env = self.env()
        reg = StrategyRegistry(root=self.root, ledger=env.ledger)
        lb = env.data.lockbox_status()
        cfg = env.data.config
        out: dict[str, Any] = {"strategies": reg.status(),
                               "lockbox": {k: lb[k] for k in ("lockbox_id", "cap", "openings", "remaining",
                                                              "pending_approvals")}}
        reqs = []
        d = self.root / LOCKBOX_REQUESTS_REL
        if d.exists():
            for p in sorted(d.glob("*.yaml")):
                r = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                reqs.append({k: r.get(k) for k in ("candidate_id", "status", "requested_at",
                                                   "human_block_review_required")})
        out["lockbox_requests"] = reqs
        try:
            gc = gate_config_for_campaign(None, root=config_root(env))
            cap = int(((gc.get("G6") or {}).get("val_budget") or {}).get("cap"))
            vid = cfg.get("val_id")
            if vid:
                out["val_budget"] = {k: v for k, v in env.ledger.val_status(vid, cap).items()}
        except Exception as e:  # noqa: BLE001
            out["val_budget"] = {"error": f"{type(e).__name__}: {e}"}
        return out

    @tool
    def params_status(self) -> dict[str, Any]:
        from btc_quant.params.costs import all_cost_models, cost_versions, round_trip_bps
        from btc_quant.params.proposals import list_proposals
        from btc_quant.params.registry import load_registry, params_version, validate
        from btc_quant.research.campaign import config_root
        root = config_root(self.env())
        reg = load_registry(root, validate_=False)
        errs = validate(reg)
        vs = cost_versions(root)
        return {"params_version": params_version(reg=reg),
                "n_params": len(reg.params), "by_class": {str(c): len(v) for c, v in reg.by_class().items()},
                "validation": errs or "OK",
                "cost_models": {n: {"version": vs.get(n), "fee_bp": m.fee_rate * 1e4,
                                    "round_trip_bp": round_trip_bps(m)} for n, m in all_cost_models(root).items()},
                "pending_proposals": [{k: p.get(k) for k in ("id", "class", "kind", "title", "status")}
                                      for p in list_proposals(root, status="pending")],
                "note": "Read-only. Class ② criteria may only be proposed via propose_criterion (synthetic "
                        "calibration, anti-goalpost); approvals are human-only."}

    @tool
    def propose_criterion(self, key: str, candidates: list, motivation: str, references: list[str] | None = None,
                          max_fpr: float = 0.05, min_power: float = 0.70, power_sharpe: float = 2.0,
                          mode: str = "bounded", n_null_markets: int = 20, n_planted_markets: int = 10,
                          jobs: int = 1) -> dict[str, Any]:
        from btc_quant.params.criteria import Targets, propose_criterion
        from btc_quant.research.campaign import config_root
        if mode not in ("bounded", "quick", "full"):
            raise ToolFailure("invalid_argument", "mode must be bounded | quick | full")
        if not candidates:
            raise ToolFailure("invalid_argument", "give candidate values")
        st = propose_criterion(key, list(candidates), Targets(max_fpr, min_power, power_sharpe), motivation,
                               references=list(references or []), mode=mode, n_null_markets=int(n_null_markets),
                               n_planted_markets=int(n_planted_markets), jobs=max(1, int(jobs)),
                               root=config_root(self.env()), progress=self.log, runner=self._criteria_runner)
        return {"key": st.key, "current": st.current, "candidates": st.candidates, "status": st.status,
                "recommended": st.recommended, "rows": [_scalars(r) for r in st.rows],
                "proposal_id": st.proposal_id, "report_path": st.report_path,
                "note": "A proposal applies from the next campaign and only after human approval "
                        "(python -m btc_quant.params approve <id>, human only)."}

    @tool
    def forward_status(self, strategy_key: str | None = None, limit: int = 30) -> dict[str, Any]:
        from btc_quant.registry.strategies import StrategyRegistry
        env = self.env()
        reg = StrategyRegistry(root=self.root, ledger=env.ledger)
        st = reg.status()
        recs = env.ledger.forward_results(strategy_key)
        by: dict[str, list] = {}
        for r in recs:
            by.setdefault(r["strategy_key"], []).append(r)
        rows = []
        for k, hs in sorted(by.items()):
            hs = sorted(hs, key=lambda h: h["as_of"])
            last = hs[-1]
            ok = [h for h in hs if h.get("status") == "ok"]
            rows.append({"strategy_key": k, "state": last.get("state"), "last_as_of": last["as_of"],
                         "last": self._project("forward_results", last), "n_records": len(hs), "n_ok": len(ok),
                         "within_band": f"{sum(1 for h in ok if h.get('within_band'))}/{len(ok)}"})
        lim = max(1, min(int(limit), MAX_ROWS))
        fwd_s, _ = env.data.segment_bounds("forward")
        return {"forward_start": fwd_s.isoformat(),
                "tracked_states": {s: st.get(s, []) for s in ("validated", "challenger")},
                "strategies": rows[:lim],
                "note": "Read-only. The daily forward run (python -m btc_quant.research.forward run) is a "
                        "scheduled job, not an MCP tool."}


# ============================================================== 수용 검사 서브프로세스
_ACCEPT_SCRIPT = r'''
import importlib.util, json, sys
path, modname, docs = sys.argv[1], sys.argv[2], (sys.argv[3] or None)
from btc_quant.strategy.blocks import REGISTRY
from btc_quant.strategy.blocks.acceptance import acceptance_check
before = set(REGISTRY._defs)
if modname in sys.modules:
    names = sorted(n for n, d in REGISTRY._defs.items() if getattr(d.fn, "__module__", None) == modname)
else:
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    names = sorted(set(REGISTRY._defs) - before)
out = []
for n in names:
    d = REGISTRY.get(n)
    rep = acceptance_check(n, docs_path=docs)
    out.append({"block": n, "kind": d.kind, "status": d.status, "passed": rep.passed, "failed": rep.failed,
                "checks": rep.to_dict()["checks"], "requires": list(d.requires), "live_ready": d.live_ready})
sys.stdout.write("\x00ACCEPT_JSON" + json.dumps({"blocks": out}, default=str))
'''


def _load_block_module(path: Path, modname: str) -> bool | str:
    """수용 통과 모듈을 이 프로세스에 적재 (이미 적재돼 있으면 그대로 — 편집본은 서버 재시작 필요)."""
    import importlib.util
    if modname in sys.modules:
        return "already loaded at server start (restart the MCP server to load an edited version)"
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(modname, None)
        raise
    return True
