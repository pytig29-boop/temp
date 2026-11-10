"""성과 지표 (순수 함수, I/O 없음).

규약
- 일간 수익률 Series: UTC 일자 index, 무거래일 0 포함 (backtest.types.BacktestResult.daily_returns).
- Sharpe 는 일간 평균/표준편차(ddof=1) × √365 (코인은 365일 거래).
- 낙폭(drawdown)은 **양수 크기**로 표기한다 (0.15 = 15% 낙폭). 손실 한도는 음수 수익률로 표기.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

ANN = 365
ROLLING_WINDOW_DAYS = 182
ROLLING_STEP_DAYS = 30
RECENT_DAYS = 365


def _arr(x) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    return a[np.isfinite(a)]


def sharpe(daily, ann: int = ANN) -> float:
    """연율화 Sharpe = mean/std(ddof=1)·√ann. 표본 < 2 또는 std=0 이면 0.0."""
    a = _arr(daily)
    if a.size < 2:
        return 0.0
    sd = a.std(ddof=1)
    if not np.isfinite(sd) or sd <= 0:
        return 0.0
    return float(a.mean() / sd * np.sqrt(ann))


@dataclass(frozen=True)
class WeightedSharpe:
    sharpe: float        # 연율화
    mean: float          # 가중 일간 평균
    std: float           # 가중 일간 표준편차 (reliability-weight 불편 보정)
    n_eff: float         # Kish 유효 표본 = (Σw)² / Σw²
    n: int


def _day_age(index, anchor=None) -> np.ndarray:
    """각 관측의 anchor(기본: 마지막 관측) 대비 경과 일수."""
    if isinstance(index, pd.DatetimeIndex):
        anchor = index.max() if anchor is None else pd.Timestamp(anchor)
        if anchor.tzinfo is None and index.tz is not None:
            anchor = anchor.tz_localize(index.tz)
        return ((anchor - index) / pd.Timedelta(days=1)).to_numpy(dtype=float)
    n = len(index)
    return np.arange(n - 1, -1, -1, dtype=float)


def exp_weights(index, half_life_days: float = 365, anchor=None) -> np.ndarray:
    """w = 0.5 ** (age_days / half_life). 마지막 날(anchor) 가중치 = 1."""
    age = _day_age(index, anchor)
    return np.power(0.5, age / float(half_life_days))


def weighted_sharpe(daily: pd.Series, half_life_days: float = 365, anchor=None,
                    ann: int = ANN) -> WeightedSharpe:
    """지수 가중 Sharpe (PLAN D1/§2.2: 반감기 12개월, 시계열 끝 기준).

    mean_w = Σw x / Σw,  var_w = Σw (x-mean_w)² / (V1 − V2/V1)  (V1=Σw, V2=Σw²; 빈도 아닌
    신뢰도 가중의 불편 분산). 균등 가중이면 일반 Sharpe 와 정확히 일치한다.
    """
    s = pd.Series(daily).dropna()
    x = s.to_numpy(dtype=float)
    n = x.size
    if n < 2:
        return WeightedSharpe(0.0, float(x.mean()) if n else 0.0, 0.0, float(n), n)
    w = exp_weights(s.index, half_life_days, anchor)
    v1, v2 = w.sum(), (w ** 2).sum()
    m = float((w * x).sum() / v1)
    denom = v1 - v2 / v1
    var = float((w * (x - m) ** 2).sum() / denom) if denom > 0 else 0.0
    sd = np.sqrt(max(var, 0.0))
    sr = float(m / sd * np.sqrt(ann)) if sd > 0 else 0.0
    return WeightedSharpe(sr, m, float(sd), float(v1 ** 2 / v2), n)


def equity_curve(daily) -> np.ndarray:
    return np.cumprod(1.0 + _arr(daily))


def max_drawdown(daily) -> tuple[float, int]:
    """(최대 낙폭 크기 ≥ 0, 최장 수중 기간[관측 수=일]).

    낙폭은 시작 자본 1.0 을 초기 고점으로 포함해 계산한다. 수중 기간 = 직전 고점 미만에
    연속으로 머문 최장 일수 (회복 못 한 마지막 구간 포함).
    """
    eq = equity_curve(daily)
    if eq.size == 0:
        return 0.0, 0
    eq = np.concatenate([[1.0], eq])
    peak = np.maximum.accumulate(eq)
    dd = 1.0 - eq / peak
    under = dd > 1e-12
    longest = cur = 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    return float(dd.max()), int(longest)


def trade_stats(trades: pd.DataFrame, n_days: int) -> dict:
    """거래 통계. trades 는 TRADE_COLUMNS (net_ret, hold_min 사용).
    n_positions = 독립 포지션 수 — 사다리(분할 매수)면 사다리 수, 아니면 거래 수 (G1 표본·G2 거래 수 맞춤 단위).
    부분 사이징·사다리 거래 행은 분할(tranche) 하나씩이고 net_ret 은 분할 명목 대비 (슬리브 대비 = weight × net_ret)."""
    from btc_quant.backtest.sizing import n_positions
    n = 0 if trades is None else len(trades)
    if n == 0:
        return {"n_trades": 0, "n_positions": 0, "expectancy": 0.0, "win_rate": 0.0, "profit_factor": 0.0,
                "avg_hold_min": 0.0, "trades_per_day": 0.0}
    r = trades["net_ret"].to_numpy(dtype=float)
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    pf = float(gains / losses) if losses > 0 else (float("inf") if gains > 0 else 0.0)
    return {
        "n_trades": int(n),
        "n_positions": n_positions(trades),
        "expectancy": float(r.mean()),
        "win_rate": float((r > 0).mean()),
        "profit_factor": pf,
        "avg_hold_min": float(trades["hold_min"].astype(float).mean()),
        "trades_per_day": float(n / n_days) if n_days > 0 else 0.0,
    }


def yearly_table(daily: pd.Series, trades: pd.DataFrame | None = None) -> pd.DataFrame:
    """달력 연도별 성과표 (index=연도): n_days, total_return, sharpe, max_dd, n_trades.

    거래 연도는 exit_time 기준 (daily_returns 의 청산일 기준 귀속과 일치).
    """
    s = pd.Series(daily).dropna()
    rows = {}
    years = s.index.year
    tr_years = None
    if trades is not None and len(trades):
        tr_years = pd.DatetimeIndex(pd.to_datetime(trades["exit_time"])).year
    for y in sorted(set(years)):
        part = s[years == y]
        mdd, _ = max_drawdown(part)
        rows[int(y)] = {
            "n_days": int(part.size),
            "total_return": float(np.prod(1 + part.to_numpy()) - 1),
            "sharpe": sharpe(part),
            "max_dd": mdd,
            "n_trades": int((tr_years == y).sum()) if tr_years is not None else 0,
        }
    return pd.DataFrame.from_dict(rows, orient="index")


def rolling_sharpe(daily, window: int = ROLLING_WINDOW_DAYS,
                   step: int = ROLLING_STEP_DAYS) -> pd.Series:
    """겹치는 고정 길이 창의 Sharpe. 창은 **시계열 끝에 정렬**(마지막 창이 마지막 날에서 끝남)
    하고 step 일씩 과거로 이동; 결과 index 는 창의 마지막 관측 라벨, 시간순 정렬.
    길이 < window 이면 빈 Series."""
    s = pd.Series(daily).dropna()
    n = s.size
    ends = list(range(n, window - 1, -step))[::-1]
    vals = [sharpe(s.iloc[e - window:e]) for e in ends]
    return pd.Series(vals, index=[s.index[e - 1] for e in ends], dtype=float)


def rolling_positive_fraction(daily, window: int = ROLLING_WINDOW_DAYS,
                              step: int = ROLLING_STEP_DAYS) -> tuple[float, int]:
    """(Sharpe > 0 인 롤링 창 비율, 창 개수). 창이 없으면 (0.0, 0)."""
    r = rolling_sharpe(daily, window, step)
    if r.empty:
        return 0.0, 0
    return float((r > 0).mean()), int(r.size)


# ============================================================== 노출·매수보유(B&H) 기준 (정보 전용, 관문 아님)
# 노출(exposure) 두 가지
#   exposure_time = 보유 분 합 / 구간 전체 분      (robustness.check_alpha 의 'exposure' 와 같은 정의)
#   exposure_days = 포지션이 하루 중 한 번이라도 열려 있던 날의 비율  ("시장에 있었던 날")
# 관문 변형(G4/G6 exposure_aware, 기본 OFF)은 exposure_days 를 쓴다: 몇 달 동안 '쉬는' 전략을 가려내는 데는
# 하루 단위 참여 여부가 맞고, 짧게 자주 거래하는 전략(시간 노출은 작지만 매일 거래)을 '쉬었다'로 보지 않는다.
NEUTRAL_EXPOSURE = 0.10            # 롤링 창이 '중립'(쉬는 창)인 exposure_days 상한 — gates.yaml exposure_aware.G4 기본값과 같음
BH_REGIME_LOOKBACK_DAYS = 30       # 국면 = 직전 30일 BTC 수익 부호 (인과, robustness.regime_lookback_days 와 같음)
BH_N_EPISODES = 3                  # B&H 최악 낙폭 구간 수
# 캠페인 안에서 모든 스펙에 같은 값(벤치마크 자체) → 원장 runs 에는 쓰지 않고 manifest["benchmark"] 에 한 번
BENCHMARK_CONSTANT_KEYS = ("bh_total_return", "bh_sharpe", "bh_max_dd", "bh_n_up_days", "bh_n_down_days",
                           "bh_n_up_regime_days", "bh_n_down_regime_days",
                           *(f"bh_dd{i}_{k}" for i in range(1, BH_N_EPISODES + 1)
                             for k in ("depth", "start", "end")))


def buy_hold_daily(close: pd.Series, days: pd.DatetimeIndex, first_price: float | None = None) -> pd.Series:
    """1분봉 close → `days`(UTC 자정 라벨) 위의 BTC 매수보유 일간 수익 (비용 없음).

    r_d = close(d 의 마지막 1분봉) / close(d−1 의 마지막 1분봉) − 1. 첫날 기준가 = first_price(기본: 첫날 첫 1분봉
    close). 데이터가 없는 날은 0(가격 그대로)."""
    c = pd.Series(close).dropna().astype(float)
    if c.empty or len(days) == 0:
        return pd.Series(0.0, index=days, dtype=float)
    idx = pd.DatetimeIndex(days)
    cidx = c.index
    if isinstance(cidx, pd.DatetimeIndex) and idx.tz is not None and cidx.tz is None:
        c.index = cidx.tz_localize(idx.tz)
    elif isinstance(cidx, pd.DatetimeIndex) and idx.tz is None and cidx.tz is not None:
        c.index = cidx.tz_convert(None)
    c = c[(c.index >= idx[0]) & (c.index < idx[-1] + pd.Timedelta(days=1))]
    if c.empty:
        return pd.Series(0.0, index=days, dtype=float)
    last = c.groupby(c.index.floor("D")).last().reindex(idx).ffill()
    p0 = float(first_price) if first_price is not None else float(c.iloc[0])
    last = last.fillna(p0)
    prev = last.shift(1)
    prev.iloc[0] = p0
    return (last / prev - 1.0).astype(float)


def days_in_market(trades: pd.DataFrame | None, days: pd.DatetimeIndex) -> np.ndarray:
    """일자별 bool: 그날 포지션이 한 번이라도 열려 있었나 (진입일 ~ 청산일 포함)."""
    n = len(days)
    out = np.zeros(n, dtype=bool)
    if trades is None or not len(trades) or n == 0 or not isinstance(days, pd.DatetimeIndex):
        return out
    idx = pd.DatetimeIndex(days)

    def norm(x):
        t = pd.DatetimeIndex(pd.to_datetime(x))
        if idx.tz is not None and t.tz is None:
            t = t.tz_localize(idx.tz)
        elif idx.tz is None and t.tz is not None:
            t = t.tz_convert(None)
        return t.floor("D")
    ref = idx.as_unit("ns").asi8                     # 단위(ns/us)가 다를 수 있다 → 같은 단위로 비교
    e0 = np.searchsorted(ref, norm(trades["entry_time"]).as_unit("ns").asi8, side="left")
    e1 = np.searchsorted(ref, norm(trades["exit_time"]).as_unit("ns").asi8, side="right")
    diff = np.zeros(n + 1, dtype=np.int64)
    np.add.at(diff, np.clip(e0, 0, n), 1)
    np.add.at(diff, np.clip(e1, 0, n), -1)
    return np.cumsum(diff[:n]) > 0


def rolling_exposure(in_market: np.ndarray, window: int = ROLLING_WINDOW_DAYS,
                     step: int = ROLLING_STEP_DAYS) -> np.ndarray:
    """rolling_sharpe 와 같은 창(끝 정렬, step 일씩 과거로)의 exposure_days. 길이 < window 면 빈 배열."""
    x = np.asarray(in_market, dtype=float)
    n = x.size
    ends = list(range(n, window - 1, -step))[::-1]
    if not ends:
        return np.zeros(0)
    cs = np.concatenate([[0.0], np.cumsum(x)])
    return np.array([(cs[e] - cs[e - window]) / window for e in ends], dtype=float)


def rolling_exposure_aware(daily, in_market, neutral_exposure: float = NEUTRAL_EXPOSURE,
                           window: int = ROLLING_WINDOW_DAYS, step: int = ROLLING_STEP_DAYS) -> dict:
    """노출 인식 롤링 안정성 (G4 변형 입력): exposure_days < neutral_exposure 인 창 = '중립'(쉬는 창) →
    양수 비율의 분모에서 제외. 반환 {rolling_n_windows, rolling_n_neutral, rolling_n_active,
    rolling_frac_positive_active, rolling_neutral_exposure}. 활성 창이 없으면 frac = 0."""
    r = rolling_sharpe(daily, window, step)
    ex = rolling_exposure(in_market, window, step)
    if r.size != ex.size:          # 길이가 다르면(일자 불일치) 판정 불가 → 모두 활성으로
        ex = np.ones(r.size)
    neutral = ex < float(neutral_exposure)
    act = ~neutral
    n_act = int(act.sum())
    pos = r.to_numpy() > 0
    return {"rolling_n_windows": int(r.size), "rolling_n_neutral": int(neutral.sum()), "rolling_n_active": n_act,
            "rolling_frac_positive_active": float((pos & act).sum() / n_act) if n_act else 0.0,
            "rolling_neutral_exposure": float(neutral_exposure)}


def _compound(x: np.ndarray) -> float:
    return float(np.prod(1.0 + x) - 1.0) if x.size else 0.0


def drawdown_episodes(daily, n: int = BH_N_EPISODES) -> list[dict]:
    """겹치지 않는 낙폭 구간(고점 → 저점) 깊이 순 상위 n 개: [{depth, peak_pos, trough_pos}] — 위치는
    daily 의 행 번호. peak_pos 는 고점이 *확정된* 날(그 다음 날부터 하락에 포함), -1 = 시작 자본."""
    x = np.asarray(pd.Series(daily).fillna(0.0).to_numpy(float))
    if x.size == 0:
        return []
    eq = np.concatenate([[1.0], np.cumprod(1.0 + x)])
    peak = np.maximum.accumulate(eq)
    dd = 1.0 - eq / peak
    eps = []
    i, m = 0, eq.size
    while i < m:
        if dd[i] <= 1e-12:
            i += 1
            continue
        j = i
        while j < m and dd[j] > 1e-12:
            j += 1
        seg = dd[i:j]
        t = i + int(np.argmax(seg))
        eps.append({"depth": float(dd[t]), "peak_pos": i - 2, "trough_pos": t - 1})
        i = j
    eps.sort(key=lambda e: -e["depth"])
    return eps[:n]


def benchmark_metrics(daily: pd.Series, trades: pd.DataFrame | None, bh_daily: pd.Series | None,
                      regime_lookback_days: int = BH_REGIME_LOOKBACK_DAYS, n_episodes: int = BH_N_EPISODES,
                      neutral_exposure: float = NEUTRAL_EXPOSURE, n_sleeves: int = 1) -> dict:
    """전략 일간 수익 vs 같은 기간 BTC 매수보유 (정보 전용 — 어떤 관문도 이것으로 판정하지 않는다;
    단 G4/G6 exposure_aware 변형(기본 OFF)은 exposure_days·bh_total_return·bh_max_dd 를 읽는다).

    키 (bh_daily 가 없으면 노출 관련 키만)
      exposure_time, exposure_days, days_in_market, downside_dev(연율, 0 기준 하방편차),
      rolling_n_neutral, rolling_n_active, rolling_frac_positive_active, rolling_neutral_exposure,
      bh_total_return, bh_sharpe, bh_max_dd, excess_return_vs_bh, sharpe_minus_bh,
      bh_matched_total_return(= exposure_time × BTC 일간 수익의 복리), excess_return_vs_matched_bh,
      up_capture / down_capture (일간, BTC 수익 부호로 나눈 날들: 전략 평균 / BTC 평균),
      up_regime_return / down_regime_return / up_regime_capture / down_regime_capture
        (국면 = 직전 regime_lookback_days 일 BTC 수익 부호, 전일 종가까지 — 인과),
      bh_dd{i}_depth / bh_dd{i}_start / bh_dd{i}_end / bh_dd{i}_strategy_return (B&H 최악 낙폭 구간 i 동안
        전략 복리 수익), worst_bh_dd_strategy_return (가장 깊은 구간의 전략 수익).

    n_sleeves (다자산 유니버스, PLAN D15): 거래 목록이 N 개 동일 슬리브의 합이면 exposure_time = 보유 분 합 /
    (일수 × 1440 × N) = 슬리브 평균 노출 시간. days_in_market 은 '어느 슬리브든 보유한 날'. 기본 1 = 이전과 같다.
    부분 사이징·사다리(거래 표에 weight 열): exposure_time = Σ weight × 보유 분 / … = 평균 보유 비중(자본 가중)."""
    s = pd.Series(daily).dropna().astype(float)
    n = s.size
    x = s.to_numpy()
    out: dict = {}
    idx = s.index if isinstance(s.index, pd.DatetimeIndex) else None
    inm = days_in_market(trades, idx) if idx is not None else np.zeros(n, dtype=bool)
    # 보유 시간 × 크기 (부분 사이징·사다리: weight 가중 = 평균 보유 비중; 기본 weight 1 = 보유 시간 비율)
    hold = 0.0
    if trades is not None and len(trades):
        hm = trades["hold_min"].astype(float).to_numpy()
        hold = float((hm * trades["weight"].to_numpy(float)).sum() if "weight" in trades.columns else hm.sum())
    out["exposure_time"] = float(min(1.0, hold / (n * 1440.0 * max(int(n_sleeves), 1)))) if n else 0.0
    out["days_in_market"] = int(inm.sum())
    out["exposure_days"] = float(inm.mean()) if n else 0.0
    out["downside_dev"] = float(np.sqrt(np.mean(np.minimum(x, 0.0) ** 2)) * np.sqrt(ANN)) if n else 0.0
    out.update({k: v for k, v in rolling_exposure_aware(s, inm, neutral_exposure).items()
                if k != "rolling_n_windows"})
    if bh_daily is None or n == 0:
        return out
    b = pd.Series(bh_daily).astype(float)
    if idx is not None and isinstance(b.index, pd.DatetimeIndex):
        b = b.reindex(idx).fillna(0.0)
    else:
        b = pd.Series(b.to_numpy()[:n], index=s.index)
    y = b.to_numpy()
    total = _compound(x)
    bh_total = _compound(y)
    bh_mdd, _ = max_drawdown(y)
    out.update(bh_total_return=bh_total, bh_sharpe=sharpe(y), bh_max_dd=bh_mdd,
               excess_return_vs_bh=total - bh_total, sharpe_minus_bh=sharpe(x) - sharpe(y))
    matched = out["exposure_time"] * y
    out["bh_matched_total_return"] = _compound(matched)
    out["excess_return_vs_matched_bh"] = total - out["bh_matched_total_return"]

    def capture(mask) -> float:
        if not mask.any():
            return float("nan")
        mb = float(y[mask].mean())
        return float(x[mask].mean() / mb) if mb != 0 else float("nan")
    up, dn = y > 0, y < 0
    out.update(up_capture=capture(up), down_capture=capture(dn), bh_n_up_days=int(up.sum()),
               bh_n_down_days=int(dn.sum()))
    # 인과 국면: 직전 L 일 BTC 누적 수익 부호 (d 의 국면은 d−1 까지의 수익)
    L = int(regime_lookback_days)
    ly = np.log1p(y)
    cs = np.concatenate([[0.0], np.cumsum(ly)])
    trail = np.full(n, np.nan)
    if n > L:
        trail[L:] = cs[L:n] - cs[0:n - L]
    ru, rd = trail > 0, trail <= 0
    rd &= np.isfinite(trail)
    out.update(up_regime_return=_compound(x[ru]), down_regime_return=_compound(x[rd]),
               up_regime_capture=capture(ru), down_regime_capture=capture(rd),
               bh_n_up_regime_days=int(ru.sum()), bh_n_down_regime_days=int(rd.sum()))
    eps = drawdown_episodes(y, n_episodes)
    for i in range(1, n_episodes + 1):
        if i <= len(eps):
            e = eps[i - 1]
            a, z = max(e["peak_pos"] + 1, 0), e["trough_pos"]
            lab = (lambda p: str(s.index[p].date()) if idx is not None else str(p))
            out[f"bh_dd{i}_depth"] = e["depth"]
            out[f"bh_dd{i}_start"] = lab(a)
            out[f"bh_dd{i}_end"] = lab(z)
            out[f"bh_dd{i}_strategy_return"] = _compound(x[a:z + 1])
        else:
            out[f"bh_dd{i}_depth"] = float("nan")
            out[f"bh_dd{i}_start"] = None
            out[f"bh_dd{i}_end"] = None
            out[f"bh_dd{i}_strategy_return"] = float("nan")
    out["worst_bh_dd_strategy_return"] = out["bh_dd1_strategy_return"]
    return out


# ============================================================== 꼬리 위험 (정보 전용 + 견고성 'tail' 검사 입력)
TAIL_CVAR_Q = 0.05                 # CVaR: 일간 수익 하위 5% 평균
BEAR_YEAR = 2022                   # Dev 약세장 하위 구간 (BTC 2022: −64%)
TAIL_KEYS = ("worst_day", "worst_week", "cvar5_daily", "max_inventory", "max_inventory_minutes",
             "max_inventory_episodes", "bear_total_return", "bear_sharpe", "bear_max_dd", "bear_n_days")


def tail_metrics(daily, trades: pd.DataFrame | None = None, cvar_q: float = TAIL_CVAR_Q,
                 bear_year: int = BEAR_YEAR, n_sleeves: int = 1) -> dict:
    """꼬리 위험 지표 (사다리·부분 사이징 평가용, 모든 전략에 계산 — 정보 전용):
    worst_day / worst_week(월~일 복리) / cvar5_daily(하위 cvar_q 일간 수익 평균), 최대 보유 비중과 그 비중에 머문 최장
    시간(분)·횟수(`sizing.inventory_profile`), 약세장 연도(bear_year, Dev 2022) 하위 구간 총수익·Sharpe·MDD·일수
    (그 연도가 없으면 NaN). 왜도는 summarize 의 skew, B&H 최악 낙폭 구간 수익은 benchmark_metrics 의 bh_dd*_strategy_return."""
    from btc_quant.backtest.sizing import inventory_profile
    s = pd.Series(daily).dropna().astype(float)
    x = s.to_numpy()
    out: dict = {"worst_day": float(x.min()) if x.size else 0.0}
    if isinstance(s.index, pd.DatetimeIndex) and x.size:
        wk = (1 + s).resample("W-SUN").prod() - 1
        out["worst_week"] = float(wk.min())
    else:
        out["worst_week"] = float(min((np.prod(1 + x[i:i + 7]) - 1 for i in range(0, x.size, 7)), default=0.0))
    if x.size:
        k = max(1, int(np.ceil(cvar_q * x.size)))
        out["cvar5_daily"] = float(np.sort(x)[:k].mean())
    else:
        out["cvar5_daily"] = 0.0
    out.update(inventory_profile(trades, n_sleeves))
    if isinstance(s.index, pd.DatetimeIndex) and (s.index.year == bear_year).any():
        b = s[s.index.year == bear_year]
        out.update(bear_total_return=float(np.prod(1 + b.to_numpy()) - 1), bear_sharpe=sharpe(b),
                   bear_max_dd=max_drawdown(b)[0], bear_n_days=int(b.size))
    else:
        out.update(bear_total_return=float("nan"), bear_sharpe=float("nan"), bear_max_dd=float("nan"), bear_n_days=0)
    return out


def recent(daily: pd.Series, days: int = RECENT_DAYS) -> pd.Series:
    """마지막 `days` 일 (DatetimeIndex 면 달력 기준 (end-days, end], 아니면 마지막 days 개)."""
    s = pd.Series(daily).dropna()
    if isinstance(s.index, pd.DatetimeIndex) and s.size:
        return s[s.index > s.index.max() - pd.Timedelta(days=days)]
    return s.iloc[-days:]


def summarize(result, half_life_days: float = 365, benchmark: pd.Series | None = None,
              neutral_exposure: float = NEUTRAL_EXPOSURE, n_sleeves: int | None = None) -> dict:
    """BacktestResult → 평탄 dict (관문 입력). 낙폭은 양수 크기.

    키: n_days, sharpe, sharpe_w, mean_w, std_w, n_eff, mean_daily, std_daily, skew,
    kurtosis(정규=3), total_return, cagr, max_dd, max_dd_days, n_trades, expectancy, win_rate,
    profit_factor, avg_hold_min, trades_per_day, recent_sharpe, recent_n_trades, n_positions, recent_n_positions,
    rolling_frac_positive, rolling_n_windows, sharpe_year_YYYY
    + 꼬리 위험 (`tail_metrics`, 정보 전용): worst_day, worst_week, cvar5_daily, max_inventory(_minutes/_episodes),
    bear_total_return/bear_sharpe/bear_max_dd/bear_n_days (Dev 2022)
    + 노출·B&H 기준 (`benchmark_metrics`, 정보 전용): exposure_time, exposure_days, days_in_market, downside_dev,
    rolling_n_neutral/active, rolling_frac_positive_active, rolling_neutral_exposure, 그리고 benchmark(같은
    일자의 BTC 매수보유 일간 수익, `buy_hold_daily`)가 주어지면 bh_* · capture · 낙폭 구간 키.
    n_sleeves: 다자산 포트폴리오 결과(`research.universe.PortfolioResult`)면 슬리브 수 (None = result.n_sleeves 또는 1).
    거래 통계(n_trades·expectancy 등)는 풀링한 거래 목록, Sharpe·낙폭은 포트폴리오 일간 수익 기준.
    """
    s = pd.Series(result.daily_returns).dropna().astype(float)
    x = s.to_numpy()
    n = x.size
    ws = weighted_sharpe(s, half_life_days)
    mdd, mdd_days = max_drawdown(s)
    total = float(np.prod(1 + x) - 1) if n else 0.0
    out = {
        "spec_id": result.spec_id,
        "cost": result.cost.name,
        "n_days": int(n),
        "sharpe": sharpe(s),
        "sharpe_w": ws.sharpe,
        "mean_w": ws.mean,
        "std_w": ws.std,
        "n_eff": ws.n_eff,
        "mean_daily": float(x.mean()) if n else 0.0,
        "std_daily": float(x.std(ddof=1)) if n > 1 else 0.0,
        "skew": float(stats.skew(x)) if n > 2 and x.std() > 0 else 0.0,
        "kurtosis": float(stats.kurtosis(x, fisher=False)) if n > 3 and x.std() > 0 else 3.0,
        "total_return": total,
        "cagr": float((1 + total) ** (ANN / n) - 1) if n and total > -1 else -1.0,
        "max_dd": mdd,
        "max_dd_days": mdd_days,
    }
    trades = result.trades
    out.update(trade_stats(trades, n))
    rec = recent(s)
    out["recent_sharpe"] = sharpe(rec)
    out["recent_n_positions"] = 0
    if trades is not None and len(trades) and rec.size:
        et = pd.to_datetime(trades["exit_time"])
        lo = rec.index.min()
        if isinstance(lo, pd.Timestamp):
            et_idx = pd.DatetimeIndex(et)
            if et_idx.tz is None and lo.tzinfo is not None:
                et_idx = et_idx.tz_localize(lo.tzinfo)
            elif et_idx.tz is not None and lo.tzinfo is None:
                lo = lo.tz_localize(et_idx.tz)
            # 일자 index 는 자정 라벨 → 해당 일자 전체 포함
            rmask = np.asarray(et_idx.normalize() >= lo.normalize())
            out["recent_n_trades"] = int(rmask.sum())
            from btc_quant.backtest.sizing import n_positions
            out["recent_n_positions"] = n_positions(trades[rmask])
        else:
            out["recent_n_trades"] = 0
    else:
        out["recent_n_trades"] = 0
    out["rolling_frac_positive"], out["rolling_n_windows"] = rolling_positive_fraction(s)
    ns = int(n_sleeves if n_sleeves is not None else (getattr(result, "n_sleeves", 1) or 1))
    out.update(tail_metrics(s, trades, n_sleeves=ns))
    out.update(benchmark_metrics(s, trades, benchmark, neutral_exposure=neutral_exposure, n_sleeves=ns))
    if isinstance(s.index, pd.DatetimeIndex) and n:
        for y, row in yearly_table(s, trades).iterrows():
            out[f"sharpe_year_{y}"] = float(row["sharpe"])
    return out
