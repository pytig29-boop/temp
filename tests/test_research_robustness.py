"""견고성 묶음 (research/robustness.py): 구성된 사례에서 각 검사가 올바르게 반응하는지.

- 이상치 의존 전략(소수 거래 대박) → outliers 실패
- 지연 취약 엣지(결정 직후 1분 안에 끝나는 점프) → latency 실패; 느린 드리프트 엣지 → 통과
- 상승 국면에서만 버는 상시 롱 전략 → regime 표시(flag)
- 연도 하나에만 있는 엣지 → leave_year_out 실패
- 다른 거래소: 같은 시장(+잡음) → 통과, 독립 시장 → 실패
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest.types import COINONE_CONSERVATIVE
from btc_quant.research import robustness as R
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

START = pd.Timestamp("2023-01-01", tz="UTC")
SIG = 0.0005            # 1분 로그수익 표준편차


def _frame(logret: np.ndarray, idx: pd.DatetimeIndex, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 1)
    c = 5e7 * np.exp(np.cumsum(logret))
    o = np.concatenate([[5e7], c[:-1]])
    wick = np.abs(rng.normal(0, SIG * 0.3, (2, len(c))))
    h = np.maximum(o, c) * (1 + wick[0])
    lo = np.minimum(o, c) * (1 - wick[1])
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 1.0,
                         "filled": False}, index=idx)


def _times(idx: pd.DatetimeIndex, every_min: int, offset: int = 60) -> np.ndarray:
    """매 every_min 분(5분 경계) 결정 위치."""
    pos = np.arange(offset, len(idx) - 400, every_min)
    return pos[(pos % 5) == 0]


def _signal_fn(dec_pos: np.ndarray):
    """결정 1분봉 위치 → 5분봉 entry_ok (결정 시각 - 5분)."""
    def fn(m1):
        t = m1.index[dec_pos] - pd.Timedelta(minutes=5)
        idx5 = pd.date_range(m1.index[0], m1.index[-1], freq="5min")
        e = pd.Series(False, index=idx5)
        e.loc[t] = True
        return e, None
    return fn


def make_case(days: int, seed: int, every_min: int = 120, jump=0.0, drift=0.0, drift_len=60,
              spikes: int = 0, spike=0.0, edge_until: pd.Timestamp | None = None):
    idx = pd.date_range(START, periods=days * 1440, freq="1min", name="ts")
    rng = np.random.default_rng(seed)
    r = rng.normal(0, SIG, len(idx))
    pos = _times(idx, every_min)
    live = pos if edge_until is None else pos[idx[pos] < edge_until]
    if jump:
        r[live] += jump                      # 결정 봉 안에서 즉시 점프 (시가 진입만 잡을 수 있음)
    if drift:
        for k in range(drift_len):
            r[live + k] += drift / drift_len
    if spikes:
        chosen = rng.choice(live, spikes, replace=False)
        r[chosen + 10] += spike
    return _frame(r, idx, seed), pos


def _end(m1):
    return m1.index[-1].floor("D")


@pytest.fixture(scope="module")
def drift_case():
    m1, pos = make_case(days=730, seed=3, drift=0.004)
    return m1, pos


def test_slow_drift_edge_is_robust(drift_case):
    m1, pos = drift_case
    cand = R.Candidate.from_signals(_signal_fn(pos), 3.0, 3.0, 60, name="drift")
    out = R.run_suite(cand, m1, START, _end(m1), COINONE_CONSERVATIVE,
                      checks=("latency", "outliers", "leave_year_out", "cost", "alpha"))
    c = out["checks"]
    assert out["base"]["sharpe"] > 2
    assert c["latency"]["passed"], c["latency"]
    assert {s["delay_min"] for s in c["latency"]["scenarios"]} == {1, 5, 6}
    assert c["outliers"]["passed"] and len(c["outliers"]["removals"]) == 2
    assert c["leave_year_out"]["passed"] and len(c["leave_year_out"]["years"]) == 2
    assert c["cost"]["passed"] and [s["mult"] for s in c["cost"]["scenarios"]] == [1.0, 1.5, 2.0]
    sr = [s["sharpe"] for s in c["cost"]["scenarios"]]
    assert sr[0] > sr[1] > sr[2]                     # 비용 증가 → Sharpe 단조 감소
    assert c["alpha"]["passed"] and c["alpha"]["alpha_t"] > R.ALPHA_MIN_TSTAT
    assert 0 < c["alpha"]["exposure"] < 1
    assert out["passed"] and out["failed"] == []


def test_latency_fragile_edge_fails_latency():
    m1, pos = make_case(days=200, seed=5, jump=0.004)
    cand = R.Candidate.from_signals(_signal_fn(pos), 3.0, 3.0, 30, name="jump")
    out = R.run_suite(cand, m1, START, _end(m1), checks=("latency", "outliers"))
    lat = out["checks"]["latency"]
    assert out["base"]["sharpe"] > 2
    assert not lat["passed"] and lat["min_retention"] < 0      # 1분 지연이면 엣지가 사라지고 비용만 남음
    one = next(s for s in lat["scenarios"] if s["delay_min"] == 1)
    assert one["retention"] < R.LATENCY_MIN_RETENTION
    assert "latency" in out["failed"] and not out["passed"]


def test_outlier_driven_strategy_fails_outliers():
    m1, pos = make_case(days=365, seed=11, spikes=5, spike=0.25, every_min=1440)
    cand = R.Candidate.from_signals(_signal_fn(pos), 2.0, 15.0, 60, name="lottery")
    out = R.run_suite(cand, m1, START, _end(m1), checks=("outliers",))
    o = out["checks"]["outliers"]
    assert out["base"]["sharpe"] > 0
    assert not o["passed"]
    top5 = next(r for r in o["removals"] if r["removed"] == "top_5")
    assert top5["sharpe"] < 0 and top5["removed_pnl_share"] > 1     # 상위 5건이 총손익보다 큼
    assert out["verdict"].startswith("fragile")


def test_leave_year_out_catches_single_year_edge():
    m1, pos = make_case(days=730, seed=13, drift=0.004, edge_until=pd.Timestamp("2024-01-01", tz="UTC"))
    cand = R.Candidate.from_signals(_signal_fn(pos), 3.0, 3.0, 60)
    out = R.run_suite(cand, m1, START, _end(m1), checks=("leave_year_out",))
    ly = out["checks"]["leave_year_out"]
    assert out["base"]["sharpe"] > 0
    assert not ly["passed"] and ly["worst_year_removed"] == 2023 and ly["min_sharpe"] < 0


def test_leave_year_out_skipped_with_one_year():
    m1, pos = make_case(days=120, seed=2, drift=0.004)
    out = R.run_suite(R.Candidate.from_signals(_signal_fn(pos), 3.0, 3.0, 60), m1, START, _end(m1),
                      checks=("leave_year_out",))
    assert out["checks"]["leave_year_out"]["passed"] is None and out["skipped"] == ["leave_year_out"]
    assert out["passed"] and "skipped" in out["verdict"]


def test_regime_only_strategy_flagged():
    # 40일 블록마다 상승/횡보/하락 추세가 바뀌는 시장에서 상시 롱 → 손익이 상승 국면에 몰림
    days = 360
    idx = pd.date_range(START, periods=days * 1440, freq="1min", name="ts")
    rng = np.random.default_rng(21)
    r = rng.normal(0, SIG, len(idx))
    regime = np.repeat(np.resize([0.012, 0.0, -0.008], days // 40 + 1), 40)[:days]   # 일간 드리프트
    r += np.repeat(regime / 1440, 1440)
    m1 = _frame(r, idx, 21)
    pos = _times(idx, 5)
    cand = R.Candidate.from_signals(_signal_fn(pos), 20.0, 20.0, 1440, name="always_long")
    out = R.run_suite(cand, m1, START, _end(m1), checks=("regime", "alpha"))
    reg = out["checks"]["regime"]
    assert not reg["passed"] and reg["trend"]["flag"]
    assert reg["trend"]["dominant"] == "up" and reg["trend"]["max_pnl_share"] > R.REGIME_MAX_PNL_SHARE
    assert reg["trend"]["regimes"]["down"]["sharpe"] < 0
    shares = [v["trade_share"] for v in reg["trend"]["regimes"].values()]
    assert abs(sum(shares) - 1) < 1e-9
    a = out["checks"]["alpha"]
    assert a["beta"] > 0.5 and not a["passed"]      # 베타일 뿐 알파 아님


def test_regime_labels_are_causal():
    m1, _ = make_case(days=120, seed=4)
    days = pd.date_range(START, periods=100, freq="D")
    full = R.regime_labels(m1, days)
    cut = m1[m1.index < pd.Timestamp("2023-03-15", tz="UTC")]
    part = R.regime_labels(cut, days)
    d = pd.Timestamp("2023-03-14", tz="UTC")        # 그날 라벨 특성은 전일 종가까지만 사용
    assert full.loc[d, "trail_ret"] == pytest.approx(part.loc[d, "trail_ret"])
    assert full.loc[d, "trail_vol"] == pytest.approx(part.loc[d, "trail_vol"])


def test_cross_venue(drift_case):
    m1, pos = drift_case
    rng = np.random.default_rng(99)
    twin = m1.copy()
    noise = np.exp(rng.normal(0, 1e-4, len(m1)))       # 같은 시장 + 미세 잡음 (다른 거래소 호가)
    for k in ("open", "high", "low", "close"):
        twin[k] = m1[k] * noise
    twin["high"] = twin[["open", "high", "close"]].max(axis=1)
    twin["low"] = twin[["open", "low", "close"]].min(axis=1)
    cand = R.Candidate.from_signals(_signal_fn(pos), 3.0, 3.0, 60)
    ok = R.run_suite(cand, m1, START, _end(m1), other_m1=twin, other_venue="upbit", checks=("cross_venue",))
    cv = ok["checks"]["cross_venue"]
    assert cv["passed"] and cv["same_sign"] and cv["daily_corr"] > 0.9 and cv["ratio"] > 0.8
    indep, _ = make_case(days=730, seed=77)              # 엣지 없는 독립 시장
    bad = R.run_suite(cand, m1, START, _end(m1), other_m1=indep, other_venue="x", checks=("cross_venue",))
    assert not bad["checks"]["cross_venue"]["passed"]
    none = R.run_suite(cand, m1, START, _end(m1), checks=("cross_venue",))
    assert none["checks"]["cross_venue"]["passed"] is None


def test_spec_api_and_structure():
    """StrategySpec 경로(fast.prepare_spec) — 모든 검사가 키를 갖고 JSON 직렬화 가능한 dict."""
    import json

    from test_research_synth import make_market

    from btc_quant.data.quality import regularize
    m1 = regularize(make_market(True, seed=3))
    spec = StrategySpec("brk", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(1.0, 1.2, 60))
    out = R.robustness_suite(spec, m1, "2023-01-01", "2024-01-01", other_m1=m1, other_venue="same")
    assert set(out["checks"]) == set(R.CHECKS)
    for k, v in out["checks"].items():
        assert v["passed"] in (True, False, None), k
    assert out["checks"]["cross_venue"]["ratio"] == pytest.approx(1.0)
    assert out["thresholds"]["latency_min_retention"] == R.LATENCY_MIN_RETENTION
    assert isinstance(out["passed"], bool)
    json.dumps(out, default=str)
    with pytest.raises(ValueError):
        R.robustness_suite(spec, m1, "2023-01-01", "2024-01-01", checks=("nope",))
