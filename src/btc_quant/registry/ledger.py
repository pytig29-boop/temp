"""연구 원장 (append-only JSONL, PLAN §2.3·§4.3·§5.1·§5.3).

research/ledger/ 아래 git 친화적 JSONL 파일:
  hypotheses.jsonl  — 가설 사전등록
  campaigns.jsonl   — 격자·예산·비용모델·기간 (실행 *전에* 기록)
  runs.jsonl        — 개별 실행 (스펙·비용·기간·데이터 스냅샷·코드 커밋·지표)
  lockbox.jsonl     — 잠금 구간 개봉(event=open) / 결과(event=result)
  transitions.jsonl — 전략 레지스트리 상태 전이

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
}


class LedgerError(RuntimeError):
    pass


class BudgetExceededError(LedgerError):
    pass


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
                   code_commit: str, metrics: dict, family: str | None = None) -> str:
        """spec: StrategySpec 또는 그 dict. 캠페인 예산(고유 logic_hash 수)을 넘는 새 시도는 거부."""
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
                "metrics": metrics or {}})["id"]

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

    # ------------------------------------------------------------ 상태 전이
    def record_transition(self, strategy_id: str, version: int, from_state: str | None,
                          to_state: str, reason: str, actor: str, **extra) -> str:
        return self.append("transitions", {
            "strategy_id": strategy_id, "version": int(version), "from_state": from_state,
            "to_state": to_state, "reason": reason, "actor": actor, **extra})["id"]

    def transitions(self, strategy_id: str | None = None) -> list[dict]:
        return [r for r in self.records("transitions")
                if strategy_id is None or r["strategy_id"] == strategy_id]


def _period(period) -> dict:
    if isinstance(period, dict):
        return dict(period)
    if isinstance(period, (list, tuple)) and len(period) == 2:
        return {"start": period[0], "end": period[1]}
    if isinstance(period, str):
        return {"segment": period}
    raise ValueError(f"bad period {period!r}")
