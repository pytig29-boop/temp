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

자산 유니버스 (`universe: [BTC-KRW, XRP-KRW, ETH-KRW]`, PLAN D15, `research.universe`)
- 생략하면 [data.symbol] (기본 BTC-KRW) → 스펙·해시·결과·원장 기록이 이전과 같다.
- 선언하면 같은 스펙을 자산마다 같은 빠른 엔진으로 실행(자산별 Dev 1분봉·같은 심볼의 업비트 보조 데이터)하고,
  슬리브(1/N) 일간 수익의 평균 = 포트폴리오 일간 수익으로 모든 지표·관문을 계산한다. 거래 수는 자산 합계.
  스펙 하나 = 원장 시도 1회(유니버스는 파라미터가 아님). 자산별 결과는 결과표 `asset.<SYM>.*` 열과 원장
  metrics.per_asset 에 보고만 한다. B&H 기준 = 동일 비중 N자산 매수보유.

주문 집행 (`execution`, docs/ENGINE_SPEC.md "지정가 집행", 2026-11-10)
- 생략 = 시장가(기존). 지정가 격자: `execution: {entry: limit, offset_bp: [3, 5] | offset_atr: [0.3, 0.5],
  tif_bars: [1] (선택, 목록), take_profit: market | limit (단일 값)}` — 오프셋 값·tif 값마다 집행 변형 1개
  (격자 곱에 포함 → 예산·시도 수에 그대로 반영). 스펙 `execution` 에 들어가므로 logic_hash 가 갈린다.
- 신호 캐시(`SignalCache.prepared`)는 `fast.prepare` 를 직접 쓰므로 집행을 **반드시** `simulate_*(execution=…)` 로
  넘긴다(이 모듈·pipeline·nulls·robustness 모두). 지정가 체결 규칙 = `backtest.types.LIMIT_FILL_DEFAULT`
  (시장 측정치 class ①; manifest `fill_model` 에 기록).

부분 사이징·분할 매수 사다리 (`sizing`, `ladder`, docs/ENGINE_SPEC.md "부분 사이징·분할 매수", PLAN D16, 2026-11-10)
- 생략 = 전액 단일 진입(기존). 격자는 작게 고정한다(같은 비중 고정, 비중 맞춤 없음):
    sizing: {entry_fraction: [0.5]}                                # 목록, 값 ≤ 3개
    ladder: {tranches: [3, 4], step_bp: [100, 150] | step_atr: [0.5, 1.0],   # 분할 수 ≤ 2개 값, 간격 2~3개 값
             order: market | limit, exit: per_tranche | average (단일 값; 목록이면 격자 모드 = 파라미터 +1),
             max_exposure: 1.0 (단일 값, class ③ 상한 risk_prefs.max_exposure_per_sleeve 이하)}
  sizing 과 ladder 는 함께 쓸 수 없다. 변형마다 격자 곱에 포함 → 예산·시도 수에 그대로 반영.
- 파라미터 수: 스펙 n_params(사다리 +2 = 분할 수·간격) + 격자 모드(exit 목록, entry_fraction 목록 > 1개) 각 +1 ≤ MAX_PARAMS.
  넘으면 원장에 쓰기 전에 CampaignError (MAX_PARAMS 는 바꾸지 않는다 — 보고 대상).
- 신호 캐시는 `fast.prepare` 를 직접 쓰므로 사이징·사다리도 `simulate_*(sizing=…, ladder=…)` 로 명시해 넘긴다.
  G2 귀무 = 같은 사다리 구조 + 무작위 첫 진입 시각(독립 포지션 수 맞춤), G3 인접 = 분할 수·간격 격자 한 칸 포함.

신호 청산 (`signal_exit`, 선택, 2026-11-10 — docs/MANAGED_HOLD.md "기본 롱 / 필터 청산")
- 생략 = 신호 청산 없음(기존과 같은 스펙·해시·호출). 지정하면 모든 스펙의 `exit.signal_exit` 에 들어간다:
    signal_exit: {block: <exit_signal 블록>, grid: {param: [값 1개], ...}, match_entry: [param, ...]}
  `grid` 의 각 파라미터는 **값 1개**(고정; 격자를 넓히지 않는다 — 스펙 수·시도 수 불변). `match_entry` 의 파라미터는
  진입 블록의 같은 이름 파라미터 값을 그대로 쓴다(예: 진입 trend_ema_4h(span=n) ↔ 청산 trend_ema_off(span=n)).
  블록 기본 격자의 나머지 파라미터도 값이 1개여야 한다. 신호 청산 파라미터는 스펙 n_params 에 포함된다(보수적).
- "추세가 살아 있는 동안 보유" = 진입 블록이 추세 중 계속 True(상태형) + 추세가 꺼지면 신호 청산 + 아주 넓은 SL/TP·
  긴 max_hold. 엔진 의미론은 그대로(ENGINE_SPEC 상태 기계: 신호 청산은 결정 경계 시가, 청산 후 쿨다운 봉 1개).
- 신호 캐시(`SignalCache.prepared(entry, filters, signal_exit)`)와 파이프라인 Dev 재계산(`Pipeline.dev_sleeves`)이 같은
  청산 신호를 쓴다. G2 귀무(`nulls.RandomEntryNull`)는 이전처럼 신호 청산 없이 같은 SL/TP/만기 + 무작위 진입이다 —
  기본 롱 전략에 대한 그 귀무의 적합성은 관문 검토 질문으로 남겨 둔다(docs/MANAGED_HOLD.md §5; 관문은 바꾸지 않음).
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
from btc_quant.backtest.types import LIMIT_FILL_DEFAULT, CostModel
from btc_quant.data.quality import prepare_venue_m1
from btc_quant.evaluation import metrics as M
from btc_quant.registry.ledger import Ledger, repo_root, utcnow
from btc_quant.registry.snapshot import snapshot_id
from btc_quant.registry.splits import DataAccess
from btc_quant.registry.strategies import StrategyRegistry
from btc_quant.strategy.blocks import REGISTRY, Context, Registry
from btc_quant.strategy.features import to_tf
from btc_quant.strategy.spec import (DEFAULT_ATR_BARS, DEFAULT_MAX_EXPOSURE, DEFAULT_TIF_BARS, DEFAULT_TIMEFRAME,
                                     DEFAULT_UNIVERSE, ENTRY_MODES, FULL, LADDER_EXITS, LADDER_MAX_TRANCHES,
                                     LADDER_MIN_TRANCHES, LADDER_ORDERS, MARKET, MAX_FILTERS, MAX_PARAMS, TIMEFRAMES,
                                     TP_MODES, BlockRef, ExecutionSpec, ExitSpec, LadderSpec, SizingSpec,
                                     StrategySpec, tf_minutes, tf_name)

from . import universe as U

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


def gross_metrics(p: fast.Prepared, tr: fast.TradeArrays, cost: CostModel, start, end,
                  accounting: str = "realized") -> dict:
    """같은 거래를 zero-cost(수수료·스프레드·슬리피지 0, tick 은 원래 실행 값)로 → {gross_sharpe_w, gross_expectancy}.
    gross_sharpe_w = 가중 Sharpe(`metrics.summarize` 의 sharpe_w 와 같은 식), gross_expectancy = 평균 gross 거래 수익.
    accounting: 캠페인 일간 수익 회계(PLAN D18) — 'mtm' 이면 zero-cost 평가 기준."""
    zero = CostModel("zero_cost", 0.0, 0.0, 0.0, cost.tick)
    tr0 = fast.reprice(tr, zero)
    days, mat = fast.daily_returns_matrix(p, [tr0], start, end, accounting=accounting, cost=zero)
    ws = M.weighted_sharpe(pd.Series(mat[0], index=days), HALF_LIFE_DAYS)
    return {"gross_sharpe_w": float(ws.sharpe),
            "gross_expectancy": float(tr0.net_ret.mean()) if len(tr0.net_ret) else 0.0}


def gross_metrics_sleeves(pairs: list[tuple[fast.Prepared, fast.TradeArrays]], cost: CostModel, start, end,
                          accounting: str = "realized") -> dict:
    """다자산 유니버스: 슬리브마다 zero-cost 재가격 → 포트폴리오(평균) 일간 수익의 가중 Sharpe, 풀링 거래 평균.
    슬리브 1개면 `gross_metrics` 그대로."""
    if len(pairs) == 1:
        return gross_metrics(pairs[0][0], pairs[0][1], cost, start, end, accounting)
    zero = CostModel("zero_cost", 0.0, 0.0, 0.0, cost.tick)
    mats, nets, days = [], [], None
    for p, tr in pairs:
        tr0 = fast.reprice(tr, zero)
        days, mat = fast.daily_returns_matrix(p, [tr0], start, end, accounting=accounting, cost=zero)
        mats.append(mat)
        nets.append(np.asarray(tr0.net_ret, float))
    ws = M.weighted_sharpe(pd.Series(U.combine_matrix(mats)[0], index=days), HALF_LIFE_DAYS)
    allr = np.concatenate(nets) if nets else np.zeros(0)
    return {"gross_sharpe_w": float(ws.sharpe), "gross_expectancy": float(allr.mean()) if allr.size else 0.0}


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
    if root is not None and not p.exists():
        # 임시 연구 저장소에 costs.yaml 이 없으면 코드 저장소의 비용 레지스트리 (파이프라인과 같은 해석; v1 프리셋 아님)
        p = costs_path(repo_root())
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


def sleeve_cost_name(name: str, symbol: str, root: str | Path | None = None) -> str:
    """다자산 슬리브의 비용 모델 이름: config/costs.yaml 에 `<name>@<SYM>` (예: coinone_conservative@XRP-KRW) 이 있으면 그것,
    없으면 `<name>` (PLAN D15 — 자산별 호가 단위·스프레드가 다르다; 자산별 모델 추가는 비용 레지스트리 절차·사람 결정).
    기본 심볼(BTC-KRW)은 항상 `<name>` — 단일 자산 경로는 이전과 같다."""
    if symbol == U.DEFAULT_SYMBOL:
        return name
    cand = f"{name}@{symbol}"
    return cand if cand in (_cost_doc(root).get("models") or {}) else name


def get_sleeve_cost(name: str, symbol: str, root: str | Path | None = None) -> CostModel:
    return get_cost(sleeve_cost_name(name, symbol, root), root)


def campaign_accounting(campaign_id: str | None, env: "ResearchEnv | None" = None) -> str:
    """캠페인의 관문 일간 수익 회계 (PLAN D18): 등록 시점의 `evaluation.daily_returns`
    (`gate_config_for_campaign` — 승인 전에 등록된 캠페인은 'realized' 그대로)."""
    from btc_quant.evaluation.gates import daily_returns_mode
    from btc_quant.params import gate_config_for_campaign
    return daily_returns_mode(gate_config_for_campaign(campaign_id, root=config_root(env)))


def manifest_accounting(manifest: dict | None) -> str:
    """저장된 캠페인의 일간 수익 회계 — 매니페스트 `daily_returns` (D18 이전 캠페인은 키가 없음 = 'realized')."""
    return str((manifest or {}).get("daily_returns") or "realized")


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
    decision_tf: str = DEFAULT_TIMEFRAME   # 결정 주기 TIMEFRAMES 키 "5m" | "1m" | "15m" | "1h" | "4h" (D10, §10 스윙)
    exit_mode: str = "pct"                 # "pct" | "atr"
    atr_bars: int = DEFAULT_ATR_BARS       # ATR 모드 ATR 창 (결정 주기 봉 수)
    from_screen: str | None = None         # 선별 캠페인 id (원장) — 이 선별에서 통과한 구성만 허용
    requires_screen_pass: bool = False     # True: 원장의 어떤 선별에서든 통과한 구성만 허용
    power: dict = field(default_factory=dict)   # {target_sharpe: float} (선택)
    universe: tuple[str, ...] = DEFAULT_UNIVERSE   # 자산 유니버스 (D15) — 기본 [data.symbol]
    execution: dict = field(default_factory=dict)   # 집행 격자 (정규화된 dict; 빈 dict = 시장가)
    sizing: dict = field(default_factory=dict)      # 사이징 격자 {entry_fraction: [...]} (빈 dict = 전액)
    ladder: dict = field(default_factory=dict)      # 사다리 격자 (정규화된 dict; 빈 dict = 단일 진입)
    signal_exit: dict = field(default_factory=dict)  # {block, params(고정), match_entry} (빈 dict = 신호 청산 없음)
    gate_profile: str = "default"   # 관문 프로필 (Phase 5): default | managed_hold (gates.yaml profiles.*.enabled 필요)

    @property
    def tf_min(self) -> int:
        return tf_minutes(self.decision_tf)

    @property
    def multi_asset(self) -> bool:
        return len(self.universe) > 1

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
                   "family", "segment", "timeframe", "from_screen", "requires_screen_pass", "power", "universe",
                   "execution", "sizing", "ladder", "signal_exit", "gate_profile"}
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
        universe = template_universe(d)
        execution = _execution_grid(d.get("execution"))
        sizing, ladder = _position_grid(d.get("sizing"), d.get("ladder"))
        entry = _block_grid(d.get("entry"), "entry", registry)
        signal_exit = _signal_exit_grid(d.get("signal_exit"), entry, registry)
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
        gate_profile = d.get("gate_profile", "default")
        if gate_profile not in GATE_PROFILES:
            raise CampaignError(f"gate_profile must be one of {GATE_PROFILES}")
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
                       for r in registry.get(b.block).requires}
                      | (set(registry.get(signal_exit["block"]).requires) if signal_exit else set()))
        missing = [r for r in need if r not in aux]
        if missing:
            raise CampaignError(f"blocks require aux data {missing}; declare data.aux: {need} "
                                f"(live-available: {list(LIVE_AUX)})")
        tpl = cls(name=name, hypothesis={k: hyp[k] for k in ("text", "rationale", "success_criteria")},
                  exchange=exchange, symbol=universe[0],
                  timeframe=str(data.get("timeframe", "1m")), aux=aux, entry=entry, filter_sets=fsets,
                  exits=exits, costs=costs, budget=budget,
                  family=str(d.get("family") or entry.block), segment=segment, raw=d,
                  decision_tf=tf, exit_mode=mode, atr_bars=int(atr_bars),
                  from_screen=from_screen.strip() if from_screen else None, requires_screen_pass=rsp,
                  power=power, universe=universe, execution=execution, sizing=sizing, ladder=ladder,
                  signal_exit=signal_exit, gate_profile=gate_profile)
        tpl.validate_specs(registry)
        return tpl

    # ---------------------------------------------------------- 격자
    def n_specs(self) -> int:
        n_exit = math.prod(len(v) for v in self.exits.values())
        n_entry = math.prod(len(v) for v in self.entry.grid.values())
        n_f = sum(math.prod(math.prod(len(v) for v in f.grid.values()) for f in fs)
                  for fs in self.filter_sets)
        return n_entry * n_f * n_exit * len(self.execution_combos()) * len(self.position_combos())

    # ---------------------------------------------------------- 사이징·사다리
    def position_combos(self) -> list[tuple[SizingSpec, LadderSpec | None]]:
        """(사이징, 사다리) 변형 (격자 순서 = 분할 수 → 간격 → 청산 방식; 사이징 = entry_fraction 순). 없으면 [(전액, None)]."""
        if self.ladder:
            g = self.ladder
            key = "step_atr" if "step_atr" in g else "step_bp"
            return [(FULL, LadderSpec(int(n), order=g["order"], exit=str(x), max_exposure=float(g["max_exposure"]),
                                      **{key: float(v)}))
                    for n in g["tranches"] for v in g[key] for x in g["exit"]]
        if self.sizing:
            return [(SizingSpec(float(f)), None) for f in self.sizing["entry_fraction"]]
        return [(FULL, None)]

    def gridded_mode_params(self) -> int:
        """격자로 고른 모드·크기 선택의 파라미터 수 (스펙 n_params 에 없는 것): 사다리 exit 목록 > 1 → +1,
        entry_fraction 목록 > 1 → +1. 고정 선택은 0."""
        n = 0
        if self.ladder and len(self.ladder["exit"]) > 1:
            n += 1
        if self.sizing and len(self.sizing["entry_fraction"]) > 1:
            n += 1
        return n

    # ---------------------------------------------------------- 집행
    def execution_combos(self) -> list[ExecutionSpec]:
        """집행 변형 (격자 순서 = 오프셋 → tif). 집행 격자가 없으면 [시장가]."""
        e = self.execution
        if not e:
            return [MARKET]
        if e["entry"] == "market":
            return [ExecutionSpec(take_profit=e["take_profit"])]
        key = "offset_atr" if "offset_atr" in e else "offset_bp"
        return [ExecutionSpec(entry="limit", take_profit=e["take_profit"], tif_bars=int(t), **{key: float(o)})
                for o in e[key] for t in e["tif_bars"]]

    @property
    def needs_atr_window(self) -> bool:
        """신호 캐시가 ATR 비율 배열을 준비해야 하는가 (ATR 청산 · ATR 오프셋 지정가 · ATR 간격 사다리)."""
        return (self.exit_mode == "atr" or "offset_atr" in (self.execution or {})
                or "step_atr" in (self.ladder or {}))

    @property
    def prep_atr_bars(self) -> int | None:
        """SignalCache/prepare 의 ATR 창 (ATR 청산 창, 고정 % 청산 + ATR 오프셋이면 기본 14)."""
        return self.atr_bars if self.needs_atr_window else None

    def exit_combos(self) -> list[tuple[float, float, int]]:
        """(sl, tp, max_hold_min) — sl/tp 는 고정 % 모드에서 %, ATR 모드에서 ATR 배수."""
        return list(itertools.product(*(self.exits[k] for k in self.exit_keys)))

    @property
    def sim_atr_bars(self) -> int | None:
        """fast.simulate_many(..., atr_bars=) 인자: ATR 모드면 ATR 창, 아니면 None."""
        return self.atr_bars if self.exit_mode == "atr" else None

    def exit_ref(self, entry: BlockRef) -> BlockRef | None:
        """진입 BlockRef → 신호 청산 BlockRef (템플릿 `signal_exit`; 없으면 None). match_entry 파라미터는 진입 값."""
        se = self.signal_exit
        if not se:
            return None
        params = dict(se["params"])
        for k in se.get("match_entry") or ():
            params[k] = entry.params[k]
        return BlockRef(se["block"], {k: params[k] for k in se["order"]})

    def make_exit(self, sl: float, tp: float, hold: int, signal_exit: BlockRef | None = None) -> ExitSpec:
        if self.exit_mode == "atr":
            return ExitSpec(max_hold_min=int(hold), stop_loss_atr=float(sl), take_profit_atr=float(tp),
                            atr_bars=int(self.atr_bars), signal_exit=signal_exit)
        return ExitSpec(float(sl), float(tp), int(hold), signal_exit=signal_exit)

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
                  hold: int, execution: ExecutionSpec | None = None, sizing: SizingSpec | None = None,
                  ladder: LadderSpec | None = None) -> StrategySpec:
        """sl/tp: 고정 % 모드는 %, ATR 모드는 ATR 배수. 결정 주기 = 템플릿 timeframe. execution: 집행 변형 (None = 시장가).
        sizing/ladder: 사이징·사다리 변형 (None = 전액 단일 진입)."""
        ex = self.make_exit(sl, tp, hold, self.exit_ref(entry))
        xe = execution or MARKET
        sz = sizing or FULL
        tmp = StrategySpec("x", entry, ex, tuple(filters), timeframe=self.decision_tf, universe=self.universe,
                           execution=xe, sizing=sz, ladder=ladder)
        return StrategySpec(f"{self.name}_{tmp.logic_hash()[:10]}", entry, ex, tuple(filters),
                            timeframe=self.decision_tf, universe=self.universe, execution=xe, sizing=sz, ladder=ladder)

    def enumerate(self) -> list[StrategySpec]:
        exits = self.exit_combos()
        return [self.make_spec(e, fs, *x, execution=xe, sizing=sz, ladder=lad) for e, fs, _ in self.signal_combos()
                for xe in self.execution_combos() for sz, lad in self.position_combos() for x in exits]

    def validate_specs(self, registry: Registry = REGISTRY) -> None:
        """구조(필터 세트)마다 대표 스펙 하나로 StrategySpec.validate (모든 스펙의 파라미터 수가 같음)."""
        x = tuple(self.exits[k][0] for k in self.exit_keys)
        e = BlockRef(self.entry.block, self.entry.combos()[0])
        xe = max(self.execution_combos(), key=lambda q: q.n_params())   # 파라미터 수 최대 변형으로 검증
        sz, lad = self.position_combos()[0]
        extra = self.gridded_mode_params()
        for fs in self.filter_sets:
            spec = self.make_spec(e, tuple(BlockRef(f.block, f.combos()[0]) for f in fs), *x, execution=xe,
                                  sizing=sz, ladder=lad)
            try:
                spec.validate(registry)
            except (ValueError, KeyError) as err:
                raise CampaignError(f"invalid spec structure {[f.block for f in fs]}: {err}") from err
            if spec.n_params() + extra > MAX_PARAMS:
                raise CampaignError(
                    f"spec structure {[f.block for f in fs]} has {spec.n_params()} params + {extra} gridded mode(s) "
                    f"> MAX_PARAMS {MAX_PARAMS} (ladder adds tranches + step; a gridded exit mode / entry_fraction "
                    "adds 1). Fix the mode or drop a parameter — MAX_PARAMS is not raised to fit (report it).")

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
        if self.gate_profile != "default":
            rec["gate_profile"] = self.gate_profile
        if self.screen_gated:
            rec["screen_gate"] = {"from_screen": self.from_screen, "requires_screen_pass": self.requires_screen_pass}
        if not U.is_default(self.universe):
            rec["universe"] = list(self.universe)
        if self.execution:
            rec["execution"] = dict(self.execution)
        if self.sizing:
            rec["sizing"] = dict(self.sizing)
        if self.ladder:
            rec["ladder"] = dict(self.ladder)
        if self.signal_exit:
            rec["signal_exit"] = {"block": self.signal_exit["block"], "params": dict(self.signal_exit["params"]),
                                  "match_entry": list(self.signal_exit.get("match_entry") or [])}
        return rec


def _position_grid(sz, lad) -> tuple[dict, dict]:
    """템플릿 `sizing`·`ladder` → 정규화 dict ({} = 없음). 격자는 작게: entry_fraction ≤ 3개, tranches ≤ 2개(2~5),
    간격 2~3개(1개도 허용), order 단일 값, exit 단일 값 또는 두 모드 목록, max_exposure 단일 값 ≤ class ③ 상한."""
    from btc_quant.params import max_exposure_cap
    cap = max_exposure_cap()
    if sz is not None and lad is not None:
        raise CampaignError("sizing and ladder cannot be combined (ladder tranche size = max_exposure / tranches)")
    sizing: dict = {}
    if sz is not None:
        if not isinstance(sz, dict) or set(sz) - {"entry_fraction"} or "entry_fraction" not in sz:
            raise CampaignError("sizing must be a mapping {entry_fraction: [values]}")
        vals = _value_list(sz["entry_fraction"] if isinstance(sz["entry_fraction"], list) else [sz["entry_fraction"]],
                           "sizing.entry_fraction")
        if len(vals) > 3:
            raise CampaignError("sizing.entry_fraction: at most 3 values (keep the grid small)")
        for v in vals:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0 < v <= 1):
                raise CampaignError("sizing.entry_fraction values must be in (0, 1]")
            if v > cap + 1e-12:
                raise CampaignError(f"sizing.entry_fraction {v} > class ③ cap risk_prefs.max_exposure_per_sleeve {cap}")
        sizing = {"entry_fraction": [float(v) for v in vals]}
        if sizing["entry_fraction"] == [1.0]:
            sizing = {}
    ladder: dict = {}
    if lad is not None:
        if not isinstance(lad, dict):
            raise CampaignError("ladder must be a mapping {tranches, step_bp|step_atr, order, exit, max_exposure}")
        unknown = set(lad) - {"tranches", "step_bp", "step_atr", "order", "exit", "max_exposure"}
        if unknown:
            raise CampaignError(f"ladder: unknown keys {sorted(unknown)} (weights are fixed equal — no weight grid)")
        if "tranches" not in lad:
            raise CampaignError("ladder.tranches required (a list, e.g. [3, 4])")
        tr = _value_list(lad["tranches"] if isinstance(lad["tranches"], list) else [lad["tranches"]],
                         "ladder.tranches")
        if len(tr) > 2:
            raise CampaignError("ladder.tranches: at most 2 values (keep the grid small, e.g. [3, 4])")
        for n in tr:
            if isinstance(n, bool) or not isinstance(n, int) or not (LADDER_MIN_TRANCHES <= n <= LADDER_MAX_TRANCHES):
                raise CampaignError(f"ladder.tranches values must be integers in [{LADDER_MIN_TRANCHES}, "
                                    f"{LADDER_MAX_TRANCHES}]")
        if ("step_bp" in lad) == ("step_atr" in lad):
            raise CampaignError("ladder needs exactly one of step_bp / step_atr (a list)")
        key = "step_bp" if "step_bp" in lad else "step_atr"
        steps = _value_list(lad[key] if isinstance(lad[key], list) else [lad[key]], f"ladder.{key}")
        if len(steps) > 3:
            raise CampaignError(f"ladder.{key}: at most 3 values (keep the grid small)")
        for v in steps:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0:
                raise CampaignError(f"ladder.{key} values must be > 0")
            if key == "step_bp" and (max(tr) - 1) * v >= 1e4:
                raise CampaignError("ladder.step_bp × (tranches − 1) must be < 10000")
        order = lad.get("order", "market")
        if not isinstance(order, str) or order not in LADDER_ORDERS:
            raise CampaignError(f"ladder.order must be one of {LADDER_ORDERS} (a single value)")
        ex = lad.get("exit", "average")
        exits = _value_list(ex if isinstance(ex, list) else [ex], "ladder.exit")
        if any(x not in LADDER_EXITS for x in exits):
            raise CampaignError(f"ladder.exit values must be in {LADDER_EXITS}")
        mx = lad.get("max_exposure", DEFAULT_MAX_EXPOSURE)
        if isinstance(mx, (list, bool)) or not isinstance(mx, (int, float)) or not (0 < mx <= 1):
            raise CampaignError("ladder.max_exposure must be a single value in (0, 1] (class ③ preference, not a grid)")
        if mx > cap + 1e-12:
            raise CampaignError(f"ladder.max_exposure {mx} > class ③ cap risk_prefs.max_exposure_per_sleeve {cap}")
        ladder = {"tranches": [int(n) for n in tr], key: [float(v) for v in steps], "order": order,
                  "exit": [str(x) for x in exits], "max_exposure": float(mx)}
    return sizing, ladder


def _execution_grid(d) -> dict:
    """템플릿 `execution` → 정규화 dict ({} = 시장가). 모르는 키·잘못된 값은 CampaignError (조용히 무시하지 않는다)."""
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise CampaignError("execution must be a mapping {entry, offset_bp|offset_atr, tif_bars, take_profit}")
    unknown = set(d) - {"entry", "offset_bp", "offset_atr", "tif_bars", "take_profit"}
    if unknown:
        raise CampaignError(f"execution: unknown keys {sorted(unknown)}")
    entry = str(d.get("entry", "market"))
    tpm = str(d.get("take_profit", "market"))
    if entry not in ENTRY_MODES:
        raise CampaignError(f"execution.entry must be one of {ENTRY_MODES} (a single value, not a grid)")
    if tpm not in TP_MODES:
        raise CampaignError(f"execution.take_profit must be one of {TP_MODES} (a single value, not a grid)")
    out: dict = {"entry": entry, "take_profit": tpm}
    if entry == "market":
        if any(k in d for k in ("offset_bp", "offset_atr", "tif_bars")):
            raise CampaignError("execution.offset_bp/offset_atr/tif_bars are only valid with entry: limit")
        return out if tpm != "market" else {}
    if ("offset_bp" in d) == ("offset_atr" in d):
        raise CampaignError("execution (limit entry) needs exactly one of offset_bp / offset_atr (a list)")
    key = "offset_bp" if "offset_bp" in d else "offset_atr"
    vals = _value_list(d[key] if isinstance(d[key], list) else [d[key]], f"execution.{key}")
    for v in vals:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or (key == "offset_atr" and v == 0):
            raise CampaignError(f"execution.{key} values must be numbers ≥ 0 (offset_atr > 0)")
    tifs = d.get("tif_bars", [DEFAULT_TIF_BARS])
    tifs = _value_list(tifs if isinstance(tifs, list) else [tifs], "execution.tif_bars")
    for t in tifs:
        if isinstance(t, bool) or not isinstance(t, int) or t < 1:
            raise CampaignError("execution.tif_bars values must be positive integers")
    out[key] = [float(v) for v in vals]
    out["tif_bars"] = [int(t) for t in tifs]
    return out


def template_universe(d: dict) -> tuple[str, ...]:
    """템플릿(캠페인·선별 공통) → 자산 유니버스. `universe` 가 없으면 (data.symbol,) (기본 BTC-KRW, 하위 호환).
    있으면 D15 고정 자산 안에서 정식 순서로; data.symbol 을 함께 주면 유니버스 안에 있어야 한다."""
    data = d.get("data") or {}
    sym = data.get("symbol")
    if d.get("universe") is None:
        return (str(sym or U.DEFAULT_SYMBOL),)
    try:
        uni = U.normalize_universe(d["universe"])
    except U.UniverseError as e:
        raise CampaignError(str(e)) from None
    if sym is not None and str(sym) not in uni:
        raise CampaignError(f"data.symbol {sym!r} is not in universe {list(uni)} (omit data.symbol with a universe)")
    return uni


def _value_list(v, where: str) -> list:
    if not isinstance(v, list) or not v:
        raise CampaignError(f"{where} must be a non-empty list")
    if len(set(map(repr, v))) != len(v):
        raise CampaignError(f"{where} has duplicate values")
    return list(v)


def _signal_exit_grid(d, entry: BlockGrid, registry: Registry) -> dict:
    """템플릿 `signal_exit` → {block, params(고정 값), match_entry, order} ({} = 없음). 격자를 넓히지 않는다:
    match_entry 가 아닌 파라미터는 값이 정확히 1개여야 한다."""
    if d is None:
        return {}
    if not isinstance(d, dict) or "block" not in d:
        raise CampaignError("signal_exit must be a mapping {block, grid?, match_entry?}")
    extra = set(d) - {"block", "grid", "match_entry"}
    if extra:
        raise CampaignError(f"signal_exit: unknown keys {sorted(extra)}")
    match = d.get("match_entry") or []
    if not isinstance(match, list) or not all(isinstance(k, str) for k in match):
        raise CampaignError("signal_exit.match_entry must be a list of parameter names")
    bg = _block_grid({"block": d["block"], **({"grid": d["grid"]} if d.get("grid") else {})}, "exit_signal",
                     registry)
    bad = [k for k in match if k not in bg.grid or k not in entry.grid]
    if bad:
        raise CampaignError(f"signal_exit.match_entry {bad}: must be parameters of both {bg.block} and "
                            f"the entry block {entry.block}")
    if set(match) & set(d.get("grid") or {}):
        raise CampaignError("signal_exit: a match_entry parameter cannot also be given in grid")
    params = {}
    for k, vals in bg.grid.items():
        if k in match:
            continue
        if len(vals) != 1:
            raise CampaignError(f"signal_exit {bg.block}.{k}: exactly one value (signal exits are fixed or tied "
                                "to the entry via match_entry; they do not widen the grid)")
        params[k] = vals[0]
    return {"block": bg.block, "params": params, "match_entry": list(match), "order": list(bg.grid)}


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

    ctx: 보조 데이터(`aux_context`). tf_min: 결정 주기 B (분: 1/5/15/60/240). atr_bars: ATR 청산 창(있으면 prepare 에서
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

    def exit_sig(self, signal_exit: BlockRef | None) -> pd.Series | None:
        """신호 청산 블록 값 (signals.build_signals 와 같은 exit_sig; None = 신호 청산 없음)."""
        if signal_exit is None:
            return None
        return pd.Series(self.block(signal_exit), index=self.bars.index, name="exit_sig")

    def prepared(self, entry: BlockRef, filters, signal_exit: BlockRef | None = None) -> fast.Prepared:
        return fast.prepare(self.m1, self.entry_ok(entry, filters), self.exit_sig(signal_exit),
                            timeframe=self.timeframe, atr_bars=self.atr_bars, bars=self.bars)


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
                segment: str | None = None, symbol: str | None = None) -> dict[str, pd.DataFrame]:
    """템플릿 data.aux 의 보조 거래소 1분봉 (같은 구간, DataAccess 경유, regularize(clean_wicks)).
    symbol: 유니버스 자산(기본 tpl.symbol) — 보조 거래소도 **같은 심볼**을 읽는다."""
    out = {}
    sym = symbol or tpl.symbol
    for a in tpl.aux:
        raw = (env.data.read(segment, a, sym, tpl.timeframe) if segment is not None
               else env.data.read_range(start, end, a, sym, tpl.timeframe))
        if raw.empty:
            raise CampaignError(f"no {a} aux data for {segment or (start, end)}")
        out[a] = prepare_m1(raw, a)
    return out


def data_snapshot(env: ResearchEnv, tpl: "CampaignTemplate", start, end) -> str:
    """대상 시계열 스냅샷 id (+ 보조 데이터가 있으면 `|<aux>:<id>` 를 이어 붙임).
    다자산 유니버스: 첫 자산은 위와 같은 형식, 다른 자산은 `|<SYM>:<id>` 와 `|<aux>/<SYM>:<id>` 를 이어 붙인다."""
    snap = snapshot_id(tpl.exchange, tpl.symbol, tpl.timeframe, start, end, data_root=env.data.data_root)
    for a in tpl.aux:
        snap += f"|{a}:" + snapshot_id(a, tpl.symbol, tpl.timeframe, start, end, data_root=env.data.data_root)
    for sym in tpl.universe:
        if sym == tpl.symbol:
            continue
        snap += f"|{sym}:" + snapshot_id(tpl.exchange, sym, tpl.timeframe, start, end, data_root=env.data.data_root)
        for a in tpl.aux:
            snap += f"|{a}/{sym}:" + snapshot_id(a, sym, tpl.timeframe, start, end, data_root=env.data.data_root)
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


def record_universe(grid: dict) -> tuple[str, ...]:
    """원장 캠페인·선별 grid 기록 → 유니버스 (기록이 없으면 (data.symbol,) — 기본 BTC-KRW)."""
    u = (grid or {}).get("universe") or ((grid or {}).get("data") or {}).get("universe")
    if u:
        return tuple(u)
    return (str(((grid or {}).get("data") or {}).get("symbol") or U.DEFAULT_SYMBOL),)


def screen_passes(ledger: Ledger, screen_id: str | None = None, exchange: str | None = None,
                  universe: tuple[str, ...] | None = None) -> dict[str, dict]:
    """원장의 선별 통과 구성 {signal_config_key: 근거}. screen_id 가 있으면 그 선별만.
    exchange 가 있으면 대상 거래소가 같은 선별만 (선별 캠페인 grid.data.exchange).
    universe 가 있으면 같은 유니버스로 풀링한 선별만 (D15: 3자산 캠페인은 3자산 풀링 선별 통과가 필요)."""
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
        if universe is not None and record_universe(grid) != tuple(universe):
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
    passes = screen_passes(ledger, tpl.from_screen, tpl.exchange, tpl.universe)
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
            f"(exchange {tpl.exchange}, timeframe {tpl.decision_tf}, universe {list(tpl.universe)}): {missing[:5]}"
            f"{' …' if len(missing) > 5 else ''}. Screen them first (research.cli screen) or narrow the grid.")
    return {"from_screen": tpl.from_screen, "requires_screen_pass": tpl.requires_screen_pass,
            "n_configs": len(matches), "matches": matches,
            "rule": "every (entry params × filter params × timeframe × exchange × universe) config passed a "
                    "recorded screen"}


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


METRIC_DROP = ("spec_id", "cost", *M.BENCHMARK_CONSTANT_KEYS)   # B&H 자체 값은 manifest["benchmark"] 에 한 번


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
             + (f", universe {list(tpl.universe)} (pooled, equal sleeves; 1 trial per spec)"
                if not U.is_default(tpl.universe) else "")
             + (f", execution {tpl.execution}" if tpl.execution else "")
             + (f", sizing {tpl.sizing}" if tpl.sizing else "") + (f", ladder {tpl.ladder}" if tpl.ladder else "")
             + f"; evaluated on dev with {tpl.costs['primary']} (primary) and {tpl.costs['grade']} (grade)")
    return hyp_id, camp_id


def execution_columns(tpl: CampaignTemplate, xe: ExecutionSpec) -> dict:
    """결과표의 집행 열 (집행 격자가 있는 템플릿만): exec.offset_bp|exec.offset_atr, exec.tif_bars."""
    if not tpl.execution or tpl.execution.get("entry") != "limit":
        return {}
    key = "offset_atr" if "offset_atr" in tpl.execution else "offset_bp"
    return {f"exec.{key}": getattr(xe, key), "exec.tif_bars": xe.tif_bars}


def position_columns(tpl: CampaignTemplate, sz: SizingSpec, lad: LadderSpec | None) -> dict:
    """결과표의 사이징·사다리 열 (해당 격자가 있는 템플릿만)."""
    if tpl.ladder and lad is not None:
        key = "step_atr" if "step_atr" in tpl.ladder else "step_bp"
        return {"ladder.tranches": lad.tranches, f"ladder.{key}": getattr(lad, key), "ladder.exit": lad.exit}
    if tpl.sizing:
        return {"sizing.entry_fraction": sz.entry_fraction}
    return {}


def exit_columns(tpl: CampaignTemplate, sl, tp, hold) -> dict:
    """결과표의 청산 열: 고정 % = sl_pct/tp_pct/max_hold_min, ATR = sl_atr/tp_atr/max_hold_min/atr_bars."""
    k_sl, k_tp, k_h = tpl.exit_keys
    out = {k_sl: sl, k_tp: tp, k_h: hold}
    if tpl.exit_mode == "atr":
        out["atr_bars"] = tpl.atr_bars
    return out


GATE_PROFILES = ("default", "managed_hold")


def check_gate_profile(tpl: "CampaignTemplate", env: "ResearchEnv") -> None:
    """기본이 아닌 관문 프로필은 gates.yaml profiles.<name>.enabled (사람 승인) 일 때만 새 캠페인에 쓸 수 있다."""
    if tpl.gate_profile == "default":
        return
    from btc_quant.params._io import read_yaml
    cfg = read_yaml(config_root(env) / "config" / "gates.yaml")
    prof = ((cfg.get("profiles") or {}).get(tpl.gate_profile) or {})
    if not prof.get("enabled"):
        raise CampaignError(f"gate_profile {tpl.gate_profile!r} is not enabled in config/gates.yaml "
                            "(class ② approval needed)")


def run_campaign(template: CampaignTemplate | str | Path, root: str | Path | None = None,
                 env: ResearchEnv | None = None,
                 progress: Callable[[str], None] | None = None, strict_power: bool = False) -> CampaignResult:
    tpl = template if isinstance(template, CampaignTemplate) else CampaignTemplate.from_yaml(template)
    env = env or ResearchEnv.at(root)
    log = progress or (lambda s: None)
    n_specs = tpl.check_budget()                       # 예산 초과면 원장에 쓰기 전에 거부
    check_gate_profile(tpl, env)                       # 승인되지 않은 관문 프로필 → 원장 쓰기 전 거부
    from btc_quant.evaluation.gates import load_gate_config
    from btc_quant.params.floor import check_floor     # 바닥 규칙 (D29) → 원장 쓰기 전 거부
    check_floor(load_gate_config(config_root(env) / "config" / "gates.yaml"), config_root(env), tpl=tpl)
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
    # 관문 일간 수익 회계 (PLAN D18, class ②): 등록 시점 기준 설정 → 매니페스트에 고정 (파이프라인이 따른다)
    acct = campaign_accounting(camp_id, env)
    if acct != "realized":
        log(f"daily returns: {acct} (mark-to-market at each UTC day close; PLAN D18)")

    t0 = time.time()
    syms = list(tpl.universe)
    multi = len(syms) > 1
    m1s, auxs = {}, {}
    for sym in syms:      # 자산마다 같은 구성 (기본 유니버스 = 이전과 같은 호출 하나)
        m1s[sym] = load_segment(env, tpl.segment, tpl.exchange, sym, tpl.timeframe)
        auxs[sym] = load_aux_m1(env, tpl, segment=tpl.segment, symbol=sym)
    caches = {sym: SignalCache(m1s[sym], aux_context(auxs[sym], tpl.tf_min), tf_min=tpl.tf_min,
                               atr_bars=tpl.prep_atr_bars) for sym in syms}
    m1, aux_m1 = m1s[syms[0]], auxs[syms[0]]
    eval_costs = [tpl.costs["primary"], tpl.costs["grade"]]
    cost_by_name = {c: get_cost(c, config_root(env)) for c in eval_costs}
    # 슬리브별 비용 (자산별 모델 `<name>@<SYM>` 이 있으면; 없으면 같은 모델)
    sleeve_costs = {sym: {c: get_sleeve_cost(c, sym, config_root(env)) for c in eval_costs} for sym in syms}
    exits = tpl.exit_combos()
    rows, items, ret_cols, specs = [], [], {}, {}
    days = None
    bh = None                   # 매수보유 일간 수익 (정보 전용; 모든 스펙에 같은 일자) — 다자산: 동일 비중 B&H
    bench_const: dict = {}
    combos = tpl.signal_combos()
    xcombos = tpl.execution_combos()
    pcombos = tpl.position_combos()
    loop = [(entry, filters, set_idx, xe, pc) for entry, filters, set_idx in combos for xe in xcombos
            for pc in pcombos]
    for ci, (entry, filters, set_idx, xe, (sz, lad)) in enumerate(loop):
        if ci == 0 or loop[ci - 1][:3] != (entry, filters, set_idx):
            ps = {sym: caches[sym].prepared(entry, filters, tpl.exit_ref(entry)) for sym in syms}
        xarg = None if (xe.is_default and lad is None and sz.is_default) else xe   # 기본 = 기존 호출 그대로
        pkw = {} if (lad is None and sz.is_default) else {"sizing": None if sz.is_default else sz, "ladder": lad}
        for cost_name in eval_costs:
            cost = cost_by_name[cost_name]
            trs = {sym: (fast.simulate_many(ps[sym], exits, sleeve_costs[sym][cost_name], start, end,
                                            atr_bars=tpl.sim_atr_bars) if xarg is None else
                         fast.simulate_many(ps[sym], exits, sleeve_costs[sym][cost_name], start, end,
                                            atr_bars=tpl.sim_atr_bars, execution=xarg, fill=LIMIT_FILL_DEFAULT,
                                            **pkw))
                   for sym in syms}
            if cost_name == tpl.costs["primary"]:
                mats = []
                for sym in syms:
                    days, m_ = fast.daily_returns_matrix(ps[sym], trs[sym], start, end, accounting=acct,
                                                         cost=sleeve_costs[sym][cost_name])
                    mats.append(m_)
                mat = U.combine_matrix(mats)
            for xi, (sl, tp, hold) in enumerate(exits):
                spec = tpl.make_spec(entry, filters, sl, tp, hold, execution=xe, sizing=sz, ladder=lad)
                lh = spec.logic_hash()
                specs[lh] = spec
                sleeves = {sym: fast.to_result(ps[sym], trs[sym][xi], sleeve_costs[sym][cost_name], start, end,
                                               spec.id, lh, accounting=acct) for sym in syms}
                res = U.portfolio_result(sleeves)
                if bh is None or not bh.index.equals(res.daily_returns.index):
                    bh = U.buy_hold_universe({sym: m1s[sym]["close"] for sym in syms}, res.daily_returns.index)
                met = {**M.summarize(res, HALF_LIFE_DAYS, benchmark=bh),
                       **gross_metrics_sleeves([(ps[sym], trs[sym][xi]) for sym in syms], cost, start, end, acct)}
                if not bench_const:
                    bench_const = {k: met.get(k) for k in M.BENCHMARK_CONSTANT_KEYS if k in met}
                pa_cols = {}
                if multi:      # 자산별 결과 (보고 전용)
                    pa = U.per_asset_metrics(sleeves, HALF_LIFE_DAYS)
                    pa_cols = U.per_asset_columns(pa)
                    met["per_asset"] = pa
                if cost_name == tpl.costs["primary"]:
                    ret_cols[lh] = mat[xi]
                rows.append({"logic_hash": lh, "spec_id": spec.id, "filter_set": set_idx,
                             "filters": "+".join(f.block for f in filters) or "-",
                             **{f"entry.{k}": v for k, v in entry.params.items()},
                             **{f"{f.block}.{k}": v for f in filters for k, v in f.params.items()},
                             **exit_columns(tpl, sl, tp, hold), **execution_columns(tpl, xe),
                             **position_columns(tpl, sz, lad),
                             "spec_json": json.dumps(spec.to_dict(), sort_keys=True),
                             **{k: v for k, v in met.items() if k != "per_asset"}, **pa_cols, "cost": cost_name})
                items.append((spec, cost_name, _metrics_record(met)))
        log(f"  signals {ci + 1}/{len(loop)} done ({len(rows)} runs, {time.time() - t0:.1f}s)")
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
        "benchmark": {"kind": "BTC buy & hold (same venue 1m close, no cost)", **bench_const},
        "params": pver, "params_version": pver.get("params_version"), "cost_versions": pver.get("cost_versions"),
        "daily_returns": acct,
    }
    if tpl.gate_profile != "default":       # 관문 프로필 (없는 매니페스트 = default, 이전과 같다)
        manifest["gate_profile"] = tpl.gate_profile
    if tpl.signal_exit:                     # 신호 청산 (없는 템플릿은 매니페스트가 이전과 같다)
        manifest["signal_exit"] = tpl.to_record()["signal_exit"]
    if tpl.execution:                       # 지정가 집행 격자 (시장가 템플릿은 매니페스트가 이전과 같다)
        manifest["execution"] = {"grid": dict(tpl.execution), "n_variants": len(xcombos),
                                 "fill_model": {k: getattr(LIMIT_FILL_DEFAULT, k) for k in
                                                ("name", "through_ticks", "tick", "through_bp", "fill_prob",
                                                 "min_volume", "seed", "tick_from_data")}}
    if tpl.sizing or tpl.ladder:             # 사이징·사다리 격자 (없으면 매니페스트가 이전과 같다)
        from btc_quant.params import max_exposure_cap
        manifest["position"] = {"sizing": dict(tpl.sizing) or None, "ladder": dict(tpl.ladder) or None,
                                "n_variants": len(pcombos), "gridded_mode_params": tpl.gridded_mode_params(),
                                "max_exposure_cap": max_exposure_cap(config_root(env)),
                                "accounting": "sleeve equity; tranche size = max_exposure/N of equity at the first "
                                              "fill; cash earns 0; "
                                              + ("realized daily returns (ENGINE_SPEC)" if acct == "realized" else
                                                 "mark-to-market daily returns at each UTC day close (PLAN D18)"),
                                "fill_model": {k: getattr(LIMIT_FILL_DEFAULT, k) for k in
                                               ("name", "through_ticks", "tick", "through_bp", "fill_prob",
                                                "min_volume", "seed", "tick_from_data")}}
    if not U.is_default(tpl.universe):      # 기본 유니버스는 매니페스트도 이전과 같다
        manifest["universe"] = syms
        manifest["sleeves"] = {"weight": 1.0 / len(syms), "accounting": "equal fixed sleeves; portfolio daily "
                               "return = mean of sleeve daily returns; trades pooled (G1 = sum over assets)",
                               "costs": {sym: {c: m.name for c, m in sleeve_costs[sym].items()} for sym in syms}}
        manifest["data"].update(
            symbol=syms[0], universe=syms, rows_1m=int(sum(len(v) for v in m1s.values())),
            filled_frac=float(np.mean([v["filled"].mean() for v in m1s.values()])),
            by_symbol={sym: {"rows_1m": int(len(m1s[sym])), "filled_frac": float(m1s[sym]["filled"].mean()),
                             "aux_rows_1m": {k: int(len(v)) for k, v in auxs[sym].items()}} for sym in syms})
        if multi:
            manifest["benchmark"]["kind"] = (f"equal-weight {len(syms)}-asset buy & hold ({', '.join(syms)}; same "
                                             "venue 1m close, daily-rebalanced sleeves like the strategy, no cost)")
        elif syms[0] != U.DEFAULT_SYMBOL:
            manifest["benchmark"]["kind"] = f"{syms[0]} buy & hold (same venue 1m close, no cost)"
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
    ctx = dev_aux_context(env, tpl) if tpl.aux and len(tpl.universe) <= 1 else None
    res = screen(tpl, env=env, ctx=ctx, progress=progress)
    if lessons:
        from btc_quant.research.lessons import LessonBook
        book = LessonBook.at(env.root)
        res.lesson_facts = [f.get("id") for f in book.record_facts_from_screen(res)]
        book.render_markdown()
    return res
