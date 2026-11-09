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
    for d in (GRIDS, GRIDS_1M, CUM_GRIDS):
        if grid in d:
            return d[grid]
    raise KeyError(grid)


def scenario_spec(scenario: str) -> StrategySpec:
    return PLANTED_SPEC_1M if scenario == "1m_atr" else PLANTED_SPEC


def plant_params(scenario: str, plant_lookback: int = PLANT_LOOKBACK) -> dict:
    """gen_market 의 심기 인자 (plant_lookback, plant_bar, plant_hold)."""
    if scenario == "1m_atr":
        return dict(PLANT_1M)
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


def _engine_m1(df: pd.DataFrame) -> pd.DataFrame:
    from .campaign import prepare_m1
    return prepare_m1(df)


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
    m1 = _engine_m1(gen_market(str(start), str(end), seed, k, **plant_params(scenario, plb)))
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
    df = gen_market(layout.data_start, layout.val_end, seed, k_edge, **plant_params(scenario, plant_lookback))
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
    why = [n for k, n in G6_FLAGS if g.get(k) is False]
    if g.get("val_holm_passed") is False:
        why.append("holm")
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
    from .pipeline import run_pipeline

    t0 = time.time()
    scenario = task.get("scenario", "5m")
    tpl = CampaignTemplate.from_dict(template_for(grid, f"calib_{grid}", family))
    cr = run_campaign(tpl, env=env)
    out = run_pipeline(cr, env=env, top_k=task["top_k"], n_null=task["n_null"], gate_cfg=_gate_cfg(task),
                       register=False, reference_check=False)
    sspec = scenario_spec(scenario)
    ex = sspec.exit
    star = tpl.make_spec(sspec.entry, (), ex.sl, ex.tp, ex.max_hold_min)
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
    ap.add_argument("--edge-from", default=None, help="이전 결과 json 의 edge_map 재사용(같은 설정일 때만)")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    ap.add_argument("--out", default=None, help="markdown report path")
    ap.add_argument("--json", default=None, help="raw result json path")
    a = ap.parse_args(argv)
    cfg = (full_config(a.jobs) if a.full else smooth_config(a.jobs) if a.full_smooth
           else m1_config(a.jobs) if a.full_1m else cumulative_config(a.jobs) if a.cumulative
           else quick_config(a.jobs))
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
