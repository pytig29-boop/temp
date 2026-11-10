"""파이프라인 자체 검증 (PLAN §10b-8): 합성 시장 다수로 관문 전체(G0~G6)의 오탐률·검정력을 잰다.

절차
1. 합성 시장 (`gen_market`): 1분 로그수익 = 계절성 × √(시간 GARCH(1,1) 분산/60) × 표준화 t(ν=4)
   - 변동성 군집: 시간 단위 GARCH(1,1), α=0.05, β=0.945 (지속성 0.995, 반감기 ≈ 6일), 목표 연 변동성 55%
     (일간 초과첨도 ≈ 2~11, |일간 수익| 자기상관 ≈ 0.2 — BTC 와 비슷한 수준)
   - 두꺼운 꼬리: t(4) 혁신 (분산 1로 정규화)
   - 일중 계절성: UTC 14시(KST 23시) 정점, 최저/최고 변동 비 ≈ 0.5
   - 심은 엣지(planted): donchian_breakout(lookback=48) 돌파가 확정된 5분봉 다음 12개 5분봉(60분) 동안
     1분 드리프트 = k × (그 시점 1분 σ). 변동성에 비례하므로 국면과 무관하게 같은 '엣지 Sharpe'.
     k=0 이면 귀무 시장(무작위 보행 + 군집·꼬리·계절성).
2. 엣지 세기 보정 (`pilot_edge_map`): 심은 스펙 S* = donchian(48) + SL 1.0% / TP 1.2% / 60분, 무필터를
   긴 시장(여러 seed)에서 primary 비용(coinone_conservative, **현재 버전** — config/costs.yaml 레지스트리를
   research.campaign.get_cost 로 읽음; 캠페인·파이프라인도 같은 레지스트리)으로 실행 → k ↦ 연 Sharpe(일간, 비가중) 의
   단조 보간표. 목표 Sharpe 0.5/1/2/3 에 해당하는 k 를 역보간. "엣지 세기" = **S* 의 참 연 Sharpe
   (비용 후, 전 구간 기대값)**.
3. 시장마다 임시 저장소(config/splits.yaml + data/raw) → `run_campaign` (사전등록 격자) →
   `run_pipeline`(top_k, 귀무 n_null, register=False, reference_check=False: 기준 엔진 대조는 통계와 무관하고
   차등 테스트가 따로 보장). 결과에서
   - 파이프라인 = 군집 대표 선택 → G0~G5 → G6(규칙 b + Val 사용 원장·누적 Holm) → 견고성 묶음(Dev).
     견고성 실패 후보는 validated 가 아니다(라벨 'R(실패 검사)'; 레지스트리 candidate 에 머묾).
   - false positive: 귀무 시장에서 validated(G0~G6 + 견고성 통과) 후보가 하나라도 있음
   - power(exact): 심은 스펙 S* 자체가 validated / power(any): 어떤 후보든 validated
   - dev 생존(G0~G5)도 따로 집계(G6 의 6개월 Val 이 걸러 내는 몫을 분리)
4. 격자 크기별 검정력 감소(시도 비용): 같은 엣지 세기에서 격자 4 / 12 / 48 / 405 스펙.
5. 1분·ATR 시나리오 (`--full-1m`, scenario="1m_atr"): 1분봉 donchian(240) 돌파 후 20분 드리프트, S*_1m =
   1분 결정·ATR 청산(하루 10회 이상 거래) → 고회전 단기 엣지의 검정력.
6. 누적 원장 (`--cumulative`): 시장마다 한 원장·한 family 에서 CUM_SEQUENCE 캠페인 10개(마지막 g12 가 S* 포함)를
   차례로 → 누적 시도(N_family → G5 DSR 의 N)와 Val 사용(k → 누적 Holm 문턱)의 효과. seed 는 full 과 짝.

7. 국면 전환 시장 (`--regime`, PLAN D14 초안): 상승·하락·횡보 국면(수개월, Val 국면 seed%3 순환)에서 귀무·국면 조건부
   엣지(무필터 S*, 필터 S*_f)·하락 음수 엣지(S*_f) 시장마다 캠페인 1회 → 관문 변형(현재 / exposure_aware ON / 완화)별로
   복사한 원장에서 파이프라인 → 오탐률(전체·하락 Val)·검정력 비교. `--g6-stage N`: 귀무 국면 시장에서 Dev 관문을
   건너뛰고 상위 후보를 모두 Val 에 올려 G6 단계 조건부 통과율을 잰다. 리포트 research/reports/calibration_regime_*.md.
8. 롱 온리 기준 2종 (`--rcheck`, `--rcheck-quick`): 같은 국면 시장(+ 추가 귀무 국면 시장, 표준 귀무 시장)에서
   견고성 '국면' 검사 방식(RCHECK_MODES: 비중 0.70/0.80/0.90, 지배 국면 노출-맞춤 B&H 초과 ≥ 0, 정보 전용) ×
   G4 '최근 12개월' 변형(G4_RECENT_VARIANTS: Sharpe > 0 OR 최근 참여일 < 10%/25% 이면서 총수익 ≥ −2%) 의 오탐·검정력.
   G4 변형은 복사한 원장에서 파이프라인을 다시 돌리고(보정 프로세스 안에서만 check_g4 를 감싼 반사실), 국면 검사 방식은
   같은 결과에 사후 적용(robustness.regime_verdict). 상위 후보 전부의 Dev 견고성 조건부 진단 포함.
   리포트 research/reports/calibration_regime_check_*.md.
9. Val 길이·저회전 (기본 꺼짐, `--val-months N [--val-end D]`, `--full-low` / `--full-vlow`, `--markets`, `--strengths`):
   `Layout.with_val_months` 가 val_end 를 고정한 채 Val 을 N 개월로 바꾼다(Dev 는 그만큼 짧아짐). 같은 seed·같은
   데이터 구간이면 Val 6 vs 12 를 **같은 시장끼리 짝지어** 비교할 수 있다(엣지 표는 `--edge-from` 재사용).
   저회전 시나리오(`5m_low` 하루 ≤ 1회, `5m_vlow` ≤ 0.33회): 5분봉 donchian(288)(직전 1일 고가) 돌파 후 1일/3일
   σ 비례 드리프트를 **평균 중립**으로 심는다(`_gen_regime` 의 보정, 국면 드리프트 0) — 긴 보유 드리프트가 B&H 를
   폭주시키고 무작위 진입 귀무(G2)까지 같이 벌게 만드는 것을 막는다. 리포트 research/reports/val_length_design_*.md.

모드
- quick: 짧은 구간(Dev 8개월·Val 2개월), 시장 2개(귀무 1 + 심은 1), 귀무 100개(→ config G2.null_runs 도
  100 으로 낮춤; 테스트용 구조 점검이지 수치 결론용 아님). 수 초~수십 초.
- full: 실제 분할과 같은 길이(Dev 2021-01~2025-10 = 4.83년, Val 6개월), 귀무 1,000개(config 그대로),
  시장 수십 개, 다중 프로세스. 수십 분. 결과는 research/reports/pipeline_calibration.md.

CLI: `uv run python -m btc_quant.research.calibration_study --full --jobs 4 --json full.json`
     (`--full-smooth`, `--full-1m`, `--cumulative --edge-from full.json` 도 같은 방식). 리포트는
     research/reports/pipeline_calibration.md (표는 `tables_markdown`, 해석은 사람이/AI가).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from numba import njit

from btc_quant.backtest import fast
from btc_quant.evaluation import gates as G
from btc_quant.evaluation import metrics as M
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

# ============================================================== 상수
ANNUAL_VOL = 0.55
GARCH_ALPHA, GARCH_BETA = 0.05, 0.945
T_DF = 4.0
SEASON_AMP = 0.35                  # s(h) ∝ 1 + amp·cos(2π(h−14)/24)
WICK = 0.3                         # 1분봉 꼬리 = |N(0, σ·WICK)|
PLANT_LOOKBACK, PLANT_HOLD5 = 48, 12
PLANT_EXIT = (1.0, 1.2, 60)        # S* 의 SL%, TP%, 최대보유(분)
TOP_K = 5

PLANTED_SPEC = StrategySpec("planted", BlockRef("donchian_breakout", {"lookback": PLANT_LOOKBACK}),
                            ExitSpec(*PLANT_EXIT))

# 격자 (모두 S* 를 포함). 키 = 스펙 수.
GRIDS: dict[str, dict] = {
    "g4": {"entry": {"lookback": [24, 48]}, "filters": [[]],
           "exits": {"sl_pct": [1.0], "tp_pct": [1.2], "max_hold_min": [30, 60]}},
    "g12": {"entry": {"lookback": [24, 48, 96]}, "filters": [[]],
            "exits": {"sl_pct": [0.6, 1.0], "tp_pct": [0.8, 1.2], "max_hold_min": [60]}},
    "g48": {"entry": {"lookback": [24, 48, 96, 144]}, "filters": [[]],
            "exits": {"sl_pct": [0.6, 1.0, 1.5], "tp_pct": [0.8, 1.2], "max_hold_min": [30, 60]}},
    "g405": {"entry": {"lookback": [24, 48, 96]},
             "filters": [[], [{"block": "session_kst", "grid": {"session": ["all_but_quiet", "night"]}}],
                         [{"block": "vol_regime", "grid": {"window": [24], "min_pct": [0.33, 0.5]}}]],
             "exits": {"sl_pct": [0.6, 1.0, 1.5], "tp_pct": [0.8, 1.2, 2.0], "max_hold_min": [30, 60, 120]}},
}

# ---- 1분 결정 주기 + ATR 청산 시나리오 (D10/D9): 단기(정보 지평 20분) 고회전 엣지.
# 1분봉 donchian(240) 돌파(직전 4시간 고가)가 확정된 봉 다음 20개 1분봉 동안 σ 비례 드리프트.
# S*_1m = donchian(240) 1m, SL 3·ATR / TP 4·ATR (ATR 14봉) / 20분 — 하루 약 10~13회 거래.
PLANT_1M = {"plant_bar": 1, "plant_lookback": 240, "plant_hold": 20}
PLANTED_SPEC_1M = StrategySpec("planted_1m", BlockRef("donchian_breakout", {"lookback": 240}),
                               ExitSpec(max_hold_min=20, stop_loss_atr=3.0, take_profit_atr=4.0, atr_bars=14),
                               timeframe="1m")
GRIDS_1M: dict[str, dict] = {
    "a12": {"timeframe": "1m", "entry": {"lookback": [120, 240, 480]}, "filters": [[]],
            "exits": {"mode": "atr", "atr_bars": 14, "sl_atr": [2.0, 3.0], "tp_atr": [3.0, 4.0],
                      "max_hold_min": [20]}},
}

# ---- 저회전 시나리오 (기본 꺼짐: --full-low / --full-vlow). 5분봉 donchian(288) 돌파(직전 1일 고가) 확정 뒤
# hold 봉 동안 σ 비례 드리프트, 평균 중립(gen_scenario_market). S*_low = 1일 보유(하루 ≤ 1회 거래),
# S*_vlow = 3일 보유(≤ 0.33회). 격자 = lookback {144, 288, 576} × SL × TP × 보유 1개 (12 스펙, S* 포함).
LOW_SCENARIOS: dict[str, dict] = {
    "5m_low": {"plant_bar": 5, "plant_lookback": 288, "plant_hold": 288},
    "5m_vlow": {"plant_bar": 5, "plant_lookback": 288, "plant_hold": 864},
}
PLANTED_SPEC_LOW = StrategySpec("planted_low", BlockRef("donchian_breakout", {"lookback": 288}),
                                ExitSpec(6.0, 10.0, 1440))
PLANTED_SPEC_VLOW = StrategySpec("planted_vlow", BlockRef("donchian_breakout", {"lookback": 288}),
                                 ExitSpec(10.0, 20.0, 4320))
GRIDS_LOW: dict[str, dict] = {
    "l12": {"entry": {"lookback": [144, 288, 576]}, "filters": [[]],
            "exits": {"sl_pct": [4.0, 6.0], "tp_pct": [6.0, 10.0], "max_hold_min": [1440]}},
    "v12": {"entry": {"lookback": [144, 288, 576]}, "filters": [[]],
            "exits": {"sl_pct": [6.0, 10.0], "tp_pct": [10.0, 20.0], "max_hold_min": [4320]}},
}
LOW_PILOT_KS = (0.0, 0.003, 0.005, 0.007, 0.009, 0.011, 0.014, 0.018)

# ---- 누적 원장 시나리오: 같은 시장·같은 원장(같은 family)에서 캠페인 10개를 차례로. 앞 9개는 S* 를 포함하지
# 않는 그럴듯한 변형(일부는 같은 돌파 신호의 다른 청산·필터 → 심은 시장에서 엣지와 상관), 마지막 = g12(S* 포함).
_EX12 = {"sl_pct": [0.6, 1.0], "tp_pct": [0.8, 1.2], "max_hold_min": [60]}
CUM_GRIDS: dict[str, dict] = {
    "c1": {"entry": {"lookback": [12, 18, 36]}, "filters": [[]], "exits": _EX12},
    "c2": {"entry": {"lookback": [24, 48, 96]}, "filters": [[]],
           "exits": {"sl_pct": [0.6, 1.0], "tp_pct": [0.8, 1.2], "max_hold_min": [30]}},
    "c3": {"entry": {"lookback": [144, 192, 288]}, "filters": [[]], "exits": _EX12},
    "c4": {"entry": {"lookback": [24, 48, 96]}, "filters": [[]],
           "exits": {"sl_pct": [1.5, 2.0], "tp_pct": [2.0, 3.0], "max_hold_min": [120]}},
    "c5": {"entry": {"lookback": [6, 12, 18]}, "filters": [[]],
           "exits": {"sl_pct": [0.3, 0.5], "tp_pct": [0.3, 0.5], "max_hold_min": [30]}},
    "c6": {"entry": {"lookback": [48, 72, 96]}, "filters": [[]],
           "exits": {"sl_pct": [0.6, 1.0], "tp_pct": [0.8, 1.2], "max_hold_min": [240]}},
    "c7": {"entry": {"lookback": [24, 48, 96]},
           "filters": [[{"block": "vol_regime", "grid": {"window": [24], "min_pct": [0.33, 0.5]}}]],
           "exits": {"sl_pct": [1.0], "tp_pct": [1.2], "max_hold_min": [30, 60]}},
    "c8": {"entry": {"lookback": [72, 120, 168]}, "filters": [[]],
           "exits": {"sl_pct": [0.8, 1.2], "tp_pct": [1.0, 1.6], "max_hold_min": [90]}},
    "c9": {"entry": {"lookback": [96, 144, 192]}, "filters": [[]],
           "exits": {"sl_pct": [1.0, 1.5], "tp_pct": [1.2, 2.0], "max_hold_min": [60]}},
}
CUM_SEQUENCE = ("c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "g12")


def grid_def(grid: str) -> dict:
    for d in (GRIDS, GRIDS_1M, CUM_GRIDS, GRIDS_REGIME, GRIDS_LOW):
        if grid in d:
            return d[grid]
    raise KeyError(grid)


def scenario_spec(scenario: str) -> StrategySpec:
    return {"1m_atr": PLANTED_SPEC_1M, "5m_low": PLANTED_SPEC_LOW,
            "5m_vlow": PLANTED_SPEC_VLOW}.get(scenario, PLANTED_SPEC)


def plant_params(scenario: str, plant_lookback: int = PLANT_LOOKBACK) -> dict:
    """gen_market 의 심기 인자 (plant_lookback, plant_bar, plant_hold)."""
    if scenario == "1m_atr":
        return dict(PLANT_1M)
    if scenario in LOW_SCENARIOS:
        return dict(LOW_SCENARIOS[scenario])
    return {"plant_bar": 5, "plant_lookback": int(plant_lookback), "plant_hold": PLANT_HOLD5}


@dataclass(frozen=True)
class Layout:
    """합성 데이터 기간과 분할."""
    data_start: str
    dev_start: str
    val_start: str
    val_end: str
    lockbox_end: str

    def splits(self) -> dict:
        return {"lockbox_id": "lbC", "lockbox_cap": 10, "segments": {
            "dev": {"start": self.dev_start, "end": self.val_start},
            "val": {"start": self.val_start, "end": self.val_end},
            "lockbox": {"start": self.val_end, "end": self.lockbox_end},
            "forward": {"start": self.lockbox_end, "end": None}}}

    @property
    def dev_years(self) -> float:
        return (pd.Timestamp(self.val_start) - pd.Timestamp(self.dev_start)).days / 365.25

    @property
    def val_months(self) -> float:
        return (pd.Timestamp(self.val_end) - pd.Timestamp(self.val_start)).days / (365.25 / 12)

    def with_val_months(self, months: int, val_end: str | None = None) -> "Layout":
        """Val 길이를 months 개월로 바꾼 분할 (기본 꺼짐 — `--val-months`). val_end(기본 = 지금 값)는 고정하고
        val_start 를 앞당긴다 → Dev 가 그만큼 짧아진다(합계 고정). 데이터 구간(data_start..val_end)이 같으면 같은
        seed 의 시장이 같으므로 Val 길이별 결과를 시장끼리 짝지어 비교할 수 있다. Dev 는 최소 90일."""
        ve = pd.Timestamp(val_end or self.val_end)
        vs = ve - pd.DateOffset(months=int(months))
        if int(months) < 1 or vs < pd.Timestamp(self.dev_start) + pd.Timedelta(days=90):
            raise ValueError(f"val_months={months} leaves Dev < 90 days (dev_start {self.dev_start})")
        le = max(pd.Timestamp(self.lockbox_end), ve + pd.Timedelta(days=7))
        f = "%Y-%m-%d"
        return Layout(self.data_start, self.dev_start, vs.strftime(f), ve.strftime(f), le.strftime(f))


FULL_LAYOUT = Layout("2020-12-01", "2021-01-01", "2025-11-01", "2026-05-01", "2026-11-09")   # 실제 분할과 같은 길이
QUICK_LAYOUT = Layout("2023-01-01", "2023-01-01", "2023-09-01", "2023-11-01", "2023-12-01")


@dataclass
class StudyConfig:
    mode: str
    layout: Layout
    strengths: tuple[float, ...]           # 목표 연 Sharpe (0 = 귀무)
    n_null_markets: int                    # 무작위 보행 귀무 (k=0)
    n_planted_markets: int                 # 세기마다
    grid: str                              # 주 실험 격자
    decay_grids: tuple[str, ...]           # 격자 크기 실험 (decay_strength 에서)
    decay_strength: float
    n_decay_markets: int
    n_null_decay: int                      # 가장 큰 격자의 귀무 시장 수 (격자 크기에 따른 오탐)
    n_null: int                            # G2 귀무 run 수
    top_k: int = TOP_K
    pilot_ks: tuple[float, ...] = (0.0, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.05)
    pilot_seeds: int = 4
    pilot_years: float = 6.0
    seed0: int = 1000
    jobs: int = 1
    override_null_runs: bool = False       # quick: config G2.null_runs 를 n_null 로 낮춤
    n_breakeven_markets: int = 0           # '손익분기' 귀무: S* 의 참 비용후 Sharpe = 0 (k 역보간)
    plant_lookback: int = PLANT_LOOKBACK   # 48 = 칼날 엣지(S* 만), 24 = 완만한 엣지(인접 lookback 도 공유)
    scenario: str = "5m"                   # "5m" (5분 돌파·고정 % 청산) | "1m_atr" (1분 돌파·ATR 청산, 단기 엣지)
    # 누적 원장: 시장마다 한 원장에서 CUM_SEQUENCE 캠페인을 차례로. {strength: n_markets}; seed 는 주 실험과 짝.
    cumulative: dict | None = None
    cum_sequence: tuple[str, ...] = CUM_SEQUENCE

    def to_record(self) -> dict:
        d = asdict(self)
        d["layout"] = asdict(self.layout)
        return d


def quick_config(jobs: int = 1) -> StudyConfig:
    return StudyConfig("quick", QUICK_LAYOUT, strengths=(0.0, 3.0), n_null_markets=1, n_planted_markets=1,
                       grid="g4", decay_grids=(), decay_strength=3.0, n_decay_markets=0, n_null_decay=0,
                       n_null=100, top_k=2, pilot_ks=(0.0, 0.04, 0.1), pilot_seeds=1, pilot_years=1.0,
                       jobs=jobs, override_null_runs=True)


def smooth_config(jobs: int = 4) -> StudyConfig:
    """완만한 엣지 변형: 드리프트를 lookback 24 돌파 전체에 심음 → S*(48) 와 인접 lookback 이 엣지를 공유
    (G3 고원이 '칼날' 설계 때문에 떨어지는지 분리). 귀무·격자 실험은 full 과 같으므로 생략."""
    return StudyConfig("full-smooth", FULL_LAYOUT, strengths=(0.5, 1.0, 2.0, 3.0), n_null_markets=0,
                       n_planted_markets=8, grid="g12", decay_grids=(), decay_strength=2.0, n_decay_markets=0,
                       n_null_decay=0, n_null=1000, jobs=jobs, plant_lookback=24, seed0=5000)


def full_config(jobs: int = 4) -> StudyConfig:
    return StudyConfig("full", FULL_LAYOUT, strengths=(0.0, 0.5, 1.0, 2.0, 3.0), n_null_markets=20,
                       n_planted_markets=10, grid="g12", decay_grids=("g4", "g48", "g405"),
                       decay_strength=2.0, n_decay_markets=10, n_null_decay=10, n_null=1000, jobs=jobs,
                       n_breakeven_markets=10)


def m1_config(jobs: int = 4) -> StudyConfig:
    """1분·ATR 고회전 엣지: S*_1m (정보 지평 20분) 의 참 비용후 Sharpe 별 검정력 + 귀무."""
    return StudyConfig("full-1m-atr", FULL_LAYOUT, strengths=(0.0, 2.0, 3.0, 5.0, 8.0, 12.0), n_null_markets=6,
                       n_planted_markets=6, grid="a12", decay_grids=(), decay_strength=3.0, n_decay_markets=0,
                       n_null_decay=0, n_null=1000, jobs=jobs, scenario="1m_atr", seed0=7000,
                       pilot_ks=(0.0, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.1, 0.12), pilot_seeds=3,
                       pilot_years=3.0)


def low_config(jobs: int = 4, scenario: str = "5m_low") -> StudyConfig:
    """저회전 엣지(기본 꺼짐, --full-low / --full-vlow): S*_low(1일 보유) 또는 S*_vlow(3일 보유)의 참 비용후
    Sharpe 별 검정력 + 귀무. Val 길이 비교(--val-months)와 함께 쓴다."""
    if scenario not in LOW_SCENARIOS:
        raise ValueError(scenario)
    return StudyConfig(f"full-{scenario.split('_')[1]}", FULL_LAYOUT, strengths=(0.0, 1.0, 2.0, 3.0),
                       n_null_markets=8, n_planted_markets=8, grid="l12" if scenario == "5m_low" else "v12",
                       decay_grids=(), decay_strength=2.0, n_decay_markets=0, n_null_decay=0, n_null=1000,
                       jobs=jobs, scenario=scenario, seed0=13_000 if scenario == "5m_low" else 15_000,
                       pilot_ks=LOW_PILOT_KS, pilot_seeds=4, pilot_years=6.0)


def apply_overrides(cfg: StudyConfig, val_months: int = 0, val_end: str | None = None, markets: int = 0,
                    strengths: tuple[float, ...] | None = None) -> StudyConfig:
    """CLI 의 기본 꺼짐 선택지: Val 길이(val_months > 0), 세기·시장 수 덮어쓰기(markets > 0 이면 격자 크기·손익분기
    실험은 끔). 0/None = 그대로."""
    if val_end and not val_months:
        raise ValueError("--val-end needs --val-months")
    if val_months:
        cfg.layout = cfg.layout.with_val_months(val_months, val_end)
        cfg.mode = f"{cfg.mode}-val{int(val_months)}m"
    if strengths:
        cfg.strengths = tuple(float(x) for x in strengths)
    if markets:
        cfg.n_null_markets = cfg.n_planted_markets = int(markets)
        cfg.decay_grids, cfg.n_decay_markets, cfg.n_null_decay, cfg.n_breakeven_markets = (), 0, 0, 0
    return cfg


def cumulative_config(jobs: int = 4) -> StudyConfig:
    """누적 원장: 귀무 10 + 목표 Sharpe 2 10 + 3 6 시장, 시장마다 캠페인 10개(CUM_SEQUENCE) 를 한 원장에서.
    seed0·strength 순서가 full_config 와 같아서 마지막 캠페인(g12) 결과를 새 원장 결과와 짝지어 비교할 수 있다."""
    return StudyConfig("full-cumulative", FULL_LAYOUT, strengths=(0.0, 0.5, 1.0, 2.0, 3.0), n_null_markets=20,
                       n_planted_markets=10, grid="g12", decay_grids=(), decay_strength=2.0, n_decay_markets=0,
                       n_null_decay=0, n_null=1000, jobs=jobs, cumulative={0.0: 10, 2.0: 10, 3.0: 6})


# ============================================================== 합성 시장
def season_profile(amp: float = SEASON_AMP) -> np.ndarray:
    h = np.arange(24)
    s = 1 + amp * np.cos(2 * np.pi * (h - 14) / 24)
    return s / np.sqrt(np.mean(s ** 2))


@njit(cache=True)
def _gen(n1, minute0, seed, k_edge, lookback, hold5, h_target, alpha, beta, season, df_t, wick, bar_min):
    """bar_min = 돌파를 판정하는 봉 길이(분, 5 또는 1). lookback·hold5 는 그 봉 수."""
    np.random.seed(seed)
    o = np.empty(n1)
    hi = np.empty(n1)
    lo = np.empty(n1)
    c = np.empty(n1)
    n5 = n1 // bar_min + 1
    hi5 = np.empty(n5)
    omega = h_target * (1.0 - alpha - beta)
    h = h_target
    t_scale = np.sqrt((df_t - 2.0) / df_t)
    price = 50_000_000.0
    r_hour = 0.0
    drift_left = 0
    bar_hi = -1.0
    for k in range(n1):
        m = minute0 + k
        hod = (m // 60) % 24
        if k > 0 and m % 60 == 0:      # 시간 경계: GARCH 갱신
            h = omega + alpha * r_hour * r_hour + beta * h
            h = min(h, 25.0 * h_target)
            r_hour = 0.0
        sig = np.sqrt(h / 60.0) * season[hod]
        z = np.random.randn() / np.sqrt(np.random.chisquare(df_t) / df_t) * t_scale
        eps = sig * z
        d = k_edge * sig if drift_left > 0 else 0.0
        r_hour += eps
        op = price
        price = price * np.exp(d + eps)
        o[k] = op
        c[k] = price
        hi[k] = max(op, price) * (1.0 + abs(np.random.randn()) * sig * wick)
        lo[k] = min(op, price) * (1.0 - abs(np.random.randn()) * sig * wick)
        bar_hi = max(bar_hi, hi[k])
        if m % bar_min == bar_min - 1:  # 결정 봉 종료
            t = k // bar_min
            hi5[t] = bar_hi
            bar_hi = -1.0
            if drift_left > 0:
                drift_left -= 1
            if k_edge != 0.0 and t >= lookback and drift_left == 0:
                mx = -1.0
                for q in range(t - lookback, t):
                    mx = max(mx, hi5[q])
                if price > mx:          # 돌파 확정 → 다음 hold5 개 5분봉 동안 드리프트
                    drift_left = hold5
    return o, hi, lo, c


def gen_market(start: str, end: str, seed: int, k_edge: float = 0.0,
               plant_lookback: int = PLANT_LOOKBACK, plant_bar: int = 5,
               plant_hold: int = PLANT_HOLD5) -> pd.DataFrame:
    """[start, end) 1분봉 합성 시장 (start 는 자정 정렬 권장). plant_lookback = 드리프트를 심는 돌파 lookback
    (48 = S* 에만 있는 '칼날' 엣지, 24 = lookback 24 돌파 전체(48·96 돌파 포함)에 있는 '완만한' 엣지).
    plant_bar = 돌파 판정 봉 길이(분; 5 = 5분봉, 1 = 1분봉 단기 엣지), plant_hold = 드리프트 지속 봉 수."""
    idx = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left", name="ts")
    minute0 = int(idx[0].value // 60_000_000_000)
    if minute0 % 5:
        raise ValueError("start must be aligned to 5 minutes")
    h_target = ANNUAL_VOL ** 2 / (365 * 24)
    if plant_bar not in (1, 5):
        raise ValueError("plant_bar must be 1 or 5")
    o, h, lo, c = _gen(len(idx), minute0, int(seed), float(k_edge), int(plant_lookback), int(plant_hold), h_target,
                       GARCH_ALPHA, GARCH_BETA, season_profile(), T_DF, WICK, int(plant_bar))
    rng = np.random.default_rng(seed)
    vol = rng.lognormal(0.0, 0.5, len(idx))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": vol,
                         "quote_volume": vol * c}, index=idx)


# ============================================================== 국면 전환 시장 (PLAN D14 초안 — 노출 인식 관문 보정)
# 롱 온리 전략의 국면 의존을 재현한다: 상승·하락·횡보 국면이 몇 달씩 이어지고(실제 BTC 처럼), 국면마다
# (1) 시장 자체의 드리프트(B&H 수익)가 다르고 (2) 심은 돌파 엣지의 세기가 다르다.
#   null          : 엣지 없음 (국면 드리프트만). 5분·60분 보유 전략에 국면 드리프트는 비용보다 훨씬 작다
#                   (연 ±0.9 로그 드리프트 = 60분에 약 ±1bp, 왕복 비용 4bp) → 귀무.
#   심은 엣지는 평균 중립으로 넣는다(돌파 밖 분에 반대 드리프트, `_gen_regime`): B&H 의 국면 드리프트 ≈ REGIME_DRIFT.
#   (표준 시장 `_gen` 은 보정 없음 — 상승 국면과 돌파 드리프트가 겹치면 B&H 가 비현실적으로 폭주하므로 국면 시장만 보정)
#   regime_cond   : (ii) 엣지가 상승 국면에서만 양(+), 횡보 절반, 하락 0 (평탄). S* = 무필터 돌파.
#   regime_cond_f : (ii) 와 같은 시장, 심은 스펙만 S*_f (하락 국면 엣지 0 — 새는 거래는 비용만 잃음).
#   regime_filter : (iii) 엣지가 상승 +, 횡보 절반, 하락 음(−) — 국면 필터가 이용할 수 있는 구조.
#                   S*_f = 같은 돌파 + trend_regime_1h(장기 EMA) 필터 → 하락 국면에서 대체로 쉼
#                   (60일 EMA. 국면 드리프트가 변동성보다 작아 필터는 새고 늦다: 시험 시장에서 하락 국면 시장 참여일
#                   ≈ 10~30%, 상승 국면 ≈ 70~85% — 실제 추세 필터처럼 불완전).
# Val(6개월)의 국면은 시장 seed 로 정한다: val_regime 을 주면 그 국면, 아니면 (seed % 3) 순환 →
# 시장 3개 중 1개는 하락 Val (bear Val 에서의 오탐을 따로 잴 수 있게).
REGIMES = ("bull", "bear", "side")
REGIME_DRIFT = {"bull": 0.9, "bear": -0.9, "side": 0.0}            # 연 로그 드리프트 (B&H, 엣지 드리프트 별도)
REGIME_DAYS = {"bull": (180, 540), "bear": (120, 360), "side": (60, 240)}   # 국면 길이(일) 균등 분포
REGIME_NEXT = {"bull": (("bear", 0.6), ("side", 0.4)), "bear": (("bull", 0.5), ("side", 0.5)),
               "side": (("bull", 0.5), ("bear", 0.5))}
REGIME_EDGE = {"null": {"bull": 0.0, "bear": 0.0, "side": 0.0},
               "regime_cond": {"bull": 1.0, "bear": 0.0, "side": 0.5},
               "regime_filter": {"bull": 1.0, "bear": -0.5, "side": 0.5},
               "regime_cond_f": {"bull": 1.0, "bear": 0.0, "side": 0.5}}
FILTERED_KINDS = ("regime_filter", "regime_cond_f")                 # 심은 스펙 = S*_f (국면 필터 포함)
REGIME_FILTER_SPAN = 1440                                           # trend_regime_1h span(시간) = 60일 EMA
REGIME_PLANT_LOOKBACK = 24                                          # 완만한 엣지: lookback 24 돌파 전체
REGIME_FILTER = BlockRef("trend_regime_1h", {"span": REGIME_FILTER_SPAN})
PLANTED_SPEC_REGIME = StrategySpec("planted_rf", BlockRef("donchian_breakout", {"lookback": PLANT_LOOKBACK}),
                                   ExitSpec(*PLANT_EXIT), (REGIME_FILTER,))
GRIDS_REGIME: dict[str, dict] = {
    # 베타 탐침(--rcheck-beta): 저회전·장기 보유 롱 → 귀무 시장에서도 상승 국면 베타로 Dev 손익이 양수일 수 있는 후보
    "rb8": {"entry": {"lookback": [24, 96]},
            "filters": [[], [{"block": "trend_regime_1h", "grid": {"span": [REGIME_FILTER_SPAN]}}]],
            "exits": {"sl_pct": [5.0], "tp_pct": [10.0], "max_hold_min": [1440, 4320]}},
    "r12": {"entry": {"lookback": [24, 48, 96]},
            "filters": [[], [{"block": "trend_regime_1h", "grid": {"span": [REGIME_FILTER_SPAN]}}]],
            "exits": {"sl_pct": [0.6, 1.0], "tp_pct": [1.2], "max_hold_min": [60]}},
}


def regime_schedule(start: str, val_start: str, end: str, seed: int, val_regime: str | None = None
                    ) -> pd.Series:
    """일자별 국면 라벨 [start, end). Dev 는 마르코프 국면 전환(REGIME_DAYS·REGIME_NEXT), Val 이후는 val_regime
    (없으면 REGIMES[seed % 3])."""
    rng = np.random.default_rng(int(seed) + 77_000)
    days = pd.date_range(start, end, freq="D", tz="UTC", inclusive="left")
    vs = pd.Timestamp(val_start, tz="UTC")
    n_dev = int((days < vs).sum())
    lab = []
    cur = REGIMES[int(rng.integers(0, 3))]
    while len(lab) < n_dev:
        lo, hi = REGIME_DAYS[cur]
        lab += [cur] * int(rng.integers(lo, hi + 1))
        nxt, pr = zip(*REGIME_NEXT[cur])
        cur = str(rng.choice(nxt, p=pr))
    vr = val_regime or REGIMES[int(seed) % 3]
    lab = lab[:n_dev] + [vr] * (len(days) - n_dev)
    return pd.Series(lab, index=days, dtype=object)


@njit(cache=True)
def _gen_regime(n1, minute0, seed, k_edge, lookback, hold5, h_target, alpha, beta, season, df_t, wick, bar_min,
                mu_day, edge_day):
    """_gen + 일자별 드리프트(mu_day: 1분 로그 드리프트)와 엣지 배수(edge_day). 같은 난수 순서.
    엣지 보정(평균 중립): 돌파 후 드리프트가 시장 평균 드리프트를 바꾸지 않도록, 드리프트가 없는 분에 −k·배수·σ·f/(1−f)
    를 더한다(f = 드리프트 중인 분의 비율, 1주 EMA). 그래서 B&H 의 국면 드리프트 ≈ mu_day (상승장에서 돌파가 잦아
    가격이 폭주하는 되먹임을 막음 — 국면 연구는 B&H 대비 판단을 재므로 B&H 가 현실적이어야 한다)."""
    np.random.seed(seed)
    o = np.empty(n1)
    hi = np.empty(n1)
    lo = np.empty(n1)
    c = np.empty(n1)
    n5 = n1 // bar_min + 1
    hi5 = np.empty(n5)
    omega = h_target * (1.0 - alpha - beta)
    h = h_target
    t_scale = np.sqrt((df_t - 2.0) / df_t)
    price = 50_000_000.0
    r_hour = 0.0
    drift_left = 0
    bar_hi = -1.0
    f_ema = 0.25
    for k in range(n1):
        m = minute0 + k
        hod = (m // 60) % 24
        day = k // 1440
        if k > 0 and m % 60 == 0:
            h = omega + alpha * r_hour * r_hour + beta * h
            h = min(h, 25.0 * h_target)
            r_hour = 0.0
        sig = np.sqrt(h / 60.0) * season[hod]
        z = np.random.randn() / np.sqrt(np.random.chisquare(df_t) / df_t) * t_scale
        eps = sig * z
        d = mu_day[day]
        if drift_left > 0:
            d += k_edge * edge_day[day] * sig
            f_ema += (1.0 - f_ema) / 10080.0
        else:
            f = min(f_ema, 0.9)
            d -= k_edge * edge_day[day] * sig * f / (1.0 - f)
            f_ema += (0.0 - f_ema) / 10080.0
        r_hour += eps
        op = price
        price = price * np.exp(d + eps)
        o[k] = op
        c[k] = price
        hi[k] = max(op, price) * (1.0 + abs(np.random.randn()) * sig * wick)
        lo[k] = min(op, price) * (1.0 - abs(np.random.randn()) * sig * wick)
        bar_hi = max(bar_hi, hi[k])
        if m % bar_min == bar_min - 1:
            t = k // bar_min
            hi5[t] = bar_hi
            bar_hi = -1.0
            if drift_left > 0:
                drift_left -= 1
            if k_edge != 0.0 and t >= lookback and drift_left == 0:
                mx = -1.0
                for q in range(t - lookback, t):
                    mx = max(mx, hi5[q])
                if price > mx:
                    drift_left = hold5
    return o, hi, lo, c


def gen_regime_market(start: str, val_start: str, end: str, seed: int, k_edge: float = 0.0,
                      edge_kind: str = "null", val_regime: str | None = None,
                      plant_lookback: int = REGIME_PLANT_LOOKBACK) -> tuple[pd.DataFrame, pd.Series]:
    """국면 전환 1분봉 합성 시장 [start, end) + 일자별 국면 라벨. start 는 자정 정렬."""
    idx = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left", name="ts")
    minute0 = int(idx[0].value // 60_000_000_000)
    if minute0 % 1440:
        raise ValueError("start must be midnight-aligned")
    sched = regime_schedule(start, val_start, end, seed, val_regime)
    mu = np.array([REGIME_DRIFT[r] / (365.0 * 1440.0) for r in sched], dtype=float)
    em = np.array([REGIME_EDGE[edge_kind][r] for r in sched], dtype=float)
    h_target = ANNUAL_VOL ** 2 / (365 * 24)
    o, h, lo, c = _gen_regime(len(idx), minute0, int(seed), float(k_edge), int(plant_lookback), PLANT_HOLD5,
                              h_target, GARCH_ALPHA, GARCH_BETA, season_profile(), T_DF, WICK, 5, mu, em)
    rng = np.random.default_rng(seed)
    vol = rng.lognormal(0.0, 0.5, len(idx))
    return (pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": vol, "quote_volume": vol * c},
                         index=idx), sched)


def gen_scenario_market(start: str, end: str, seed: int, k_edge: float, scenario: str = "5m",
                        plant_lookback: int = PLANT_LOOKBACK) -> pd.DataFrame:
    """시나리오별 1분봉 시장. 기존 시나리오(5m, 1m_atr) = gen_market 그대로. 저회전(LOW_SCENARIOS) = 같은 GARCH·꼬리·
    계절성에 긴 보유 드리프트를 평균 중립으로 심은 시장(`_gen_regime`, 국면 드리프트 0·엣지 배수 1). start 는 자정 정렬."""
    pp = plant_params(scenario, plant_lookback)
    if scenario not in LOW_SCENARIOS:
        return gen_market(start, end, seed, k_edge, **pp)
    idx = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left", name="ts")
    minute0 = int(idx[0].value // 60_000_000_000)
    if minute0 % 1440:
        raise ValueError("start must be midnight-aligned")
    n_days = len(idx) // 1440 + 2
    h_target = ANNUAL_VOL ** 2 / (365 * 24)
    o, h, lo, c = _gen_regime(len(idx), minute0, int(seed), float(k_edge), int(pp["plant_lookback"]),
                              int(pp["plant_hold"]), h_target, GARCH_ALPHA, GARCH_BETA, season_profile(), T_DF, WICK,
                              int(pp["plant_bar"]), np.zeros(n_days), np.ones(n_days))
    rng = np.random.default_rng(seed)
    vol = rng.lognormal(0.0, 0.5, len(idx))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": vol, "quote_volume": vol * c},
                        index=idx)


def _engine_m1(df: pd.DataFrame) -> pd.DataFrame:
    from .campaign import prepare_m1
    return prepare_m1(df, "coinone")      # 합성 시장 = 파이프라인에서 coinone 으로 저장 (make_root) → 같은 준비


# ============================================================== 엣지 세기 보정
PRIMARY_COST = "coinone_conservative"     # = campaign.DEFAULT_COSTS["primary"] (gates.yaml primary_metric.cost)


def primary_cost():
    """현재 버전의 primary 비용 모델 (config/costs.yaml 레지스트리, research.campaign.get_cost)."""
    from .campaign import get_cost
    return get_cost(PRIMARY_COST)


def planted_sharpe(m1: pd.DataFrame, start, end, spec: StrategySpec = PLANTED_SPEC) -> float:
    res = fast.run_fast(spec, m1, primary_cost(), start, end)
    return M.sharpe(res.daily_returns)


def planted_stats(m1: pd.DataFrame, start, end, spec: StrategySpec = PLANTED_SPEC) -> dict:
    res = fast.run_fast(spec, m1, primary_cost(), start, end)
    n_days = max(1.0, (pd.Timestamp(end) - pd.Timestamp(start)).days)
    return {"sharpe": M.sharpe(res.daily_returns), "trades_per_day": len(res.trades) / n_days}


def _pilot_one(args) -> tuple[float, int, float, float]:
    k, seed, years, plb, scenario = args
    start = pd.Timestamp("2010-01-01", tz="UTC")
    end = start + pd.Timedelta(days=round(365.25 * years))
    m1 = _engine_m1(gen_scenario_market(str(start), str(end), seed, k, scenario, plb))
    st = planted_stats(m1, start + pd.Timedelta(days=3), end, scenario_spec(scenario))
    return k, seed, st["sharpe"], st["trades_per_day"]


def pilot_edge_map(cfg: StudyConfig, pool=None) -> dict:
    """k → S* 연 Sharpe (seed 평균). 단조 보정(누적 최대) 후 목표 Sharpe 를 k 로 역보간."""
    tasks = [(k, cfg.seed0 + 50_000 + s, cfg.pilot_years, cfg.plant_lookback, cfg.scenario)
             for k in cfg.pilot_ks for s in range(cfg.pilot_seeds)]
    rows = list(pool.map(_pilot_one, tasks)) if pool else [_pilot_one(t) for t in tasks]
    ks = np.array(cfg.pilot_ks, float)
    sr = np.array([np.mean([r[2] for r in rows if r[0] == k]) for k in ks])
    sr_mono = np.maximum.accumulate(sr)
    k_for = {}
    for s in cfg.strengths:
        if s <= 0:
            k_for[s] = 0.0
        elif s > sr_mono[-1]:
            # 외삽 (마지막 두 점 선형)
            slope = (sr_mono[-1] - sr_mono[-2]) / (ks[-1] - ks[-2])
            k_for[s] = float(ks[-1] + (s - sr_mono[-1]) / slope)
        else:
            k_for[s] = float(np.interp(s, sr_mono, ks))
    k_be = float(np.interp(0.0, sr_mono, ks)) if sr_mono[0] < 0 < sr_mono[-1] else 0.0
    tpd = [float(np.mean([r[3] for r in rows if r[0] == k])) for k in ks]
    return {"ks": ks.tolist(), "sharpe_mean": sr.tolist(), "trades_per_day": tpd, "per_seed": [list(r) for r in rows],
            "pilot_years": cfg.pilot_years, "pilot_seeds": cfg.pilot_seeds, "k_for_strength": k_for,
            "k_breakeven": k_be, "cost_model": asdict(primary_cost())}


# ============================================================== 시장 1개 실행
def template_for(grid: str, name: str, family: str | None = None) -> dict:
    g = grid_def(grid)
    d = {"name": name,
         "hypothesis": {"text": "calibration study: donchian breakout continuation (synthetic)",
                        "rationale": "pipeline self-validation (PLAN 10b-8)",
                        "success_criteria": "planted spec validated; nothing validated on null markets"},
         "data": {"exchange": "coinone", "symbol": "BTC-KRW", "timeframe": "1m", "aux": []},
         "entry": {"block": "donchian_breakout", "grid": g["entry"]},
         "filters": g["filters"], "exits": g["exits"], "budget": 10_000, "family": family or f"calib_{grid}"}
    if "timeframe" in g:
        d["timeframe"] = g["timeframe"]
    return d


def make_root(tmp: Path, layout: Layout, seed: int, k_edge: float, plant_lookback: int = PLANT_LOOKBACK,
              scenario: str = "5m") -> Path:
    from btc_quant.data.store import write_ohlcv
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "splits.yaml").write_text(yaml.safe_dump(layout.splits()))
    df = gen_scenario_market(layout.data_start, layout.val_end, seed, k_edge, scenario, plant_lookback)
    write_ohlcv(df, "coinone", "BTC-KRW", "1m", root=tmp / "data" / "raw")
    return tmp


G6_FLAGS = (("ok_sharpe", "sharpe"), ("ok_mdd", "mdd"), ("ok_stress_sharpe", "stress_sr"),
            ("ok_stress_mdd", "stress_mdd"), ("ok_mc_mdd", "mc"))


def candidate_outcome(c: dict) -> str:
    """후보 하나의 결과 라벨: validated | R(실패 검사…) (G0~G6 통과, 견고성 묶음 실패 → candidate 에 머묾)
    | G0..G5 (Dev 첫 실패) | G6(sharpe/mdd/stress_sr/mc/holm…)."""
    st = str(c.get("status", ""))
    if st.startswith("validated"):
        if "robustness FAIL" in st:
            failed = (c.get("robustness") or {}).get("failed") or []
            return f"R({'+'.join(failed) or '?'})"
        return "validated"
    if not c.get("dev_passed"):
        return str(c.get("first_failed"))
    g = c.get("gates", {}).get("G6") or {}
    flags = G6_FLAGS
    if g.get("stress_mode") == "off":                 # R1: stress Sharpe 는 표시만 → 라벨에서 제외
        flags = tuple(f for f in flags if f[0] != "ok_stress_sharpe")
    if g.get("drawdown_mode") == "size":              # R1: 낙폭 두 조건 대신 크기 하한 하나
        flags = tuple(("ok_position_scale", "size") if f[0] == "ok_mdd" else f for f in flags if f[0] != "ok_mc_mdd")
    why = [n for k, n in flags if g.get(k) is False]
    ev = g.get("val_evidence_passed", g.get("val_holm_passed"))
    if ev is False:
        why.append("holm")                            # Val 증거 실패 (방식 무관하게 같은 라벨)
    return f"G6({'+'.join(why) or '?'})"


def _gate_cfg(task: dict) -> dict:
    cfg = G.load_gate_config()
    if task.get("override_null_runs") or task.get("gate_overrides"):
        cfg = json.loads(json.dumps(cfg))
    if task.get("override_null_runs"):
        cfg["G2"]["null_runs"] = task["n_null"]
    for dotted, val in (task.get("gate_overrides") or {}).items():   # params.criteria: 한 기준의 후보 값
        *parents, leaf = dotted.split(".")
        d = cfg
        for k in parents:
            d = d[k]
        d[leaf] = val
    return cfg


def _campaign_record(env, task: dict, grid: str, family: str | None = None) -> dict:
    """캠페인(grid) + 파이프라인 1회 → 결과 레코드 (S* 가 이 격자에 없으면 star_* 는 None)."""
    from .campaign import CampaignTemplate, run_campaign

    tpl = CampaignTemplate.from_dict(template_for(grid, f"calib_{grid}", family))
    cr = run_campaign(tpl, env=env)
    return _evaluate_record(env, cr, task, grid)


def _star_spec(task: dict) -> StrategySpec:
    """이 시장의 심은 스펙: 국면 필터 시장(star_filters)이면 S*_f(돌파 + trend_regime_1h), 아니면 시나리오 S*."""
    return PLANTED_SPEC_REGIME if task.get("star_filters") else scenario_spec(task.get("scenario", "5m"))


def _evaluate_record(env, cr, task: dict, grid: str) -> dict:
    """저장된 캠페인 결과 cr 에 파이프라인 1회(task 의 관문 설정 = _gate_cfg) → 결과 레코드."""
    from .pipeline import run_pipeline

    t0 = time.time()
    tpl = cr.template
    out = run_pipeline(cr, env=env, top_k=task["top_k"], n_null=task["n_null"], gate_cfg=_gate_cfg(task),
                       register=False, reference_check=False)
    sspec = _star_spec(task)
    ex = sspec.exit
    star = tpl.make_spec(sspec.entry, tuple(sspec.filters), ex.sl, ex.tp, ex.max_hold_min)
    lh_star = star.logic_hash()
    ranked = cr.ranked()
    hit = ranked.index[ranked["logic_hash"] == lh_star]
    has_star = len(hit) > 0
    rank_star = int(hit[0]) + 1 if has_star else None
    prim = cr.primary()
    cands = out["candidates"]
    labels = [candidate_outcome(c) for c in cands]
    validated = [c for c, lab in zip(cands, labels) if lab == "validated"]
    g6_pass = [c for c in cands if str(c.get("status", "")).startswith("validated")]
    rep, rep_corr = None, float("nan")
    if has_star:
        # S* 의 대표: S* 자체가 후보면 그것, 아니면 S* 와 일간 수익 상관이 가장 높은 후보(ρ ≥ 0.5 = 같은 군집 수준)
        x = cr.returns[lh_star].to_numpy(float)
        for c, lab in zip(cands, labels):
            if c["logic_hash"] == lh_star:
                rep, rep_corr = (c, lab), 1.0
                break
        if rep is None and cands and x.std() > 0:
            cors = [float(np.corrcoef(x, cr.returns[c["logic_hash"]].to_numpy(float))[0, 1]) for c in cands]
            j = int(np.nanargmax(cors))
            if cors[j] >= 0.5:
                rep, rep_corr = (cands[j], labels[j]), cors[j]
    g6 = rep[0]["gates"].get("G6") if rep else None
    rob = rep[0].get("robustness") if rep else None
    st = out["campaign_stats"]
    rec = {
        "grid": grid, "n_specs": len(prim), "n_candidates": len(cands), "n_validated": len(validated),
        "n_g6_passed": len(g6_pass), "n_robustness_fail": int(out.get("n_robustness_fail") or 0),
        "n_dev_survivors": out["n_dev_survivors"],
        "any_validated": bool(validated), "any_g6_passed": bool(g6_pass),
        "any_dev_survivor": out["n_dev_survivors"] > 0,
        "has_star": has_star, "star_rank": rank_star,
        "star_sharpe_dev": float(prim.at[lh_star, "sharpe"]) if has_star else None,
        "star_sharpe_w_dev": float(prim.at[lh_star, "sharpe_w"]) if has_star else None,
        "star_trades_per_day": float(prim.at[lh_star, "trades_per_day"]) if has_star else None,
        "star_is_candidate": bool(rep and rep_corr == 1.0),
        "star_rep_corr": rep_corr,
        "star_dev_passed": bool(rep and rep[0]["dev_passed"]),
        "star_g6_passed": bool(rep and str(rep[0].get("status", "")).startswith("validated")),
        "star_validated": bool(rep and rep[1] == "validated"),
        "star_outcome": ("no_star" if not has_star else "not_selected" if rep is None else rep[1]),
        "star_val_sharpe": None if not g6 else g6.get("sharpe"),
        "star_val_mdd": None if not g6 else g6.get("max_dd"),
        "star_val_stress_sharpe": None if not g6 else g6.get("stress_sharpe"),
        "star_val_stress_mdd": None if not g6 else g6.get("stress_mdd"),
        "star_val_mc_mdd95": None if not g6 else g6.get("mc_mdd95"),
        "star_val_p": None if not g6 else g6.get("val_p_value"),
        "star_val_k": None if not g6 else g6.get("val_k"),
        "star_val_holm_threshold": None if not g6 else (g6.get("val_budget") or {}).get("threshold"),
        "star_robustness_failed": None if not rob else list(rob.get("failed") or []),
        "star_robustness_skipped": None if not rob else list(rob.get("skipped") or []),
        "star_g5": None if not rep else {k: rep[0]["gates"]["G5"].get(k) for k in ("dsr", "pbo", "n_trials")
                                          if k in rep[0]["gates"]["G5"]},
        "outcomes": pd.Series(labels, dtype=object).value_counts().to_dict() if labels else {},
        "robustness_failed_checks": [f for c in cands for f in ((c.get("robustness") or {}).get("failed") or [])],
        "validated_specs": [{"entry": c["spec"]["entry"], "filters": c["spec"]["filters"],
                             "exit": {k: c["spec"]["exit"].get(k) for k in ("stop_loss_pct", "take_profit_pct",
                                                                            "stop_loss_atr", "take_profit_atr",
                                                                            "max_hold_min")},
                             "trades_per_day": c["metrics"].get("trades_per_day")} for c in validated],
        "k_eff": st["k_eff_clusters"], "pbo": st.get("pbo"), "n_family_trials": st.get("n_family_trials"),
        "n_trials_dsr": st.get("n_trials_dsr"),
        "val_used": (out.get("val_status") or {}).get("used"),
        "runtime_sec": round(time.time() - t0, 1),
    }
    if task.get("regime"):         # 노출 인식 변형 진단 (S* 대표 후보의 G4/G6 shadow·경로)
        g4 = rep[0]["gates"].get("G4") if rep else None
        ea4 = (g4 or {}).get("exposure_aware") or {}
        ea6 = (g6 or {}).get("exposure_aware") or {}
        bv = (g6 or {}).get("benchmark_val") or {}
        rec.update(
            star_first_failed=None if not rep else rep[0].get("first_failed"),
            star_g4_base=None if not g4 else bool(g4.get("ok_rolling") and g4.get("ok_recent")),
            star_g4_variant=ea4.get("passed"), star_g4_via=ea4.get("via"),
            star_g4_n_neutral=ea4.get("rolling_n_neutral"), star_g4_n_active=ea4.get("rolling_n_active"),
            star_dev_exposure=None if not rep else (rep[0].get("metrics") or {}).get("exposure_days"),
            star_g6_path=None if not g6 else g6.get("path"), star_g6_flat_status=ea6.get("status"),
            star_val_exposure=bv.get("exposure_days"), star_val_return=(g6 or {}).get("total_return"),
            star_val_bh_return=bv.get("bh_total_return"), star_val_n_trades=(g6 or {}).get("n_trades"),
            validated_paths=[(c["gates"].get("G6") or {}).get("path") for c, lab in zip(cands, labels)
                             if lab == "validated"])
    if task.get("rcheck"):         # 국면 검사·G4 최근 조건 보정: 후보별 사후 판정 입력
        rec["rcheck_cands"] = _rcheck_candidates(cands, labels, rep[0]["logic_hash"] if rep else None)
    return rec


def run_market(task: dict) -> dict:
    """task: {kind, strength, k, grid, seed, layout, n_null, top_k, override_null_runs, scenario, …}.
    시장마다 새 임시 저장소(새 원장·새 Val 사용 이력)."""
    from .campaign import ResearchEnv

    t0 = time.time()
    layout = Layout(**task["layout"])
    tmp = Path(tempfile.mkdtemp(prefix="calib_"))
    try:
        make_root(tmp, layout, task["seed"], task["k"], task.get("plant_lookback", PLANT_LOOKBACK),
                  task.get("scenario", "5m"))
        env = ResearchEnv.at(tmp)
        rec = _campaign_record(env, task, task["grid"])
        rec.update({k: task[k] for k in ("kind", "strength", "k", "grid", "seed")},
                   plant_lookback=task.get("plant_lookback", PLANT_LOOKBACK), scenario=task.get("scenario", "5m"),
                   runtime_sec=round(time.time() - t0, 1))
        return rec
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================== 국면 연구 (노출 인식 관문 변형: 현재 vs 변형)
# 같은 시장·같은 캠페인 결과에 관문 설정만 바꾼 파이프라인을 각각 *따로 복사한 원장*에서 돌린다(Val 사용 원장이
# 섞이지 않게 — 같은 logic_hash 재사용 규칙 때문에 한 원장에서 두 번 돌리면 두 번째가 첫 결정을 재사용한다).
REGIME_VARIANTS: dict[str, dict] = {
    "current": {"exposure_aware.enabled": False},                        # 지금 관문 (변형은 shadow 정보만)
    "variant": {"exposure_aware.enabled": True},                         # gates.yaml 의 변형 기본값 그대로
    "variant_loose": {"exposure_aware.enabled": True,                    # 쉼 경로 손실 허용치만 넓힘
                      "exposure_aware.G6.flat_min_return": -0.05,
                      "exposure_aware.G6.flat_min_stress_return": -0.08},
}
REGIME_KINDS = ("null", "regime_cond", "regime_cond_f", "regime_filter")


@dataclass
class RegimeStudyConfig:
    mode: str
    layout: Layout
    strength: float                         # 심은 스펙의 참 연 Sharpe (전 구간, 비용 후)
    n_markets: dict                         # {kind: 시장 수} — Val 국면은 seed % 3 순환(bull/bear/side)
    n_null: int                             # G2 귀무 run 수
    top_k: int = TOP_K
    grid: str = "r12"
    pilot_ks: tuple[float, ...] = (0.0, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04)
    pilot_seeds: int = 6
    pilot_years: float = 5.8
    seed0: int = 11_000
    jobs: int = 1
    override_null_runs: bool = False
    variants: tuple[str, ...] = ("current", "variant")

    def to_record(self) -> dict:
        d = asdict(self)
        d["layout"] = asdict(self.layout)
        return d


def regime_quick_config(jobs: int = 1) -> RegimeStudyConfig:
    """테스트용 구조 점검 (수치 결론용 아님): 짧은 구간, 시장 3개, 귀무 100."""
    return RegimeStudyConfig("regime-quick", QUICK_LAYOUT, strength=3.0,
                             n_markets={"null": 1, "regime_cond": 0, "regime_filter": 2}, n_null=100, top_k=2,
                             pilot_ks=(0.0, 0.03, 0.06), pilot_seeds=1, pilot_years=1.0, jobs=jobs,
                             override_null_runs=True)


def regime_full_config(jobs: int = 4) -> RegimeStudyConfig:
    return RegimeStudyConfig("regime-full", FULL_LAYOUT, strength=2.0,
                             n_markets={"null": 30, "regime_cond": 6, "regime_cond_f": 15, "regime_filter": 15},
                             n_null=1000, jobs=jobs,
                             variants=("current", "variant", "variant_loose"))


def _pilot_regime_one(args) -> tuple[str, float, int, float, float]:
    kind, k, seed, years = args
    start = pd.Timestamp("2010-01-01", tz="UTC")
    end = start + pd.Timedelta(days=round(365.25 * years))
    vs = end - pd.Timedelta(days=182)
    df, _ = gen_regime_market(str(start.date()), str(vs.date()), str(end.date()), seed, k, kind)
    m1 = _engine_m1(df)
    spec = PLANTED_SPEC_REGIME if kind in FILTERED_KINDS else PLANTED_SPEC
    st = planted_stats(m1, start + pd.Timedelta(days=31), end, spec)
    return kind, k, seed, st["sharpe"], st["trades_per_day"]


def pilot_regime_edge_map(cfg: RegimeStudyConfig, pool=None) -> dict:
    """국면 시장 종류별 k ↦ 심은 스펙(regime_cond: S*, regime_filter: S*_f) 의 연 Sharpe(seed 평균; 국면 순서는
    seed 마다 무작위, Val 국면 seed % 3) → 목표 strength 의 k (단조 보정 후 역보간, 넘으면 선형 외삽)."""
    kinds = [k for k in REGIME_KINDS if k != "null" and cfg.n_markets.get(k)]
    tasks = [(kind, k, cfg.seed0 + 50_000 + s, cfg.pilot_years) for kind in kinds for k in cfg.pilot_ks
             for s in range(cfg.pilot_seeds)]
    rows = list(pool.map(_pilot_regime_one, tasks)) if pool else [_pilot_regime_one(t) for t in tasks]
    ks = np.array(cfg.pilot_ks, float)
    out: dict = {"ks": ks.tolist(), "pilot_years": cfg.pilot_years, "pilot_seeds": cfg.pilot_seeds, "kinds": {},
                 "k_for": {"null": 0.0}}
    for kind in kinds:
        sr = np.array([np.mean([r[3] for r in rows if r[0] == kind and r[1] == k]) for k in ks])
        tpd = [float(np.mean([r[4] for r in rows if r[0] == kind and r[1] == k])) for k in ks]
        mono = np.maximum.accumulate(sr)
        s = float(cfg.strength)
        if s > mono[-1]:
            slope = (mono[-1] - mono[-2]) / (ks[-1] - ks[-2])
            kk = float(ks[-1] + (s - mono[-1]) / slope)
        else:
            kk = float(np.interp(s, mono, ks))
        out["kinds"][kind] = {"sharpe_mean": sr.tolist(), "trades_per_day": tpd}
        out["k_for"][kind] = kk
    return out


def make_regime_root(tmp: Path, layout: Layout, seed: int, k_edge: float, edge_kind: str,
                     val_regime: str | None = None) -> pd.Series:
    from btc_quant.data.store import write_ohlcv
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "splits.yaml").write_text(yaml.safe_dump(layout.splits()))
    df, sched = gen_regime_market(layout.data_start, layout.val_start, layout.val_end, seed, k_edge, edge_kind,
                                  val_regime)
    write_ohlcv(df, "coinone", "BTC-KRW", "1m", root=tmp / "data" / "raw")
    return sched


def _clone_root(src: Path, dst: Path) -> Path:
    """원장·캠페인·설정만 복사, 데이터는 심볼릭 링크 (변형별 독립 원장)."""
    dst.mkdir(parents=True, exist_ok=True)
    for d in ("config", "research"):
        if (src / d).exists():
            shutil.copytree(src / d, dst / d)
    os.symlink((src / "data").resolve(), dst / "data")
    return dst


def run_regime_market(task: dict) -> list[dict]:
    """국면 시장 1개: 캠페인 1회 → 변형마다 (복사한 원장에서) 파이프라인 1회. 변형별 레코드 목록."""
    from .campaign import CampaignTemplate, ResearchEnv, load_campaign, run_campaign

    t0 = time.time()
    layout = Layout(**task["layout"])
    tmp = Path(tempfile.mkdtemp(prefix="calibreg_"))
    try:
        base = tmp / "base"
        base.mkdir()
        sched = make_regime_root(base, layout, task["seed"], task["k"], task["kind"], task.get("val_regime"))
        env = ResearchEnv.at(base)
        tpl = CampaignTemplate.from_dict(template_for(task["grid"], f"calib_{task['grid']}"))
        cr = run_campaign(tpl, env=env)
        vs = pd.Timestamp(layout.val_start, tz="UTC")
        dev_lab = sched[sched.index < vs]
        info = {"val_regime": str(sched.iloc[-1]),
                "dev_regime_share": {r: float((dev_lab == r).mean()) for r in REGIMES},
                "dev_last_year_regime_share": {r: float((dev_lab.iloc[-365:] == r).mean()) for r in REGIMES}}
        out = []
        for vname in task["variants"]:
            root_v = _clone_root(base, tmp / vname)
            env_v = ResearchEnv.at(root_v)
            cr_v = load_campaign(cr.campaign_id, env=env_v)
            ov = (task.get("variant_overrides") or {}).get(vname)       # params.criteria: 후보 값별 설정
            ov = REGIME_VARIANTS[vname] if ov is None else ov
            t_v = {**task, "gate_overrides": {**ov, **(task.get("gate_overrides") or {})}}
            rec = _evaluate_record(env_v, cr_v, t_v, task["grid"])
            rec.update({k: task[k] for k in ("kind", "strength", "k", "grid", "seed")}, variant=vname, **info,
                       study="regime", runtime_total_sec=round(time.time() - t0, 1))
            out.append(rec)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_regime_tasks(cfg: RegimeStudyConfig, k_for: dict) -> list[dict]:
    base = {"layout": asdict(cfg.layout), "n_null": cfg.n_null, "top_k": cfg.top_k,
            "override_null_runs": cfg.override_null_runs, "grid": cfg.grid, "regime": True,
            "variants": list(cfg.variants)}
    tasks, seed = [], cfg.seed0
    for kind in REGIME_KINDS:
        for _ in range(int(cfg.n_markets.get(kind, 0))):
            tasks.append({**base, "kind": kind, "strength": 0.0 if kind == "null" else float(cfg.strength),
                          "k": float(k_for.get(kind, 0.0)), "seed": seed, "star_filters": kind in FILTERED_KINDS,
                          "val_regime": REGIMES[seed % 3]})
            seed += 1
    return tasks


def summarize_regime(runs: list[dict]) -> dict:
    """변형 × 시장 종류 × (전체 | Val 국면별): 아무 후보 validated(귀무 = 오탐), S* validated(검정력), Dev 생존 등."""
    df = pd.DataFrame(runs)
    rows = []
    if df.empty:
        return {"rows": rows}
    for (var, kind), g in df.groupby(["variant", "kind"], sort=True):
        for vr, gg in [("all", g), *[(r, g[g["val_regime"] == r]) for r in REGIMES]]:
            n = len(gg)
            if not n:
                continue
            row = {"variant": var, "kind": kind, "val_regime": vr, "n_markets": n}
            for col in ("any_validated", "star_validated", "star_dev_passed", "star_g6_passed", "any_dev_survivor"):
                x = int(gg[col].fillna(False).astype(bool).sum())
                row[col] = x / n
                row[col + "_ci"] = wilson(x, n)
            row["star_outcome"] = {str(k): int(v) for k, v in gg["star_outcome"].value_counts().items()}
            row["star_g6_path"] = {str(k): int(v) for k, v in gg["star_g6_path"].fillna("-").value_counts().items()}
            row["star_g6_flat_status"] = {str(k): int(v) for k, v in
                                          gg["star_g6_flat_status"].fillna("-").value_counts().items()}
            row["star_g4_via"] = {str(k): int(v) for k, v in gg["star_g4_via"].fillna("-").value_counts().items()}
            for col in ("star_sharpe_dev", "star_dev_exposure", "star_val_exposure", "star_val_bh_return"):
                v = pd.to_numeric(gg[col], errors="coerce")
                row[col + "_mean"] = float(v.mean()) if v.notna().any() else float("nan")
            paths: dict[str, int] = {}
            for lst in gg["validated_paths"]:
                for p_ in lst or []:
                    paths[str(p_)] = paths.get(str(p_), 0) + 1
            row["validated_paths"] = paths
            rows.append(row)
    return {"rows": rows}


def run_regime_study(cfg: RegimeStudyConfig, progress=print, edge: dict | None = None) -> dict:
    t0 = time.time()
    pool = ProcessPoolExecutor(max_workers=cfg.jobs, mp_context=get_context("spawn")) if cfg.jobs > 1 else None
    try:
        if edge is None:
            progress(f"[regime] pilot edge map ({len(cfg.pilot_ks)} k × {cfg.pilot_seeds} seeds × {cfg.pilot_years}y)")
            edge = pilot_regime_edge_map(cfg, pool)
        else:
            edge = {**edge, "reused": True}
        progress(f"[regime] k for strength {cfg.strength}: {edge['k_for']}")
        tasks = build_regime_tasks(cfg, edge["k_for"])
        progress(f"[regime] {len(tasks)} markets × {len(cfg.variants)} gate variants")
        runs = []
        it = pool.map(run_regime_market, tasks) if pool else map(run_regime_market, tasks)
        for i, recs in enumerate(it, start=1):
            runs += recs
            r0 = recs[0]
            progress(f"[regime] {i}/{len(tasks)} {r0['kind']} val={r0['val_regime']} seed={r0['seed']}: "
                     + "; ".join(f"{r['variant']}: validated={r['n_validated']} star={r['star_outcome']} "
                                 f"g6path={r.get('star_g6_path')} flat={r.get('star_g6_flat_status')}"
                                 for r in recs) + f" ({r0['runtime_total_sec']}s)")
    finally:
        if pool:
            pool.shutdown()
    from .campaign import run_params
    try:
        pv = run_params()
    except Exception as e:  # noqa: BLE001
        pv = {"error": str(e)}
    return {"config": cfg.to_record(), "edge_map": edge, "runs": runs, "summary": summarize_regime(runs),
            "runtime_sec": round(time.time() - t0, 1), "created": pd.Timestamp.now("UTC").isoformat(),
            "gate_config_version": G.load_gate_config().get("version"),
            "exposure_aware_cfg": G.load_gate_config().get("exposure_aware"),
            "params_version": pv.get("params_version"), "cost_versions": pv.get("cost_versions")}


def g6_stage_market(task: dict) -> list[dict]:
    """G6 단계 조건부 진단 (귀무 시장용): Dev 관문을 건너뛰고 캠페인 군집 대표 상위 top_k 를 *모두* Val 에
    올려 G6 판정을 현재 규칙 vs 변형으로 비교한다(원장·Val 예산 미사용; Holm 은 k=1 문턱 α 로 근사).
    귀무 시장에서는 Dev 관문이 거의 모든 후보를 막아 전체 오탐률이 0 으로 같아지므로, '쉼 경로가 G6 단계에서
    얼마나 걸러 내는가'를 따로 잰다. 반환 = 후보별 레코드."""
    from btc_quant.evaluation import inference

    from .campaign import CampaignTemplate, ResearchEnv, get_cost, run_campaign
    from .pipeline import Pipeline

    layout = Layout(**task["layout"])
    tmp = Path(tempfile.mkdtemp(prefix="calibg6_"))
    try:
        sched = make_regime_root(tmp, layout, task["seed"], task["k"], task["kind"], task.get("val_regime"))
        env = ResearchEnv.at(tmp)
        tpl = CampaignTemplate.from_dict(template_for(task["grid"], f"calib_{task['grid']}"))
        cr = run_campaign(tpl, env=env)
        cfgs = {v: _gate_cfg({"gate_overrides": REGIME_VARIANTS[v]}) for v in task["variants"]}
        p0 = Pipeline(cr, env, gate_cfg=cfgs["current"], register=False, reference_check=False, robustness=False,
                      lessons=False, val_budget=False)
        top, _ = p0.select_candidates()
        m1v, vs, ve = p0.load_val()
        prim = cr.primary()
        alpha = float(cfgs["current"]["G6"]["val_budget"]["alpha"])
        out = []
        for lh in top[: task["top_k"]]:
            spec = cr.specs[lh]
            devm = {**prim.loc[lh].to_dict(), **p0.exposure_metrics(p0.dev_result(spec, tpl.costs["primary"]))}
            res = None
            for v, cfg in cfgs.items():
                p = Pipeline(cr, env, gate_cfg=cfg, register=False, reference_check=False, robustness=False,
                             lessons=False, val_budget=False)
                p._cache, p._m1, p._ctx_val = p0._cache, p0._m1, p0._ctx_val
                g, res_v, met, _ = p.g6(spec, m1v, vs, ve, tpl.costs["primary"], tpl.costs["stress"], record=False,
                                        budget=False, dev_metrics=devm)
                res = res_v
                pv = inference.bootstrap_mean_test(res.daily_returns.to_numpy(float), n_boot=500, seed=0)["p_value"]
                base_ok = g["path"] == "sharpe" or (g.get("base_passed") is True)
                ea = g.get("exposure_aware") or {}
                ok4, _ = G.check_g4(devm, cfg)
                out.append({"seed": task["seed"], "kind": task["kind"], "val_regime": str(sched.iloc[-1]),
                            "variant": v, "logic_hash": lh, "filtered": bool(spec.filters),
                            "dev_sharpe_w": float(devm["sharpe_w"]), "dev_exposure": devm.get("exposure_days"),
                            "g4_passed": bool(ok4), "g6_checks_passed": bool(g["passed"]), "path": g.get("path"),
                            "flat_status": ea.get("status"), "val_exposure": met.get("exposure_days"),
                            "val_return": met.get("total_return"), "val_n_trades": met.get("n_trades"),
                            "val_p": float(pv),
                            "g6_with_holm": bool((base_ok and pv <= alpha) or g.get("path") == "flat")})
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_g6_stage(n_null: int = 30, layout: Layout = FULL_LAYOUT, seed0: int = 11_000, jobs: int = 4,
                 top_k: int = TOP_K, variants=("current", "variant", "variant_loose"), progress=print) -> dict:
    """귀무 국면 시장 n_null 개(seed 는 regime_full_config 귀무와 같은 순서 → 같은 시장)의 G6 단계 진단."""
    tasks = [{"layout": asdict(layout), "seed": seed0 + i, "k": 0.0, "kind": "null", "grid": "r12", "top_k": top_k,
              "val_regime": REGIMES[(seed0 + i) % 3], "variants": list(variants)} for i in range(n_null)]
    rows = []
    pool = ProcessPoolExecutor(max_workers=jobs, mp_context=get_context("spawn")) if jobs > 1 else None
    try:
        it = pool.map(g6_stage_market, tasks) if pool else map(g6_stage_market, tasks)
        for i, r in enumerate(it, start=1):
            rows += r
            progress(f"[g6stage] {i}/{len(tasks)} seed={tasks[i - 1]['seed']} val={tasks[i - 1]['val_regime']}: "
                     + ", ".join(f"{x['variant']}:{int(x['g6_with_holm'])}" for x in r))
    finally:
        if pool:
            pool.shutdown()
    df = pd.DataFrame(rows)
    summ = []
    if not df.empty:
        for (v, vr, f), g in df.groupby(["variant", "val_regime", "filtered"]):
            n = len(g)
            x = int(g["g6_with_holm"].sum())
            summ.append({"variant": v, "val_regime": vr, "filtered": bool(f), "n_candidates": n,
                         "g6_pass": x / n, "g6_pass_ci": wilson(x, n), "flat_path": int((g["path"] == "flat").sum()),
                         "g4_pass": float(g["g4_passed"].mean())})
    return {"rows": rows, "summary": summ}


def g6_stage_markdown(res: dict) -> str:
    L = ["| 관문 | Val 국면 | 국면 필터 후보 | 후보 수 | G6 통과(+Holm k=1 근사) | 쉼 경로 통과 수 | G4 통과(Dev) |",
         "|---|---|---|---|---|---|---|"]
    for r in sorted(res["summary"], key=lambda r: (r["val_regime"], r["filtered"], r["variant"])):
        L.append(f"| {r['variant']} | {r['val_regime']} | {'예' if r['filtered'] else '아니오'} | {r['n_candidates']} | "
                 f"{_pct(r['g6_pass'])}{_ci(r['g6_pass_ci'])} | {r['flat_path']} | {_pct(r['g4_pass'])} |")
    return "\n".join(L)


def regime_tables_markdown(result: dict) -> str:
    rows = result["summary"]["rows"]
    kl = {"null": "귀무", "regime_cond": "(ii) 국면 조건부 엣지 · S* 무필터",
          "regime_cond_f": "(ii') 국면 조건부 엣지 · S*_f", "regime_filter": "(iii) 국면 필터 엣지(하락 −) · S*_f"}
    L = ["| 시장 | Val 국면 | 시장 수 | 관문 | 아무 후보 validated (귀무=오탐) | S* validated (검정력) | "
         "S* Dev 생존 | S* G6 통과(견고성 전) | S* 결과 | S* G6 경로 | S* 쉼 경로 상태 | G4 경로 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    order = {k: i for i, k in enumerate(REGIME_KINDS)}
    vorder = {"all": 0, "bear": 1, "bull": 2, "side": 3}
    for r in sorted(rows, key=lambda r: (order[r["kind"]], vorder[r["val_regime"]], r["variant"])):
        star = "—" if r["kind"] == "null" else f"{_pct(r['star_validated'])}{_ci(r['star_validated_ci'])}"
        L.append(f"| {kl[r['kind']]} | {r['val_regime']} | {r['n_markets']} | {r['variant']} | "
                 f"{_pct(r['any_validated'])}{_ci(r['any_validated_ci'])} | {star} | "
                 f"{_pct(r['star_dev_passed'])} | {_pct(r['star_g6_passed'])} | {_ff(r['star_outcome'])} | "
                 f"{_ff(r['star_g6_path'])} | {_ff(r['star_g6_flat_status'])} | {_ff(r['star_g4_via'])} |")
    e = result["edge_map"]
    L += ["", f"엣지 세기 보정(파일럿 {e.get('pilot_years')}년 × {e.get('pilot_seeds')} seed"
          + (", 재사용" if e.get("reused") else "") + "): 목표 Sharpe "
          f"{result['config']['strength']:g} → k = " + ", ".join(f"{k}: {v:.4f}" for k, v in e["k_for"].items())]
    for kind, d in (e.get("kinds") or {}).items():
        L.append(f"- {kind}: k {e['ks']} → Sharpe {np.round(d['sharpe_mean'], 2).tolist()}, 거래/일 "
                 f"{np.round(d['trades_per_day'], 1).tolist()}")
    return "\n".join(L)


# ============================================================== 롱 온리 기준 2종 보정 (견고성 '국면' 검사 · G4 최근 12개월)
# 질문: 롱 온리 국면 필터 추세 전략은 구조적으로 상승 국면에서 손익 대부분을 번다. 견고성 '국면' 검사(한 국면 손익
# 비중 > 70% → 실패)가 알파 검사(노출-맞춤 B&H, 알파 t ≥ 2)와 G2(거래 수 맞춤 무작위 진입 귀무) 너머의 보호를 더하는지,
# 아니면 참 엣지만 지우는지. G4 의 '최근 12개월 Sharpe > 0' 도 같은 질문(최근 1년이 하락장이면 필터 전략은 대부분 쉼).
# 설계(공통 난수): 시장마다 캠페인 1회 → G4 변형마다 *복사한 원장*에서 파이프라인 1회(Dev 생존·Val 사용이 바뀌므로)
# → 국면 검사 방식별 판정은 같은 파이프라인 결과에 사후 적용(견고성 묶음은 G6 통과 후보에만 돌고 원장·Val 에 되먹이지
# 않으므로 사후 적용 = 해당 설정으로 돌린 결과; robustness.regime_verdict 를 check_regime 과 함께 쓴다).
RCHECK_MODES: dict[str, dict] = {           # 국면 검사 방식 (gates.yaml robustness.* 덮어쓰기)
    "current": {},                                                       # 비중 > 0.70 → 실패
    "share80": {"robustness.regime_max_pnl_share": 0.80},
    "share90": {"robustness.regime_max_pnl_share": 0.90},
    "dom_alpha": {"robustness.regime_mode": "dominant_alpha"},           # 비중 > 0.70 이면 지배 국면 안 노출-맞춤 B&H 초과 ≥ 0
    "info": {"robustness.regime_mode": "informational"},                 # 표시만, 판정 제외
}
# G4 최근 12개월 조건 변형: Sharpe > 0 OR (최근 12개월 시장 참여일 < max_exposure AND 최근 12개월 총수익 ≥ min_return).
# gates.py 를 바꾸지 않는 *보정 전용 반사실*(calibration 프로세스 안에서만 check_g4 를 감쌈) — 채택하려면 관문 코드 변경 필요.
G4_RECENT_VARIANTS: dict[str, dict | None] = {
    "current": None,
    "g4flat10": {"max_exposure": 0.10, "min_return": -0.02},
    "g4flat25": {"max_exposure": 0.25, "min_return": -0.02},
}
# 추가 파이프라인 변형 (관문 설정 덮어쓰기 + 선택적 G4 최근 조건): 노출 인식 G4/G6 변형(exposure_aware, 기본 OFF)을
# 켠 상태에서 국면 검사가 묶는지 본다 (기본 실행 목록에는 없음 — CLI --rcheck-variants 로 선택).
RCHECK_EXTRA_VARIANTS: dict[str, tuple[str, dict]] = {      # 이름 → (G4_RECENT_VARIANTS 이름, 관문 덮어쓰기)
    "ea_on": ("current", {"exposure_aware.enabled": True}),
    "ea_on_g4flat10": ("g4flat10", {"exposure_aware.enabled": True}),
}


def rcheck_variant_spec(name: str) -> tuple[dict | None, dict]:
    """파이프라인 변형 이름 → (G4 최근 조건 opt, 관문 덮어쓰기)."""
    if name in G4_RECENT_VARIANTS:
        return G4_RECENT_VARIANTS[name], {}
    g4, ov = RCHECK_EXTRA_VARIANTS[name]
    return G4_RECENT_VARIANTS[g4], dict(ov)
RCHECK_DIAG_CHECKS = ("outliers", "leave_year_out", "regime", "alpha")   # 조건부 진단(기본 실행만 쓰는 검사)


def recent_flat_metrics(daily: pd.Series, trades: pd.DataFrame | None, days: int = 365) -> dict:
    """최근 `days` 일(metrics.recent 와 같은 창)의 시장 참여일 비율·총수익 (G4 최근 조건 변형 입력)."""
    s = pd.Series(daily).dropna().astype(float)
    if not s.size:
        return {"recent_exposure_days": float("nan"), "recent_total_return": float("nan")}
    inm = M.days_in_market(trades, s.index) if isinstance(s.index, pd.DatetimeIndex) else np.zeros(s.size, bool)
    m = (s.index > s.index.max() - pd.Timedelta(days=days)) if isinstance(s.index, pd.DatetimeIndex) \
        else np.arange(s.size) >= s.size - days
    return {"recent_exposure_days": float(np.mean(inm[m])) if m.any() else float("nan"),
            "recent_total_return": float(np.prod(1 + s.to_numpy()[m]) - 1)}


def g4_recent_variant(metrics: dict, cfg: dict | None, opt: dict | None, base_check=None) -> tuple[bool, dict]:
    """기존 G4 판정 + 최근 12개월 '쉼' 대체 경로: 롤링 조건은 통과했고 최근 Sharpe 조건만 실패했을 때,
    최근 참여일 < opt.max_exposure 이고 최근 총수익 ≥ opt.min_return 이면 통과. opt=None → 기존 판정 그대로."""
    ok, d = (base_check or G.check_g4)(metrics, cfg)
    if not opt:
        return ok, d
    ex, rr = metrics.get("recent_exposure_days"), metrics.get("recent_total_return")
    d = {**d, "recent_flat": {"max_exposure": opt["max_exposure"], "min_return": opt["min_return"],
                              "recent_exposure_days": ex, "recent_total_return": rr, "applied": False}}
    if (not ok and d.get("ok_rolling") and not d.get("ok_recent") and ex is not None and rr is not None
            and ex == ex and rr == rr and ex < opt["max_exposure"] and rr >= opt["min_return"]):
        d["recent_flat"]["applied"] = True
        d["ok_recent"] = True
        return True, d
    return ok, d


class _G4RecentPatch:
    """보정 프로세스 안에서만 G.check_g4 와 Pipeline.exposure_metrics 를 감싼다(opt=None 이면 아무것도 안 함)."""

    def __init__(self, opt: dict | None):
        self.opt = opt

    def __enter__(self):
        if not self.opt:
            return self
        from . import pipeline as PL
        self._g4, self._em, self._PL = G.check_g4, PL.Pipeline.exposure_metrics, PL
        orig_g4, orig_em, opt = self._g4, self._em, self.opt

        def em(pself, res):
            out = orig_em(pself, res)
            out.update(recent_flat_metrics(res.daily_returns, res.trades,
                                           int(pself.cfg["G4"].get("recent_days", 365))))
            return out

        G.check_g4 = lambda metrics, cfg=None: g4_recent_variant(metrics, cfg, opt, orig_g4)
        PL.Pipeline.exposure_metrics = em
        return self

    def __exit__(self, *exc):
        if self.opt:
            G.check_g4 = self._g4
            self._PL.Pipeline.exposure_metrics = self._em
        return False


def _trim_regime(chk: dict | None) -> dict | None:
    if not chk:
        return None
    keys = ("total_pnl_log", "max_pnl_share", "dominant", "dominant_exposure",
            "dominant_excess_vs_matched_bh_annual", "dominant_mean_annual", "dominant_matched_bh_annual")
    return {d: {k: chk[d].get(k) for k in keys} for d in ("trend", "vol") if d in chk}


def rcheck_thresholds(gate_cfg: dict, mode: str) -> dict:
    """국면 검사 방식 mode 의 견고성 기준 (gate_cfg 의 robustness + RCHECK_MODES 덮어쓰기)."""
    from .robustness import load_thresholds
    th = load_thresholds(gate_cfg.get("robustness") or {})
    for dotted, v in RCHECK_MODES[mode].items():
        th[dotted.split(".", 1)[1]] = v
    return th


def rcheck_candidate_validated(c: dict, th: dict) -> bool:
    """후보 레코드(_rcheck_candidates) 가 국면 검사 기준 th 에서 validated 인가 (G6 통과 + 견고성 통과)."""
    from .robustness import regime_verdict
    if not c["g6_passed"]:
        return False
    if c["rob_failed"] is None:                  # 견고성 미실행(robustness=False) → 기존 라벨
        return True
    if any(f != "regime" for f in c["rob_failed"]):
        return False
    return True if c["regime"] is None else bool(regime_verdict(c["regime"], th))


def _rcheck_candidates(cands: list[dict], labels: list[str], rep_lh: str | None) -> list[dict]:
    out = []
    for c, lab in zip(cands, labels):
        rob = c.get("robustness")
        out.append({"logic_hash": c["logic_hash"], "filtered": bool(c["spec"].get("filters")),
                    "dev_passed": bool(c.get("dev_passed")), "first_failed": c.get("first_failed"),
                    "g2_passed": bool((c["gates"].get("G2") or {}).get("passed")),
                    "g4_recent_flat": bool(((c["gates"].get("G4") or {}).get("recent_flat") or {}).get("applied")),
                    "g6_passed": str(c.get("status", "")).startswith("validated"), "label": lab,
                    "rob_failed": None if not rob else list(rob.get("failed") or []),
                    "regime": None if not rob else _trim_regime((rob.get("checks") or {}).get("regime")),
                    "is_rep": c["logic_hash"] == rep_lh})
    return out


def rcheck_mode_outcomes(rec: dict, gate_cfg: dict) -> dict:
    """레코드(rcheck_cands 포함) → 국면 검사 방식별 {any_validated, star_validated}."""
    out = {}
    for mode in RCHECK_MODES:
        th = rcheck_thresholds(gate_cfg, mode)
        val = [c for c in rec["rcheck_cands"] if rcheck_candidate_validated(c, th)]
        out[mode] = {"any_validated": bool(val), "star_validated": any(c["is_rep"] for c in val),
                     "n_validated": len(val)}
    return out


def _rcheck_diag(env, cr, task: dict, cands: list[dict]) -> None:
    """조건부 진단: 상위 후보 *모두*(Dev·Val 관문과 무관)에 Dev 견고성 검사(RCHECK_DIAG_CHECKS)를 돌려
    국면 검사 방식별 판정·알파 판정을 붙인다 (원장·Val 미사용)."""
    from .campaign import get_cost
    from .pipeline import Pipeline
    from .robustness import regime_verdict, robustness_suite
    cfg = _gate_cfg(task)
    p = Pipeline(cr, env, gate_cfg=cfg, register=False, reference_check=False, robustness=False,
                 lessons=False, val_budget=False)
    c = p.cache
    for cd in cands:
        rb = robustness_suite(cr.specs[cd["logic_hash"]], c.m1, p.start, p.end, get_cost(p.costs["primary"]),
                              ctx=c.ctx, checks=RCHECK_DIAG_CHECKS, thresholds=cfg.get("robustness"))
        ch = rb["checks"]
        reg = _trim_regime(ch["regime"])
        cd["diag"] = {"alpha_passed": ch["alpha"]["passed"], "alpha_t": ch["alpha"].get("alpha_t"),
                      "excess_vs_matched_bh_annual": ch["alpha"].get("excess_vs_matched_bh_annual"),
                      "outliers_passed": ch["outliers"]["passed"], "loyo_passed": ch["leave_year_out"]["passed"],
                      "dev_sharpe": rb["base"]["sharpe"], "regime": reg,
                      "regime_pass": {m: bool(regime_verdict(reg, rcheck_thresholds(cfg, m))) for m in RCHECK_MODES}}


def run_rcheck_market(task: dict) -> list[dict]:
    """시장 1개(국면 시장 또는 표준 귀무 시장): 캠페인 1회 → G4 변형마다 복사한 원장에서 파이프라인 1회 → 국면 검사
    방식별 사후 판정. G4 'current' 실행에는 조건부 진단(_rcheck_diag)도 붙인다(task['diag'])."""
    from .campaign import CampaignTemplate, ResearchEnv, load_campaign, run_campaign

    t0 = time.time()
    layout = Layout(**task["layout"])
    tmp = Path(tempfile.mkdtemp(prefix="calibrc_"))
    try:
        base = tmp / "base"
        base.mkdir()
        if task["market"] == "standard":
            make_root(base, layout, task["seed"], task["k"])
            info = {"val_regime": "none", "dev_regime_share": None}
        else:
            sched = make_regime_root(base, layout, task["seed"], task["k"], task["kind"], task.get("val_regime"))
            vs = pd.Timestamp(layout.val_start, tz="UTC")
            dev_lab = sched[sched.index < vs]
            info = {"val_regime": str(sched.iloc[-1]),
                    "dev_regime_share": {r: float((dev_lab == r).mean()) for r in REGIMES},
                    "dev_last_year_regime_share": {r: float((dev_lab.iloc[-365:] == r).mean()) for r in REGIMES}}
        env = ResearchEnv.at(base)
        tpl = CampaignTemplate.from_dict(template_for(task["grid"], f"calib_{task['grid']}"))
        cr = run_campaign(tpl, env=env)
        out = []
        for vname in task["g4_variants"]:
            root_v = _clone_root(base, tmp / vname)
            env_v = ResearchEnv.at(root_v)
            cr_v = load_campaign(cr.campaign_id, env=env_v)
            g4opt, ov = rcheck_variant_spec(vname)
            t_v = {**task, "gate_overrides": {**ov, **(task.get("gate_overrides") or {})}} if ov else task
            with _G4RecentPatch(g4opt):
                rec = _evaluate_record(env_v, cr_v, t_v, task["grid"])
            gcfg = _gate_cfg(t_v)
            rec["rcheck_modes"] = rcheck_mode_outcomes(rec, gcfg)
            cur = rec["rcheck_modes"]["current"]
            rec["rcheck_consistent"] = bool(cur["any_validated"] == rec["any_validated"]
                                            and cur["star_validated"] == rec["star_validated"])
            if task.get("diag") and vname == "current":
                _rcheck_diag(env_v, cr_v, task, rec["rcheck_cands"])
            rec.update({k: task[k] for k in ("kind", "strength", "k", "grid", "seed", "market")}, g4_variant=vname,
                       **info, study="rcheck", runtime_total_sec=round(time.time() - t0, 1))
            out.append(rec)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dataclass
class RcheckStudyConfig:
    mode: str
    layout: Layout
    strength: float
    n_markets: dict                         # 국면 시장 {kind: 수} — regime_full_config 와 같은 seed 순서
    n_extra_null: int                       # 추가 귀무 국면 시장 (seed extra_seed0 부터, Val 국면 seed%3)
    n_standard_null: int                    # 표준 귀무 시장 (gen_market k=0, 국면 없음)
    n_null: int
    top_k: int = TOP_K
    grid: str = "r12"
    seed0: int = 11_000
    extra_seed0: int = 11_200
    standard_seed0: int = 9_000
    jobs: int = 1
    override_null_runs: bool = False
    g4_variants: tuple[str, ...] = tuple(G4_RECENT_VARIANTS)
    diag: bool = True
    pilot_ks: tuple[float, ...] = (0.0, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04)
    pilot_seeds: int = 6
    pilot_years: float = 5.8

    def to_record(self) -> dict:
        d = asdict(self)
        d["layout"] = asdict(self.layout)
        return d


def rcheck_quick_config(jobs: int = 1) -> RcheckStudyConfig:
    """테스트용 구조 점검(수치 결론용 아님): 짧은 구간, 국면 시장 2 + 표준 귀무 1, 귀무 100."""
    return RcheckStudyConfig("rcheck-quick", QUICK_LAYOUT, strength=3.0,
                             n_markets={"null": 1, "regime_filter": 1}, n_extra_null=0, n_standard_null=1,
                             n_null=100, top_k=2, override_null_runs=True, jobs=jobs,
                             pilot_ks=(0.0, 0.03, 0.06), pilot_seeds=1, pilot_years=1.0)


def rcheck_full_config(jobs: int = 4) -> RcheckStudyConfig:
    return RcheckStudyConfig("rcheck-full", FULL_LAYOUT, strength=2.0,
                             n_markets={"null": 30, "regime_cond": 6, "regime_cond_f": 15, "regime_filter": 15},
                             n_extra_null=30, n_standard_null=30, n_null=1000, jobs=jobs)


def build_rcheck_tasks(cfg: RcheckStudyConfig, k_for: dict) -> list[dict]:
    rcfg = RegimeStudyConfig("rc", cfg.layout, cfg.strength, cfg.n_markets, cfg.n_null, cfg.top_k, cfg.grid,
                             seed0=cfg.seed0, override_null_runs=cfg.override_null_runs)
    common = {"g4_variants": list(cfg.g4_variants), "diag": cfg.diag, "rcheck": True}
    tasks = [{**t, **common, "market": "regime"} for t in build_regime_tasks(rcfg, k_for)]
    base = {k: tasks[0][k] for k in ("layout", "n_null", "top_k", "override_null_runs", "grid", "regime")} if tasks \
        else {"layout": asdict(cfg.layout), "n_null": cfg.n_null, "top_k": cfg.top_k,
              "override_null_runs": cfg.override_null_runs, "grid": cfg.grid, "regime": True}
    for i in range(cfg.n_extra_null):
        s = cfg.extra_seed0 + i
        tasks.append({**base, **common, "market": "regime", "kind": "null", "strength": 0.0, "k": 0.0, "seed": s,
                      "star_filters": False, "val_regime": REGIMES[s % 3]})
    for i in range(cfg.n_standard_null):
        tasks.append({**base, **common, "market": "standard", "kind": "std_null", "strength": 0.0, "k": 0.0,
                      "seed": cfg.standard_seed0 + i, "star_filters": False, "val_regime": None})
    return tasks


RCHECK_POWER_KINDS = FILTERED_KINDS                      # 검정력 판정 = 국면 필터 엣지 S*_f 시장
RCHECK_NULL_KINDS = ("null", "std_null")


def summarize_rcheck(runs: list[dict]) -> dict:
    """(G4 변형 × 국면 검사 방식) 조합마다 오탐(귀무 국면 + 표준 귀무, 하락 Val 귀무)·검정력(S*_f, Sharpe 2)."""
    rows = []
    if not runs:
        return {"rows": rows, "diag": {}}
    g4s = list(dict.fromkeys(r["g4_variant"] for r in runs))
    for g4 in g4s:
        rr = [r for r in runs if r["g4_variant"] == g4]
        for mode in RCHECK_MODES:
            def frac(sel, key):
                x = sum(bool(r["rcheck_modes"][mode][key]) for r in sel)
                return x, len(sel), (x / len(sel) if sel else float("nan")), wilson(x, len(sel))
            nul = [r for r in rr if r["kind"] in RCHECK_NULL_KINDS]
            row = {"g4_variant": g4, "regime_mode": mode}
            for name, sel, key in (
                    ("fpr", nul, "any_validated"),
                    ("fpr_regime_null", [r for r in rr if r["kind"] == "null"], "any_validated"),
                    ("fpr_std_null", [r for r in rr if r["kind"] == "std_null"], "any_validated"),
                    ("fpr_bear_val", [r for r in rr if r["kind"] == "null" and r.get("val_regime") == "bear"],
                     "any_validated"),
                    ("power", [r for r in rr if r["kind"] in RCHECK_POWER_KINDS], "star_validated"),
                    ("power_bull_val", [r for r in rr if r["kind"] in RCHECK_POWER_KINDS
                                        and r.get("val_regime") == "bull"], "star_validated"),
                    ("power_regime_cond", [r for r in rr if r["kind"] == "regime_cond"], "star_validated"),
                    ("power_regime_filter", [r for r in rr if r["kind"] == "regime_filter"], "star_validated"),
                    ("power_regime_cond_f", [r for r in rr if r["kind"] == "regime_cond_f"], "star_validated")):
                x, n, p, ci = frac(sel, key)
                row.update({name: p, name + "_x": x, name + "_n": n, name + "_ci": ci})
            row["star_dev_passed"] = (sum(bool(r["star_dev_passed"]) for r in rr if r["kind"] in RCHECK_POWER_KINDS)
                                      / max(1, sum(r["kind"] in RCHECK_POWER_KINDS for r in rr)))
            row["star_g6_passed"] = (sum(bool(r["star_g6_passed"]) for r in rr if r["kind"] in RCHECK_POWER_KINDS)
                                     / max(1, sum(r["kind"] in RCHECK_POWER_KINDS for r in rr)))
            rows.append(row)
    return {"rows": rows, "diag": summarize_rcheck_diag(runs),
            "inconsistent": [(r["seed"], r["g4_variant"]) for r in runs if not r.get("rcheck_consistent", True)]}


def summarize_rcheck_diag(runs: list[dict]) -> dict:
    """조건부 진단: 상위 후보(G4 current 실행) 중 (귀무 | 심은 S* 대표) × (G2 통과 여부) 별로
    알파 통과율과 국면 검사 방식별 통과율, 그리고 'G2·알파 모두 통과했는데 국면 검사만이 막는' 비율."""
    out = {}
    cands = []
    for r in runs:
        if r["g4_variant"] != "current":
            continue
        for c in r.get("rcheck_cands") or []:
            if "diag" not in c:
                continue
            grp = ("null" if r["kind"] in RCHECK_NULL_KINDS else
                   "planted_rep" if c["is_rep"] and r["kind"] in RCHECK_POWER_KINDS else
                   "planted_rep_unfiltered_edge" if c["is_rep"] else "planted_other")
            cands.append({**c, "grp": grp, "kind": r["kind"]})
    for grp in ("null", "planted_rep", "planted_rep_unfiltered_edge", "planted_other"):
        for g2 in ("all", "g2_pass", "g2_alpha_pass"):
            sel = [c for c in cands if c["grp"] == grp]
            if g2 == "g2_pass":
                sel = [c for c in sel if c["g2_passed"]]
            elif g2 == "g2_alpha_pass":
                sel = [c for c in sel if c["g2_passed"] and c["diag"]["alpha_passed"]]
            n = len(sel)
            if not n:
                continue
            row = {"n": n, "alpha_pass": sum(bool(c["diag"]["alpha_passed"]) for c in sel) / n,
                   "filtered": sum(c["filtered"] for c in sel) / n}
            for m in RCHECK_MODES:
                x = sum(c["diag"]["regime_pass"][m] for c in sel)
                row[f"regime_pass_{m}"] = x / n
                row[f"regime_pass_{m}_ci"] = wilson(x, n)
                # 국면 검사만이 막는 비율(알파·이상치·연도 제외는 통과, 국면 검사 실패)
                y = sum((not c["diag"]["regime_pass"][m]) and bool(c["diag"]["alpha_passed"])
                        and c["diag"]["outliers_passed"] is not False and c["diag"]["loyo_passed"] is not False
                        for c in sel)
                row[f"regime_only_block_{m}"] = y / n
            out[f"{grp}|{g2}"] = row
    return out


def run_rcheck_study(cfg: RcheckStudyConfig, progress=print, edge: dict | None = None) -> dict:
    t0 = time.time()
    pool = ProcessPoolExecutor(max_workers=cfg.jobs, mp_context=get_context("spawn")) if cfg.jobs > 1 else None
    try:
        if edge is None:
            rcfg = RegimeStudyConfig("rc", cfg.layout, cfg.strength, cfg.n_markets, cfg.n_null, cfg.top_k, cfg.grid,
                                     pilot_ks=cfg.pilot_ks, pilot_seeds=cfg.pilot_seeds, pilot_years=cfg.pilot_years,
                                     seed0=cfg.seed0)
            progress(f"[rcheck] pilot edge map ({len(cfg.pilot_ks)} k × {cfg.pilot_seeds} seeds × {cfg.pilot_years}y)")
            edge = pilot_regime_edge_map(rcfg, pool)
        else:
            edge = {**edge, "reused": True}
        bad = {k: v for k, v in edge["k_for"].items() if not np.isfinite(v)}
        if bad:                   # 짧은 파일럿(quick)에서 보간이 평평하면 외삽이 무한대 → 파일럿 최대 k 로 (구조 점검용)
            edge = {**edge, "k_for": {**edge["k_for"], **{k: float(max(cfg.pilot_ks)) for k in bad}},
                    "k_clipped": sorted(bad)}
        progress(f"[rcheck] k for strength {cfg.strength}: {edge['k_for']}")
        tasks = build_rcheck_tasks(cfg, edge["k_for"])
        progress(f"[rcheck] {len(tasks)} markets × {len(cfg.g4_variants)} G4 variants × {len(RCHECK_MODES)} "
                 "regime-check modes (post hoc)")
        runs = []
        it = pool.map(run_rcheck_market, tasks) if pool else map(run_rcheck_market, tasks)
        for i, recs in enumerate(it, start=1):
            runs += recs
            r0 = recs[0]
            progress(f"[rcheck] {i}/{len(tasks)} {r0['kind']} val={r0['val_regime']} seed={r0['seed']}: "
                     + "; ".join(f"{r['g4_variant']}: star={r['star_outcome']} "
                                 + ",".join(f"{m}={int(v['any_validated'])}/{int(v['star_validated'])}"
                                            for m, v in r['rcheck_modes'].items()) for r in recs)
                     + f" ({r0['runtime_total_sec']}s)")
    finally:
        if pool:
            pool.shutdown()
    from .campaign import run_params
    try:
        pv = run_params()
    except Exception as e:  # noqa: BLE001
        pv = {"error": str(e)}
    return {"config": cfg.to_record(), "edge_map": edge, "runs": runs, "summary": summarize_rcheck(runs),
            "modes": RCHECK_MODES, "g4_variants": G4_RECENT_VARIANTS, "extra_variants": RCHECK_EXTRA_VARIANTS,
            "runtime_sec": round(time.time() - t0, 1), "created": pd.Timestamp.now("UTC").isoformat(),
            "gate_config_version": G.load_gate_config().get("version"),
            "params_version": pv.get("params_version"), "cost_versions": pv.get("cost_versions")}


def beta_probe_market(task: dict) -> list[dict]:
    """베타 탐침 (조건부 진단, 원장·Val 미사용): 귀무 시장(국면 또는 표준)에서 저회전 장기 보유 롱 스펙(격자 rb8)을
    전부 Dev 에서 평가 — G2(같은 필터·청산 + 무작위 진입 귀무, n_null 회; p ≤ null_top_fraction 이고 가중 Sharpe > 0),
    알파 검사, 국면 검사 방식별 판정. '국면 검사가 알파·G2 너머로 막는 귀무 후보' 를 직접 센다."""
    from .campaign import CampaignTemplate, ResearchEnv, get_cost, run_campaign
    from .pipeline import Pipeline
    from .robustness import regime_verdict, robustness_suite

    layout = Layout(**task["layout"])
    tmp = Path(tempfile.mkdtemp(prefix="calibbeta_"))
    try:
        if task["market"] == "standard":
            make_root(tmp, layout, task["seed"], 0.0)
            vr = "none"
        else:
            sched = make_regime_root(tmp, layout, task["seed"], 0.0, "null", task.get("val_regime"))
            vr = str(sched.iloc[-1])
        env = ResearchEnv.at(tmp)
        cr = run_campaign(CampaignTemplate.from_dict(template_for("rb8", "calib_rb8")), env=env)
        cfg = _gate_cfg({})
        p = Pipeline(cr, env, gate_cfg=cfg, n_null=int(task["n_null"]), register=False, reference_check=False,
                     robustness=False, lessons=False, val_budget=False)
        c = p.cache
        out = []
        for lh, spec in cr.specs.items():
            row = p.prim.loc[lh].to_dict()
            g2 = p.g2(spec, row, p.costs["primary"])
            rb = robustness_suite(spec, c.m1, p.start, p.end, get_cost(p.costs["primary"]), ctx=c.ctx,
                                  checks=("regime", "alpha"), thresholds=cfg.get("robustness"))
            reg = _trim_regime(rb["checks"]["regime"])
            out.append({"seed": task["seed"], "market": task["market"], "val_regime": vr, "logic_hash": lh,
                        "filtered": bool(spec.filters), "sharpe_w": float(row["sharpe_w"]),
                        "dev_total_return": rb["base"]["total_return"],
                        "g2_p": g2.get("p_value"), "g2_pass": bool(g2.get("ok_positive") and g2.get("ok_null")),
                        "alpha_passed": rb["checks"]["alpha"]["passed"], "alpha_t": rb["checks"]["alpha"].get("alpha_t"),
                        "beta": rb["checks"]["alpha"].get("beta"), "regime": reg,
                        "regime_pass": {m: bool(regime_verdict(reg, rcheck_thresholds(cfg, m))) for m in RCHECK_MODES}})
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_beta_probe(n_regime_null: int = 60, n_std_null: int = 30, n_null: int = 500, jobs: int = 4,
                   layout: Layout = FULL_LAYOUT, progress=print) -> dict:
    """귀무 국면 시장 n_regime_null 개(seed 11000.. 30개 + 11200.. — rcheck_full_config 귀무와 같은 시장) + 표준 귀무."""
    seeds = [11_000 + i for i in range(min(30, n_regime_null))] + [11_200 + i for i in range(max(0, n_regime_null - 30))]
    tasks = [{"layout": asdict(layout), "seed": s, "market": "regime", "val_regime": REGIMES[s % 3], "n_null": n_null}
             for s in seeds]
    tasks += [{"layout": asdict(layout), "seed": 9_000 + i, "market": "standard", "n_null": n_null}
              for i in range(n_std_null)]
    rows = []
    pool = ProcessPoolExecutor(max_workers=jobs, mp_context=get_context("spawn")) if jobs > 1 else None
    try:
        it = pool.map(beta_probe_market, tasks) if pool else map(beta_probe_market, tasks)
        for i, r in enumerate(it, start=1):
            rows += r
            progress(f"[beta] {i}/{len(tasks)} seed={tasks[i - 1]['seed']}: "
                     + ", ".join(f"{'F' if x['filtered'] else 'U'} ret={x['dev_total_return']:+.2f} g2={int(x['g2_pass'])} "
                                 f"a={int(bool(x['alpha_passed']))} r={int(x['regime_pass']['current'])}" for x in r))
    finally:
        if pool:
            pool.shutdown()
    return {"rows": rows, "summary": summarize_beta_probe(rows), "n_null": n_null}


def summarize_beta_probe(rows: list[dict]) -> dict:
    """귀무 후보 중 (Dev 총수익 > 0) 에서 각 방어선 통과율과 조합: G2, 알파, 국면 검사 방식별, G2∧알파∧국면."""
    out = {}
    for name, sel in (("all", rows), ("dev_ret_pos", [r for r in rows if r["dev_total_return"] > 0]),
                      ("dev_ret_pos_filtered", [r for r in rows if r["dev_total_return"] > 0 and r["filtered"]])):
        n = len(sel)
        if not n:
            continue
        row = {"n": n}
        for k, f in (("g2", lambda r: r["g2_pass"]), ("alpha", lambda r: bool(r["alpha_passed"])),
                     ("g2_alpha", lambda r: r["g2_pass"] and bool(r["alpha_passed"]))):
            x = sum(bool(f(r)) for r in sel)
            row[k] = (x, n, wilson(x, n))
        for m in RCHECK_MODES:
            x = sum(r["regime_pass"][m] for r in sel)
            y = sum(r["regime_pass"][m] and bool(r["alpha_passed"]) for r in sel)
            z = sum(r["regime_pass"][m] and bool(r["alpha_passed"]) and r["g2_pass"] for r in sel)
            row[f"regime_{m}"] = (x, n, wilson(x, n))
            row[f"alpha_regime_{m}"] = (y, n, wilson(y, n))
            row[f"g2_alpha_regime_{m}"] = (z, n, wilson(z, n))
        out[name] = row
    return out


def rcheck_runner_from_results(runs: list[dict], value_to_mode: dict, g4_variant: str = "current"):
    """--rcheck 결과(같은 코드·같은 seed)를 params.criteria.propose_criterion(study="regime") 의 runner 로.
    value_to_mode = {후보 값: RCHECK_MODES 이름} (예: {"pnl_share": "current", "dominant_alpha": "dom_alpha"}).
    레코드의 any_validated/star_validated 를 그 방식의 사후 판정으로 바꿔 넘긴다. 없는 seed 면 KeyError."""
    from btc_quant.params.criteria import _vname, regime_runs_to_value_runs
    idx = {(int(r["seed"]), r["kind"]): r for r in runs if r["g4_variant"] == g4_variant}

    def run(tasks: list[dict]) -> list[dict]:
        out = []
        for t in tasks:
            r = idx[(int(t["seed"]), t["kind"])]
            for v, mode in value_to_mode.items():
                o = r["rcheck_modes"][mode]
                out.append({**r, "variant": _vname(v), "any_validated": o["any_validated"],
                            "star_validated": o["star_validated"]})
        return regime_runs_to_value_runs(out, list(value_to_mode))
    return run


def rcheck_tables_markdown(result: dict) -> str:
    rows = result["summary"]["rows"]
    L = ["| G4 최근 조건 | 국면 검사 | 오탐 (귀무 전체) | 귀무 국면 | 표준 귀무 | 하락 Val 귀무 | 검정력 S*_f (Sharpe 2) | "
         "└ 상승 Val | └ (ii') | └ (iii) | 무필터 S* (ii) |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]

    def cell(r, k):
        return f"{r[k + '_x']}/{r[k + '_n']} = {_pct(r[k])}{_ci(r[k + '_ci'])}" if r[k + "_n"] else "–"
    for r in rows:
        L.append(f"| {r['g4_variant']} | {r['regime_mode']} | {cell(r, 'fpr')} | {cell(r, 'fpr_regime_null')} | "
                 f"{cell(r, 'fpr_std_null')} | {cell(r, 'fpr_bear_val')} | {cell(r, 'power')} | "
                 f"{cell(r, 'power_bull_val')} | {cell(r, 'power_regime_cond_f')} | {cell(r, 'power_regime_filter')} | "
                 f"{cell(r, 'power_regime_cond')} |")
    d = result["summary"].get("diag") or {}
    if d:
        L += ["", "조건부 진단 (G4 current 실행의 상위 후보 전부, Dev 견고성 검사 — Dev·Val 관문과 무관):", "",
              "| 후보 군 | 조건 | 후보 수 | 알파 통과 | " + " | ".join(f"국면 통과 {m}" for m in RCHECK_MODES)
              + " | 국면 검사만 막음 (current) |",
              "|---|---|---|---|" + "---|" * len(RCHECK_MODES) + "---|"]
        for key, r in d.items():
            grp, cond = key.split("|")
            L.append(f"| {grp} | {cond} | {r['n']} | {_pct(r['alpha_pass'])} | "
                     + " | ".join(f"{_pct(r[f'regime_pass_{m}'])}{_ci(r[f'regime_pass_{m}_ci'])}"
                                  for m in RCHECK_MODES)
                     + f" | {_pct(r['regime_only_block_current'])} |")
    inc = result["summary"].get("inconsistent")
    L += ["", f"사후 판정 일관성(국면 검사 current 사후 판정 = 파이프라인 라벨): 불일치 {len(inc or [])}건"]
    return "\n".join(L)


def run_sequence(task: dict) -> dict:
    """누적 원장: 한 시장·한 저장소(한 원장, 한 family)에서 task["sequence"] 캠페인을 차례로 실행.
    시도 수(N_family → G5 DSR 의 N)와 Val 사용(k → 누적 Holm 문턱)이 쌓인다."""
    from .campaign import ResearchEnv

    t0 = time.time()
    layout = Layout(**task["layout"])
    tmp = Path(tempfile.mkdtemp(prefix="calibcum_"))
    try:
        make_root(tmp, layout, task["seed"], task["k"], task.get("plant_lookback", PLANT_LOOKBACK),
                  task.get("scenario", "5m"))
        env = ResearchEnv.at(tmp)
        steps = []
        for pos, grid in enumerate(task["sequence"], start=1):
            r = _campaign_record(env, task, grid, family="calib_cum")
            r["pos"] = pos
            steps.append(r)
        last = steps[-1]
        rec = {**{k: task[k] for k in ("kind", "strength", "k", "seed")}, "grid": "cum", "steps": steps,
               "program_any_validated": any(s["any_validated"] for s in steps),
               "program_any_g6_passed": any(s["any_g6_passed"] for s in steps),
               "first_validated_pos": next((s["pos"] for s in steps if s["any_validated"]), None),
               "n_validated_total": sum(s["n_validated"] for s in steps),
               "val_used_total": max([s["val_used"] or 0 for s in steps]),
               **{f"last_{k}": last[k] for k in ("star_validated", "star_g6_passed", "star_dev_passed",
                                                 "star_outcome", "star_val_k", "star_val_p",
                                                 "star_val_holm_threshold", "star_g5", "n_family_trials",
                                                 "n_trials_dsr", "any_validated")},
               "runtime_sec": round(time.time() - t0, 1)}
        return rec
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================== 연구
def build_tasks(cfg: StudyConfig, k_for: dict, k_breakeven: float = 0.0) -> list[dict]:
    base = {"layout": asdict(cfg.layout), "n_null": cfg.n_null, "top_k": cfg.top_k,
            "override_null_runs": cfg.override_null_runs, "plant_lookback": cfg.plant_lookback,
            "scenario": cfg.scenario}
    tasks, seed = [], cfg.seed0
    if cfg.cumulative:
        # 주 실험과 같은 seed 배정 순서 → 같은 시장 (첫 n 개)
        for s in cfg.strengths:
            n_main = cfg.n_null_markets if s <= 0 else cfg.n_planted_markets
            n = int(cfg.cumulative.get(s, cfg.cumulative.get(float(s), 0)))
            for i in range(n_main):
                if i < n:
                    tasks.append({**base, "kind": "null" if s <= 0 else "planted", "strength": float(s),
                                  "k": float(k_for[s]), "grid": "cum", "seed": seed, "study": "cumulative",
                                  "sequence": list(cfg.cum_sequence)})
                seed += 1
        return tasks
    for s in cfg.strengths:
        n = cfg.n_null_markets if s <= 0 else cfg.n_planted_markets
        for _ in range(n):
            tasks.append({**base, "kind": "null" if s <= 0 else "planted", "strength": float(s),
                          "k": float(k_for[s]), "grid": cfg.grid, "seed": seed, "study": "main"})
            seed += 1
    for _ in range(cfg.n_breakeven_markets):
        tasks.append({**base, "kind": "breakeven", "strength": 0.0, "k": float(k_breakeven), "grid": cfg.grid,
                      "seed": seed, "study": "main"})
        seed += 1
    for g in cfg.decay_grids:
        for _ in range(cfg.n_decay_markets):
            tasks.append({**base, "kind": "planted", "strength": float(cfg.decay_strength),
                          "k": float(k_for[cfg.decay_strength]), "grid": g, "seed": seed, "study": "decay"})
            seed += 1
    if cfg.decay_grids and cfg.n_null_decay:
        g = max(cfg.decay_grids, key=lambda x: int(x[1:]))
        for _ in range(cfg.n_null_decay):
            tasks.append({**base, "kind": "null", "strength": 0.0, "k": 0.0, "grid": g, "seed": seed,
                          "study": "decay"})
            seed += 1
    return tasks


def wilson(x: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = x / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def summarize_runs(runs: list[dict]) -> dict:
    out: dict = {"by_strength": [], "by_grid": [], "cumulative": []}
    cum = [r for r in runs if r.get("grid") == "cum"]
    runs = [r for r in runs if r.get("grid") != "cum"]
    df = pd.DataFrame(runs)
    if not df.empty:
        for (grid, kind, s), g in df.groupby(["grid", "kind", "strength"], sort=True):
            n = len(g)
            row = {"grid": grid, "kind": kind, "n_specs": int(g["n_specs"].iloc[0]), "strength": float(s),
                   "n_markets": n,
                   "star_sharpe_dev_mean": float(g["star_sharpe_dev"].mean()),
                   "star_sharpe_dev_sd": float(g["star_sharpe_dev"].std(ddof=1)) if n > 1 else float("nan"),
                   "star_rank_median": float(g["star_rank"].median())}
            for col in ("any_validated", "star_validated", "any_dev_survivor", "star_dev_passed",
                        "any_g6_passed", "star_g6_passed"):
                if col not in g:
                    continue
                x = int(g[col].sum())
                row[col] = x / n
                row[col + "_ci"] = wilson(x, n)
            row["star_outcome"] = {str(k): int(v) for k, v in g["star_outcome"].value_counts().items()}
            row["star_is_candidate"] = float(g["star_is_candidate"].mean())
            if "star_trades_per_day" in g:
                row["star_trades_per_day_mean"] = float(g["star_trades_per_day"].mean())
            if "robustness_failed_checks" in g:
                cnt: dict[str, int] = {}
                for lst in g["robustness_failed_checks"]:
                    for f in lst or []:
                        cnt[f] = cnt.get(f, 0) + 1
                row["robustness_failed_checks"] = cnt
            row["mean_runtime_sec"] = float(g["runtime_sec"].mean())
            out["by_strength"].append(row)
    if cum:
        dc = pd.DataFrame(cum)
        for (kind, s), g in dc.groupby(["kind", "strength"], sort=True):
            n = len(g)
            row = {"kind": kind, "strength": float(s), "n_markets": n}
            for col in ("program_any_validated", "last_star_validated", "last_star_g6_passed",
                        "last_star_dev_passed", "last_any_validated"):
                x = int(g[col].sum())
                row[col] = x / n
                row[col + "_ci"] = wilson(x, n)
            row["last_star_outcome"] = {str(k): int(v) for k, v in g["last_star_outcome"].value_counts().items()}
            row["val_used_mean"] = float(g["val_used_total"].mean())
            row["n_validated_total"] = int(g["n_validated_total"].sum())
            row["first_validated_pos"] = [x for x in g["first_validated_pos"] if x is not None]
            row["last_n_trials_dsr_mean"] = float(pd.to_numeric(g["last_n_trials_dsr"]).mean())
            row["last_n_family_trials_mean"] = float(pd.to_numeric(g["last_n_family_trials"]).mean())
            ks = pd.to_numeric(g["last_star_val_k"], errors="coerce").dropna()
            row["last_star_val_k_median"] = float(ks.median()) if len(ks) else float("nan")
            # 캠페인 위치별: 아무 후보 validated 비율, Dev 생존, Val 누적 사용
            per = {}
            for steps in g["steps"]:
                for st in steps:
                    d = per.setdefault(st["pos"], {"grid": st["grid"], "validated": 0, "dev": 0, "val_used": [],
                                                   "n_trials_dsr": []})
                    d["validated"] += int(st["any_validated"])
                    d["dev"] += int(st["any_dev_survivor"])
                    d["val_used"].append(st["val_used"] or 0)
                    d["n_trials_dsr"].append(st["n_trials_dsr"])
            row["by_pos"] = {p: {"grid": d["grid"], "any_validated": d["validated"] / n,
                                 "any_dev_survivor": d["dev"] / n, "val_used_mean": float(np.mean(d["val_used"])),
                                 "n_trials_dsr_mean": float(np.mean(d["n_trials_dsr"]))}
                             for p, d in sorted(per.items())}
            row["mean_runtime_sec"] = float(g["runtime_sec"].mean())
            out["cumulative"].append(row)
    return out


def run_study(cfg: StudyConfig, progress=print, edge: dict | None = None) -> dict:
    """edge: 이미 잰 엣지 보정표(같은 설정의 이전 결과 'edge_map')를 재사용하면 파일럿 생략."""
    t0 = time.time()
    pool = None
    if cfg.jobs > 1:
        pool = ProcessPoolExecutor(max_workers=cfg.jobs, mp_context=get_context("spawn"))
    try:
        if edge is None:
            progress(f"[calib] pilot edge map ({len(cfg.pilot_ks)} k × {cfg.pilot_seeds} seeds × "
                     f"{cfg.pilot_years}y, {cfg.scenario})")
            edge = pilot_edge_map(cfg, pool)
        else:
            edge = {**edge, "k_for_strength": {float(k): float(v) for k, v in edge["k_for_strength"].items()},
                    "reused": True}
        progress(f"[calib] k for strengths: {edge['k_for_strength']}  (pilot SR {np.round(edge['sharpe_mean'], 2)})")
        tasks = build_tasks(cfg, edge["k_for_strength"], edge["k_breakeven"])
        progress(f"[calib] {len(tasks)} markets")
        runs = []
        fn = run_sequence if cfg.cumulative else run_market
        it = pool.map(fn, tasks) if pool else map(fn, tasks)
        for i, (t, r) in enumerate(zip(tasks, it), start=1):
            r["study"] = t["study"]
            runs.append(r)
            if cfg.cumulative:
                progress(f"[calib] {i}/{len(tasks)} cum {r['kind']} s={r['strength']} seed={r['seed']}: "
                         f"program_validated={r['n_validated_total']} first_pos={r['first_validated_pos']} "
                         f"last S*={r['last_star_outcome']} k={r['last_star_val_k']} "
                         f"N_dsr={r['last_n_trials_dsr']} ({r['runtime_sec']}s)")
            else:
                progress(f"[calib] {i}/{len(tasks)} {r['kind']} s={r['strength']} {r['grid']} seed={r['seed']}: "
                         f"validated={r['n_validated']} g6={r['n_g6_passed']} star={r['star_outcome']} "
                         f"rank={r['star_rank']} SR*={r['star_sharpe_dev']:.2f} tpd={r['star_trades_per_day']:.1f} "
                         f"outcomes={r['outcomes']} ({r['runtime_sec']}s)")
    finally:
        if pool:
            pool.shutdown()
    from .campaign import run_params
    try:
        pv = run_params()
    except Exception as e:  # noqa: BLE001
        pv = {"error": str(e)}
    return {"config": cfg.to_record(), "edge_map": edge, "runs": runs, "summary": summarize_runs(runs),
            "runtime_sec": round(time.time() - t0, 1), "created": pd.Timestamp.now("UTC").isoformat(),
            "gate_config_version": G.load_gate_config().get("version"),
            "params_version": pv.get("params_version"), "cost_versions": pv.get("cost_versions")}


# ============================================================== 리포트 (표 자동 생성; 해석은 사람이/AI가 덧붙임)
def _pct(x: float) -> str:
    return "—" if math.isnan(x) else f"{100 * x:.0f}%"


def _ci(t) -> str:
    return "" if t[0] != t[0] else f" [{100 * t[0]:.0f}–{100 * t[1]:.0f}]"


def _label(r: dict) -> str:
    return {"null": "0 (귀무: 무작위 보행)", "breakeven": "0 (귀무: 손익분기 엣지)"}.get(r["kind"], f"{r['strength']:g}")


def _ff(d: dict) -> str:
    return ", ".join(f"{k}:{v}" for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))


def cumulative_markdown(result: dict) -> str:
    rows = result["summary"].get("cumulative") or []
    if not rows:
        return ""
    seq = result["config"].get("cum_sequence") or list(CUM_SEQUENCE)
    L = [f"누적 원장 (시장마다 한 원장·한 family 에서 캠페인 {len(seq)}개: {' → '.join(seq)}; 마지막 g12 가 S* 포함):", "",
         (f"| 시장 | 시장 수 | 프로그램 전체: 아무 후보 validated ({len(seq)}캠페인 중 하나라도) | 마지막(g12) S* validated | "
          f"마지막 S* G6 통과(견고성 전) | 마지막 S* Dev 생존 | 마지막 S* 결과 | Val 사용(평균, {len(seq)}캠페인 후) | "
          "S* 의 Val k(중앙값) | G5 N(DSR, 마지막) |"),
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lab = "0 (귀무)" if r["kind"] == "null" else f"{r['strength']:g}"
        L.append(f"| {lab} | {r['n_markets']} | {_pct(r['program_any_validated'])}{_ci(r['program_any_validated_ci'])} | "
                 f"{_pct(r['last_star_validated'])}{_ci(r['last_star_validated_ci'])} | "
                 f"{_pct(r['last_star_g6_passed'])} | {_pct(r['last_star_dev_passed'])} | "
                 f"{_ff(r['last_star_outcome'])} | {r['val_used_mean']:.1f} | {r['last_star_val_k_median']:g} | "
                 f"{r['last_n_trials_dsr_mean']:.1f} |")
    L += ["", "캠페인 위치별 (아무 후보 validated 비율 / Dev 생존 비율 / 누적 Val 사용 평균 / G5 N 평균):", "",
          "| 위치 | 격자 | " + " | ".join(("귀무" if r["kind"] == "null" else f"SR {r['strength']:g}") for r in rows) + " |",
          "|---|---|" + "---|" * len(rows)]
    poss = sorted({int(p) for r in rows for p in r["by_pos"]})
    for p in poss:
        cells = []
        grid = ""
        for r in rows:
            d = r["by_pos"].get(p) or r["by_pos"].get(str(p))
            grid = d["grid"]
            cells.append(f"{_pct(d['any_validated'])} / {_pct(d['any_dev_survivor'])} / {d['val_used_mean']:.1f} / "
                         f"{d['n_trials_dsr_mean']:.1f}")
        L.append(f"| {p} | {grid} | " + " | ".join(cells) + " |")
    return "\n".join(L)


def tables_markdown(result: dict) -> str:
    rows = result["summary"]["by_strength"]
    cfg = result["config"]
    if result["summary"].get("cumulative"):
        out = cumulative_markdown(result)
    else:
        order = {"null": 0, "breakeven": 1, "planted": 2}
        main = sorted([r for r in rows if r["grid"] == cfg["grid"]], key=lambda r: (order[r["kind"]], r["strength"]))
        L = [("| 목표 연 Sharpe (S*) | 측정 Dev Sharpe (평균±sd) | S* 거래/일 | 시장 수 | 오탐/검정력: 아무 후보 validated | "
              "S* validated | S* G6 통과(견고성 전) | S* Dev 생존(G0~G5) | S* 순위 중앙값 | S*(또는 같은 군집 대표) 결과 |"),
             "|---|---|---|---|---|---|---|---|---|---|"]
        for r in main:
            L.append(f"| {_label(r)} | "
                     f"{r['star_sharpe_dev_mean']:.2f} ± {r['star_sharpe_dev_sd']:.2f} | "
                     f"{r.get('star_trades_per_day_mean', float('nan')):.1f} | {r['n_markets']} | "
                     f"{_pct(r['any_validated'])}{_ci(r['any_validated_ci'])} | "
                     f"{_pct(r['star_validated'])}{_ci(r['star_validated_ci'])} | "
                     f"{_pct(r.get('star_g6_passed', float('nan')))} | "
                     f"{_pct(r['star_dev_passed'])} | {r['star_rank_median']:.0f} | {_ff(r['star_outcome'])} |")
        out = "\n".join(L)
    dec = [r for r in rows if r["kind"] != "breakeven" and (r["grid"] != cfg["grid"] or (
        cfg["decay_grids"] and r["strength"] == cfg["decay_strength"]))]
    if cfg["decay_grids"]:
        L = ["", f"격자 크기별 (목표 Sharpe {cfg['decay_strength']:g} 및 귀무):", "",
             ("| 격자 | 스펙 수 | 목표 Sharpe | 시장 수 | 아무 후보 validated | S* validated | S* G6 통과(견고성 전) | "
              "S* Dev 생존 | S* 순위 중앙값 | S*(대표) 결과 |"), "|---|---|---|---|---|---|---|---|---|---|"]
        for r in sorted(dec, key=lambda r: (r["strength"], r["n_specs"])):
            L.append(f"| {r['grid']} | {r['n_specs']} | {r['strength']:g} | {r['n_markets']} | "
                     f"{_pct(r['any_validated'])}{_ci(r['any_validated_ci'])} | "
                     f"{_pct(r['star_validated'])}{_ci(r['star_validated_ci'])} | "
                     f"{_pct(r.get('star_g6_passed', float('nan')))} | {_pct(r['star_dev_passed'])} | "
                     f"{r['star_rank_median']:.0f} | {_ff(r['star_outcome'])} |")
        out += "\n" + "\n".join(L)
    rf = {}
    for r in rows:
        for k, v in (r.get("robustness_failed_checks") or {}).items():
            rf[k] = rf.get(k, 0) + v
    if rf:
        out += "\n\nG6 통과 후 견고성 묶음에서 실패한 검사(후보 수, 모든 시장 합): " + _ff(rf)
    e = result["edge_map"]
    tpd = e.get("trades_per_day") or [float("nan")] * len(e["ks"])
    out += ("\n\n엣지 세기 보정(파일럿, S* 를 " + f"{e['pilot_years']:g}년 × {e['pilot_seeds']} seed 에서 실행"
            + (", 이전 결과 재사용" if e.get("reused") else "") + "):\n\n"
            "| k (σ 배수 드리프트) | S* 연 Sharpe (평균) | S* 거래/일 |\n|---|---|---|\n"
            + "\n".join(f"| {k:g} | {s:.2f} | {t:.1f} |" for k, s, t in zip(e["ks"], e["sharpe_mean"], tpd)))
    out += ("\n\n목표 Sharpe → k: " + ", ".join(f"{float(s):g}→{k:.4f}" for s, k in e["k_for_strength"].items())
            + f"; 손익분기(S* 참 Sharpe 0) k = {e.get('k_breakeven', 0.0):.4f}")
    return out


def write_report(result: dict, path: str | Path, interpretation: str = "") -> Path:
    cfg = result["config"]
    lay = cfg["layout"]
    plb = cfg.get("plant_lookback", PLANT_LOOKBACK)
    shape = "칼날: S* 의 lookback 48 돌파에만 엣지" if plb == PLANT_LOOKBACK else "완만: lookback 24 돌파 전체에 엣지"
    md = f"""# 파이프라인 자체 검증 (calibration study)

생성: {result['created']} · 모드 `{cfg['mode']}` · 소요 {result['runtime_sec']:.0f}초 · 관문 설정 v{result['gate_config_version']}
· 코드 `src/btc_quant/research/calibration_study.py`

## 설정
- 합성 시장: 시간 GARCH(1,1)(α={GARCH_ALPHA}, β={GARCH_BETA}), t({T_DF:g}) 혁신, 일중 계절성(진폭 {SEASON_AMP}),
  연 변동성 {ANNUAL_VOL:.0%}. 심은 엣지 = donchian_breakout(48) 돌파 후 60분 동안 σ 비례 드리프트.
- 분할: Dev {lay['dev_start']} ~ {lay['val_start']} ({(pd.Timestamp(lay['val_start']) - pd.Timestamp(lay['dev_start'])).days / 365.25:.2f}년),
  Val ~ {lay['val_end']}. 잠금 구간은 생성하지 않음.
- 심은 스펙 S* = donchian_breakout(lookback=48), SL {PLANT_EXIT[0]}% / TP {PLANT_EXIT[1]}% / {PLANT_EXIT[2]}분, 무필터.
  "엣지 세기" = S* 의 참 연 Sharpe (coinone_conservative 현재 버전 비용 후, 파일럿으로 보정).
  시나리오 `{cfg.get('scenario', '5m')}`{' (S*_1m = donchian(60) 1분봉, SL 3·ATR / TP 4·ATR / 20분; 드리프트 = 1분 돌파 후 20분)' if cfg.get('scenario') == '1m_atr' else ''}.
- 비용 모델 버전 {result.get('cost_versions')} · 파라미터 {result.get('params_version')}. 파이프라인 = G0~G6(규칙 b, Val 누적 Holm)
  + 견고성 묶음(Dev; 실패 시 validated 아님 → 'R(…)'), 군집 대표 선택.
- 주 격자 `{cfg['grid']}`, 파이프라인 top_k={cfg['top_k']}, G2 귀무 {cfg['n_null']}회
  {'(config G2.null_runs 도 낮춤 — quick 전용)' if cfg['override_null_runs'] else '(config 그대로)'}, 기준 엔진 대조 생략.
- 오탐 = 귀무 시장에서 validated(G0~G6) 후보 ≥ 1. 검정력("S* validated") = S* 또는 S* 와 일간 수익 상관 ≥ 0.5 인
  군집 대표 후보가 validated (파이프라인은 군집마다 대표 1개만 평가). [ ] = 95% Wilson 구간.
- 엣지 형태: plant_lookback={plb} ({shape}).

## 결과

{tables_markdown(result)}
"""
    if interpretation:
        md += "\n" + interpretation.strip() + "\n"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md, encoding="utf-8")
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    m = ap.add_mutually_exclusive_group()
    m.add_argument("--quick", action="store_true")
    m.add_argument("--full", action="store_true")
    m.add_argument("--full-smooth", action="store_true", help="완만한 엣지(plant_lookback=24) 검정력만")
    m.add_argument("--full-1m", action="store_true", help="1분 결정·ATR 청산 단기(20분) 고회전 엣지")
    m.add_argument("--cumulative", action="store_true", help="누적 원장: 시장마다 캠페인 10개를 한 원장에서")
    m.add_argument("--full-low", action="store_true", help="저회전 엣지(1일 보유, 하루 ≤ 1회) 검정력·귀무")
    m.add_argument("--full-vlow", action="store_true", help="초저회전 엣지(3일 보유, 하루 ≤ 0.33회) 검정력·귀무")
    m.add_argument("--regime", action="store_true",
                   help="국면 전환 시장: 현재 관문 vs 노출 인식 변형(exposure_aware) 오탐·검정력 (PLAN D14 초안)")
    ap.add_argument("--regime-quick", action="store_true", help="--regime 의 테스트용 짧은 설정")
    ap.add_argument("--g6-stage", type=int, default=0, metavar="N",
                    help="귀무 국면 시장 N 개의 G6 단계 조건부 진단(Dev 관문 생략, 현재 vs 변형)")
    ap.add_argument("--rcheck", action="store_true",
                    help="롱 온리 기준 2종 보정: 견고성 국면 검사 방식 × G4 최근 12개월 변형 (국면·표준 귀무 시장)")
    ap.add_argument("--rcheck-quick", action="store_true", help="--rcheck 의 테스트용 짧은 설정")
    ap.add_argument("--rcheck-variants", default=None,
                    help="--rcheck 파이프라인 변형 목록(쉼표; 기본 G4_RECENT_VARIANTS, 추가 RCHECK_EXTRA_VARIANTS)")
    ap.add_argument("--no-diag", action="store_true", help="--rcheck 조건부 진단 생략")
    ap.add_argument("--rcheck-beta", type=int, default=0, metavar="N",
                    help="베타 탐침: 귀무 국면 시장 N 개(+ 표준 귀무 30)에서 저회전 롱 스펙의 G2·알파·국면 검사 조건부 진단")
    ap.add_argument("--val-months", type=int, default=0, metavar="N",
                    help="(기본 꺼짐) Val 을 N 개월로: val_end 고정, Dev 가 그만큼 짧아짐 (Layout.with_val_months)")
    ap.add_argument("--val-end", default=None, help="--val-months 와 함께: Val 끝 날짜(YYYY-MM-DD, 기본 = 실제 분할)")
    ap.add_argument("--markets", type=int, default=0, metavar="N",
                    help="(기본 꺼짐) 세기마다 시장 N 개(귀무 포함), 격자 크기·손익분기 실험 끔")
    ap.add_argument("--strengths", default=None, help="(기본 꺼짐) 목표 Sharpe 목록, 쉼표 (예: 0,1,2,3)")
    ap.add_argument("--edge-from", default=None, help="이전 결과 json 의 edge_map 재사용(같은 설정일 때만)")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    ap.add_argument("--out", default=None, help="markdown report path")
    ap.add_argument("--json", default=None, help="raw result json path")
    a = ap.parse_args(argv)
    if a.g6_stage:
        res = run_g6_stage(a.g6_stage, jobs=a.jobs)
        if a.json:
            Path(a.json).parent.mkdir(parents=True, exist_ok=True)
            Path(a.json).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        print(g6_stage_markdown(res))
        return 0
    if a.rcheck_beta:
        res = run_beta_probe(a.rcheck_beta, jobs=a.jobs)
        if a.json:
            Path(a.json).parent.mkdir(parents=True, exist_ok=True)
            Path(a.json).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        print(json.dumps(res["summary"], indent=1, default=str))
        return 0
    if a.rcheck or a.rcheck_quick:
        ccfg = rcheck_quick_config(a.jobs) if a.rcheck_quick else rcheck_full_config(a.jobs)
        if a.rcheck_variants:
            ccfg.g4_variants = tuple(x.strip() for x in a.rcheck_variants.split(",") if x.strip())
            for v in ccfg.g4_variants:
                rcheck_variant_spec(v)
        if a.no_diag:
            ccfg.diag = False
        edge = json.loads(Path(a.edge_from).read_text(encoding="utf-8"))["edge_map"] if a.edge_from else None
        res = run_rcheck_study(ccfg, edge=edge)
        if a.json:
            Path(a.json).parent.mkdir(parents=True, exist_ok=True)
            Path(a.json).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        print(rcheck_tables_markdown(res))
        return 0
    if a.regime or a.regime_quick:
        rcfg = regime_quick_config(a.jobs) if a.regime_quick else regime_full_config(a.jobs)
        edge = json.loads(Path(a.edge_from).read_text(encoding="utf-8"))["edge_map"] if a.edge_from else None
        res = run_regime_study(rcfg, edge=edge)
        if a.json:
            Path(a.json).parent.mkdir(parents=True, exist_ok=True)
            Path(a.json).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        print(regime_tables_markdown(res))
        return 0
    cfg = (full_config(a.jobs) if a.full else smooth_config(a.jobs) if a.full_smooth
           else m1_config(a.jobs) if a.full_1m else cumulative_config(a.jobs) if a.cumulative
           else low_config(a.jobs, "5m_low") if a.full_low else low_config(a.jobs, "5m_vlow") if a.full_vlow
           else quick_config(a.jobs))
    cfg = apply_overrides(cfg, a.val_months, a.val_end, a.markets,
                          tuple(float(x) for x in a.strengths.split(",")) if a.strengths else None)
    edge = json.loads(Path(a.edge_from).read_text(encoding="utf-8"))["edge_map"] if a.edge_from else None
    res = run_study(cfg, edge=edge)
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    if a.out:
        write_report(res, a.out)
    print(tables_markdown(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
