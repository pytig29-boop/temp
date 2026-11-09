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
   긴 시장(여러 seed)에서 primary 비용(coinone_conservative)으로 실행 → k ↦ 연 Sharpe(일간, 비가중) 의
   단조 보간표. 목표 Sharpe 0.5/1/2/3 에 해당하는 k 를 역보간. "엣지 세기" = **S* 의 참 연 Sharpe
   (비용 후, 전 구간 기대값)**.
3. 시장마다 임시 저장소(config/splits.yaml + data/raw) → `run_campaign` (사전등록 격자) →
   `run_pipeline`(top_k, 귀무 n_null, register=False, reference_check=False: 기준 엔진 대조는 통계와 무관하고
   차등 테스트가 따로 보장). 결과에서
   - false positive: 귀무 시장에서 validated(G0~G6 통과) 후보가 하나라도 있음
   - power(exact): 심은 스펙 S* 자체가 validated / power(any): 어떤 후보든 validated
   - dev 생존(G0~G5)도 따로 집계(G6 의 6개월 Val 이 걸러 내는 몫을 분리)
4. 격자 크기별 검정력 감소(시도 비용): 같은 엣지 세기에서 격자 4 / 12 / 48 / 405 스펙.

모드
- quick: 짧은 구간(Dev 8개월·Val 2개월), 시장 2개(귀무 1 + 심은 1), 귀무 100개(→ config G2.null_runs 도
  100 으로 낮춤; 테스트용 구조 점검이지 수치 결론용 아님). 수 초~수십 초.
- full: 실제 분할과 같은 길이(Dev 2021-01~2025-10 = 4.83년, Val 6개월), 귀무 1,000개(config 그대로),
  시장 수십 개, 다중 프로세스. 수십 분. 결과는 research/reports/pipeline_calibration.md.

CLI: `uv run python -m btc_quant.research.calibration_study --full --jobs 4 --out research/reports/pipeline_calibration.md`
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
from btc_quant.backtest.types import COINONE_CONSERVATIVE
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

    def to_record(self) -> dict:
        d = asdict(self)
        d["layout"] = asdict(self.layout)
        return d


def quick_config(jobs: int = 1) -> StudyConfig:
    return StudyConfig("quick", QUICK_LAYOUT, strengths=(0.0, 3.0), n_null_markets=1, n_planted_markets=1,
                       grid="g4", decay_grids=(), decay_strength=3.0, n_decay_markets=0, n_null_decay=0,
                       n_null=100, top_k=2, pilot_ks=(0.0, 0.04, 0.1), pilot_seeds=1, pilot_years=1.0,
                       jobs=jobs, override_null_runs=True)


def full_config(jobs: int = 4) -> StudyConfig:
    return StudyConfig("full", FULL_LAYOUT, strengths=(0.0, 0.5, 1.0, 2.0, 3.0), n_null_markets=20,
                       n_planted_markets=10, grid="g12", decay_grids=("g4", "g48", "g405"),
                       decay_strength=2.0, n_decay_markets=10, n_null_decay=10, n_null=1000, jobs=jobs,
                       n_breakeven_markets=10)


# ============================================================== 합성 시장
def season_profile(amp: float = SEASON_AMP) -> np.ndarray:
    h = np.arange(24)
    s = 1 + amp * np.cos(2 * np.pi * (h - 14) / 24)
    return s / np.sqrt(np.mean(s ** 2))


@njit(cache=True)
def _gen(n1, minute0, seed, k_edge, lookback, hold5, h_target, alpha, beta, season, df_t, wick):
    np.random.seed(seed)
    o = np.empty(n1)
    hi = np.empty(n1)
    lo = np.empty(n1)
    c = np.empty(n1)
    n5 = n1 // 5 + 1
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
        if m % 5 == 4:                 # 5분봉 종료
            t = k // 5
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


def gen_market(start: str, end: str, seed: int, k_edge: float = 0.0) -> pd.DataFrame:
    """[start, end) 1분봉 합성 시장 (start 는 자정 정렬 권장)."""
    idx = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left", name="ts")
    minute0 = int(idx[0].value // 60_000_000_000)
    if minute0 % 5:
        raise ValueError("start must be aligned to 5 minutes")
    h_target = ANNUAL_VOL ** 2 / (365 * 24)
    o, h, lo, c = _gen(len(idx), minute0, int(seed), float(k_edge), PLANT_LOOKBACK, PLANT_HOLD5, h_target,
                       GARCH_ALPHA, GARCH_BETA, season_profile(), T_DF, WICK)
    rng = np.random.default_rng(seed)
    vol = rng.lognormal(0.0, 0.5, len(idx))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": vol,
                         "quote_volume": vol * c}, index=idx)


def _engine_m1(df: pd.DataFrame) -> pd.DataFrame:
    from .campaign import prepare_m1
    return prepare_m1(df)


# ============================================================== 엣지 세기 보정
def planted_sharpe(m1: pd.DataFrame, start, end) -> float:
    res = fast.run_fast(PLANTED_SPEC, m1, COINONE_CONSERVATIVE, start, end)
    return M.sharpe(res.daily_returns)


def _pilot_one(args) -> tuple[float, int, float]:
    k, seed, years = args
    start = pd.Timestamp("2010-01-01", tz="UTC")
    end = start + pd.Timedelta(days=round(365.25 * years))
    m1 = _engine_m1(gen_market(str(start), str(end), seed, k))
    return k, seed, planted_sharpe(m1, start + pd.Timedelta(days=3), end)


def pilot_edge_map(cfg: StudyConfig, pool=None) -> dict:
    """k → S* 연 Sharpe (seed 평균). 단조 보정(누적 최대) 후 목표 Sharpe 를 k 로 역보간."""
    tasks = [(k, cfg.seed0 + 50_000 + s, cfg.pilot_years) for k in cfg.pilot_ks for s in range(cfg.pilot_seeds)]
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
    return {"ks": ks.tolist(), "sharpe_mean": sr.tolist(), "per_seed": [list(r) for r in rows],
            "pilot_years": cfg.pilot_years, "pilot_seeds": cfg.pilot_seeds, "k_for_strength": k_for,
            "k_breakeven": k_be}


# ============================================================== 시장 1개 실행
def template_for(grid: str, name: str) -> dict:
    g = GRIDS[grid]
    return {"name": name,
            "hypothesis": {"text": "calibration study: donchian breakout continuation (synthetic)",
                           "rationale": "pipeline self-validation (PLAN 10b-8)",
                           "success_criteria": "planted spec validated; nothing validated on null markets"},
            "data": {"exchange": "coinone", "symbol": "BTC-KRW", "timeframe": "1m", "aux": []},
            "entry": {"block": "donchian_breakout", "grid": g["entry"]},
            "filters": g["filters"], "exits": g["exits"], "budget": 10_000, "family": f"calib_{grid}"}


def make_root(tmp: Path, layout: Layout, seed: int, k_edge: float) -> Path:
    from btc_quant.data.store import write_ohlcv
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "splits.yaml").write_text(yaml.safe_dump(layout.splits()))
    df = gen_market(layout.data_start, layout.val_end, seed, k_edge)
    write_ohlcv(df, "coinone", "BTC-KRW", "1m", root=tmp / "data" / "raw")
    return tmp


def candidate_outcome(c: dict) -> str:
    """후보 하나의 결과 라벨: validated | G0..G5 (Dev 첫 실패) | G6(sharpe/mdd/mc/holm…)."""
    if str(c.get("status", "")).startswith("validated"):
        return "validated"
    if not c.get("dev_passed"):
        return str(c.get("first_failed"))
    g = c.get("gates", {}).get("G6") or {}
    why = [n for k, n in (("ok_sharpe", "sharpe"), ("ok_stress_mdd", "mdd"), ("ok_mc_mdd", "mc"))
           if g.get(k) is False]
    if g.get("val_holm_passed") is False:
        why.append("holm")
    return f"G6({'+'.join(why) or '?'})"


def run_market(task: dict) -> dict:
    """task: {kind, strength, k, grid, seed, layout, n_null, top_k, override_null_runs}."""
    from .campaign import CampaignTemplate, ResearchEnv, run_campaign
    from .pipeline import run_pipeline

    t0 = time.time()
    layout = Layout(**task["layout"])
    tmp = Path(tempfile.mkdtemp(prefix="calib_"))
    try:
        make_root(tmp, layout, task["seed"], task["k"])
        env = ResearchEnv.at(tmp)
        tpl = CampaignTemplate.from_dict(template_for(task["grid"], f"calib_{task['grid']}"))
        cr = run_campaign(tpl, env=env)
        cfg = G.load_gate_config()
        if task["override_null_runs"]:
            cfg = json.loads(json.dumps(cfg))
            cfg["G2"]["null_runs"] = task["n_null"]
        out = run_pipeline(cr, env=env, top_k=task["top_k"], n_null=task["n_null"], gate_cfg=cfg,
                           register=False, reference_check=False)
        ex = PLANTED_SPEC.exit
        star = tpl.make_spec(PLANTED_SPEC.entry, (), ex.stop_loss_pct, ex.take_profit_pct, ex.max_hold_min)
        lh_star = star.logic_hash()
        ranked = cr.ranked()
        rank_star = int(ranked.index[ranked["logic_hash"] == lh_star][0]) + 1
        prim = cr.primary()
        cands = out["candidates"]
        labels = [candidate_outcome(c) for c in cands]
        validated = [c for c, lab in zip(cands, labels) if lab == "validated"]
        # S* 의 대표: S* 자체가 후보면 그것, 아니면 S* 와 일간 수익 상관이 가장 높은 후보(ρ ≥ 0.5 = 같은 군집 수준)
        rep, rep_corr = None, float("nan")
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
        rec = {
            **{k: task[k] for k in ("kind", "strength", "k", "grid", "seed")},
            "n_specs": len(prim), "n_candidates": len(cands), "n_validated": len(validated),
            "n_dev_survivors": out["n_dev_survivors"],
            "any_validated": bool(validated), "any_dev_survivor": out["n_dev_survivors"] > 0,
            "star_rank": rank_star,
            "star_sharpe_dev": float(prim.at[lh_star, "sharpe"]),
            "star_sharpe_w_dev": float(prim.at[lh_star, "sharpe_w"]),
            "star_is_candidate": bool(rep and rep_corr == 1.0),
            "star_rep_corr": rep_corr,
            "star_dev_passed": bool(rep and rep[0]["dev_passed"]),
            "star_validated": bool(rep and rep[1] == "validated"),
            "star_outcome": "not_selected" if rep is None else rep[1],
            "star_val_sharpe": None if not g6 else g6.get("sharpe"),
            "star_val_p": None if not g6 else g6.get("val_p_value"),
            "outcomes": pd.Series(labels, dtype=object).value_counts().to_dict() if labels else {},
            "k_eff": out["campaign_stats"]["k_eff_clusters"], "pbo": out["campaign_stats"].get("pbo"),
            "runtime_sec": round(time.time() - t0, 1),
        }
        return rec
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================== 연구
def build_tasks(cfg: StudyConfig, k_for: dict, k_breakeven: float = 0.0) -> list[dict]:
    base = {"layout": asdict(cfg.layout), "n_null": cfg.n_null, "top_k": cfg.top_k,
            "override_null_runs": cfg.override_null_runs}
    tasks, seed = [], cfg.seed0
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
    df = pd.DataFrame(runs)
    out: dict = {"by_strength": [], "by_grid": []}
    if df.empty:
        return out
    for (grid, kind, s), g in df.groupby(["grid", "kind", "strength"], sort=True):
        n = len(g)
        row = {"grid": grid, "kind": kind, "n_specs": int(g["n_specs"].iloc[0]), "strength": float(s),
               "n_markets": n,
               "star_sharpe_dev_mean": float(g["star_sharpe_dev"].mean()),
               "star_sharpe_dev_sd": float(g["star_sharpe_dev"].std(ddof=1)) if n > 1 else float("nan"),
               "star_rank_median": float(g["star_rank"].median())}
        for col in ("any_validated", "star_validated", "any_dev_survivor", "star_dev_passed"):
            x = int(g[col].sum())
            row[col] = x / n
            row[col + "_ci"] = wilson(x, n)
        row["star_outcome"] = {str(k): int(v) for k, v in g["star_outcome"].value_counts().items()}
        row["star_is_candidate"] = float(g["star_is_candidate"].mean())
        row["mean_runtime_sec"] = float(g["runtime_sec"].mean())
        out["by_strength"].append(row)
    return out


def run_study(cfg: StudyConfig, progress=print) -> dict:
    t0 = time.time()
    pool = None
    if cfg.jobs > 1:
        pool = ProcessPoolExecutor(max_workers=cfg.jobs, mp_context=get_context("spawn"))
    try:
        progress(f"[calib] pilot edge map ({len(cfg.pilot_ks)} k × {cfg.pilot_seeds} seeds × {cfg.pilot_years}y)")
        edge = pilot_edge_map(cfg, pool)
        progress(f"[calib] k for strengths: {edge['k_for_strength']}  (pilot SR {np.round(edge['sharpe_mean'], 2)})")
        tasks = build_tasks(cfg, edge["k_for_strength"], edge["k_breakeven"])
        progress(f"[calib] {len(tasks)} markets")
        runs = []
        it = pool.map(run_market, tasks) if pool else map(run_market, tasks)
        for i, (t, r) in enumerate(zip(tasks, it), start=1):
            r["study"] = t["study"]
            runs.append(r)
            progress(f"[calib] {i}/{len(tasks)} {r['kind']} s={r['strength']} {r['grid']} seed={r['seed']}: "
                     f"validated={r['n_validated']} star={r['star_outcome']} rank={r['star_rank']} "
                     f"SR*={r['star_sharpe_dev']:.2f} outcomes={r['outcomes']} ({r['runtime_sec']}s)")
    finally:
        if pool:
            pool.shutdown()
    return {"config": cfg.to_record(), "edge_map": edge, "runs": runs, "summary": summarize_runs(runs),
            "runtime_sec": round(time.time() - t0, 1), "created": pd.Timestamp.now("UTC").isoformat(),
            "gate_config_version": G.load_gate_config().get("version")}


# ============================================================== 리포트 (표 자동 생성; 해석은 사람이/AI가 덧붙임)
def _pct(x: float) -> str:
    return "—" if math.isnan(x) else f"{100 * x:.0f}%"


def _ci(t) -> str:
    return "" if t[0] != t[0] else f" [{100 * t[0]:.0f}–{100 * t[1]:.0f}]"


def _label(r: dict) -> str:
    return {"null": "0 (귀무: 무작위 보행)", "breakeven": "0 (귀무: 손익분기 엣지)"}.get(r["kind"], f"{r['strength']:g}")


def tables_markdown(result: dict) -> str:
    rows = result["summary"]["by_strength"]
    cfg = result["config"]
    order = {"null": 0, "breakeven": 1, "planted": 2}
    main = sorted([r for r in rows if r["grid"] == cfg["grid"]], key=lambda r: (order[r["kind"]], r["strength"]))
    L = [("| 목표 연 Sharpe (S*) | 측정 Dev Sharpe (평균±sd) | 시장 수 | 오탐/검정력: 아무 후보 validated | "
          "S* validated | S* Dev 생존(G0~G5) | S* 순위 중앙값 | S*(또는 같은 군집 대표) 결과 |"),
         "|---|---|---|---|---|---|---|---|"]
    for r in main:
        ff = ", ".join(f"{k}:{v}" for k, v in sorted(r["star_outcome"].items(), key=lambda kv: -kv[1]))
        L.append(f"| {_label(r)} | "
                 f"{r['star_sharpe_dev_mean']:.2f} ± {r['star_sharpe_dev_sd']:.2f} | {r['n_markets']} | "
                 f"{_pct(r['any_validated'])}{_ci(r['any_validated_ci'])} | "
                 f"{_pct(r['star_validated'])}{_ci(r['star_validated_ci'])} | "
                 f"{_pct(r['star_dev_passed'])} | {r['star_rank_median']:.0f} | {ff} |")
    out = "\n".join(L)
    dec = [r for r in rows if r["kind"] != "breakeven" and (r["grid"] != cfg["grid"] or (
        cfg["decay_grids"] and r["strength"] == cfg["decay_strength"]))]
    if cfg["decay_grids"]:
        L = ["", f"격자 크기별 (목표 Sharpe {cfg['decay_strength']:g} 및 귀무):", "",
             ("| 격자 | 스펙 수 | 목표 Sharpe | 시장 수 | 아무 후보 validated | S* validated | S* Dev 생존 | "
              "S* 순위 중앙값 | S*(대표) 결과 |"), "|---|---|---|---|---|---|---|---|---|"]
        for r in sorted(dec, key=lambda r: (r["strength"], r["n_specs"])):
            ff = ", ".join(f"{k}:{v}" for k, v in sorted(r["star_outcome"].items(), key=lambda kv: -kv[1]))
            L.append(f"| {r['grid']} | {r['n_specs']} | {r['strength']:g} | {r['n_markets']} | "
                     f"{_pct(r['any_validated'])}{_ci(r['any_validated_ci'])} | "
                     f"{_pct(r['star_validated'])}{_ci(r['star_validated_ci'])} | {_pct(r['star_dev_passed'])} | "
                     f"{r['star_rank_median']:.0f} | {ff} |")
        out += "\n".join(L)
    e = result["edge_map"]
    out += ("\n\n엣지 세기 보정(파일럿, S* 를 " + f"{e['pilot_years']:g}년 × {e['pilot_seeds']} seed 에서 실행):\n\n"
            "| k (σ 배수 드리프트) | S* 연 Sharpe (평균) |\n|---|---|\n"
            + "\n".join(f"| {k:g} | {s:.2f} |" for k, s in zip(e["ks"], e["sharpe_mean"])))
    out += ("\n\n목표 Sharpe → k: " + ", ".join(f"{float(s):g}→{k:.4f}" for s, k in e["k_for_strength"].items())
            + f"; 손익분기(S* 참 Sharpe 0) k = {e.get('k_breakeven', 0.0):.4f}")
    return out


def write_report(result: dict, path: str | Path, interpretation: str = "") -> Path:
    cfg = result["config"]
    lay = cfg["layout"]
    md = f"""# 파이프라인 자체 검증 (calibration study)

생성: {result['created']} · 모드 `{cfg['mode']}` · 소요 {result['runtime_sec']:.0f}초 · 관문 설정 v{result['gate_config_version']}
· 코드 `src/btc_quant/research/calibration_study.py`

## 설정
- 합성 시장: 시간 GARCH(1,1)(α={GARCH_ALPHA}, β={GARCH_BETA}), t({T_DF:g}) 혁신, 일중 계절성(진폭 {SEASON_AMP}),
  연 변동성 {ANNUAL_VOL:.0%}. 심은 엣지 = donchian_breakout(48) 돌파 후 60분 동안 σ 비례 드리프트.
- 분할: Dev {lay['dev_start']} ~ {lay['val_start']} ({(pd.Timestamp(lay['val_start']) - pd.Timestamp(lay['dev_start'])).days / 365.25:.2f}년),
  Val ~ {lay['val_end']}. 잠금 구간은 생성하지 않음.
- 심은 스펙 S* = donchian_breakout(lookback=48), SL {PLANT_EXIT[0]}% / TP {PLANT_EXIT[1]}% / {PLANT_EXIT[2]}분, 무필터.
  "엣지 세기" = S* 의 참 연 Sharpe (coinone_conservative 비용 후, 파일럿으로 보정).
- 주 격자 `{cfg['grid']}`, 파이프라인 top_k={cfg['top_k']}, G2 귀무 {cfg['n_null']}회
  {'(config G2.null_runs 도 낮춤 — quick 전용)' if cfg['override_null_runs'] else '(config 그대로)'}, 기준 엔진 대조 생략.
- 오탐 = 귀무 시장에서 validated(G0~G6) 후보 ≥ 1. 검정력 = S* 자체가 validated. [ ] = 95% Wilson 구간.

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
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    ap.add_argument("--out", default=None, help="markdown report path")
    ap.add_argument("--json", default=None, help="raw result json path")
    a = ap.parse_args(argv)
    cfg = full_config(a.jobs) if a.full else quick_config(a.jobs)
    res = run_study(cfg)
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    if a.out:
        write_report(res, a.out)
    print(tables_markdown(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
