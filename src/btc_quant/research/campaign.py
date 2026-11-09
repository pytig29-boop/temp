"""연구 캠페인: 템플릿(YAML) 검증 → 격자 전수 열거 → 원장 사전등록 → Dev 전수 실행 → 결과 저장.

PLAN §5.1 ①~③. 관문(④~⑥)은 `pipeline.py`, 리포트는 `report.py`.

템플릿 형식은 `research/campaigns/README.md` 와 `research/campaign_templates/*.yaml` 참조.

절차 (`run_campaign`)
1. 템플릿 검증 + 격자 전수 열거(StrategySpec.validate, ≤2 필터, ≤8 파라미터). 조합 수 > budget 이면
   **원장에 아무것도 쓰지 않고** `CampaignBudgetError`.
2. (v4) 신호 선별 연결 확인(`from_screen` / `requires_screen_pass`): 격자의 모든 진입(+필터) 구성이
   원장에 기록된 선별에서 통과했어야 한다. 아니면 원장에 쓰기 전에 `ScreenGateError`.
   (v4) 검정력 점검(`power.budget_report`): 격자의 실질 시도 수 → G5 통과 최소 연 Sharpe. 기본은 경고만,
   `strict_power=True` 면 목표 Sharpe(`power.target_sharpe`) 미달·미선언 시 `PowerCheckError` (원장 쓰기 전).
3. 데이터 스냅샷 id 계산(파일 해시만, 백테스트 아님) → 가설·캠페인을 원장에 등록(**실행 전**).
4. Dev 데이터는 `DataAccess.read("dev", …)` 로만 읽고 `prepare_m1(raw, exchange)` = 거래소별 clean_wicks →
   regularize → 긴 공백 직후 워밍업 봉 filled (`data.quality.prepare_venue_m1`, 페이퍼 M1History 와 동일).
   보조 데이터(`data.aux: [upbit]`)는 같은 구간을 같은 방식으로 읽어 결정 주기 봉(`to_tf`)으로 만든다 —
   페이퍼(`paper.trader.evaluate_signals`)와 같은 구성 (`aux_context`).
5. 진입·필터 블록 신호는 (블록, 파라미터)마다 한 번씩만 계산·캐시(결정 주기 봉 `to_tf(m1, B)`),
   (진입 파라미터 × 필터 파라미터) 조합마다 `prepare` 한 번, 청산 격자는 `simulate_many` 로 스윕
   (고정 % 또는 ATR 배수 — `exits.mode`).
6. 스펙 × 평가 비용(primary + grade)마다 `metrics.summarize` + 비용 전 지표(`gross_metrics`: 같은 거래를
   zero-cost 로 → gross_sharpe_w, gross_expectancy; 오답노트 COST_EATEN 분류) → 원장 runs 에 모두 기록
   (`Ledger.record_runs`, 스냅샷 id, 코드 커밋 포함).
7. research/campaigns/<campaign_id>/ 에 결과 저장 (README 참조: 무엇을 git 에 넣는지).
"""
from __future__ import annotations

import itertools
import json
import math
import functools
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import yaml

from btc_quant.backtest import fast
from btc_quant.backtest.types import CostModel
from btc_quant.data.quality import prepare_venue_m1
from btc_quant.evaluation import metrics as M
from btc_quant.registry.ledger import Ledger, repo_root, utcnow
from btc_quant.registry.snapshot import snapshot_id
from btc_quant.registry.splits import DataAccess
from btc_quant.registry.strategies import StrategyRegistry
from btc_quant.strategy.blocks import REGISTRY, Context, Registry
from btc_quant.strategy.features import to_tf
from btc_quant.strategy.spec import (DEFAULT_ATR_BARS, DEFAULT_TIMEFRAME, MAX_FILTERS, TIMEFRAMES, BlockRef,
                                     ExitSpec, StrategySpec, tf_minutes, tf_name)

# 비용 모델은 버전 관리되는 레지스트리(config/costs.yaml, `btc_quant.params.cost_model`)에서 읽는다
# (docs/PARAMS.md §4). 예전 모듈 상수 COST_MODELS / UPBIT_STRESS 는 없어졌다 — `get_cost(name)` 를 쓴다.

DEFAULT_COSTS = {"primary": "coinone_conservative", "grade": "upbit_conservative",
                 "stress": "coinone_stress", "grade_stress": "upbit_stress"}
EXIT_KEYS = ("sl_pct", "tp_pct", "max_hold_min")            # 고정 % 청산 (기존)
ATR_EXIT_KEYS = ("sl_atr", "tp_atr", "max_hold_min")        # ATR 배수 청산 (v4, ENGINE_SPEC "ATR 청산")
EXIT_MODES = ("pct", "atr")
HALF_LIFE_DAYS = 365          # PLAN D1
# 실시간으로 만들 수 있는 보조 데이터 (paper.bars.LIVE_AUX 와 같은 값). 바이낸스는 실시간 수신 불가 → 캠페인 불가.
LIVE_AUX = ("upbit",)
# 비용 전(gross) 지표 (오답노트 COST_EATEN 분류용): 같은 스윕의 같은 거래를 zero-cost CostModel 로 다시 계산
GROSS_KEYS = ("gross_sharpe_w", "gross_expectancy")


def gross_metrics(p: fast.Prepared, tr: fast.TradeArrays, cost: CostModel, start, end) -> dict:
    """같은 거래를 zero-cost(수수료·스프레드·슬리피지 0, tick 은 원래 실행 값)로 → {gross_sharpe_w, gross_expectancy}.
    gross_sharpe_w = 가중 Sharpe(`metrics.summarize` 의 sharpe_w 와 같은 식), gross_expectancy = 평균 gross 거래 수익."""
    zero = CostModel("zero_cost", 0.0, 0.0, 0.0, cost.tick)
    tr0 = fast.reprice(tr, zero)
    days, mat = fast.daily_returns_matrix(p, [tr0], start, end)
    ws = M.weighted_sharpe(pd.Series(mat[0], index=days), HALF_LIFE_DAYS)
    return {"gross_sharpe_w": float(ws.sharpe),
            "gross_expectancy": float(tr0.net_ret.mean()) if len(tr0.net_ret) else 0.0}


class CampaignError(ValueError):
    pass


class CampaignBudgetError(CampaignError):
    pass


class ScreenGateError(CampaignError):
    """신호 선별 통과 기록이 없는 진입(+필터) 구성으로 캠페인을 돌리려 함 (원장 쓰기 전 거부)."""


class PowerCheckError(CampaignError):
    """`strict_power` 에서 검정력 점검 실패 (원장 쓰기 전 거부)."""


@functools.lru_cache(maxsize=8)
def _cost_doc_cached(path: str, mtime_ns: int | None) -> dict:
    from btc_quant.params.costs import load_costs
    return load_costs(Path(path).parent.parent)


def _cost_doc(root: str | Path | None = None) -> dict:
    """config/costs.yaml 문서 (파일 mtime 기준 캐시 — 보정·승인으로 새 버전이 생기면 다시 읽는다)."""
    from btc_quant.params.costs import costs_path
    p = costs_path(root)
    try:
        mt = p.stat().st_mtime_ns
    except FileNotFoundError:
        mt = None
    return _cost_doc_cached(str(p), mt)


def get_cost(name: str, root: str | Path | None = None) -> CostModel:
    """호환용 얇은 래퍼 → `btc_quant.params.cost_model(name)` (현재 버전). 모르는 이름은 CampaignError."""
    from btc_quant.params.costs import CostRegistryError, cost_model
    doc = _cost_doc(root)
    try:
        return cost_model(name, doc=doc)
    except CostRegistryError:
        raise CampaignError(f"unknown cost model {name!r}; one of {sorted(doc['models'])}") from None


def __getattr__(name: str):
    """하위 호환: `campaign.COST_MODELS` 는 레지스트리의 현재 비용 모델 dict (매번 새로 읽음)."""
    if name == "COST_MODELS":
        from btc_quant.params.costs import all_cost_models
        return all_cost_models(doc=_cost_doc())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def config_root(env: "ResearchEnv | None" = None) -> Path:
    """파라미터 거버넌스 설정(config/params.yaml·costs.yaml·gates.yaml)이 있는 루트.
    연구 저장소(env.root)에 config/ 가 있으면 그곳, 아니면 코드 저장소(테스트용 임시 저장소 대비)."""
    if env is not None and (Path(env.root) / "config" / "gates.yaml").exists():
        return Path(env.root)
    return Path(env.code_root) if env is not None else repo_root()


def run_params(env: "ResearchEnv | None" = None) -> dict:
    """실행 기록용 파라미터 버전 (docs/PARAMS.md §4): `params_snapshot()` 요약.
    {params_version, cost_versions, registry_version, n_params}. 레지스트리가 검증에 실패하면(drift 등)
    params_version=None 과 params_error 를 남기고 계속한다(비용 버전은 costs.yaml 에서 직접)."""
    from btc_quant.params import cost_versions, params_snapshot
    root = config_root(env)
    try:
        return params_snapshot(root)
    except Exception as e:  # noqa: BLE001 — 기록 실패로 연구를 멈추지 않는다; 오류를 그대로 남긴다
        try:
            cv = cost_versions(root)
        except Exception:  # noqa: BLE001
            cv = None
        return {"params_version": None, "cost_versions": cv, "params_error": f"{type(e).__name__}: {e}"}


def run_param_fields(snap: dict | None) -> dict:
    """`Ledger.record_run(s)` 의 선택 인자 (params_version, cost_versions)."""
    snap = snap or {}
    return {"params_version": snap.get("params_version"), "cost_versions": snap.get("cost_versions")}


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
    exits: dict[str, list]          # 고정 %: sl_pct, tp_pct, max_hold_min | ATR: sl_atr, tp_atr, max_hold_min
    costs: dict[str, str]           # primary, grade, stress, grade_stress
    budget: int
    family: str
    segment: str = "dev"
    raw: dict = field(default_factory=dict)
    decision_tf: str = DEFAULT_TIMEFRAME   # 결정 주기 "5m" | "1m" (StrategySpec.timeframe, D10)
    exit_mode: str = "pct"                 # "pct" | "atr"
    atr_bars: int = DEFAULT_ATR_BARS       # ATR 모드 ATR 창 (결정 주기 봉 수)
    from_screen: str | None = None         # 선별 캠페인 id (원장) — 이 선별에서 통과한 구성만 허용
    requires_screen_pass: bool = False     # True: 원장의 어떤 선별에서든 통과한 구성만 허용
    power: dict = field(default_factory=dict)   # {target_sharpe: float} (선택)

    @property
    def tf_min(self) -> int:
        return tf_minutes(self.decision_tf)

    @property
    def exit_keys(self) -> tuple[str, str, str]:
        return ATR_EXIT_KEYS if self.exit_mode == "atr" else EXIT_KEYS

    @property
    def screen_gated(self) -> bool:
        return bool(self.from_screen) or self.requires_screen_pass

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
                   "family", "segment", "timeframe", "from_screen", "requires_screen_pass", "power"}
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
        exchange = str(data.get("exchange", "coinone"))
        aux = list(data.get("aux") or [])
        bad = [a for a in aux if a not in LIVE_AUX]
        if bad:
            raise CampaignError(f"aux data {bad} not supported: only live-available feeds {list(LIVE_AUX)} "
                                "(binance has no live feed → cannot be paper traded)")
        if len(set(aux)) != len(aux) or exchange in aux:
            raise CampaignError(f"aux {aux}: duplicates or the target exchange {exchange!r} itself")
        tf = str(d.get("timeframe", DEFAULT_TIMEFRAME))
        if tf not in TIMEFRAMES:
            raise CampaignError(f"timeframe must be one of {list(TIMEFRAMES)} (decision interval, D10)")
        tf_min = tf_minutes(tf)
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
        ex = dict(d.get("exits") or {})
        mode = str(ex.pop("mode", "pct"))
        if mode not in EXIT_MODES:
            raise CampaignError(f"exits.mode must be one of {EXIT_MODES}")
        atr_bars = DEFAULT_ATR_BARS
        if mode == "atr":
            atr_bars = ex.pop("atr_bars", DEFAULT_ATR_BARS)
            if isinstance(atr_bars, bool) or not isinstance(atr_bars, int) or atr_bars < 1:
                raise CampaignError("exits.atr_bars must be one positive integer (not a grid)")
        keys = ATR_EXIT_KEYS if mode == "atr" else EXIT_KEYS
        if set(ex) != set(keys):
            raise CampaignError(f"exits ({mode} mode) must have exactly {keys}"
                                + (" (+ mode, atr_bars)" if mode == "atr" else " (+ optional mode: pct)"))
        exits = {}
        for k in keys:
            vals = _value_list(ex[k], f"exits.{k}")
            for v in vals:
                if isinstance(v, bool) or not (isinstance(v, (int, float)) and v > 0):
                    raise CampaignError(f"exits.{k} values must be > 0")
                if k == "max_hold_min" and (int(v) != v or int(v) % tf_min):
                    raise CampaignError(f"max_hold_min values must be positive multiples of {tf_min} "
                                        f"(timeframe {tf})")
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
        from_screen = d.get("from_screen")
        if from_screen is not None and (not isinstance(from_screen, str) or not from_screen.strip()):
            raise CampaignError("from_screen must be a screen campaign id (e.g. camp-000012)")
        rsp = d.get("requires_screen_pass", False)
        if not isinstance(rsp, bool):
            raise CampaignError("requires_screen_pass must be true/false")
        power = d.get("power") or {}
        if not isinstance(power, dict) or set(power) - {"target_sharpe"}:
            raise CampaignError("power must be a mapping with optional target_sharpe")
        if "target_sharpe" in power:
            ts = power["target_sharpe"]
            if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not ts > 0:
                raise CampaignError("power.target_sharpe must be > 0 (annualised)")
            power = {"target_sharpe": float(ts)}
        # 블록이 요구하는 보조 데이터는 data.aux 에 선언돼 있어야 한다
        need = sorted({r for b in [entry, *(f for fs in fsets for f in fs)]
                       for r in registry.get(b.block).requires})
        missing = [r for r in need if r not in aux]
        if missing:
            raise CampaignError(f"blocks require aux data {missing}; declare data.aux: {need} "
                                f"(live-available: {list(LIVE_AUX)})")
        tpl = cls(name=name, hypothesis={k: hyp[k] for k in ("text", "rationale", "success_criteria")},
                  exchange=exchange, symbol=str(data.get("symbol", "BTC-KRW")),
                  timeframe=str(data.get("timeframe", "1m")), aux=aux, entry=entry, filter_sets=fsets,
                  exits=exits, costs=costs, budget=budget,
                  family=str(d.get("family") or entry.block), segment=segment, raw=d,
                  decision_tf=tf, exit_mode=mode, atr_bars=int(atr_bars),
                  from_screen=from_screen.strip() if from_screen else None, requires_screen_pass=rsp,
                  power=power)
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
        """(sl, tp, max_hold_min) — sl/tp 는 고정 % 모드에서 %, ATR 모드에서 ATR 배수."""
        return list(itertools.product(*(self.exits[k] for k in self.exit_keys)))

    @property
    def sim_atr_bars(self) -> int | None:
        """fast.simulate_many(..., atr_bars=) 인자: ATR 모드면 ATR 창, 아니면 None."""
        return self.atr_bars if self.exit_mode == "atr" else None

    def make_exit(self, sl: float, tp: float, hold: int) -> ExitSpec:
        if self.exit_mode == "atr":
            return ExitSpec(max_hold_min=int(hold), stop_loss_atr=float(sl), take_profit_atr=float(tp),
                            atr_bars=int(self.atr_bars))
        return ExitSpec(float(sl), float(tp), int(hold))

    def exit_values(self, ex: ExitSpec) -> dict[str, float]:
        """스펙의 청산 → {exit_key: 값} (G3 인접·결과표 열 이름)."""
        k_sl, k_tp, k_h = self.exit_keys
        return {k_sl: ex.sl, k_tp: ex.tp, k_h: ex.max_hold_min}

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
        """sl/tp: 고정 % 모드는 %, ATR 모드는 ATR 배수. 결정 주기 = 템플릿 timeframe."""
        ex = self.make_exit(sl, tp, hold)
        tmp = StrategySpec("x", entry, ex, tuple(filters), timeframe=self.decision_tf)
        return StrategySpec(f"{self.name}_{tmp.logic_hash()[:10]}", entry, ex, tuple(filters),
                            timeframe=self.decision_tf)

    def enumerate(self) -> list[StrategySpec]:
        exits = self.exit_combos()
        return [self.make_spec(e, fs, *x) for e, fs, _ in self.signal_combos() for x in exits]

    def validate_specs(self, registry: Registry = REGISTRY) -> None:
        """구조(필터 세트)마다 대표 스펙 하나로 StrategySpec.validate (모든 스펙의 파라미터 수가 같음)."""
        x = tuple(self.exits[k][0] for k in self.exit_keys)
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

    def exits_record(self) -> dict:
        if self.exit_mode == "atr":
            return {"mode": "atr", **self.exits, "atr_bars": self.atr_bars}
        return dict(self.exits)

    def to_record(self) -> dict:
        rec = {
            "name": self.name, "family": self.family, "segment": self.segment,
            "data": {"exchange": self.exchange, "symbol": self.symbol, "timeframe": self.timeframe,
                     "aux": self.aux},
            "timeframe": self.decision_tf,
            "entry": {"block": self.entry.block, "grid": self.entry.grid},
            "filters": [[{"block": f.block, "grid": f.grid} for f in fs] for fs in self.filter_sets],
            "exits": self.exits_record(), "costs": self.costs, "budget": self.budget,
        }
        if self.screen_gated:
            rec["screen_gate"] = {"from_screen": self.from_screen, "requires_screen_pass": self.requires_screen_pass}
        return rec


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
    """결정 주기 봉(`to_tf(m1, B)`)과 블록 평가 결과를 (블록, 파라미터)마다 한 번만 계산한다.

    ctx: 보조 데이터(`aux_context`). tf_min: 결정 주기 B (5 또는 1). atr_bars: ATR 청산 창(있으면 prepare 에서
    결정 행 ATR 비율을 함께 만든다)."""

    def __init__(self, m1: pd.DataFrame, ctx: Context | None = None, registry: Registry = REGISTRY,
                 tf_min: int = 5, atr_bars: int | None = None):
        self.m1 = m1
        self.tf_min = int(tf_min)
        self.bars = to_tf(m1, self.tf_min)
        self.complete = self.bars["complete"].to_numpy(bool)
        self.ctx = ctx or Context(bar=pd.Timedelta(minutes=self.tf_min))
        self.registry = registry
        self.atr_bars = atr_bars
        self._cache: dict[str, np.ndarray] = {}

    @property
    def timeframe(self) -> str:
        return tf_name(self.tf_min)

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
        return fast.prepare(self.m1, self.entry_ok(entry, filters), None, timeframe=self.timeframe,
                            atr_bars=self.atr_bars, bars=self.bars)


def load_segment(env: ResearchEnv, segment: str, exchange: str, symbol: str, timeframe: str
                 ) -> pd.DataFrame:
    """DataAccess 로 분할 구간을 읽어 엔진 입력 형태로 (`prepare_m1(raw, exchange)`)."""
    raw = env.data.read(segment, exchange, symbol, timeframe)
    return prepare_m1(raw, exchange)


def prepare_m1(raw: pd.DataFrame, exchange: str | None = None) -> pd.DataFrame:
    """원천 1분봉 → 엔진 입력: 거래소별 clean_wicks(k: 코인원 0.01, 그 밖 0.03) → regularize(거래량 0 평탄 봉
    = filled) → 30분 이상 공백 직후 첫 실제 봉 filled. 페이퍼 `M1History(exchange=…)` 와 같은 함수
    (`data.quality.prepare_venue_m1`). exchange=None 은 기본 꼬리 폭(0.03)."""
    if raw.empty:
        raise CampaignError("no data")
    return prepare_venue_m1(raw, exchange)


def aux_context(aux_m1: dict[str, pd.DataFrame], tf_min: int) -> Context:
    """보조 1분봉(이미 `regularize(clean_wicks(raw))`) → 블록 Context.

    페이퍼와 같은 구성: `paper.trader.evaluate_signals` 는 업비트 1분봉 이력(M1History = regularize(clean_wicks))을
    `to_tf(·, B)` 로 만들어 `ctx.aux["upbit"]` 에 넣고, 블록은 `aux_frame` 으로 대상 bars.index 에 reindex
    (ffill 없음 — 없는 시각은 NaN → 조건 False) 한다. 여기서도 같은 함수(`to_tf`)로 같은 봉을 만든다.
    bar = 결정 주기 길이 명시."""
    return Context(aux={k: to_tf(df, int(tf_min)) for k, df in (aux_m1 or {}).items()},
                   bar=pd.Timedelta(minutes=int(tf_min)))


def load_aux_m1(env: ResearchEnv, tpl: "CampaignTemplate", start=None, end=None,
                segment: str | None = None) -> dict[str, pd.DataFrame]:
    """템플릿 data.aux 의 보조 거래소 1분봉 (같은 구간, DataAccess 경유, regularize(clean_wicks))."""
    out = {}
    for a in tpl.aux:
        raw = (env.data.read(segment, a, tpl.symbol, tpl.timeframe) if segment is not None
               else env.data.read_range(start, end, a, tpl.symbol, tpl.timeframe))
        if raw.empty:
            raise CampaignError(f"no {a} aux data for {segment or (start, end)}")
        out[a] = prepare_m1(raw, a)
    return out


def data_snapshot(env: ResearchEnv, tpl: "CampaignTemplate", start, end) -> str:
    """대상 시계열 스냅샷 id (+ 보조 데이터가 있으면 `|<aux>:<id>` 를 이어 붙임)."""
    snap = snapshot_id(tpl.exchange, tpl.symbol, tpl.timeframe, start, end, data_root=env.data.data_root)
    for a in tpl.aux:
        snap += f"|{a}:" + snapshot_id(a, tpl.symbol, tpl.timeframe, start, end, data_root=env.data.data_root)
    return snap


# ============================================================== 신호 선별 연결 (v4)
def _norm_params(p: dict) -> str:
    def nv(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return v
        return float(v)
    return json.dumps({str(k): nv(v) for k, v in (p or {}).items()}, sort_keys=True, default=str)


def signal_config_key(entry: dict | BlockRef, filters, timeframe: str) -> str:
    """선별·캠페인 공통 '신호 구성' 키: 진입(블록·파라미터) + 필터 집합(순서 무관) + 결정 주기.
    숫자 파라미터는 float 로 정규화(48 == 48.0)."""
    def one(r):
        if isinstance(r, BlockRef):
            return [r.block, _norm_params(r.params)]
        return [r["block"], _norm_params(r.get("params") or {})]
    fs = sorted(json.dumps(one(f)) for f in (filters or []))
    return json.dumps([one(entry), fs, str(timeframe)])


def screen_passes(ledger: Ledger, screen_id: str | None = None, exchange: str | None = None
                  ) -> dict[str, dict]:
    """원장의 선별 통과 구성 {signal_config_key: 근거}. screen_id 가 있으면 그 선별만.
    exchange 가 있으면 대상 거래소가 같은 선별만 (선별 캠페인 grid.data.exchange)."""
    camps = {c["id"]: c for c in ledger.records("campaigns")}
    out: dict[str, dict] = {}
    for r in ledger.records("runs"):
        m = r.get("metrics") or {}
        if m.get("kind") != "signal_screen" or not (m.get("decision") or {}).get("passed"):
            continue
        if screen_id is not None and r["campaign_id"] != screen_id:
            continue
        grid = (camps.get(r["campaign_id"]) or {}).get("grid") or {}
        ex = (grid.get("data") or {}).get("exchange")
        if exchange is not None and ex != exchange:
            continue
        sp = r.get("spec") or {}
        key = signal_config_key(sp["entry"], sp.get("filters") or [], sp.get("timeframe", DEFAULT_TIMEFRAME))
        out.setdefault(key, {"screen_id": r["campaign_id"], "run_id": r["id"], "p_signal": m.get("p_signal"),
                             "n_events": m.get("n_events"), "info_horizon": m.get("info_horizon"),
                             "exchange": ex, "aux": (grid.get("data") or {}).get("aux") or []})
    return out


def check_screen_gate(ledger: Ledger, tpl: "CampaignTemplate") -> dict | None:
    """매칭 규칙 (README '신호 선별 연결'): 캠페인 격자의 **모든** 신호 구성(진입 파라미터 × 필터 세트 파라미터,
    결정 주기 포함)이 원장에 기록된 선별 시도 중 **통과(decision.passed)** 한 것과 같아야 한다.
    - from_screen 이 있으면 그 선별 캠페인 안에서만, 아니면(requires_screen_pass) 어떤 선별이든.
    - 같음 = 진입 블록·파라미터, 필터 블록·파라미터 집합(순서 무관), 결정 주기(5m/1m), 대상 거래소가 모두 같음.
    - 선별 결과가 원장에 없으면(존재하지 않는 id 포함) 거부.
    게이트가 없으면 None. 하나라도 어긋나면 ScreenGateError (원장 쓰기 전)."""
    if not tpl.screen_gated:
        return None
    if tpl.from_screen is not None:
        camp = ledger.get("campaigns", tpl.from_screen)
        if camp is None or (camp.get("grid") or {}).get("kind") != "signal_screen":
            raise ScreenGateError(f"from_screen {tpl.from_screen!r} is not a signal screen in the ledger")
    passes = screen_passes(ledger, tpl.from_screen, tpl.exchange)
    matches, missing = [], []
    for e, fs, _ in tpl.signal_combos():
        k = signal_config_key(e, fs, tpl.decision_tf)
        lbl = e.block + str(e.params) + "".join(f" & {f.block}{f.params}" for f in fs)
        if k in passes:
            matches.append({"config": lbl, **passes[k]})
        else:
            missing.append(lbl)
    if missing:
        where = f"screen {tpl.from_screen}" if tpl.from_screen else "any recorded screen"
        raise ScreenGateError(
            f"{len(missing)} of {len(matches) + len(missing)} signal configs did not pass {where} "
            f"(exchange {tpl.exchange}, timeframe {tpl.decision_tf}): {missing[:5]}"
            f"{' …' if len(missing) > 5 else ''}. Screen them first (research.cli screen) or narrow the grid.")
    return {"from_screen": tpl.from_screen, "requires_screen_pass": tpl.requires_screen_pass,
            "n_configs": len(matches), "matches": matches,
            "rule": "every (entry params × filter params × timeframe × exchange) config passed a recorded screen"}


# ============================================================== 검정력 (v4)
def power_check(env: ResearchEnv, tpl: "CampaignTemplate", n_specs: int, strict: bool = False) -> dict:
    """power.budget_report 로 계획 격자의 G5 통과 최소 연 Sharpe. 경고(기본) / strict 면 PowerCheckError.
    strict 실패 조건: 목표 Sharpe 미선언, 또는 필요 Sharpe > 목표 Sharpe."""
    from .power import budget_report
    start, end = env.data.segment_bounds(tpl.segment)
    n_days = int((end - start) / pd.Timedelta(days=1))
    target = tpl.power.get("target_sharpe")
    rep = budget_report(n_specs, n_days, tpl.family, target, root=env.root, ledger=env.ledger,
                        campaigns_dir=env.campaigns_dir)
    need = float(rep["required_sharpe_ann"])
    warn = None
    if target is None:
        warn = (f"no power.target_sharpe declared; G5 needs annual Sharpe ≈ {need:.2f} with "
                f"≈{rep['n_trials_dsr']:.1f} effective trials")
    elif need > target:
        warn = (f"required annual Sharpe {need:.2f} > target {target:.2f} (≈{rep['n_trials_dsr']:.1f} effective "
                f"trials) — shrink the grid or drop the campaign")
    out = {k: rep.get(k) for k in ("n_grid", "family", "n_family_prior", "ratio", "ratio_source", "k_eff_est",
                                   "n_trials_dsr", "n_days", "var_trial_sr", "var_source", "min_dsr",
                                   "required_sharpe_ann", "required_sharpe_ann_single_trial",
                                   "target_sharpe_ann", "max_trials_for_target", "max_grid_for_target")}
    out.update(markdown=rep["markdown"], warning=warn, strict=bool(strict),
               ok=warn is None)
    if strict and warn:
        raise PowerCheckError(f"power check failed (--strict-power): {warn}")
    return out


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
                      period: dict, data_snapshot: str, code_commit: str, family: str, *,
                      params_version: str | None = None, cost_versions: dict | None = None) -> list[str]:
    """호환용 별칭 → `Ledger.record_runs` (같은 검증·형식, 한 번의 잠금). signal_study.screen 이 쓴다."""
    return ledger.record_runs(campaign_id, items, period, data_snapshot, code_commit, family,
                              params_version=params_version, cost_versions=cost_versions)


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
             f"timeframe {tpl.decision_tf}, exits {tpl.exit_mode}"
             + (f", aux {tpl.aux}" if tpl.aux else "")
             + (f", screen-gated ({tpl.from_screen or 'any screen'})" if tpl.screen_gated else "")
             + f"; evaluated on dev with {tpl.costs['primary']} (primary) and {tpl.costs['grade']} (grade)")
    return hyp_id, camp_id


def exit_columns(tpl: CampaignTemplate, sl, tp, hold) -> dict:
    """결과표의 청산 열: 고정 % = sl_pct/tp_pct/max_hold_min, ATR = sl_atr/tp_atr/max_hold_min/atr_bars."""
    k_sl, k_tp, k_h = tpl.exit_keys
    out = {k_sl: sl, k_tp: tp, k_h: hold}
    if tpl.exit_mode == "atr":
        out["atr_bars"] = tpl.atr_bars
    return out


def run_campaign(template: CampaignTemplate | str | Path, root: str | Path | None = None,
                 env: ResearchEnv | None = None,
                 progress: Callable[[str], None] | None = None, strict_power: bool = False) -> CampaignResult:
    tpl = template if isinstance(template, CampaignTemplate) else CampaignTemplate.from_yaml(template)
    env = env or ResearchEnv.at(root)
    log = progress or (lambda s: None)
    n_specs = tpl.check_budget()                       # 예산 초과면 원장에 쓰기 전에 거부
    specs_all = tpl.enumerate()
    if len({s.logic_hash() for s in specs_all}) != len(specs_all):
        raise CampaignError("grid produces duplicate specs")
    screen = check_screen_gate(env.ledger, tpl)        # 선별 미통과 구성 → 원장 쓰기 전 거부
    pw = power_check(env, tpl, n_specs, strict=strict_power)   # strict 면 원장 쓰기 전 거부
    if pw["warning"]:
        log(f"POWER WARNING: {pw['warning']}")
    start, end = env.data.segment_bounds(tpl.segment)
    snap = data_snapshot(env, tpl, start, end)
    commit = git_commit(env.code_root)
    period = {"segment": tpl.segment, "start": start.isoformat(), "end": end.isoformat()}
    pver = run_params(env)                             # 파라미터·비용 모델 버전 (docs/PARAMS.md §4)
    hyp_id, camp_id = register_campaign(env, tpl, snap, period, n_specs)   # ① ② 사전등록
    log(f"registered {hyp_id} / {camp_id}: {n_specs} specs, snapshot {snap}, code {commit}")

    t0 = time.time()
    m1 = load_segment(env, tpl.segment, tpl.exchange, tpl.symbol, tpl.timeframe)
    aux_m1 = load_aux_m1(env, tpl, segment=tpl.segment)
    ctx = aux_context(aux_m1, tpl.tf_min)
    cache = SignalCache(m1, ctx, tf_min=tpl.tf_min, atr_bars=tpl.sim_atr_bars)
    eval_costs = [tpl.costs["primary"], tpl.costs["grade"]]
    cost_by_name = {c: get_cost(c, config_root(env)) for c in eval_costs}
    exits = tpl.exit_combos()
    rows, items, ret_cols, specs = [], [], {}, {}
    days = None
    combos = tpl.signal_combos()
    for ci, (entry, filters, set_idx) in enumerate(combos):
        p = cache.prepared(entry, filters)
        for cost_name in eval_costs:
            cost = cost_by_name[cost_name]
            trs = fast.simulate_many(p, exits, cost, start, end, atr_bars=tpl.sim_atr_bars)
            if cost_name == tpl.costs["primary"]:
                days, mat = fast.daily_returns_matrix(p, trs, start, end)
            for xi, ((sl, tp, hold), tr) in enumerate(zip(exits, trs)):
                spec = tpl.make_spec(entry, filters, sl, tp, hold)
                lh = spec.logic_hash()
                specs[lh] = spec
                res = fast.to_result(p, tr, cost, start, end, spec.id, lh)
                met = {**M.summarize(res, HALF_LIFE_DAYS), **gross_metrics(p, tr, cost, start, end)}
                if cost_name == tpl.costs["primary"]:
                    ret_cols[lh] = mat[xi]
                rows.append({"logic_hash": lh, "spec_id": spec.id, "filter_set": set_idx,
                             "filters": "+".join(f.block for f in filters) or "-",
                             **{f"entry.{k}": v for k, v in entry.params.items()},
                             **{f"{f.block}.{k}": v for f in filters for k, v in f.params.items()},
                             **exit_columns(tpl, sl, tp, hold),
                             "spec_json": json.dumps(spec.to_dict(), sort_keys=True),
                             **met, "cost": cost_name})
                items.append((spec, cost_name, _metrics_record(met)))
        log(f"  signals {ci + 1}/{len(combos)} done ({len(rows)} runs, {time.time() - t0:.1f}s)")
    env.ledger.record_runs(camp_id, items, period, snap, commit, tpl.family,   # 모든 실행 기록
                           **run_param_fields(pver))

    results = pd.DataFrame(rows)
    returns = pd.DataFrame(ret_cols, index=days)
    returns.index.name = "date"
    manifest = {
        "campaign_id": camp_id, "hypothesis_id": hyp_id, "template": tpl.name, "family": tpl.family,
        "created": utcnow(), "data": {"exchange": tpl.exchange, "symbol": tpl.symbol,
                                      "timeframe": tpl.timeframe, "rows_1m": int(len(m1)),
                                      "filled_frac": float(m1["filled"].mean()), "aux": list(tpl.aux),
                                      "aux_rows_1m": {k: int(len(v)) for k, v in aux_m1.items()}},
        "timeframe": tpl.decision_tf,
        "exits": {"mode": tpl.exit_mode, **({"atr_bars": tpl.atr_bars} if tpl.exit_mode == "atr" else {}),
                  "n_variants": len(exits)},
        "period": period, "data_snapshot": snap, "code_commit": commit, "n_specs": n_specs,
        "n_runs": len(items), "costs": tpl.costs, "evaluated_costs": eval_costs,
        "runtime_sec": round(time.time() - t0, 2), "half_life_days": HALF_LIFE_DAYS,
        "power": pw, "screen": screen,
        "params": pver, "params_version": pver.get("params_version"), "cost_versions": pver.get("cost_versions"),
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
    from .pipeline import jsonable
    (out / "manifest.json").write_text(json.dumps(jsonable(manifest), indent=2, ensure_ascii=False),
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


# ============================================================== 신호 선별 (v4, signal_study.screen 래퍼)
def _aux_screen_template_cls():
    """하위 호환 래퍼. `signal_study.ScreenTemplate` 가 이제 data.aux 를 직접 검증·보관하므로(같은 규칙·
    같은 오류) 이 하위 클래스는 원장 grid 기록에 aux 를 항상(빈 목록이어도) 남기는 것만 다르다
    — run_screen 이 예전부터 남기던 형식 그대로."""
    from btc_quant.research.signal_study import ScreenTemplate

    global _AUX_SCREEN_CLS
    if _AUX_SCREEN_CLS is None:
        @dataclass
        class AuxScreenTemplate(ScreenTemplate):
            def to_record(self) -> dict:
                rec = super().to_record()
                rec["data"] = {**rec["data"], "aux": list(self.aux)}
                return rec

        _AUX_SCREEN_CLS = AuxScreenTemplate
    return _AUX_SCREEN_CLS


_AUX_SCREEN_CLS = None


def load_screen_template(template, registry: Registry = REGISTRY):
    """YAML 경로 | dict | ScreenTemplate → ScreenTemplate (aux 는 ScreenTemplate 이 직접 지원)."""
    cls = _aux_screen_template_cls()
    from btc_quant.research.signal_study import ScreenTemplate
    if isinstance(template, ScreenTemplate):
        return template
    if isinstance(template, dict):
        return cls.from_dict(template, registry)
    if isinstance(template, (str, Path)):
        return cls.from_yaml(template, registry)
    raise CampaignError(f"unsupported screen template {type(template)}")


def run_screen(template, root: str | Path | None = None, env: ResearchEnv | None = None,
               progress: Callable[[str], None] | None = None, lessons: bool = True):
    """신호 선별 (`signal_study.screen`) + 보조 데이터(ctx, 캠페인·페이퍼와 같은 구성) + 오답노트 사실 기록.
    데이터는 Dev 만 (screen 이 DataAccess.read("dev") 로 읽는다)."""
    from btc_quant.research.signal_study import screen
    env = env or ResearchEnv.at(root)
    tpl = load_screen_template(template)
    from btc_quant.research.signal_study import dev_aux_context
    ctx = dev_aux_context(env, tpl) if tpl.aux else None
    res = screen(tpl, env=env, ctx=ctx, progress=progress)
    if lessons:
        from btc_quant.research.lessons import LessonBook
        book = LessonBook.at(env.root)
        res.lesson_facts = [f.get("id") for f in book.record_facts_from_screen(res)]
        book.render_markdown()
    return res
