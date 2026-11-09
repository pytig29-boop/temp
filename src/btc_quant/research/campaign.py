"""연구 캠페인: 템플릿(YAML) 검증 → 격자 전수 열거 → 원장 사전등록 → Dev 전수 실행 → 결과 저장.

PLAN §5.1 ①~③. 관문(④~⑥)은 `pipeline.py`, 리포트는 `report.py`.

템플릿 형식은 `research/campaigns/README.md` 와 `research/campaign_templates/*.yaml` 참조.

절차 (`run_campaign`)
1. 템플릿 검증 + 격자 전수 열거(StrategySpec.validate, ≤2 필터, ≤8 파라미터). 조합 수 > budget 이면
   **원장에 아무것도 쓰지 않고** `CampaignBudgetError`.
2. 데이터 스냅샷 id 계산(파일 해시만, 백테스트 아님) → 가설·캠페인을 원장에 등록(**실행 전**).
3. Dev 데이터는 `DataAccess.read("dev", …)` 로만 읽고 `regularize(clean_wicks(…))`.
4. 진입·필터 블록 신호는 (블록, 파라미터)마다 한 번씩만 계산·캐시, (진입 파라미터 × 필터 파라미터)
   조합마다 `prepare` 한 번, 청산 격자는 `simulate_many` 로 스윕.
5. 스펙 × 평가 비용(primary + grade)마다 `metrics.summarize` → 원장 runs 에 모두 기록
   (스냅샷 id, 코드 커밋 포함).
6. research/campaigns/<campaign_id>/ 에 결과 저장 (README 참조: 무엇을 git 에 넣는지).
"""
from __future__ import annotations

import itertools
import json
import math
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import yaml

from btc_quant.backtest import fast
from btc_quant.backtest.types import (COINONE_BASE, COINONE_CONSERVATIVE, COINONE_STRESS,
                                      UPBIT_BASE, UPBIT_CONSERVATIVE, CostModel)
from btc_quant.data.quality import clean_wicks, regularize
from btc_quant.evaluation import metrics as M
from btc_quant.registry.ledger import Ledger, LedgerError, repo_root, utcnow
from btc_quant.registry.snapshot import snapshot_id
from btc_quant.registry.splits import DataAccess
from btc_quant.registry.strategies import StrategyRegistry
from btc_quant.strategy.blocks import REGISTRY, Context, Registry
from btc_quant.strategy.features import to_5m
from btc_quant.strategy.spec import MAX_FILTERS, BlockRef, ExitSpec, StrategySpec

# types.py 에 업비트 stress 프리셋이 없어 여기서 정의 (코인원 stress 와 같은 스프레드·슬리피지 + 업비트 수수료).
# 권장: backtest/types.py 에 UPBIT_STRESS 추가.
UPBIT_STRESS = CostModel("upbit_stress", 0.0005, 5.0, 8.0)

COST_MODELS: dict[str, CostModel] = {c.name: c for c in (
    COINONE_BASE, COINONE_CONSERVATIVE, COINONE_STRESS, UPBIT_BASE, UPBIT_CONSERVATIVE, UPBIT_STRESS)}

DEFAULT_COSTS = {"primary": "coinone_conservative", "grade": "upbit_conservative",
                 "stress": "coinone_stress", "grade_stress": "upbit_stress"}
EXIT_KEYS = ("sl_pct", "tp_pct", "max_hold_min")
HALF_LIFE_DAYS = 365          # PLAN D1


class CampaignError(ValueError):
    pass


class CampaignBudgetError(CampaignError):
    pass


def get_cost(name: str) -> CostModel:
    try:
        return COST_MODELS[name]
    except KeyError:
        raise CampaignError(f"unknown cost model {name!r}; one of {sorted(COST_MODELS)}") from None


# ============================================================== 경로
@dataclass
class ResearchEnv:
    """연구 계층이 쓰는 저장소들. root 하나로 테스트용 임시 저장소를 통째로 바꿀 수 있다."""
    root: Path
    ledger: Ledger
    data: DataAccess
    registry: StrategyRegistry
    campaigns_dir: Path
    reports_dir: Path
    code_root: Path

    @classmethod
    def at(cls, root: str | Path | None = None) -> "ResearchEnv":
        r = Path(root) if root is not None else repo_root()
        ledger = Ledger(r / "research" / "ledger")
        return cls(root=r, ledger=ledger, data=DataAccess(root=r, ledger=ledger),
                   registry=StrategyRegistry(root=r, ledger=ledger),
                   campaigns_dir=r / "research" / "campaigns",
                   reports_dir=r / "research" / "reports", code_root=repo_root())

    def campaign_dir(self, campaign_id: str) -> Path:
        return self.campaigns_dir / campaign_id


def git_commit(cwd: str | Path | None = None) -> str:
    """현재 코드 커밋. 추적 파일에 커밋 안 된 변경이 있으면 '+dirty'. git 없으면 'unknown'."""
    cwd = str(cwd or repo_root())
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True,
                             timeout=10, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=cwd,
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ============================================================== 템플릿
@dataclass(frozen=True)
class BlockGrid:
    block: str
    grid: dict[str, list]           # param → 값 목록 (순서 = 격자 순서, G3 인접 정의에 사용)

    def combos(self) -> list[dict]:
        keys = list(self.grid)
        return [dict(zip(keys, vals)) for vals in itertools.product(*(self.grid[k] for k in keys))]


@dataclass
class CampaignTemplate:
    name: str
    hypothesis: dict                # text, rationale, success_criteria
    exchange: str
    symbol: str
    timeframe: str
    aux: list[str]
    entry: BlockGrid
    filter_sets: list[list[BlockGrid]]
    exits: dict[str, list]          # sl_pct, tp_pct, max_hold_min
    costs: dict[str, str]           # primary, grade, stress, grade_stress
    budget: int
    family: str
    segment: str = "dev"
    raw: dict = field(default_factory=dict)

    # ---------------------------------------------------------- 로드·검증
    @classmethod
    def from_yaml(cls, path: str | Path, registry: Registry = REGISTRY) -> "CampaignTemplate":
        d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(d, registry)

    @classmethod
    def from_dict(cls, d: dict, registry: Registry = REGISTRY) -> "CampaignTemplate":
        if not isinstance(d, dict):
            raise CampaignError("template must be a mapping")
        allowed = {"name", "hypothesis", "data", "entry", "filters", "exits", "costs", "budget",
                   "family", "segment"}
        extra = set(d) - allowed
        if extra:
            raise CampaignError(f"unknown template keys {sorted(extra)}")
        name = str(d.get("name") or "").strip()
        if not name or not all(ch.isalnum() or ch in "_-" for ch in name):
            raise CampaignError("name required: [A-Za-z0-9_-]+")
        hyp = d.get("hypothesis") or {}
        for k in ("text", "rationale", "success_criteria"):
            if not str(hyp.get(k) or "").strip():
                raise CampaignError(f"hypothesis.{k} required")
        data = d.get("data") or {}
        aux = list(data.get("aux") or [])
        if aux:
            raise CampaignError(f"aux data {aux} not supported yet (no live-available reference feed); "
                                "use aux: []")
        entry = _block_grid(d.get("entry"), "entry", registry)
        fsets_raw = d.get("filters", [[]])
        if not isinstance(fsets_raw, list) or not fsets_raw:
            raise CampaignError("filters must be a non-empty list of filter sets ([] = no filter)")
        fsets = []
        for fs in fsets_raw:
            fs = fs or []
            if not isinstance(fs, list):
                raise CampaignError("each filter set must be a list")
            if len(fs) > MAX_FILTERS:
                raise CampaignError(f"filter set has {len(fs)} filters > {MAX_FILTERS}")
            blocks = [_block_grid(f, "filter", registry) for f in fs]
            if len({b.block for b in blocks}) != len(blocks):
                raise CampaignError("duplicate filter block within a set")
            fsets.append(blocks)
        keys = [tuple(sorted(b.block for b in fs)) for fs in fsets]
        if len(set(keys)) != len(keys):
            raise CampaignError("duplicate filter sets (same blocks); merge their grids instead")
        ex = d.get("exits") or {}
        if set(ex) != set(EXIT_KEYS):
            raise CampaignError(f"exits must have exactly {EXIT_KEYS}")
        exits = {}
        for k in EXIT_KEYS:
            vals = _value_list(ex[k], f"exits.{k}")
            for v in vals:
                if not (isinstance(v, (int, float)) and v > 0):
                    raise CampaignError(f"exits.{k} values must be > 0")
                if k == "max_hold_min" and (int(v) != v or int(v) % 5):
                    raise CampaignError("max_hold_min values must be positive multiples of 5")
            exits[k] = [int(v) for v in vals] if k == "max_hold_min" else [float(v) for v in vals]
        costs = {**DEFAULT_COSTS, **(d.get("costs") or {})}
        if set(costs) != set(DEFAULT_COSTS):
            raise CampaignError(f"costs keys must be {sorted(DEFAULT_COSTS)}")
        for c in costs.values():
            get_cost(c)
        if len(set(costs.values())) != len(costs):
            raise CampaignError("cost roles must use distinct cost models")
        budget = d.get("budget")
        if not isinstance(budget, int) or budget <= 0:
            raise CampaignError("budget must be a positive integer")
        segment = d.get("segment", "dev")
        if segment != "dev":
            raise CampaignError("campaigns run on the dev segment only (PLAN §5)")
        tpl = cls(name=name, hypothesis={k: hyp[k] for k in ("text", "rationale", "success_criteria")},
                  exchange=str(data.get("exchange", "coinone")), symbol=str(data.get("symbol", "BTC-KRW")),
                  timeframe=str(data.get("timeframe", "1m")), aux=aux, entry=entry, filter_sets=fsets,
                  exits=exits, costs=costs, budget=budget,
                  family=str(d.get("family") or entry.block), segment=segment, raw=d)
        tpl.validate_specs(registry)
        return tpl

    # ---------------------------------------------------------- 격자
    def n_specs(self) -> int:
        n_exit = math.prod(len(v) for v in self.exits.values())
        n_entry = math.prod(len(v) for v in self.entry.grid.values())
        n_f = sum(math.prod(math.prod(len(v) for v in f.grid.values()) for f in fs)
                  for fs in self.filter_sets)
        return n_entry * n_f * n_exit

    def exit_combos(self) -> list[tuple[float, float, int]]:
        return list(itertools.product(self.exits["sl_pct"], self.exits["tp_pct"],
                                      self.exits["max_hold_min"]))

    def signal_combos(self) -> list[tuple[BlockRef, tuple[BlockRef, ...], int]]:
        """(진입 BlockRef, 필터 BlockRef 튜플, 필터 세트 번호) — prepare 단위."""
        out = []
        for ep in self.entry.combos():
            e = BlockRef(self.entry.block, ep)
            for si, fs in enumerate(self.filter_sets):
                for fparams in itertools.product(*(f.combos() for f in fs)):
                    out.append((e, tuple(BlockRef(f.block, p) for f, p in zip(fs, fparams)), si))
        return out

    def make_spec(self, entry: BlockRef, filters: tuple[BlockRef, ...], sl: float, tp: float,
                  hold: int) -> StrategySpec:
        tmp = StrategySpec("x", entry, ExitSpec(float(sl), float(tp), int(hold)), tuple(filters))
        return StrategySpec(f"{self.name}_{tmp.logic_hash()[:10]}", entry,
                            ExitSpec(float(sl), float(tp), int(hold)), tuple(filters))

    def enumerate(self) -> list[StrategySpec]:
        exits = self.exit_combos()
        return [self.make_spec(e, fs, *x) for e, fs, _ in self.signal_combos() for x in exits]

    def validate_specs(self, registry: Registry = REGISTRY) -> None:
        """구조(필터 세트)마다 대표 스펙 하나로 StrategySpec.validate (모든 스펙의 파라미터 수가 같음)."""
        x = (self.exits["sl_pct"][0], self.exits["tp_pct"][0], self.exits["max_hold_min"][0])
        e = BlockRef(self.entry.block, self.entry.combos()[0])
        for fs in self.filter_sets:
            spec = self.make_spec(e, tuple(BlockRef(f.block, f.combos()[0]) for f in fs), *x)
            try:
                spec.validate(registry)
            except (ValueError, KeyError) as err:
                raise CampaignError(f"invalid spec structure {[f.block for f in fs]}: {err}") from err

    def check_budget(self) -> int:
        n = self.n_specs()
        if n > self.budget:
            raise CampaignBudgetError(
                f"grid has {n} specs > budget {self.budget}; shrink the grid (do not raise the budget "
                "to fit a wider search — PLAN §5.1)")
        return n

    def cost_models(self) -> list[str]:
        return [self.costs[k] for k in ("primary", "grade", "stress", "grade_stress")]

    def to_record(self) -> dict:
        return {
            "name": self.name, "family": self.family, "segment": self.segment,
            "data": {"exchange": self.exchange, "symbol": self.symbol, "timeframe": self.timeframe,
                     "aux": self.aux},
            "entry": {"block": self.entry.block, "grid": self.entry.grid},
            "filters": [[{"block": f.block, "grid": f.grid} for f in fs] for fs in self.filter_sets],
            "exits": self.exits, "costs": self.costs, "budget": self.budget,
        }


def _value_list(v, where: str) -> list:
    if not isinstance(v, list) or not v:
        raise CampaignError(f"{where} must be a non-empty list")
    if len(set(map(repr, v))) != len(v):
        raise CampaignError(f"{where} has duplicate values")
    return list(v)


def _block_grid(d, kind: str, registry: Registry) -> BlockGrid:
    if not isinstance(d, dict) or "block" not in d:
        raise CampaignError(f"{kind} must be a mapping with 'block'")
    extra = set(d) - {"block", "grid"}
    if extra:
        raise CampaignError(f"{kind} {d.get('block')}: unknown keys {sorted(extra)}")
    name = d["block"]
    try:
        bd = registry.get(name)
    except KeyError:
        raise CampaignError(f"unknown block {name!r}") from None
    if bd.kind != kind:
        raise CampaignError(f"{name} is a {bd.kind} block, used as {kind}")
    override = d.get("grid") or {}
    unknown = set(override) - set(bd.grid)
    if unknown:
        raise CampaignError(f"{name}: unknown params {sorted(unknown)}")
    grid = {}
    for k, default in bd.grid.items():   # 기본 = 블록 격자, 지정한 파라미터만 덮어씀
        grid[k] = _value_list(override[k] if k in override else list(default), f"{name}.{k}")
    return BlockGrid(name, grid)


# ============================================================== 신호 캐시
class SignalCache:
    """5분봉과 블록 평가 결과를 (블록, 파라미터)마다 한 번만 계산한다."""

    def __init__(self, m1: pd.DataFrame, ctx: Context | None = None, registry: Registry = REGISTRY):
        self.m1 = m1
        self.bars = to_5m(m1)
        self.complete = self.bars["complete"].to_numpy(bool)
        self.ctx = ctx or Context()
        self.registry = registry
        self._cache: dict[str, np.ndarray] = {}

    def block(self, ref: BlockRef) -> np.ndarray:
        k = json.dumps([ref.block, ref.params], sort_keys=True, default=str)
        if k not in self._cache:
            self._cache[k] = self.registry.evaluate(ref, self.bars, self.ctx).to_numpy(bool)
        return self._cache[k]

    def filter_ok(self, filters) -> np.ndarray:
        """AND(필터) & complete (진입 블록 제외)."""
        ok = self.complete.copy()
        for f in filters:
            ok &= self.block(f)
        return ok

    def entry_ok(self, entry: BlockRef, filters) -> pd.Series:
        """signals.build_signals 와 같은 entry_ok (entry & 필터 & complete)."""
        return pd.Series(self.block(entry) & self.filter_ok(filters), index=self.bars.index,
                         name="entry_ok")

    def prepared(self, entry: BlockRef, filters) -> fast.Prepared:
        return fast.prepare(self.m1, self.entry_ok(entry, filters), None)


def load_segment(env: ResearchEnv, segment: str, exchange: str, symbol: str, timeframe: str
                 ) -> pd.DataFrame:
    """DataAccess 로 분할 구간을 읽어 엔진 입력 형태로 (clean_wicks → regularize)."""
    raw = env.data.read(segment, exchange, symbol, timeframe)
    return prepare_m1(raw)


def prepare_m1(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise CampaignError("no data")
    return regularize(clean_wicks(raw))


# ============================================================== 결과
@dataclass
class CampaignResult:
    campaign_id: str
    hypothesis_id: str
    dir: Path
    template: CampaignTemplate
    results: pd.DataFrame          # 행 = 스펙 × 비용
    returns: pd.DataFrame          # primary 비용 일간 수익 (index=일자, 열=logic_hash)
    specs: dict[str, StrategySpec]  # logic_hash → spec
    manifest: dict

    def primary(self) -> pd.DataFrame:
        return self.results[self.results["cost"] == self.template.costs["primary"]].set_index("logic_hash")

    def ranked(self) -> pd.DataFrame:
        """primary 비용 가중 Sharpe 내림차순 (동점은 logic_hash 로 결정적 정렬)."""
        p = self.primary().reset_index()
        return p.sort_values(["sharpe_w", "logic_hash"], ascending=[False, True]).reset_index(drop=True)


METRIC_DROP = ("spec_id", "cost")


def _metrics_record(m: dict) -> dict:
    return {k: v for k, v in m.items() if k not in METRIC_DROP}


def record_runs_batch(ledger: Ledger, campaign_id: str, items: list[tuple[StrategySpec, str, dict]],
                      period: dict, data_snapshot: str, code_commit: str, family: str) -> list[str]:
    """Ledger.record_run 과 같은 검증·형식으로 여러 실행을 한 번의 잠금 안에서 추가한다.

    Ledger.record_run 은 호출마다 runs.jsonl 전체를 다시 읽으므로(예산 확인) 수천 건이면 O(n²) 이다.
    이 함수는 같은 검증(사전등록 비용·예산)을 한 번에 하고, 레코드 형식은 record_run 과 동일하다.
    권장: Ledger 에 record_runs(batch) API 추가 (그때 이 함수는 그 API 호출로 교체).
    """
    from btc_quant.registry import ledger as L

    with ledger.lock():
        camp = ledger.get("campaigns", campaign_id)
        if camp is None:
            raise LedgerError(f"unknown campaign {campaign_id} (create_campaign first)")
        seen = {r["logic_hash"] for r in ledger.records("runs") if r["campaign_id"] == campaign_id}
        for spec, cost_name, _ in items:
            if cost_name not in camp["cost_models"]:
                raise LedgerError(f"cost {cost_name!r} not pre-registered in {campaign_id}")
            seen.add(spec.logic_hash())
        if len(seen) > camp["budget"]:
            raise L.BudgetExceededError(f"campaign {campaign_id} budget {camp['budget']} exceeded "
                                        f"({len(seen)} distinct specs)")
        p = ledger.path("runs")
        seq0 = ledger._count_lines("runs")
        lines, ids = [], []
        for j, (spec, cost_name, mets) in enumerate(items, start=1):
            seq = seq0 + j
            rec = {"id": f"{L.TABLES['runs']}-{seq:06d}", "seq": seq, "ts": utcnow(), **L._clean({
                "campaign_id": campaign_id, "hypothesis_id": camp["hypothesis_id"],
                "family": family or camp.get("family") or spec.entry.block,
                "spec_id": spec.id, "spec_version": spec.version, "logic_hash": spec.logic_hash(),
                "spec": spec.to_dict(), "cost_name": cost_name, "period": dict(period),
                "data_snapshot": data_snapshot, "code_commit": code_commit, "metrics": mets or {}})}
            lines.append(json.dumps(rec, ensure_ascii=False, allow_nan=False))
            ids.append(rec["id"])
        if not lines:
            return ids
        prefix = ""
        if p.exists() and p.stat().st_size > 0:
            with open(p, "rb") as fh:
                fh.seek(-1, 2)
                if fh.read(1) != b"\n":
                    prefix = "\n"
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(prefix + "\n".join(lines) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return ids


# ============================================================== 실행
def register_campaign(env: ResearchEnv, tpl: CampaignTemplate, snapshot: str, period: dict,
                      n_specs: int) -> tuple[str, str]:
    """가설·캠페인 사전등록 (실행 전). (hypothesis_id, campaign_id)."""
    hyp_id = env.ledger.register_hypothesis(tpl.hypothesis["text"], tpl.hypothesis["rationale"],
                                            tpl.hypothesis["success_criteria"])
    camp_id = env.ledger.create_campaign(
        hyp_id, grid=tpl.to_record(), budget=n_specs, cost_models=tpl.cost_models(),
        period={**period, "data_snapshot": snapshot}, family=tpl.family,
        note=f"campaign template {tpl.name}: {n_specs} specs (template budget {tpl.budget}); "
             f"evaluated on dev with {tpl.costs['primary']} (primary) and {tpl.costs['grade']} (grade)")
    return hyp_id, camp_id


def run_campaign(template: CampaignTemplate | str | Path, root: str | Path | None = None,
                 env: ResearchEnv | None = None,
                 progress: Callable[[str], None] | None = None) -> CampaignResult:
    tpl = template if isinstance(template, CampaignTemplate) else CampaignTemplate.from_yaml(template)
    env = env or ResearchEnv.at(root)
    log = progress or (lambda s: None)
    n_specs = tpl.check_budget()                       # 예산 초과면 원장에 쓰기 전에 거부
    specs_all = tpl.enumerate()
    if len({s.logic_hash() for s in specs_all}) != len(specs_all):
        raise CampaignError("grid produces duplicate specs")
    start, end = env.data.segment_bounds(tpl.segment)
    snap = snapshot_id(tpl.exchange, tpl.symbol, tpl.timeframe, start, end, data_root=env.data.data_root)
    commit = git_commit(env.code_root)
    period = {"segment": tpl.segment, "start": start.isoformat(), "end": end.isoformat()}
    hyp_id, camp_id = register_campaign(env, tpl, snap, period, n_specs)   # ① ② 사전등록
    log(f"registered {hyp_id} / {camp_id}: {n_specs} specs, snapshot {snap}, code {commit}")

    t0 = time.time()
    m1 = load_segment(env, tpl.segment, tpl.exchange, tpl.symbol, tpl.timeframe)
    cache = SignalCache(m1)
    eval_costs = [tpl.costs["primary"], tpl.costs["grade"]]
    exits = tpl.exit_combos()
    rows, items, ret_cols, specs = [], [], {}, {}
    days = None
    combos = tpl.signal_combos()
    for ci, (entry, filters, set_idx) in enumerate(combos):
        p = cache.prepared(entry, filters)
        for cost_name in eval_costs:
            cost = get_cost(cost_name)
            trs = fast.simulate_many(p, exits, cost, start, end)
            if cost_name == tpl.costs["primary"]:
                days, mat = fast.daily_returns_matrix(p, trs, start, end)
            for xi, ((sl, tp, hold), tr) in enumerate(zip(exits, trs)):
                spec = tpl.make_spec(entry, filters, sl, tp, hold)
                lh = spec.logic_hash()
                specs[lh] = spec
                res = fast.to_result(p, tr, cost, start, end, spec.id, lh)
                met = M.summarize(res, HALF_LIFE_DAYS)
                if cost_name == tpl.costs["primary"]:
                    ret_cols[lh] = mat[xi]
                rows.append({"logic_hash": lh, "spec_id": spec.id, "filter_set": set_idx,
                             "filters": "+".join(f.block for f in filters) or "-",
                             **{f"entry.{k}": v for k, v in entry.params.items()},
                             **{f"{f.block}.{k}": v for f in filters for k, v in f.params.items()},
                             "sl_pct": sl, "tp_pct": tp, "max_hold_min": hold,
                             "spec_json": json.dumps(spec.to_dict(), sort_keys=True),
                             **met, "cost": cost_name})
                items.append((spec, cost_name, _metrics_record(met)))
        log(f"  signals {ci + 1}/{len(combos)} done ({len(rows)} runs, {time.time() - t0:.1f}s)")
    record_runs_batch(env.ledger, camp_id, items, period, snap, commit, tpl.family)   # 모든 실행 기록

    results = pd.DataFrame(rows)
    returns = pd.DataFrame(ret_cols, index=days)
    returns.index.name = "date"
    manifest = {
        "campaign_id": camp_id, "hypothesis_id": hyp_id, "template": tpl.name, "family": tpl.family,
        "created": utcnow(), "data": {"exchange": tpl.exchange, "symbol": tpl.symbol,
                                      "timeframe": tpl.timeframe, "rows_1m": int(len(m1)),
                                      "filled_frac": float(m1["filled"].mean())},
        "period": period, "data_snapshot": snap, "code_commit": commit, "n_specs": n_specs,
        "n_runs": len(items), "costs": tpl.costs, "evaluated_costs": eval_costs,
        "runtime_sec": round(time.time() - t0, 2), "half_life_days": HALF_LIFE_DAYS,
    }
    out = save_campaign(env, tpl, camp_id, results, returns, manifest)
    log(f"campaign {camp_id} done: {len(items)} runs in {manifest['runtime_sec']}s → {out}")
    return CampaignResult(camp_id, hyp_id, out, tpl, results, returns, specs, manifest)


def save_campaign(env: ResearchEnv, tpl: CampaignTemplate, camp_id: str, results: pd.DataFrame,
                  returns: pd.DataFrame, manifest: dict) -> Path:
    out = env.campaign_dir(camp_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("# 큰 배열은 커밋하지 않는다 (research/campaigns/README.md)\n*.parquet\n",
                                    encoding="utf-8")
    (out / "template.yaml").write_text(
        yaml.safe_dump(tpl.raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    results.to_parquet(out / "results.parquet", index=False)
    returns.to_parquet(out / f"returns_{tpl.costs['primary']}.parquet")
    summary_cols = [c for c in results.columns if c not in ("spec_json",) and not c.startswith("sharpe_year_")]
    prim = results[results["cost"] == tpl.costs["primary"]].sort_values(
        ["sharpe_w", "logic_hash"], ascending=[False, True])
    prim[summary_cols].head(50).to_csv(out / "summary_top50.csv", index=False, float_format="%.6g")
    return out


def load_campaign(campaign_id: str, root: str | Path | None = None,
                  env: ResearchEnv | None = None) -> CampaignResult:
    env = env or ResearchEnv.at(root)
    d = env.campaign_dir(campaign_id)
    if not (d / "manifest.json").exists():
        raise CampaignError(f"no saved campaign at {d}")
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    tpl = CampaignTemplate.from_dict(yaml.safe_load((d / "template.yaml").read_text(encoding="utf-8")))
    results = pd.read_parquet(d / "results.parquet")
    returns = pd.read_parquet(d / f"returns_{tpl.costs['primary']}.parquet")
    specs = {}
    for lh, sj in results[["logic_hash", "spec_json"]].drop_duplicates("logic_hash").itertuples(index=False):
        d_ = json.loads(sj)
        specs[lh] = StrategySpec.from_dict(d_)
    return CampaignResult(campaign_id, manifest["hypothesis_id"], d, tpl, results, returns, specs, manifest)


def list_campaigns(root: str | Path | None = None, env: ResearchEnv | None = None) -> list[dict[str, Any]]:
    env = env or ResearchEnv.at(root)
    hyps = {h["id"]: h for h in env.ledger.records("hypotheses")}
    out = []
    for c in env.ledger.records("campaigns"):
        d = env.campaign_dir(c["id"])
        pj = d / "pipeline.json"
        verdict = None
        if pj.exists():
            verdict = json.loads(pj.read_text(encoding="utf-8")).get("verdict")
        out.append({"campaign_id": c["id"], "ts": c["ts"], "family": c.get("family"),
                    "budget": c.get("budget"), "hypothesis": hyps.get(c["hypothesis_id"], {}).get("text"),
                    "saved": (d / "manifest.json").exists(), "verdict": verdict, "note": c.get("note", "")})
    return out
