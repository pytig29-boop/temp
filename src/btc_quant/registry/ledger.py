"""연구 원장 (append-only JSONL, PLAN §2.3·§4.3·§5.1·§5.3).

research/ledger/ 아래 git 친화적 JSONL 파일:
  hypotheses.jsonl  — 가설 사전등록
  campaigns.jsonl   — 격자·예산·비용모델·기간 (실행 *전에* 기록)
  runs.jsonl        — 개별 실행 (스펙·비용·기간·데이터 스냅샷·코드 커밋·지표)
  lockbox.jsonl     — 잠금 구간 개봉(event=open) / 결과(event=result)
  transitions.jsonl — 전략 레지스트리 상태 전이
  val_evaluations.jsonl — 검증(Val) 구간 사용 기록(event=open → event=result, PLAN §10b-4).
                    k = 같은 val_id 안의 사용 순번. 누적 Holm 보정·상한의 근거.
                    결과는 pass/fail + 요약 통계만(월별·일별 분해 금지 — Val 구조 누설 방지).
  forward_results.jsonl — 전진 인큐베이션 일일 결과(PLAN §10b-5). (strategy_key, logic_hash, as_of)
                    당 1줄(멱등).

규칙: 기존 줄은 절대 수정·삭제하지 않는다. id는 파일별 단조 증가(접두사-일련번호).
동시 추가는 research/ledger/.lock 에 대한 fcntl 배타 잠금으로 직렬화한다.
조회는 duckdb read_json_auto.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import duckdb


def repo_root() -> Path:
    """pyproject.toml 이 있는 상위 디렉터리(없으면 cwd)."""
    env = os.environ.get("BTC_QUANT_ROOT")
    if env:
        return Path(env)
    for p in Path(__file__).resolve().parents:
        if (p / "pyproject.toml").exists():
            return p
    return Path.cwd()


TABLES = {
    "hypotheses": "hyp",
    "campaigns": "camp",
    "runs": "run",
    "lockbox": "lbx",
    "transitions": "trn",
    "val_evaluations": "val",
    "forward_results": "fwd",
    "criteria_revisions": "crv",     # 자율 체제(D30): 판정 기준 변경 기록 (엄격/중립/느슨/새 프로필)
}

# Val 결과 레코드에 남기지 않는 지표 (연도·월 단위 분해 = Val 구조 누설)
_VAL_DROP_PREFIXES = ("sharpe_year_", "month", "yearly", "monthly", "daily")


class LedgerError(RuntimeError):
    pass


class BudgetExceededError(LedgerError):
    pass


class ValBudgetExhaustedError(LedgerError):
    """Val 사용 상한 도달 — Val 롤링(사람)이 필요."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(o: Any) -> Any:
    """JSON 안전화: NaN/inf → None, 튜플 → 리스트, numpy/pandas 스칼라 → 파이썬."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_clean(v) for v in o]
    if hasattr(o, "item") and callable(o.item) and not isinstance(o, (str, bytes)):
        try:
            o = o.item()
        except (ValueError, TypeError):
            pass
    if hasattr(o, "isoformat") and not isinstance(o, str):
        return o.isoformat()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    return str(o)


class Ledger:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else repo_root() / "research" / "ledger"
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 저수준
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

    def _count_lines(self, table: str) -> int:
        p = self.path(table)
        if not p.exists():
            return 0
        with open(p, "rb") as fh:
            return sum(1 for line in fh if line.strip())

    def _append_locked(self, table: str, record: dict) -> dict:
        """잠금을 잡은 상태에서 호출. id·seq·ts 부여 후 한 줄 추가."""
        seq = self._count_lines(table) + 1
        rec = {"id": f"{TABLES[table]}-{seq:06d}", "seq": seq, "ts": utcnow(), **_clean(record)}
        line = json.dumps(rec, ensure_ascii=False, sort_keys=False, allow_nan=False)
        p = self.path(table)
        # 마지막 줄이 개행 없이 끝났다면(비정상 종료) 보정
        if p.exists() and p.stat().st_size > 0:
            with open(p, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    line = "\n" + line
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return rec

    def _append_many_locked(self, table: str, records: list[dict]) -> list[dict]:
        """잠금을 잡은 상태에서 호출. 여러 줄을 한 번의 쓰기로 추가 (줄 수는 한 번만 센다)."""
        if not records:
            return []
        seq0 = self._count_lines(table)
        now = utcnow()
        out, lines = [], []
        for j, r in enumerate(records, start=1):
            rec = {"id": f"{TABLES[table]}-{seq0 + j:06d}", "seq": seq0 + j, "ts": now, **_clean(r)}
            out.append(rec)
            lines.append(json.dumps(rec, ensure_ascii=False, sort_keys=False, allow_nan=False))
        p = self.path(table)
        prefix = ""
        if p.exists() and p.stat().st_size > 0:
            with open(p, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    prefix = "\n"
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(prefix + "\n".join(lines) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return out

    def append(self, table: str, record: dict) -> dict:
        with self.lock():
            return self._append_locked(table, record)

    def records(self, table: str) -> list[dict]:
        """전체 레코드(파이썬 json 파싱 — 중첩 구조를 그대로 보존)."""
        p = self.path(table)
        if not p.exists():
            return []
        out = []
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(json.loads(line))
        return out

    def get(self, table: str, id_: str) -> dict | None:
        for r in self.records(table):
            if r["id"] == id_:
                return r
        return None

    def query(self, sql: str) -> "duckdb.DuckDBPyRelation":
        """원장 테이블을 같은 이름의 뷰로 노출한 뒤 SQL 실행 (pandas는 .df())."""
        con = duckdb.connect()
        for t in TABLES:
            p = self.path(t)
            if p.exists() and p.stat().st_size > 0:
                con.execute(
                    f"CREATE VIEW {t} AS SELECT * FROM read_json_auto('{p.as_posix()}', "
                    "format='newline_delimited', union_by_name=true)")
        return con.sql(sql)

    # ------------------------------------------------------------ 가설·캠페인
    def register_hypothesis(self, text: str, rationale: str, success_criteria: str | dict,
                            author: str = "ai") -> str:
        if not text or not text.strip():
            raise ValueError("hypothesis text required")
        return self.append("hypotheses", {
            "text": text, "rationale": rationale, "success_criteria": success_criteria,
            "author": author})["id"]

    def create_campaign(self, hypothesis_id: str, grid: dict, budget: int,
                        cost_models: list[str], period: dict | list | tuple,
                        family: str | None = None, note: str = "") -> str:
        """격자·예산을 실행 전에 고정. budget = 이 캠페인에서 허용되는 시도(고유 logic_hash) 수."""
        if self.get("hypotheses", hypothesis_id) is None:
            raise LedgerError(f"unknown hypothesis {hypothesis_id}")
        budget = int(budget)
        if budget <= 0:
            raise ValueError("budget must be > 0")
        if not cost_models:
            raise ValueError("cost_models required")
        n_grid = 1
        for vals in (grid or {}).values():
            n_grid *= max(1, len(vals)) if isinstance(vals, (list, tuple)) else 1
        return self.append("campaigns", {
            "hypothesis_id": hypothesis_id, "grid": grid, "grid_size": n_grid,
            "budget": budget, "cost_models": list(cost_models), "period": _period(period),
            "family": family, "note": note})["id"]

    # ------------------------------------------------------------ 실행
    def record_run(self, campaign_id: str, spec, cost_name: str, period, data_snapshot: str,
                   code_commit: str, metrics: dict, family: str | None = None, *,
                   params_version: str | None = None, cost_versions: dict | None = None) -> str:
        """spec: StrategySpec 또는 그 dict. 캠페인 예산(고유 logic_hash 수)을 넘는 새 시도는 거부.

        params_version / cost_versions (선택, docs/PARAMS.md §4): `btc_quant.params.params_snapshot()` 의
        값. 주어지면 레코드에 같은 이름의 필드로 추가된다(없으면 생략 → 이전 형식과 동일)."""
        from btc_quant.strategy.spec import StrategySpec

        if not isinstance(spec, StrategySpec):
            spec = StrategySpec.from_dict(spec)
        lh = spec.logic_hash()
        with self.lock():
            camp = self.get("campaigns", campaign_id)
            if camp is None:
                raise LedgerError(f"unknown campaign {campaign_id} (create_campaign first)")
            if cost_name not in camp["cost_models"]:
                raise LedgerError(f"cost {cost_name!r} not pre-registered in {campaign_id}: "
                                  f"{camp['cost_models']}")
            seen = {r["logic_hash"] for r in self.records("runs") if r["campaign_id"] == campaign_id}
            if lh not in seen and len(seen) >= camp["budget"]:
                raise BudgetExceededError(
                    f"campaign {campaign_id} budget {camp['budget']} exhausted; "
                    "register a new campaign (trials accumulate)")
            return self._append_locked("runs", {
                "campaign_id": campaign_id,
                "hypothesis_id": camp["hypothesis_id"],
                "family": family or camp.get("family") or spec.entry.block,
                "spec_id": spec.id, "spec_version": spec.version, "logic_hash": lh,
                "spec": spec.to_dict(), "cost_name": cost_name, "period": _period(period),
                "data_snapshot": data_snapshot, "code_commit": code_commit,
                "metrics": metrics or {}, **_param_fields(params_version, cost_versions)})["id"]

    def record_runs(self, campaign_id: str, items, period, data_snapshot: str, code_commit: str,
                    family: str | None = None, *, params_version: str | None = None,
                    cost_versions: dict | None = None) -> list[str]:
        """record_run 의 일괄판 (O(n): runs.jsonl 을 한 번만 읽고 한 번에 추가).

        items: [(spec | spec dict, cost_name, metrics), ...]. 검증(사전등록 비용, 캠페인 예산 =
        고유 logic_hash 수)은 record_run 과 같고, 하나라도 어긋나면 아무것도 쓰지 않는다.
        레코드 형식도 record_run 과 동일하다(params_version / cost_versions 선택 필드 포함).
        반환 = run id 목록(입력 순서).
        """
        from btc_quant.strategy.spec import StrategySpec

        norm = []
        for spec, cost_name, mets in items:
            if not isinstance(spec, StrategySpec):
                spec = StrategySpec.from_dict(spec)
            norm.append((spec, spec.logic_hash(), cost_name, mets))
        per = _period(period)
        extra = _param_fields(params_version, cost_versions)
        with self.lock():
            camp = self.get("campaigns", campaign_id)
            if camp is None:
                raise LedgerError(f"unknown campaign {campaign_id} (create_campaign first)")
            seen = {r["logic_hash"] for r in self.records("runs") if r["campaign_id"] == campaign_id}
            for _, lh, cost_name, _ in norm:
                if cost_name not in camp["cost_models"]:
                    raise LedgerError(f"cost {cost_name!r} not pre-registered in {campaign_id}: "
                                      f"{camp['cost_models']}")
                seen.add(lh)
            if len(seen) > camp["budget"]:
                raise BudgetExceededError(
                    f"campaign {campaign_id} budget {camp['budget']} exceeded ({len(seen)} distinct "
                    "specs); register a new campaign (trials accumulate)")
            recs = [{
                "campaign_id": campaign_id, "hypothesis_id": camp["hypothesis_id"],
                "family": family or camp.get("family") or spec.entry.block,
                "spec_id": spec.id, "spec_version": spec.version, "logic_hash": lh,
                "spec": spec.to_dict(), "cost_name": cost_name, "period": per,
                "data_snapshot": data_snapshot, "code_commit": code_commit,
                "metrics": mets or {}, **extra} for spec, lh, cost_name, mets in norm]
            return [r["id"] for r in self._append_many_locked("runs", recs)]

    def record_exploration(self, family: str, description: str, configs_evaluated: int, period: dict,
                           *, labels: list[str] | None = None, author: str = "ai", note: str = "",
                           code_commit: str | None = None, data_snapshot: str | None = None) -> dict:
        """탐색적 시도(사이드 계산: 연도별 이벤트 스터디 등)를 원장에 남긴다 — 캠페인이 아닌 계산도 시도다.

        hypotheses 1줄 + campaigns 1줄(grid.kind = "exploration") + runs `configs_evaluated` 줄(고유 logic_hash =
        exploration 캠페인 id + 순번 → `trials_count(family=…)` 에 그대로 더해져 G5 DSR 의 N_family 를 올린다).
        runs.spec 은 없고 metrics.kind = "exploration" (전략 지표 없음). 반환 {hypothesis_id, campaign_id, run_ids}."""
        import hashlib

        n = int(configs_evaluated)
        if n < 1:
            raise ValueError("configs_evaluated must be ≥ 1")
        if not family or not str(family).strip():
            raise ValueError("family required (exploratory trials count toward that family)")
        if not description or not str(description).strip():
            raise ValueError("description required")
        if labels is not None and len(labels) != n:
            raise ValueError("labels must have one entry per evaluated config")
        per = _period(period)
        with self.lock():
            hyp = self._append_locked("hypotheses", {
                "text": f"[exploration] {description}", "rationale": "exploratory side calculation (logged trial)",
                "success_criteria": "n/a (exploration: counted as trials, no gate)", "author": author})
            camp = self._append_locked("campaigns", {
                "hypothesis_id": hyp["id"], "grid": {"kind": "exploration", "description": description,
                                                     "configs_evaluated": n, "segment": per.get("segment"),
                                                     **({"labels": list(labels)} if labels else {})},
                "grid_size": n, "budget": n, "cost_models": ["exploration"], "period": per, "family": family,
                "note": note or f"exploration: {n} config(s) evaluated on {per.get('segment')}"})
            recs = []
            for i in range(n):
                lh = hashlib.sha256(f"{camp['id']}:{i}".encode()).hexdigest()[:16]
                recs.append({"campaign_id": camp["id"], "hypothesis_id": hyp["id"], "family": family,
                             "spec_id": f"exploration_{camp['id']}_{i + 1}", "spec_version": None, "logic_hash": lh,
                             "spec": None, "cost_name": "exploration", "period": per,
                             "data_snapshot": data_snapshot, "code_commit": code_commit,
                             "metrics": {"kind": "exploration", "index": i + 1,
                                         **({"label": str(labels[i])} if labels else {})}})
            runs = self._append_many_locked("runs", recs)
        return {"hypothesis_id": hyp["id"], "campaign_id": camp["id"], "run_ids": [r["id"] for r in runs]}

    def trials_count(self, campaign_id: str | None = None, family: str | None = None) -> int:
        """시도 횟수 = 고유 (campaign_id, logic_hash) 수. 같은 스펙의 비용 모델별 실행은 1회로 센다.
        캠페인을 새로 만들어 다시 돌린 조합은 누적된다(PLAN §5.1)."""
        p = self.path("runs")
        if not p.exists() or p.stat().st_size == 0:
            return 0
        conds, params = [], []
        if campaign_id is not None:
            conds.append("campaign_id = ?")
            params.append(campaign_id)
        if family is not None:
            conds.append("family = ?")
            params.append(family)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        q = (f"SELECT count(*) FROM (SELECT DISTINCT campaign_id, logic_hash FROM "
             f"read_json_auto('{p.as_posix()}', format='newline_delimited', union_by_name=true)"
             f"{where})")
        return int(duckdb.connect().execute(q, params).fetchone()[0])

    # ------------------------------------------------------------ 잠금 구간
    def record_lockbox_opening(self, candidate_id: str, lockbox_id: str, approval: dict,
                               logic_hash: str | None = None, note: str = "") -> str:
        return self.append("lockbox", {
            "event": "open", "candidate_id": candidate_id, "lockbox_id": lockbox_id,
            "logic_hash": logic_hash, "approval": approval, "note": note})["id"]

    def record_lockbox_result(self, opening_id: str, result: dict) -> str:
        with self.lock():
            op = self.get("lockbox", opening_id)
            if op is None or op.get("event") != "open":
                raise LedgerError(f"unknown lockbox opening {opening_id}")
            if any(r.get("event") == "result" and r.get("opening_id") == opening_id
                   for r in self.records("lockbox")):
                raise LedgerError(f"result already recorded for {opening_id}")
            return self._append_locked("lockbox", {
                "event": "result", "opening_id": opening_id,
                "candidate_id": op["candidate_id"], "lockbox_id": op["lockbox_id"],
                "result": result})["id"]

    def lockbox_history(self, lockbox_id: str | None = None) -> list[dict]:
        """개봉 순서대로. 각 항목에 k(해당 잠금 구간 내 개봉 순번)와 result(없으면 None)."""
        recs = self.records("lockbox")
        results = {r["opening_id"]: r for r in recs if r.get("event") == "result"}
        out, k = [], {}
        for r in recs:
            if r.get("event") != "open":
                continue
            k[r["lockbox_id"]] = k.get(r["lockbox_id"], 0) + 1
            if lockbox_id is not None and r["lockbox_id"] != lockbox_id:
                continue
            res = results.get(r["id"])
            out.append({**r, "k": k[r["lockbox_id"]],
                        "result": res["result"] if res else None,
                        "result_ts": res["ts"] if res else None})
        return out

    # ------------------------------------------------------------ 검증(Val) 구간 사용 원장
    def open_val_evaluation(self, logic_hash: str, campaign_id: str, val_id: str, cap: int,
                            spec_id: str | None = None, note: str = "", extra: dict | None = None) -> dict:
        """Val 평가 *전에* 사용을 기록한다 (event=open). k = 이 val_id 의 사용 순번(1부터).

        - 같은 (val_id, logic_hash) 가 이미 열렸으면 새로 세지 않고 LedgerError (호출 측은
          val_prior() 로 이전 결정을 재사용한다).
        - k > cap 이면 ValBudgetExhaustedError (아무것도 기록하지 않음) — Val 을 롤링해야 한다.
        반환 = 기록된 레코드(+ "k").
        """
        if not logic_hash:
            raise ValueError("logic_hash required")
        with self.lock():
            hist = [r for r in self.records("val_evaluations")
                    if r.get("event") == "open" and r.get("val_id") == val_id]
            if any(r["logic_hash"] == logic_hash for r in hist):
                raise LedgerError(f"{logic_hash} already evaluated on {val_id} (reuse val_prior())")
            k = len(hist) + 1
            if k > int(cap):
                raise ValBudgetExhaustedError(
                    f"Val {val_id} reached its cap of {cap} evaluations. Roll Val (human): fold it into "
                    "dev, take a fresh val segment from later data, and give it a new val_id.")
            rec = self._append_locked("val_evaluations", {
                "event": "open", "val_id": val_id, "k": k, "logic_hash": logic_hash,
                "campaign_id": campaign_id, "spec_id": spec_id, "note": note, **(extra or {})})
            return rec

    def record_val_result(self, opening_id: str, passed: bool, p_value: float | None,
                          summary: dict | None = None, decision: dict | None = None) -> str:
        """Val 평가 결과 (event=result). summary 는 요약 통계만 — 연·월·일 분해 키는 버린다."""
        with self.lock():
            recs = self.records("val_evaluations")
            op = next((r for r in recs if r["id"] == opening_id and r.get("event") == "open"), None)
            if op is None:
                raise LedgerError(f"unknown val evaluation {opening_id}")
            if any(r.get("event") == "result" and r.get("opening_id") == opening_id for r in recs):
                raise LedgerError(f"result already recorded for {opening_id}")
            summ = {k: v for k, v in (summary or {}).items()
                    if not str(k).startswith(_VAL_DROP_PREFIXES) and not isinstance(v, (dict, list))}
            return self._append_locked("val_evaluations", {
                "event": "result", "opening_id": opening_id, "val_id": op["val_id"], "k": op["k"],
                "logic_hash": op["logic_hash"], "campaign_id": op["campaign_id"],
                "passed": bool(passed), "p_value": p_value, "summary": summ,
                "decision": decision or {}})["id"]

    def val_uses_since(self, since_ts: str) -> int:
        """자율 프로그램 판정 예산(D31): since_ts(ISO) 이후 새로 연 Val 사용 수 (val_id 무관)."""
        return sum(1 for r in self.records("val_evaluations")
                   if r.get("event") == "open" and str(r["ts"]) >= str(since_ts))

    # ------------------------------------------------------------ 판정 기준 변경 (자율 체제 D30)
    REVISION_KINDS = ("stricter", "neutral", "looser", "profile_new", "initial")

    def record_criteria_revision(self, kind: str, keys: list, frm: dict, to: dict, *, cert_id: str | None,
                                 ruleset_before: str | None, ruleset_after: str | None, reason: str,
                                 rescue_table: dict | None = None, scope: str = "dev") -> str:
        """기준 변경 1건 (추가 전용). kind ∈ REVISION_KINDS, scope = 변경이 읽는 구간(dev | val | lockbox | paper).
        looser·profile_new 는 시도 수 벌점 L 에 들어간다(`loosenings`). initial = 프로그램 시작 전 사전 확정."""
        if kind not in self.REVISION_KINDS:
            raise ValueError(f"kind must be one of {self.REVISION_KINDS}")
        if kind in ("looser", "profile_new") and not cert_id:
            raise LedgerError("a loosening needs a passed certification id")
        return self.append("criteria_revisions", {
            "kind": kind, "scope": scope, "keys": list(keys), "from": frm, "to": to, "cert_id": cert_id,
            "ruleset_before": ruleset_before, "ruleset_after": ruleset_after, "reason": reason,
            "rescue_table": rescue_table or {}})["id"]

    def criteria_revisions(self) -> list[dict]:
        return self.records("criteria_revisions")

    def loosenings(self, since_ts: str | None = None, scopes: tuple | None = None) -> int:
        """벌점 L: looser·profile_new 변경 수 (since_ts 이후, scopes 가 주어지면 그 구간만)."""
        return sum(1 for r in self.criteria_revisions()
                   if r["kind"] in ("looser", "profile_new")
                   and (since_ts is None or str(r["ts"]) >= str(since_ts))
                   and (scopes is None or r.get("scope") in scopes))

    def val_history(self, val_id: str | None = None) -> list[dict]:
        """사용 순서대로 open 레코드 + result(없으면 None: 평가 중단/진행 중)."""
        recs = self.records("val_evaluations")
        results = {r["opening_id"]: r for r in recs if r.get("event") == "result"}
        out = []
        for r in recs:
            if r.get("event") != "open" or (val_id is not None and r["val_id"] != val_id):
                continue
            res = results.get(r["id"])
            out.append({**r, "result": res, "passed": res["passed"] if res else None,
                        "p_value": res["p_value"] if res else None})
        return out

    def val_prior(self, val_id: str, logic_hash: str) -> dict | None:
        """이 logic_hash 의 이전 Val 사용(있으면). 같은 스펙은 Val 을 두 번 쓰지 않는다."""
        return next((h for h in self.val_history(val_id) if h["logic_hash"] == logic_hash), None)

    def val_prior_p_values(self, val_id: str, before_k: int | None = None) -> list[float]:
        """누적 Holm 입력: 이 val_id 의 사용 p 값(사용 순서, before_k 가 있으면 k < before_k 만).
        결과가 없는(중단된) 사용은 p = 1 로 센다 — 사용 자체가 시도이므로 빼지 않는다."""
        out = []
        for h in self.val_history(val_id):
            if before_k is not None and h["k"] >= before_k:
                continue
            p = h["p_value"]
            out.append(1.0 if p is None else float(p))
        return out

    def val_status(self, val_id: str, cap: int) -> dict:
        hist = self.val_history(val_id)
        return {"val_id": val_id, "cap": int(cap), "used": len(hist),
                "remaining": max(0, int(cap) - len(hist)),
                "passed": sum(1 for h in hist if h["passed"]),
                "pending": sum(1 for h in hist if h["result"] is None)}

    # ------------------------------------------------------------ 전진 인큐베이션
    def record_forward_result(self, strategy_key: str, logic_hash: str, as_of: str,
                              result: dict) -> tuple[str, bool]:
        """(strategy_key, logic_hash, as_of) 당 1줄. 이미 있으면 쓰지 않고 (기존 id, False)."""
        with self.lock():
            for r in self.records("forward_results"):
                if (r["strategy_key"], r["logic_hash"], r["as_of"]) == (strategy_key, logic_hash, as_of):
                    return r["id"], False
            rec = self._append_locked("forward_results", {
                "strategy_key": strategy_key, "logic_hash": logic_hash, "as_of": as_of, **result})
            return rec["id"], True

    def forward_results(self, strategy_key: str | None = None, as_of: str | None = None) -> list[dict]:
        return [r for r in self.records("forward_results")
                if (strategy_key is None or r["strategy_key"] == strategy_key)
                and (as_of is None or r["as_of"] == as_of)]

    # ------------------------------------------------------------ 상태 전이
    def record_transition(self, strategy_id: str, version: int, from_state: str | None,
                          to_state: str, reason: str, actor: str, **extra) -> str:
        return self.append("transitions", {
            "strategy_id": strategy_id, "version": int(version), "from_state": from_state,
            "to_state": to_state, "reason": reason, "actor": actor, **extra})["id"]

    def transitions(self, strategy_id: str | None = None) -> list[dict]:
        return [r for r in self.records("transitions")
                if strategy_id is None or r["strategy_id"] == strategy_id]


def _param_fields(params_version: str | None, cost_versions: dict | None) -> dict:
    """실행 레코드의 선택 필드 (파라미터 거버넌스 버전). None 이면 생략 — 이전 레코드와 같은 형식."""
    out: dict = {}
    if params_version is not None:
        out["params_version"] = str(params_version)
    if cost_versions is not None:
        out["cost_versions"] = {str(k): int(v) for k, v in dict(cost_versions).items()}
    return out


def _period(period) -> dict:
    if isinstance(period, dict):
        return dict(period)
    if isinstance(period, (list, tuple)) and len(period) == 2:
        return {"start": period[0], "end": period[1]}
    if isinstance(period, str):
        return {"segment": period}
    raise ValueError(f"bad period {period!r}")
