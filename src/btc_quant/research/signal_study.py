"""신호 선별 단계 (PLAN §10b-1): 청산 규칙 없이 진입 신호의 정보 유무·지속 시간을 이벤트 스터디로 판정.

왜: 청산 격자(×80)까지 탐색하면 실질 시도 수가 수백으로 커져 DSR 통과에 연 Sharpe 2~4 가 필요하다.
진입 신호(+필터) 하나당 시도 1회만 쓰는 값싼 선별로 '정보가 있는 신호'만 전략화 단계로 넘긴다.
방법 상세·근거는 docs/SIGNAL_STUDY.md.

이벤트 정의 (ENGINE_SPEC 과 같은 시각 규약, 결정 봉 크기 `bar_minutes` ∈ {1, 5, 15, 60, 240})
- 결정 봉 s (index = 봉 시작 시각) 가 닫히는 시각 t = s + bar_minutes 에 결정.
  트리거 = entry[s] & AND(filters[s]) & complete[s].
- 진입 기준가 = 1분봉 t 의 시가 o_t (1분봉 t 가 없거나 filled 면 이벤트 아님 — 엔진과 동일).
- 지평 H 분 수익 = close(1분봉 t+H−1) / o_t − 1  (= t+H 시각의 종가),
  MFE_H = max(high[t … t+H−1]) / o_t − 1,  MAE_H = min(low[t … t+H−1]) / o_t − 1.
- 최대 지평 창이 데이터 끝을 넘거나 추적 변동성(층화용)이 아직 없는 결정은 제외 (신호·귀무 동일).
- **군집 제거**: 시간순으로 트리거를 훑어, 마지막으로 채택한 이벤트 시각 + min_gap_min 이전의 재트리거는
  버린다(채택 이벤트 간 간격 ≥ min_gap_min). 상태형 진입(연속 True)이 같은 움직임을 n 번 세는 것을 막는다.
  남은 겹침(H > min_gap)은 아래 HAC 표준오차가 처리한다.

통계
- 지평별 평균·중앙값·적중률(>0), 같은 값의 왕복 비용 차감(net = (1+r)(1−c) − 1).
- **겹침 인식 HAC 표준오차**: Var(mean) = n⁻² Σ_i Σ_j w_ij e_i e_j,  w_ij = max(0, 1 − |t_i − t_j|/H)
  (달력 시간 Bartlett 커널, 대역폭 H = 두 수익 창의 겹침 비율 — iid 증분이면 정확한 공분산 구조이고
  삼각 커널은 양의 정부호라 분산이 음수가 되지 않는다).
- **매칭 귀무**: 같은 적격 결정 집합(필터 통과·complete·진입 가능·지평 가용)에서 이벤트와 같은 개수를,
  층(KST 시 × 추적 변동성 3분위)별로 같은 개수씩 무작위 추출 → B 회 → 귀무 평균 분포.
- 지평별 검정통계량 z_H = (신호 평균 − 귀무 중심) / max(HAC SE, 귀무 표준편차), p_H = 1 − Φ(z_H).
  · 귀무 중심이 시간대·변동성 국면 구성에서 오는 기대수익(필터 자체 효과 포함)을 제거하고,
  · 분모의 max 가 신호 이벤트의 시간 군집(같은 추세 구간에 몰린 이벤트 → 양의 상관)을 반영한다.
    귀무 추출은 시간적으로 흩어져 있어 그 분산만 쓰면 군집 신호에 반보수적이다.
  · 정규 꼬리를 쓰는 이유: B=1000 이면 경험적 p 의 하한이 0.001 이라 다중검정 보정 문턱(α/m)을
    표현할 수 없다. 경험적 p(`p_null_emp`) 도 함께 보고한다.
- 신호 p = min(1, k · min_{H∈primary} p_H)  (사전등록된 k ≤ 3 개 주 지평에 대한 Bonferroni).
- 선별 판정(`screen`): 캠페인(+같은 계열의 이전 선별) 신호 p 들에 BH(기본, q=0.10) 또는 Holm 적용,
  기각 & 최소 이벤트 수 & 최적 주 지평에서 primary 비용 순평균 > 0 이면 통과.

다자산 유니버스 (PLAN D15, 템플릿 `universe: [BTC-KRW, XRP-KRW, ETH-KRW]`): 같은 신호 구성을 자산마다 같은 규칙으로
정의하고 이벤트를 풀링한다(`study_events_pooled`). 귀무는 자산별로 매칭(자산마다 자기 적격 집합·층), 신호 구성
하나 = 시도 1회. 유니버스를 생략하면 [data.symbol] (기본 BTC-KRW) — 이전과 같은 계산·같은 원장 기록.

공개 API: study_events, study_events_pooled, study_signal, ScreenTemplate, screen, ScreenResult,
          render_screen_markdown, hac_se_mean, stratified_draws, decluster, bh_reject.
"""
from __future__ import annotations

import dataclasses
import itertools
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy import stats

from btc_quant.backtest.types import CostModel
from btc_quant.evaluation.inference import holm_reject
from btc_quant.strategy.blocks import REGISTRY, Context, Registry
from btc_quant.strategy.spec import MAX_FILTERS, TIMEFRAMES, BlockRef, ExitSpec, StrategySpec, tf_name

DEFAULT_HORIZONS = (5, 15, 30, 60, 120, 240)
DEFAULT_PRIMARY = (30, 60, 120)
MAX_PRIMARY = 3
DEFAULT_MIN_GAP_MIN = 60
# 결정 봉 길이(분) → 기본 (지평, 사전등록 주 지평, 최소 간격). 1m/5m 는 위 기본값 그대로(하위 호환).
# 긴 봉(스윙 연구, PLAN §10): 지평은 봉 1개 이상 ~ 1~2일, 군집 제거 간격 = max(60분, 2·B)
# (상태형 진입이 연속 봉에서 같은 움직임을 거듭 세지 않게; 남은 겹침은 HAC 가 처리).
HORIZON_PRESETS: dict[int, dict] = {
    1: {"horizons": DEFAULT_HORIZONS, "primary": DEFAULT_PRIMARY, "min_gap_min": DEFAULT_MIN_GAP_MIN},
    5: {"horizons": DEFAULT_HORIZONS, "primary": DEFAULT_PRIMARY, "min_gap_min": DEFAULT_MIN_GAP_MIN},
    15: {"horizons": (15, 30, 60, 120, 240, 480), "primary": (60, 120, 240), "min_gap_min": 60},
    60: {"horizons": (60, 120, 240, 480, 1440), "primary": (240, 480, 1440), "min_gap_min": 120},
    240: {"horizons": (240, 480, 1440, 2880), "primary": (480, 1440, 2880), "min_gap_min": 480},
}


def horizon_preset(bar_minutes: int) -> dict:
    """결정 봉 길이 → {horizons, primary, min_gap_min} 기본값. 표에 없는 길이는 max(60, 2·B) 간격·B 배수 지평."""
    bm = int(bar_minutes)
    if bm in HORIZON_PRESETS:
        return dict(HORIZON_PRESETS[bm])
    hs = tuple(bm * k for k in (1, 2, 4, 8, 16))
    return {"horizons": hs, "primary": hs[1:4], "min_gap_min": max(DEFAULT_MIN_GAP_MIN, 2 * bm)}


def hold_step(bar_minutes: int) -> int:
    """제안·자리표시 최대보유 반올림 단위: 5분(1m/5m, 기존) 또는 결정 봉 길이(15m 이상 — 스펙 검증상 B 의 배수)."""
    return 5 if int(bar_minutes) <= 5 else int(bar_minutes)
DEFAULT_N_NULL = 1000
DEFAULT_ALPHA = 0.10
DEFAULT_METHOD = "bh"
DEFAULT_MIN_EVENTS = 300          # = gates.yaml G1.min_trades (이보다 적으면 전략화해도 G1 에서 탈락)
VOL_WINDOW_MIN = 60               # 층화용 추적 실현변동성: 직전 60개 1분 로그수익률 표준편차
N_VOL_BINS = 3
ATR_WINDOW = 14                   # 제안용 ATR(결정 봉, 단순 평균 TR)
SATURATION = 0.9
SL_QUANTILE = 0.75                # 손절 제안: 이벤트 75% 가 정보 지평 안에 닿지 않는 |MAE|/ATR
TP_QUANTILE = 0.50                # 익절 제안: 정보 지평 안 MFE/ATR 중앙값
STRATIFY = ("hour_vol", "hour", "none")
KIND = "signal_screen"            # 원장 runs.metrics.kind
# 원장 기록용 자리표시 청산 (이벤트 스터디는 청산을 쓰지 않음 — 같은 선별의 모든 시도가 같은 값을 가져
# logic_hash 는 진입·필터·결정 봉으로만 갈린다).
SENTINEL_SL_TP = 999.0


# ============================================================== 데이터 준비
def _regular_m1(m1: pd.DataFrame) -> pd.DataFrame:
    """1분 격자가 연속이고 filled 열이 있도록 (이미 regularize 된 입력이면 그대로)."""
    idx = m1.index
    step = pd.Timedelta("1min")
    regular = len(idx) < 2 or bool((idx[1:] - idx[:-1] == step).all())
    if not regular:
        from btc_quant.data.quality import regularize
        m1 = regularize(m1)
    if "filled" not in m1:
        m1 = m1.assign(filled=False)
    return m1


def decision_bars(m1: pd.DataFrame, bar_minutes: int) -> pd.DataFrame:
    """1분봉 → 결정 봉 (index = 봉 시작, complete = 구성 1분봉이 모두 실제 봉).

    bar_minutes=5 는 features.to_5m, ≥ 15 는 features.to_tf 와 동일(엔진과 같은 complete 규칙).
    그 외(1 등)는 같은 규약의 일반 리샘플.
    """
    bar_minutes = int(bar_minutes)
    if bar_minutes < 1:
        raise ValueError("bar_minutes must be ≥ 1")
    if bar_minutes == 5:
        from btc_quant.strategy.features import to_5m
        return to_5m(m1)
    from btc_quant.strategy.features import LONG_BAR_MIN, to_tf
    if bar_minutes >= LONG_BAR_MIN:     # 15m/1h/4h: 엔진과 같은 봉·complete 규칙 (데이터 공백만 배제)
        return to_tf(m1, bar_minutes)
    cols = [c for c in ("open", "high", "low", "close", "volume", "quote_volume") if c in m1]
    if bar_minutes == 1:
        b = m1[cols].copy()
        b["complete"] = ~m1["filled"].to_numpy(bool) if "filled" in m1 else True
        return b
    rule = f"{bar_minutes}min"
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
           "quote_volume": "sum"}
    b = m1[cols].resample(rule, label="left", closed="left").agg({c: agg[c] for c in cols})
    if "filled" in m1:
        cnt = m1["close"].resample(rule, label="left", closed="left").count()
        b["complete"] = (~m1["filled"].resample(rule, label="left", closed="left").max().astype(bool)
                         & (cnt == bar_minutes))
    else:
        b["complete"] = m1["close"].resample(rule, label="left", closed="left").count() == bar_minutes
    return b.dropna(subset=["close"])


def _cost_fraction(c) -> float:
    if isinstance(c, CostModel):
        return float(-c.net_return(c.buy_fill(1.0), c.sell_fill(1.0)))
    if isinstance(c, str):
        from btc_quant.research.campaign import get_cost
        return _cost_fraction(get_cost(c))
    return float(c)


def normalize_costs(cost_round_trip) -> dict[str, float]:
    """왕복 비용 → {이름: 비율}. float(0.0013 = 13bp) | 비용모델 이름 | CostModel | 그 목록/딕셔너리.
    첫 항목이 primary (통과 판정의 순평균 조건에 사용)."""
    if cost_round_trip is None:
        return {"zero": 0.0}
    if isinstance(cost_round_trip, dict):
        return {str(k): _cost_fraction(v) for k, v in cost_round_trip.items()}
    if isinstance(cost_round_trip, (list, tuple)):
        out = {}
        for c in cost_round_trip:
            out.update(normalize_costs(c))
        return out
    if isinstance(cost_round_trip, CostModel):
        return {cost_round_trip.name: _cost_fraction(cost_round_trip)}
    if isinstance(cost_round_trip, str):
        return {cost_round_trip: _cost_fraction(cost_round_trip)}
    v = float(cost_round_trip)
    return {f"rt_{v * 1e4:g}bp": v}


# ============================================================== 구성 요소
def decluster(times_ns: np.ndarray, min_gap_min: float) -> np.ndarray:
    """정렬된 트리거 시각(ns) → 채택 마스크. 채택 이벤트 간 간격 ≥ min_gap_min (탐욕적, 시간순)."""
    t = np.asarray(times_ns, dtype=np.int64)
    keep = np.zeros(t.size, dtype=bool)
    if t.size == 0:
        return keep
    gap = round(float(min_gap_min) * 60e9)
    if gap <= 0:
        keep[:] = True
        return keep
    nxt = np.iinfo(np.int64).min
    # 다음 채택 후보를 searchsorted 로 건너뛰므로 O(채택 수 · log n)
    i = 0
    while i < t.size:
        keep[i] = True
        nxt = t[i] + gap
        i = int(np.searchsorted(t, nxt, side="left"))
    return keep


def hac_se_mean(x: np.ndarray, t_min: np.ndarray, bandwidth_min: float) -> float:
    """겹침 인식 표준오차: 달력 시간 Bartlett 커널 w = max(0, 1 − |Δt|/bandwidth). t 는 오름차순(분)."""
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < 2:
        return float("nan")
    t = np.asarray(t_min, dtype=float)
    e = x - x.mean()
    s = float(e @ e)
    bw = float(bandwidth_min)
    if bw > 0:
        for k in range(1, n):
            d = t[k:] - t[:-k]
            w = 1.0 - d / bw
            m = w > 0
            if not m.any():
                break
            s += 2.0 * float((w[m] * e[k:][m] * e[:-k][m]).sum())
    var = max(s, 0.0) / (n * (n - 1))      # n/(n−1) 소표본 보정
    return float(math.sqrt(var))


def stratified_draws(pool_strata: np.ndarray, event_strata: np.ndarray, n_draws: int,
                     rng: np.random.Generator) -> np.ndarray:
    """(n_draws × n_events) 귀무 표본 = 적격 집합(pool) 내 인덱스.

    각 행은 층 g 마다 이벤트의 층 g 개수만큼 pool 의 층 g 에서 추출한다(층 구성 정확히 일치).
    층 크기가 이벤트 수의 20배 이상이면 복원추출(비복원과 사실상 같고 빠름), 아니면 행마다 비복원.
    """
    ps = np.asarray(pool_strata)
    es = np.asarray(event_strata)
    out = np.empty((int(n_draws), es.size), dtype=np.int64)
    col = 0
    for g in np.unique(es):
        cnt = int((es == g).sum())
        mem = np.flatnonzero(ps == g)
        if mem.size < cnt:
            raise ValueError(f"stratum {g}: pool {mem.size} < events {cnt}")
        if mem.size >= 20 * cnt:
            out[:, col:col + cnt] = mem[rng.integers(0, mem.size, (int(n_draws), cnt))]
        else:
            for b in range(int(n_draws)):
                out[b, col:col + cnt] = rng.choice(mem, cnt, replace=False)
        col += cnt
    return out


def bh_reject(p_values, alpha: float) -> np.ndarray:
    """Benjamini–Hochberg step-up (FDR ≤ α, 독립·양의 의존에서)."""
    p = np.asarray(p_values, dtype=float)
    m = p.size
    rej = np.zeros(m, dtype=bool)
    if m == 0:
        return rej
    order = np.argsort(p, kind="stable")
    thr = alpha * np.arange(1, m + 1) / m
    ok = p[order] <= thr
    if ok.any():
        k = int(np.flatnonzero(ok).max())
        rej[order[: k + 1]] = True
    return rej


def correct(p_values, method: str = DEFAULT_METHOD, alpha: float = DEFAULT_ALPHA) -> np.ndarray:
    if method == "bh":
        return bh_reject(p_values, alpha)
    if method == "holm":
        return holm_reject(p_values, alpha)
    raise ValueError("method must be 'bh' or 'holm'")


def _rolling_rv(close: np.ndarray, window: int) -> np.ndarray:
    lr = np.diff(np.log(close), prepend=np.nan)
    return pd.Series(lr).rolling(window, min_periods=window).std().to_numpy()


def _atr_pct(bars: pd.DataFrame, window: int = ATR_WINDOW) -> np.ndarray:
    h, lo, c = bars["high"], bars["low"], bars["close"]
    pc = c.shift(1)
    tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return (tr.rolling(window, min_periods=window).mean() / c).to_numpy()


# ============================================================== 핵심: 이벤트 스터디
def _validate_study_args(horizons, primary_horizons, stratify):
    horizons = sorted({int(h) for h in horizons})
    primary = sorted({int(h) for h in primary_horizons})
    if not horizons or horizons[0] < 1:
        raise ValueError("horizons must be ≥ 1 minute")
    if not primary or len(primary) > MAX_PRIMARY or not set(primary) <= set(horizons):
        raise ValueError(f"primary_horizons must be 1..{MAX_PRIMARY} of horizons {horizons}")
    if stratify not in STRATIFY:
        raise ValueError(f"stratify must be one of {STRATIFY}")
    return horizons, primary


def _event_arrays(m1: pd.DataFrame, bars_index, entry, filter_ok, bm: int, horizons: list[int],
                  min_gap_min: float, start, end, stratify: str) -> dict:
    """한 자산의 적격 결정 집합·층·이벤트(군집 제거 후)·지평 수익 F·MFE/MAE (study_events 의 데이터 단계)."""
    m1 = _regular_m1(m1)
    entry = np.asarray(entry, bool)
    filter_ok = np.asarray(filter_ok, bool)
    bidx = pd.DatetimeIndex(bars_index)
    if not (entry.size == filter_ok.size == len(bidx)):
        raise ValueError("entry / filter_ok / bars_index length mismatch")

    o = m1["open"].to_numpy(float)
    hi = m1["high"].to_numpy(float)
    lo = m1["low"].to_numpy(float)
    c = m1["close"].to_numpy(float)
    filled = m1["filled"].to_numpy(bool)
    n1 = len(m1)
    hmax = horizons[-1]

    t_dec = bidx + pd.Timedelta(minutes=bm)
    pos = m1.index.get_indexer(t_dec)
    rv = _rolling_rv(c, VOL_WINDOW_MIN)
    ok = filter_ok & (pos >= 0)
    if start is not None:
        ok &= t_dec >= pd.Timestamp(start)
    if end is not None:
        ok &= t_dec < pd.Timestamp(end)
    p_safe = np.where(pos >= 0, pos, 0)
    ok &= ~filled[p_safe]
    ok &= p_safe + hmax - 1 < n1
    rv_at = np.where(p_safe >= 1, rv[np.maximum(p_safe - 1, 0)], np.nan)   # t 까지 알려진 정보
    ok &= np.isfinite(rv_at)

    pool = np.flatnonzero(ok)                 # 적격 결정 봉 (bars 위치)
    pool_pos = pos[pool]                      # 1분봉 진입 위치
    pool_t = t_dec[pool]
    H = np.asarray(horizons)
    ref = o[pool_pos]
    F = c[pool_pos[:, None] + H[None, :] - 1] / ref[:, None] - 1.0     # (pool × nH)

    # 층: KST 시 × 변동성 3분위 (분위 경계 = 적격 집합 분포)
    hours = pool_t.tz_convert("Asia/Seoul").hour.to_numpy() if len(pool_t) else np.zeros(0, int)
    rv_pool = rv_at[pool]
    if len(pool):
        edges = np.quantile(rv_pool, np.linspace(0, 1, N_VOL_BINS + 1)[1:-1])
        vbin = np.searchsorted(edges, rv_pool, side="right")
    else:
        edges, vbin = np.zeros(0), np.zeros(0, int)
    if stratify == "hour_vol":
        strata = hours * N_VOL_BINS + vbin
    elif stratify == "hour":
        strata = hours.astype(int)
    else:
        strata = np.zeros(len(pool), dtype=int)

    trig = entry[pool]
    trig_idx = np.flatnonzero(trig)
    keep = decluster(pool_t[trig_idx].as_unit("ns").asi8, min_gap_min)
    ev = trig_idx[keep]                       # pool 내 이벤트 인덱스
    n = int(ev.size)
    ev_t_min = (pool_t[ev].as_unit("ns").asi8 // 60_000_000_000).astype(float)
    ev_times = pool_t[ev]
    out = {"m1": m1, "bidx": bidx, "pool": pool, "F": F, "strata": strata, "ev": ev, "n": n,
           "ev_t_min": ev_t_min, "ev_times": ev_times, "edges": edges,
           "n_raw_triggers": int(trig_idx.size), "n_pool": len(pool)}
    if n >= 2:
        ev_pos = pool_pos[ev]
        mfe = np.empty((n, len(H)))
        mae = np.empty((n, len(H)))
        chunk = max(1, int(4e6 // hmax))
        ar = np.arange(hmax)
        for a in range(0, n, chunk):
            pp = ev_pos[a:a + chunk]
            win = pp[:, None] + ar[None, :]
            cmx = np.maximum.accumulate(hi[win], axis=1)
            cmn = np.minimum.accumulate(lo[win], axis=1)
            r0 = o[pp][:, None]
            mfe[a:a + chunk] = cmx[:, H - 1] / r0 - 1.0
            mae[a:a + chunk] = cmn[:, H - 1] / r0 - 1.0
        out.update(mfe=mfe, mae=mae)
    return out


def _null_means(arr: dict, n_null: int, n_h: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """매칭 귀무: 같은 적격 집합에서 이벤트와 같은 층 구성으로 n_null 회 추출 → 지평별 평균 (n_null × nH)."""
    strata, ev, F = arr["strata"], arr["ev"], arr["F"]
    n = arr["n"]
    ev_strata = strata[ev]
    null_means = np.empty((int(n_null), n_h))
    bchunk = max(1, int(2e7 // max(n * n_h, 1)))
    for b0 in range(0, int(n_null), bchunk):
        b1 = min(int(n_null), b0 + bchunk)
        idx = stratified_draws(strata, ev_strata, b1 - b0, rng)
        null_means[b0:b1] = F[idx].mean(axis=1)
    comp_ev = np.bincount(ev_strata, minlength=int(strata.max()) + 1 if len(strata) else 1)
    return null_means, comp_ev


def _horizon_rows(Fe, ev_t_min, null_means, mfe, mae, horizons, costs, n_null) -> list[dict]:
    n = Fe.shape[0]
    rows = []
    for j, h in enumerate(horizons):
        x = Fe[:, j]
        mean = float(x.mean())
        se = hac_se_mean(x, ev_t_min, h)
        sd_iid = float(x.std(ddof=1) / math.sqrt(n))
        nm = null_means[:, j]
        center = float(nm.mean()) if n_null else 0.0
        nsd = float(nm.std(ddof=1)) if n_null > 1 else 0.0
        denom = max(se, nsd) if np.isfinite(se) else nsd
        excess = mean - center
        z = excess / denom if denom > 0 else 0.0
        row = {"horizon": h, "n": n, "mean": mean, "median": float(np.median(x)),
               "std": float(x.std(ddof=1)), "hit_rate": float((x > 0).mean()),
               "se_iid": sd_iid, "se_hac": se, "t_hac": mean / se if se > 0 else 0.0,
               "p_zero": float(stats.norm.sf(mean / se)) if se > 0 else 1.0,
               "null_mean": center, "null_sd": nsd,
               "null_p05": float(np.percentile(nm, 5)) if n_null else float("nan"),
               "null_p95": float(np.percentile(nm, 95)) if n_null else float("nan"),
               "excess": excess, "z": float(z), "p_value": float(stats.norm.sf(z)),
               "p_null_emp": float((1 + (nm >= mean).sum()) / (n_null + 1)),
               "null_percentile": float(((nm < mean).sum() + 0.5 * (nm == mean).sum()) / max(n_null, 1)),
               "mfe_mean": float(mfe[:, j].mean()), "mfe_median": float(np.median(mfe[:, j])),
               "mae_mean": float(mae[:, j].mean()), "mae_median": float(np.median(mae[:, j])),
               "mfe_q75": float(np.quantile(mfe[:, j], 0.75)),
               "mae_q25": float(np.quantile(mae[:, j], 0.25))}
        for name, cr in costs.items():
            xn = (1.0 + x) * (1.0 - cr) - 1.0
            row[f"mean_net[{name}]"] = float(xn.mean())
            row[f"hit_net[{name}]"] = float((xn > 0).mean())
            row[f"t_net[{name}]"] = float(xn.mean() / se) if se > 0 else 0.0
        rows.append(row)
    return rows


def _signal_summary(rows, horizons, primary, mfe, mae, bm: int, atr_ev) -> dict:
    """신호 p (주 지평 Bonferroni)·정보 지평·프로필·청산 제안. atr_ev = 이벤트별 결정 봉 ATR/close (없으면 None)."""
    jp = [horizons.index(h) for h in primary]
    pps = [rows[j]["p_value"] for j in jp]
    best_j = jp[int(np.argmin(pps))]
    p_signal = float(min(1.0, len(primary) * min(pps)))
    zs = np.array([r["z"] for r in rows])
    exc = np.array([r["excess"] for r in rows])
    info_h = int(horizons[int(np.argmax(zs))]) if zs.max() > 0 else None
    sat_h = None
    if exc.max() > 0:
        sat_h = int(horizons[int(np.flatnonzero(exc >= SATURATION * exc.max())[0])])
    if info_h is None:
        profile = "none (excess ≤ 0 at all horizons)"
    elif info_h == horizons[-1]:
        profile = "rising (z still increasing at the longest horizon — information may persist longer)"
    elif info_h == horizons[0]:
        profile = "decaying (z maximal at the shortest horizon)"
    else:
        profile = "peaked"
    sugg = None
    if info_h is not None:
        k = horizons.index(info_h)
        step = hold_step(bm)
        hold = int(math.ceil(info_h / step) * step)
        sugg = {"max_hold_min": hold, "basis_horizon": info_h,
                "tp_pct_q50_mfe": float(np.quantile(mfe[:, k], TP_QUANTILE) * 100),
                "sl_pct_q75_absmae": float(np.quantile(-mae[:, k], SL_QUANTILE) * 100)}
        if atr_ev is not None:
            a = np.asarray(atr_ev, float)
            good = np.isfinite(a) & (a > 0)
            if good.sum() >= 2:
                sugg.update({
                    "atr_window": ATR_WINDOW, "atr_pct_median": float(np.median(a[good]) * 100),
                    "tp_atr": float(np.quantile(mfe[good, k] / a[good], TP_QUANTILE)),
                    "sl_atr": float(np.quantile(-mae[good, k] / a[good], SL_QUANTILE))})
        sugg["note"] = "suggestions from MFE/MAE quantiles (not optimized); fix before the exit campaign"
    return {"p_signal": p_signal, "best_primary_horizon": int(horizons[best_j]), "info_horizon": info_h,
            "saturation_horizon": sat_h, "profile": profile, "suggestion": sugg}


def _event_atr(arr: dict, atr_pct, bm: int):
    """이벤트별 결정 봉 ATR 비율 (study_events 와 같은 규칙: 없으면 1분봉에서 결정 봉을 만들어 계산)."""
    if atr_pct is None:
        db = decision_bars(arr["m1"], bm)
        j = db.index.get_indexer(arr["bidx"])
        atr_all = _atr_pct(db)
        atr_pct = np.where(j >= 0, atr_all[np.maximum(j, 0)], np.nan)
    return np.asarray(atr_pct, float)[arr["pool"][arr["ev"]]]


def study_events(m1: pd.DataFrame, bars_index: pd.DatetimeIndex, entry, filter_ok, bar_minutes: int,
                 horizons=DEFAULT_HORIZONS, primary_horizons=DEFAULT_PRIMARY,
                 cost_round_trip=0.0, n_null: int = DEFAULT_N_NULL, seed: int = 0,
                 min_gap_min: float = DEFAULT_MIN_GAP_MIN, start=None, end=None,
                 stratify: str = "hour_vol", atr_pct=None, min_events: int = DEFAULT_MIN_EVENTS) -> dict:
    """결정 봉 배열 수준의 이벤트 스터디.

    m1: 1분봉(open/high/low/close[, filled]). bars_index: 결정 봉 시작 시각. entry: 진입 bool 배열,
    filter_ok: 필터 AND & complete bool 배열 (bars_index 와 같은 길이). start/end: 결정 시각 t 범위 [start, end).
    atr_pct: 결정 봉 ATR/close (제안용, 없으면 제안의 ATR 배수 생략).
    """
    t0 = time.time()
    horizons, primary = _validate_study_args(horizons, primary_horizons, stratify)
    costs = normalize_costs(cost_round_trip)
    bm = int(bar_minutes)
    arr = _event_arrays(m1, bars_index, entry, filter_ok, bm, horizons, min_gap_min, start, end, stratify)
    n, ev_times = arr["n"], arr["ev_times"]
    base = {
        "bar_minutes": bm, "horizons": horizons, "primary_horizons": primary,
        "min_gap_min": float(min_gap_min), "n_null": int(n_null), "seed": int(seed),
        "stratify": stratify, "costs": costs, "n_pool": arr["n_pool"],
        "n_raw_triggers": arr["n_raw_triggers"], "n_events": n,
        "event_rate": float(arr["n_raw_triggers"] / arr["n_pool"]) if arr["n_pool"] else 0.0,
        "first_event": ev_times[0].isoformat() if n else None,
        "last_event": ev_times[-1].isoformat() if n else None,
        "vol_tercile_edges": [float(x) for x in arr["edges"]], "min_events": int(min_events),
        "enough_events": n >= int(min_events),
    }
    if n < 2:
        rows = [{"horizon": h, "n": n, "mean": float("nan"), "z": float("nan"), "p_value": 1.0}
                for h in horizons]
        return {**base, "table": rows, "p_signal": 1.0, "best_primary_horizon": primary[0],
                "info_horizon": None, "saturation_horizon": None, "profile": "no events",
                "suggestion": None, "runtime_sec": round(time.time() - t0, 3)}
    rng = np.random.default_rng(seed)
    null_means, comp_ev = _null_means(arr, n_null, len(horizons), rng)
    rows = _horizon_rows(arr["F"][arr["ev"]], arr["ev_t_min"], null_means, arr["mfe"], arr["mae"], horizons,
                         costs, n_null)
    summ = _signal_summary(rows, horizons, primary, arr["mfe"], arr["mae"], bm,
                           _event_atr(arr, atr_pct, bm))
    return {**base, "table": rows, **summ,
            "strata_events": {int(g): int(v) for g, v in enumerate(comp_ev) if v},
            "runtime_sec": round(time.time() - t0, 3)}


def study_events_pooled(assets: list[dict], bar_minutes: int, horizons=DEFAULT_HORIZONS,
                        primary_horizons=DEFAULT_PRIMARY, cost_round_trip=0.0, n_null: int = DEFAULT_N_NULL,
                        seed: int = 0, min_gap_min: float = DEFAULT_MIN_GAP_MIN, start=None, end=None,
                        stratify: str = "hour_vol", min_events: int = DEFAULT_MIN_EVENTS) -> dict:
    """다자산 유니버스(PLAN D15) 풀링 이벤트 스터디 — 같은 신호를 자산마다 같은 규칙으로 정의하고 이벤트를 합친다.

    assets: [{"symbol", "m1", "bars_index", "entry", "filter_ok", "atr_pct"(선택)}].
    - 적격 집합·층(KST 시 × 그 자산의 변동성 3분위)·군집 제거는 **자산마다** (자산 간 군집 제거 없음).
    - 귀무는 **자산별 매칭**: 자산 a 의 이벤트 수·층 구성대로 자산 a 의 적격 집합에서 추출(seed 는 자산마다 다름) →
      풀링 귀무 평균 = Σ_a n_a·귀무평균_a / N (신호 풀링 평균과 같은 가중).
    - HAC 표준오차는 풀링 이벤트를 시각순으로 놓고 달력 시간 커널을 적용 → 같은 시각의 자산 간 이벤트는 가중 1
      (자산 간 동시 상관을 같은 자산 안의 겹침처럼 보수적으로 처리).
    - 신호 p·정보 지평·청산 제안은 풀링 표 기준, 자산별 표·p 는 `per_asset` 에 보고만 한다.
    선별 판정(BH/Holm·최소 이벤트·순평균)은 풀링 결과로 — 신호 구성 하나 = 시도 1회."""
    t0 = time.time()
    horizons, primary = _validate_study_args(horizons, primary_horizons, stratify)
    costs = normalize_costs(cost_round_trip)
    bm = int(bar_minutes)
    arrs, per = [], {}
    for i, a in enumerate(assets):
        arr = _event_arrays(a["m1"], a["bars_index"], a["entry"], a["filter_ok"], bm, horizons, min_gap_min,
                            start, end, stratify)
        arr["symbol"] = a["symbol"]
        arr["atr_ev"] = _event_atr(arr, a.get("atr_pct"), bm) if arr["n"] >= 2 else None
        if arr["n"] >= 2:
            arr["null_means"], arr["comp_ev"] = _null_means(arr, n_null, len(horizons),
                                                            np.random.default_rng([int(seed), i]))
        arrs.append(arr)
    use = [a for a in arrs if a["n"] >= 2]
    N = int(sum(a["n"] for a in arrs))
    n_pool = int(sum(a["n_pool"] for a in arrs))
    n_raw = int(sum(a["n_raw_triggers"] for a in arrs))
    all_times = [t for a in arrs for t in a["ev_times"]]
    base = {
        "bar_minutes": bm, "horizons": horizons, "primary_horizons": primary,
        "min_gap_min": float(min_gap_min), "n_null": int(n_null), "seed": int(seed),
        "stratify": stratify, "costs": costs, "n_pool": n_pool, "n_raw_triggers": n_raw, "n_events": N,
        "event_rate": float(n_raw / n_pool) if n_pool else 0.0,
        "first_event": min(all_times).isoformat() if all_times else None,
        "last_event": max(all_times).isoformat() if all_times else None,
        "min_events": int(min_events), "enough_events": N >= int(min_events),
        "universe": [a["symbol"] for a in arrs], "pooling": "events pooled across assets; null matched per asset",
    }
    for a in arrs:      # 자산별 (보고 전용)
        d = {"n_events": a["n"], "n_pool": a["n_pool"], "n_raw_triggers": a["n_raw_triggers"],
             "vol_tercile_edges": [float(x) for x in a["edges"]]}
        if a["n"] >= 2:
            rws = _horizon_rows(a["F"][a["ev"]], a["ev_t_min"], a["null_means"], a["mfe"], a["mae"], horizons,
                                costs, n_null)
            sm = _signal_summary(rws, horizons, primary, a["mfe"], a["mae"], bm, None)
            d.update(p_signal=sm["p_signal"], info_horizon=sm["info_horizon"],
                     best_primary_horizon=sm["best_primary_horizon"],
                     table=[{k: r.get(k) for k in ("horizon", "mean", "excess", "z", "p_value")} for r in rws])
        else:
            d.update(p_signal=1.0, info_horizon=None, table=[])
        per[a["symbol"]] = d
    if N < 2 or not use:
        rows = [{"horizon": h, "n": N, "mean": float("nan"), "z": float("nan"), "p_value": 1.0} for h in horizons]
        return {**base, "table": rows, "p_signal": 1.0, "best_primary_horizon": primary[0], "info_horizon": None,
                "saturation_horizon": None, "profile": "no events", "suggestion": None, "per_asset": per,
                "runtime_sec": round(time.time() - t0, 3)}
    t_all = np.concatenate([a["ev_t_min"] for a in use])
    order = np.argsort(t_all, kind="stable")
    Fe = np.concatenate([a["F"][a["ev"]] for a in use])[order]
    mfe = np.concatenate([a["mfe"] for a in use])[order]
    mae = np.concatenate([a["mae"] for a in use])[order]
    atr_ev = np.concatenate([a["atr_ev"] for a in use])[order]
    Nu = int(sum(a["n"] for a in use))
    null_means = sum(a["n"] * a["null_means"] for a in use) / Nu
    rows = _horizon_rows(Fe, t_all[order], null_means, mfe, mae, horizons, costs, n_null)
    summ = _signal_summary(rows, horizons, primary, mfe, mae, bm, atr_ev)
    return {**base, "table": rows, **summ, "per_asset": per,
            "strata_events": {a["symbol"]: {int(g): int(v) for g, v in enumerate(a["comp_ev"]) if v} for a in use},
            "runtime_sec": round(time.time() - t0, 3)}


def study_signal(m1: pd.DataFrame, entry_ref: BlockRef, filter_refs=(), bar_minutes: int = 5,
                 horizons=None, cost_round_trip=0.0, n_null: int = DEFAULT_N_NULL,
                 seed: int = 0, ctx: Context | None = None, registry: Registry = REGISTRY,
                 bars: pd.DataFrame | None = None, block_cache: dict | None = None, **kw) -> dict:
    """블록 참조로 이벤트 스터디. m1 은 DataAccess 로 읽은 1분봉(regularize 권장). kw → study_events.
    horizons 를 생략하면 결정 봉 길이의 기본값(`horizon_preset`: 지평·주 지평·최소 간격; 1m/5m 는 기존 기본값)."""
    if horizons is None:
        pre = horizon_preset(bar_minutes)
        horizons = pre["horizons"]
        kw.setdefault("primary_horizons", pre["primary"])
        kw.setdefault("min_gap_min", pre["min_gap_min"])
    m1 = _regular_m1(m1)
    bars = decision_bars(m1, bar_minutes) if bars is None else bars
    ctx = ctx or Context()
    cache = block_cache if block_cache is not None else {}

    def ev(ref):
        k = json.dumps([ref.block, ref.params, int(bar_minutes)], sort_keys=True, default=str)
        if k not in cache:
            cache[k] = registry.evaluate(ref, bars, ctx).to_numpy(bool)
        return cache[k]

    fok = bars["complete"].to_numpy(bool).copy()
    for f in filter_refs:
        fok &= ev(f)
    if "atr_pct" not in kw:
        kw["atr_pct"] = _atr_pct(bars)
    out = study_events(m1, bars.index, ev(entry_ref), fok, bar_minutes, horizons=horizons,
                       cost_round_trip=cost_round_trip, n_null=n_null, seed=seed, **kw)
    out["entry"] = {"block": entry_ref.block, "params": dict(entry_ref.params)}
    out["filters"] = [{"block": f.block, "params": dict(f.params)} for f in filter_refs]
    return out


# ============================================================== 선별 캠페인
@dataclass
class ScreenTemplate:
    """선별 캠페인 템플릿. YAML 예: research/campaigns/README 형식 + docs/SIGNAL_STUDY.md §5."""
    name: str
    hypothesis: dict
    exchange: str
    symbol: str
    timeframe: str
    bar_minutes: int
    signals: list                     # [(BlockGrid entry, [[BlockGrid filter, ...], ...])]
    horizons: list[int]
    primary_horizons: list[int]
    min_gap_min: float
    n_null: int
    seed: int
    min_events: int
    stratify: str
    costs: dict[str, str]             # primary, grade
    method: str
    alpha: float
    budget: int
    family: str | None
    segment: str = "dev"
    raw: dict = field(default_factory=dict)
    aux: list = field(default_factory=list)   # 보조 데이터 (data.aux, 실시간 수신 가능한 것만: campaign.LIVE_AUX)
    universe: tuple = ("BTC-KRW",)            # 자산 유니버스 (D15) — 기본 [data.symbol]

    @property
    def multi_asset(self) -> bool:
        return len(self.universe) > 1

    @classmethod
    def from_yaml(cls, path, registry: Registry = REGISTRY) -> ScreenTemplate:
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")), registry)

    @classmethod
    def from_dict(cls, d: dict, registry: Registry = REGISTRY) -> ScreenTemplate:
        from btc_quant.research.campaign import LIVE_AUX, CampaignError, _block_grid, get_cost, template_universe

        if not isinstance(d, dict):
            raise CampaignError("template must be a mapping")
        allowed = {"name", "hypothesis", "data", "bar_minutes", "signals", "study", "costs",
                   "correction", "budget", "family", "segment", "universe"}
        extra = set(d) - allowed
        if extra:
            raise CampaignError(f"unknown screen template keys {sorted(extra)}")
        name = str(d.get("name") or "").strip()
        if not name or not all(ch.isalnum() or ch in "_-" for ch in name):
            raise CampaignError("name required: [A-Za-z0-9_-]+")
        hyp = d.get("hypothesis") or {}
        for k in ("text", "rationale", "success_criteria"):
            if not str(hyp.get(k) or "").strip():
                raise CampaignError(f"hypothesis.{k} required")
        data = d.get("data") or {}
        aux = list(data.get("aux") or [])
        exchange = str(data.get("exchange", "upbit"))
        bad = [a for a in aux if a not in LIVE_AUX]
        if bad:
            raise CampaignError(f"screen aux {bad} not supported (live-available: {list(LIVE_AUX)})")
        if len(set(aux)) != len(aux) or exchange in aux:
            raise CampaignError(f"screen aux {aux}: duplicates or the target exchange {exchange!r} itself")
        if d.get("segment", "dev") != "dev":
            raise CampaignError("signal screens run on the dev segment only (PLAN §5)")
        universe = template_universe(d)
        sigs_raw = d.get("signals")
        if not isinstance(sigs_raw, list) or not sigs_raw:
            raise CampaignError("signals must be a non-empty list of {entry, filters}")
        sigs = []
        for s in sigs_raw:
            e = _grid_from(s.get("entry"))
            eg = _block_grid(e, "entry", registry)
            fsets = []
            for fs in (s.get("filters") or [[]]):
                fs = fs or []
                if len(fs) > MAX_FILTERS:
                    raise CampaignError(f"filter set has {len(fs)} filters > {MAX_FILTERS}")
                fsets.append([_block_grid(_grid_from(f), "filter", registry) for f in fs])
            sigs.append((eg, fsets))
        st = d.get("study") or {}
        bm = int(d.get("bar_minutes", 5))
        if bm not in TIMEFRAMES.values():
            raise CampaignError(f"bar_minutes must be one of {sorted(TIMEFRAMES.values())} "
                                "(decision bar minutes: 1/5 = D10, 15/60/240 = swing research, PLAN §10)")
        pre = horizon_preset(bm)            # 지평·주 지평·최소 간격 기본값은 결정 봉 길이별
        horizons = sorted({int(h) for h in st.get("horizons", pre["horizons"])})
        primary = sorted({int(h) for h in st.get("primary_horizons", pre["primary"])})
        if not primary or len(primary) > MAX_PRIMARY or not set(primary) <= set(horizons):
            raise CampaignError(f"study.primary_horizons must be 1..{MAX_PRIMARY} of horizons")
        stratify = str(st.get("stratify", "hour_vol"))
        if stratify not in STRATIFY:
            raise CampaignError(f"study.stratify must be one of {STRATIFY}")
        costs = {"primary": "coinone_conservative", "grade": "upbit_conservative", **(d.get("costs") or {})}
        if set(costs) != {"primary", "grade"} or costs["primary"] == costs["grade"]:
            raise CampaignError("costs must be {primary, grade} with distinct models")
        for v in costs.values():
            get_cost(v)
        corr = d.get("correction") or {}
        method = str(corr.get("method", DEFAULT_METHOD))
        if method not in ("bh", "holm"):
            raise CampaignError("correction.method must be bh or holm")
        alpha = float(corr.get("alpha", DEFAULT_ALPHA))
        if not 0 < alpha < 0.5:
            raise CampaignError("correction.alpha must be in (0, 0.5)")
        budget = d.get("budget")
        if not isinstance(budget, int) or budget <= 0:
            raise CampaignError("budget must be a positive integer")
        tpl = cls(name=name, hypothesis={k: hyp[k] for k in ("text", "rationale", "success_criteria")},
                  exchange=exchange, symbol=universe[0],
                  timeframe=str(data.get("timeframe", "1m")), bar_minutes=bm, signals=sigs,
                  horizons=horizons, primary_horizons=primary,
                  min_gap_min=float(st.get("min_gap_min", pre["min_gap_min"])),
                  n_null=int(st.get("n_null", DEFAULT_N_NULL)), seed=int(st.get("seed", 0)),
                  min_events=int(st.get("min_events", DEFAULT_MIN_EVENTS)), stratify=stratify,
                  costs=costs, method=method, alpha=alpha, budget=budget,
                  family=(str(d["family"]) if d.get("family") else None), raw=d, aux=aux, universe=universe)
        configs = tpl.configs()
        keys = [_config_key(e, fs) for e, fs in configs]
        if len(set(keys)) != len(keys):
            raise CampaignError("screen produces duplicate signal configs")
        for e, fs in configs[:1] + configs[-1:]:
            try:
                tpl.trial_spec(e, fs).validate(registry)
            except (ValueError, KeyError) as err:
                raise CampaignError(f"invalid signal config: {err}") from err
        need = sorted({r for e, fs in configs for b in (e, *fs) for r in registry.get(b.block).requires})
        missing = [r for r in need if r not in aux]
        if missing:
            raise CampaignError(f"screen blocks require aux data {missing}; declare data.aux: {need}")
        return tpl

    def configs(self) -> list[tuple[BlockRef, tuple[BlockRef, ...]]]:
        out = []
        for eg, fsets in self.signals:
            for ep in eg.combos():
                e = BlockRef(eg.block, ep)
                for fs in fsets:
                    for fparams in itertools.product(*(f.combos() for f in fs)):
                        out.append((e, tuple(BlockRef(f.block, p) for f, p in zip(fs, fparams))))
        return out

    def n_configs(self) -> int:
        return len(self.configs())

    def check_budget(self) -> int:
        from btc_quant.research.campaign import CampaignBudgetError
        n = self.n_configs()
        if n > self.budget:
            raise CampaignBudgetError(f"screen has {n} signal configs > budget {self.budget}; shrink it")
        return n

    def family_of(self, entry: BlockRef) -> str:
        return self.family or entry.block

    def trial_spec(self, entry: BlockRef, filters: tuple[BlockRef, ...]) -> StrategySpec:
        """원장 기록용 스펙: 자리표시 청산(SL=TP=999%, 최대보유=최장 지평). 결정 봉은 spec 에 timeframe
        필드가 있으면 거기에, 없으면 id 접미사로 남는다."""
        step = hold_step(self.bar_minutes)
        hold = int(math.ceil(max(self.horizons) / step) * step)
        ex = ExitSpec(SENTINEL_SL_TP, SENTINEL_SL_TP, hold)
        kw = {}
        names = {f.name for f in dataclasses.fields(StrategySpec)}
        if "timeframe" in names:
            kw["timeframe"] = tf_name(self.bar_minutes)
        if "universe" in names and len(self.universe) and tuple(self.universe) != ("BTC-KRW",):
            kw["universe"] = tuple(self.universe)
        tmp = StrategySpec("x", entry, ex, tuple(filters), **kw)
        return StrategySpec(f"{self.name}_sig{self.bar_minutes}m_{tmp.logic_hash()[:10]}", entry, ex,
                            tuple(filters), **kw)

    def to_record(self) -> dict:
        return {"kind": KIND, "name": self.name, "bar_minutes": self.bar_minutes,
                "data": {"exchange": self.exchange, "symbol": self.symbol, "timeframe": self.timeframe,
                         **({"aux": list(self.aux)} if self.aux else {}),
                         **({"universe": list(self.universe)} if tuple(self.universe) != ("BTC-KRW",) else {})},
                "signals": [{"entry": {"block": e.block, "grid": e.grid},
                             "filters": [[{"block": f.block, "grid": f.grid} for f in fs] for fs in fsets]}
                            for e, fsets in self.signals],
                "study": {"horizons": self.horizons, "primary_horizons": self.primary_horizons,
                          "min_gap_min": self.min_gap_min, "n_null": self.n_null, "seed": self.seed,
                          "min_events": self.min_events, "stratify": self.stratify},
                "costs": self.costs, "correction": {"method": self.method, "alpha": self.alpha},
                "budget": self.budget, "family": self.family}


def _grid_from(d):
    """{block, grid} 또는 {block, params} (단일 값) → {block, grid}."""
    if not isinstance(d, dict) or "block" not in d:
        return d
    if "params" in d:
        if "grid" in d:
            raise ValueError("give grid or params, not both")
        return {"block": d["block"], "grid": {k: [v] for k, v in (d["params"] or {}).items()}}
    return d


def _config_key(e: BlockRef, fs) -> str:
    return json.dumps([[e.block, e.params], [[f.block, f.params] for f in fs]], sort_keys=True, default=str)


def _label(e: BlockRef, fs) -> str:
    def one(r):
        return r.block + ("(" + ",".join(f"{k}={v}" for k, v in r.params.items()) + ")" if r.params else "")
    return one(e) + "".join(" & " + one(f) for f in fs)


@dataclass
class ScreenResult:
    campaign_id: str
    hypothesis_id: str
    template: ScreenTemplate
    table: pd.DataFrame               # 행 = 신호 구성
    studies: list[dict]
    decision: dict
    dir: Path | None = None

    def passed(self) -> pd.DataFrame:
        return self.table[self.table["passed"]]

    def markdown(self) -> str:
        return render_screen_markdown(self)


def _summary_metrics(st: dict, primary_cost: str) -> dict:
    """원장 runs.metrics 에 넣을 요약 (지평별 핵심 값만)."""
    keep = ("horizon", "mean", "median", "hit_rate", "se_hac", "null_mean", "null_sd", "excess", "z",
            "p_value", "p_null_emp", "mfe_median", "mae_median", f"mean_net[{primary_cost}]")
    return {"kind": KIND, "n_events": st["n_events"], "n_raw_triggers": st["n_raw_triggers"],
            "n_pool": st["n_pool"], "p_signal": st["p_signal"],
            "best_primary_horizon": st["best_primary_horizon"], "info_horizon": st["info_horizon"],
            "saturation_horizon": st["saturation_horizon"], "profile": st["profile"],
            "suggestion": st["suggestion"], "bar_minutes": st["bar_minutes"],
            "primary_horizons": st["primary_horizons"], "min_gap_min": st["min_gap_min"],
            "n_null": st["n_null"], "stratify": st["stratify"],
            "horizons": [{k: r.get(k) for k in keep} for r in st["table"]],
            **({"universe": st["universe"],
                "per_asset": {sym: {k: v.get(k) for k in ("n_events", "p_signal", "info_horizon")}
                              for sym, v in st["per_asset"].items()}} if st.get("per_asset") else {})}


def prior_screen_pvalues(ledger, families: set[str], exclude_campaign: str | None = None) -> list[dict]:
    """같은 계열의 이전 선별 시도(원장 runs, metrics.kind == signal_screen) p 값들."""
    out = []
    for r in ledger.records("runs"):
        m = r.get("metrics") or {}
        if m.get("kind") != KIND or r.get("family") not in families:
            continue
        if exclude_campaign is not None and r.get("campaign_id") == exclude_campaign:
            continue
        if m.get("p_signal") is not None:
            out.append({"campaign_id": r["campaign_id"], "logic_hash": r["logic_hash"],
                        "p_signal": float(m["p_signal"]), "family": r.get("family")})
    # 같은 (캠페인, logic_hash) 중복 제거 (비용별 실행이 여러 줄일 수 있음)
    seen, uniq = set(), []
    for x in out:
        k = (x["campaign_id"], x["logic_hash"])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


def dev_aux_context(env, tpl: ScreenTemplate, symbol: str | None = None) -> Context:
    """선별 템플릿 data.aux 의 Dev 보조 1분봉 → regularize(clean_wicks) → `campaign.aux_context`
    (결정 주기 봉 `to_tf`, ffill 없음 — research/campaign.py·paper 와 같은 구성). symbol: 유니버스 자산(기본 tpl.symbol)."""
    from btc_quant.research.campaign import CampaignError, aux_context, prepare_m1
    aux_m1 = {}
    for a in tpl.aux:
        raw = env.data.read("dev", a, symbol or tpl.symbol, tpl.timeframe)
        if raw.empty:
            raise CampaignError(f"no {a} aux data on dev")
        aux_m1[a] = prepare_m1(raw, a)
    return aux_context(aux_m1, tpl.bar_minutes)


def screen(template, root=None, env=None, include_prior: bool = True, save: bool = True,
           m1: pd.DataFrame | None = None, ctx: Context | None = None,
           progress: Callable[[str], None] | None = None) -> ScreenResult:
    """선별 캠페인: 템플릿 → 사전등록(원장) → Dev 1분봉 → 신호 구성마다 study → 다중검정 → 원장 기록.

    template: ScreenTemplate | dict | YAML 경로. 데이터는 DataAccess.read("dev", …) 로만 읽는다
    (m1 인자는 이미 같은 경로로 읽은 Dev 데이터를 재사용할 때만; 범위는 Dev 경계로 다시 자른다).
    """
    from btc_quant.registry.snapshot import snapshot_id
    from btc_quant.research.campaign import (
        CampaignError,
        ResearchEnv,
        git_commit,
        load_segment,
        record_runs_batch,
        run_param_fields,
        run_params,
    )

    tpl = template if isinstance(template, ScreenTemplate) else (
        ScreenTemplate.from_dict(template) if isinstance(template, dict) else ScreenTemplate.from_yaml(template))
    env = env or ResearchEnv.at(root)
    log = progress or (lambda s: None)
    n_cfg = tpl.check_budget()                      # 원장 쓰기 전에 거부
    configs = tpl.configs()
    multi = len(tpl.universe) > 1
    ctxs = None
    if multi:                                       # 다자산: 자산별 보조 ctx (같은 심볼의 보조 거래소)
        ctxs = {sym: (dev_aux_context(env, tpl, sym) if tpl.aux else None) for sym in tpl.universe}
    elif ctx is None and tpl.aux:                   # data.aux: 캠페인·페이퍼와 같은 구성의 ctx (Dev, 원장 쓰기 전)
        ctx = dev_aux_context(env, tpl)
    start, end = env.data.segment_bounds("dev")
    snap = snapshot_id(tpl.exchange, tpl.symbol, tpl.timeframe, start, end, data_root=env.data.data_root)
    for sym in tpl.universe[1:]:
        snap += f"|{sym}:" + snapshot_id(tpl.exchange, sym, tpl.timeframe, start, end, data_root=env.data.data_root)
    commit = git_commit(env.code_root)
    pver = run_params(env)                          # 파라미터·비용 모델 버전 (docs/PARAMS.md §4)
    period = {"segment": "dev", "start": start.isoformat(), "end": end.isoformat()}
    families = sorted({tpl.family_of(e) for e, _ in configs})
    hyp_id = env.ledger.register_hypothesis(tpl.hypothesis["text"], tpl.hypothesis["rationale"],
                                            tpl.hypothesis["success_criteria"])
    camp_id = env.ledger.create_campaign(
        hyp_id, grid=tpl.to_record(), budget=n_cfg, cost_models=[tpl.costs["primary"], tpl.costs["grade"]],
        period={**period, "data_snapshot": snap}, family=families[0] if len(families) == 1 else None,
        note=f"{KIND} {tpl.name}: {n_cfg} signal configs × 1 trial (event study, no exits); "
             f"primary horizons {tpl.primary_horizons}, {tpl.method} α={tpl.alpha}"
             + (f"; universe {list(tpl.universe)} (events pooled, null matched per asset)" if multi else ""))
    log(f"registered {hyp_id} / {camp_id}: {n_cfg} signal configs")

    if multi:
        return _screen_universe(tpl, env, configs, ctxs, start, end, snap, commit, pver, period, hyp_id, camp_id,
                                n_cfg, include_prior, save, log)
    if m1 is None:
        m1 = load_segment(env, "dev", tpl.exchange, tpl.symbol, tpl.timeframe)
    else:
        idx = m1.index
        m1 = m1[(idx >= start) & (idx < end)]
        if m1.empty:
            raise CampaignError("no dev data")
        m1 = _regular_m1(m1)
    bars = decision_bars(m1, tpl.bar_minutes)
    atr = _atr_pct(bars)
    cache: dict = {}
    cost_map = {tpl.costs["primary"]: tpl.costs["primary"], tpl.costs["grade"]: tpl.costs["grade"]}
    studies = []
    t0 = time.time()
    for i, (e, fs) in enumerate(configs):
        st = study_signal(m1, e, fs, tpl.bar_minutes, horizons=tpl.horizons, cost_round_trip=cost_map,
                          n_null=tpl.n_null, seed=tpl.seed + i, ctx=ctx, bars=bars, block_cache=cache,
                          primary_horizons=tpl.primary_horizons, min_gap_min=tpl.min_gap_min,
                          start=start, end=end, stratify=tpl.stratify, atr_pct=atr,
                          min_events=tpl.min_events)
        st["label"] = _label(e, fs)
        st["family"] = tpl.family_of(e)
        studies.append(st)
        log(f"  {i + 1}/{n_cfg} {st['label']}: n={st['n_events']} p={st['p_signal']:.3g} "
            f"info_h={st['info_horizon']} ({time.time() - t0:.1f}s)")

    return _finish_screen(tpl, env, configs, studies, snap, commit, pver, period, hyp_id, camp_id, n_cfg,
                          families, include_prior, save, log)


def _screen_universe(tpl, env, configs, ctxs, start, end, snap, commit, pver, period, hyp_id, camp_id, n_cfg,
                     include_prior, save, log):
    """다자산 선별: 자산별 Dev 1분봉·결정 봉·블록 캐시 → 신호 구성마다 `study_events_pooled`."""
    from btc_quant.research.campaign import load_segment
    syms = list(tpl.universe)
    m1s = {sym: load_segment(env, "dev", tpl.exchange, sym, tpl.timeframe) for sym in syms}
    bars = {sym: decision_bars(m1s[sym], tpl.bar_minutes) for sym in syms}
    atrs = {sym: _atr_pct(bars[sym]) for sym in syms}
    caches: dict = {sym: {} for sym in syms}

    def ev(sym, ref):
        k = json.dumps([ref.block, ref.params, int(tpl.bar_minutes)], sort_keys=True, default=str)
        if k not in caches[sym]:
            caches[sym][k] = REGISTRY.evaluate(ref, bars[sym], ctxs.get(sym) or Context()).to_numpy(bool)
        return caches[sym][k]
    cost_map = {tpl.costs["primary"]: tpl.costs["primary"], tpl.costs["grade"]: tpl.costs["grade"]}
    studies = []
    t0 = time.time()
    for i, (e, fs) in enumerate(configs):
        assets = []
        for sym in syms:
            fok = bars[sym]["complete"].to_numpy(bool).copy()
            for f in fs:
                fok &= ev(sym, f)
            assets.append({"symbol": sym, "m1": m1s[sym], "bars_index": bars[sym].index, "entry": ev(sym, e),
                           "filter_ok": fok, "atr_pct": atrs[sym]})
        st = study_events_pooled(assets, tpl.bar_minutes, horizons=tpl.horizons,
                                 primary_horizons=tpl.primary_horizons, cost_round_trip=cost_map,
                                 n_null=tpl.n_null, seed=tpl.seed + i, min_gap_min=tpl.min_gap_min,
                                 start=start, end=end, stratify=tpl.stratify, min_events=tpl.min_events)
        st["entry"] = {"block": e.block, "params": dict(e.params)}
        st["filters"] = [{"block": f.block, "params": dict(f.params)} for f in fs]
        st["label"] = _label(e, fs)
        st["family"] = tpl.family_of(e)
        studies.append(st)
        log(f"  {i + 1}/{n_cfg} {st['label']}: n={st['n_events']} "
            f"({', '.join(f'{k}:{v['n_events']}' for k, v in st['per_asset'].items())}) p={st['p_signal']:.3g} "
            f"info_h={st['info_horizon']} ({time.time() - t0:.1f}s)")
    families = sorted({tpl.family_of(e) for e, _ in configs})
    return _finish_screen(tpl, env, configs, studies, snap, commit, pver, period, hyp_id, camp_id, n_cfg,
                          families, include_prior, save, log)


def _finish_screen(tpl, env, configs, studies, snap, commit, pver, period, hyp_id, camp_id, n_cfg, families,
                   include_prior, save, log) -> ScreenResult:
    from btc_quant.research.campaign import record_runs_batch, run_param_fields
    # ---- 다중검정: 이번 선별 + (선택) 같은 계열의 이전 선별
    prior = prior_screen_pvalues(env.ledger, set(families), exclude_campaign=camp_id) if include_prior else []
    p_now = [s["p_signal"] for s in studies]
    rej_all = correct(p_now + [x["p_signal"] for x in prior], tpl.method, tpl.alpha)
    rej = rej_all[: len(p_now)]
    pc = tpl.costs["primary"]
    rows = []
    for st, r in zip(studies, rej):
        bj = st["horizons"].index(st["best_primary_horizon"])
        brow = st["table"][bj]
        net = brow.get(f"mean_net[{pc}]", float("nan"))
        net_ok = bool(np.isfinite(net) and net > 0)
        passed = bool(r and st["enough_events"] and net_ok)
        reason = None if passed else ("not significant after correction" if not r else
                                      "too few events" if not st["enough_events"] else
                                      f"net mean ≤ 0 under {pc}")
        st["decision"] = {"significant": bool(r), "net_positive": net_ok, "passed": passed,
                          "fail_reason": reason}
        rows.append({"label": st["label"], "family": st["family"], "entry": st["entry"]["block"],
                     "filters": "+".join(f["block"] for f in st["filters"]) or "-",
                     "n_events": st["n_events"], "p_signal": st["p_signal"],
                     "best_h": st["best_primary_horizon"], "excess_bp": brow["excess"] * 1e4
                     if np.isfinite(brow.get("excess", np.nan)) else float("nan"),
                     "z": brow.get("z"), f"net_bp[{pc}]": net * 1e4,
                     f"net_bp[{tpl.costs['grade']}]": brow.get(f"mean_net[{tpl.costs['grade']}]", np.nan) * 1e4,
                     "info_h": st["info_horizon"], "profile": st["profile"],
                     **({f"n_events[{sym}]": v["n_events"] for sym, v in st["per_asset"].items()}
                        if st.get("per_asset") else {}),
                     "significant": bool(r), "passed": passed, "fail_reason": reason})
    table = pd.DataFrame(rows)
    decision = {"method": tpl.method, "alpha": tpl.alpha, "m_current": len(p_now), "m_prior": len(prior),
                "m_total": len(p_now) + len(prior), "primary_horizons": tpl.primary_horizons,
                "rule": (f"pass ⇔ {tpl.method.upper()}(α={tpl.alpha}) rejects p_signal = min(1, "
                         f"{len(tpl.primary_horizons)}·min_H p_H) over the screening family, n_events ≥ "
                         f"{tpl.min_events}, mean net of {pc} > 0 at the best primary horizon"),
                "n_passed": int(table["passed"].sum()) if len(table) else 0}

    # ---- 원장: 신호 구성당 시도 1회 (primary 비용 이름으로 한 줄)
    by_fam: dict[str, list] = {}
    for (e, fs), st in zip(configs, studies):
        met = {**_summary_metrics(st, pc), "decision": st["decision"], "correction": decision}
        by_fam.setdefault(st["family"], []).append((tpl.trial_spec(e, fs), pc, met))
    for fam, items in by_fam.items():
        record_runs_batch(env.ledger, camp_id, items, period, snap, commit, fam, **run_param_fields(pver))

    res = ScreenResult(camp_id, hyp_id, tpl, table, studies, decision)
    if save:
        out = env.campaign_dir(camp_id)
        out.mkdir(parents=True, exist_ok=True)
        (out / "template.yaml").write_text(yaml.safe_dump(tpl.raw, allow_unicode=True, sort_keys=False),
                                           encoding="utf-8")
        from btc_quant.research.pipeline import jsonable
        (out / "signal_screen.json").write_text(json.dumps(jsonable(
            {"campaign_id": camp_id, "hypothesis_id": hyp_id, "kind": KIND, "period": period,
             "data_snapshot": snap, "code_commit": commit, "params": pver, "decision": decision,
             "studies": studies}),
            indent=2, ensure_ascii=False), encoding="utf-8")
        table.to_csv(out / "signal_screen.csv", index=False, float_format="%.6g")
        res.dir = out
        (out / "signal_screen.md").write_text(render_screen_markdown(res), encoding="utf-8")
    log(f"screen {camp_id} done: {decision['n_passed']}/{n_cfg} passed")
    return res


# ============================================================== 리포트
def _f(x, nd=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "-"
    return "-" if not np.isfinite(x) else f"{x:.{nd}f}"


def render_study_markdown(st: dict) -> str:
    """한 신호의 지평별 표."""
    costs = list(st.get("costs", {}))
    pc = costs[0] if costs else None
    lines = [(f"**{st.get('label', st.get('entry', {}).get('block', 'signal'))}** — 이벤트 {st['n_events']}"
             f" (트리거 {st['n_raw_triggers']}, 적격 {st['n_pool']}, 결정 봉 {st['bar_minutes']}분, "
             f"최소 간격 {st['min_gap_min']:g}분) · 신호 p={_f(st['p_signal'], 4)} · 정보 지평 "
             f"{st['info_horizon']}분 ({st['profile']})"), "",
             "| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp"
             + (f" | 순평균bp[{pc}]" if pc else "") + " |",
             "|---|---|---|---|---|---|---|---|---|---|---" + ("|---" if pc else "") + "|"]
    for r in st["table"]:
        if "median" not in r:
            continue
        lines.append(f"| {r['horizon']} | {_f(r['mean'] * 1e4, 1)} | {_f(r['median'] * 1e4, 1)} | "
                     f"{_f(r['hit_rate'], 3)} | {_f(r['null_mean'] * 1e4, 1)} | {_f(r['excess'] * 1e4, 1)} | "
                     f"{_f(r['z'])} | {_f(r['p_value'], 4)} | {_f(r['p_null_emp'], 4)} | "
                     f"{_f(r['mfe_median'] * 1e4, 1)} | {_f(r['mae_median'] * 1e4, 1)}"
                     + (f" | {_f(r[f'mean_net[{pc}]'] * 1e4, 1)}" if pc else "") + " |")
    s = st.get("suggestion")
    if s:
        lines += ["", f"제안(최적화 아님): 최대보유 {s['max_hold_min']}분, 익절 ≈ MFE 중앙 {_f(s['tp_pct_q50_mfe'])}%"
                  + (f" ({_f(s['tp_atr'])}×ATR)" if "tp_atr" in s else "")
                  + f", 손절 ≈ |MAE| 75% {_f(s['sl_pct_q75_absmae'])}%"
                  + (f" ({_f(s['sl_atr'])}×ATR, ATR 중앙 {_f(s['atr_pct_median'], 3)}%)" if "sl_atr" in s else "")]
    return "\n".join(lines)


def render_screen_markdown(res: ScreenResult) -> str:
    tpl, d = res.template, res.decision
    lines = [f"# 신호 선별 {res.campaign_id} — {tpl.name}", "",
             f"- 가설 {res.hypothesis_id}: {tpl.hypothesis['text']}",
             (f"- 데이터: {tpl.exchange} {tpl.symbol} {tpl.timeframe} (Dev), 결정 봉 {tpl.bar_minutes}분"
              if len(tpl.universe) <= 1 else
              f"- 데이터: {tpl.exchange} 유니버스 {list(tpl.universe)} {tpl.timeframe} (Dev), 결정 봉 {tpl.bar_minutes}분 "
              "— 자산별 이벤트 풀링, 귀무는 자산별 매칭(D15)"),
             (f"- 지평 {tpl.horizons}, 사전등록 주 지평 {tpl.primary_horizons}, 최소 간격 {tpl.min_gap_min:g}분, "
              f"귀무 {tpl.n_null}회 ({tpl.stratify} 층화)"),
             f"- 판정 규칙: {d['rule']}",
             f"- 다중검정 집합: 이번 {d['m_current']} + 같은 계열 이전 {d['m_prior']} = {d['m_total']}",
             f"- **통과 {d['n_passed']} / {d['m_current']}**", "", "## 요약", ""]
    if len(res.table):
        cols = [c for c in res.table.columns if c not in ("family", "entry", "filters")]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for r in res.table.to_dict("records"):
            lines.append("| " + " | ".join(_f(r[c], 4 if c == "p_signal" else 2) if isinstance(r[c], float)
                                           else str(r[c]) for c in cols) + " |")
    lines += ["", "## 신호별 상세", ""]
    for st in res.studies:
        lines += [render_study_markdown(st), ""]
        if st.get("per_asset"):
            lines += ["자산별 (보고 전용 — 판정은 풀링 결과):", "",
                      "| 자산 | 이벤트 | 적격 | 신호 p | 정보 지평 |", "|---|---|---|---|---|"]
            for sym, v in st["per_asset"].items():
                lines.append(f"| {sym} | {v['n_events']} | {v['n_pool']} | {_f(v.get('p_signal'), 4)} | "
                             f"{v.get('info_horizon')} |")
            lines.append("")
    lines += ["", ("> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. "
                   "선별 시도는 같은 계열의 시도 수에 누적된다.")]
    return "\n".join(lines)


__all__ = [
    "KIND",
    "ScreenResult",
    "ScreenTemplate",
    "bh_reject",
    "correct",
    "decision_bars",
    "decluster",
    "hac_se_mean",
    "horizon_preset",
    "normalize_costs",
    "prior_screen_pvalues",
    "render_screen_markdown",
    "render_study_markdown",
    "screen",
    "stratified_draws",
    "study_events",
    "study_signal",
]
