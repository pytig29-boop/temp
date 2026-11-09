"""페이퍼: 체결 → 1분봉, 1분봉 이력(clean_wicks·regularize 동치), lookback 계산."""
import numpy as np
import pandas as pd
import pytest

from btc_quant.data import quality
from btc_quant.paper.bars import (Bar, M1History, MinuteBarBuilder, fetch_warmup, minute_ts,
                                  required_lookback_bars, ts_minute, window_minutes)
from btc_quant.strategy.blocks import REGISTRY
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

T0 = pd.Timestamp("2026-11-10 00:00", tz="UTC")


def synth_trades(rng, start: pd.Timestamp, minutes: int, p0=100_000_000.0, empty_frac=0.05,
                 n_gaps=3, wick_frac=0.003):
    """(ts_ms, price, qty) 목록. 일부 분은 체결 없음, 몇 개의 연속 공백, 드물게 비정상 꼬리 체결."""
    out = []
    price = p0
    gaps = np.zeros(minutes, dtype=bool)
    for _ in range(n_gaps):
        a = int(rng.integers(5, max(6, minutes - 10)))
        gaps[a:a + int(rng.integers(1, 8))] = True
    t0 = int(start.value // 1_000_000)
    for m in range(minutes):
        if gaps[m] or rng.random() < empty_frac:
            continue
        k = int(rng.integers(1, 8))
        offs = np.sort(rng.integers(0, 60_000, k))
        for off in offs:
            price *= float(np.exp(rng.normal(0, 4e-4)))
            px = price
            if rng.random() < wick_frac:
                px = price * (1.06 if rng.random() < 0.5 else 0.94)   # 오체결 꼬리
            out.append((t0 + m * 60_000 + int(off), round(px), float(rng.uniform(1e-4, 0.05))))
    return out


def raw_from_trades(trades) -> pd.DataFrame:
    df = pd.DataFrame(trades, columns=["ts_ms", "price", "qty"])
    df["ts"] = pd.to_datetime(df["ts_ms"] // 60_000 * 60_000, unit="ms", utc=True)
    g = df.groupby("ts")
    raw = pd.DataFrame({"open": g["price"].first(), "high": g["price"].max(), "low": g["price"].min(),
                        "close": g["price"].last(), "volume": g["qty"].sum()}).astype(float)
    raw.index.name = "ts"
    return raw


def build(trades, grace=2.0, start_minute=None, hist=None, end_s=None):
    b = MinuteBarBuilder(grace, start_minute=start_minute)
    hist = hist or M1History()
    bars = []
    for ts_ms, p, q in trades:
        b.on_trade(ts_ms, p, q)
        for bar in b.on_time(ts_ms / 1000):
            bars += hist.append(bar)
    if end_s is not None:
        for bar in b.on_time(end_s):
            bars += hist.append(bar)
    return b, hist, bars


def test_builder_minute_buckets_and_grace():
    b = MinuteBarBuilder(grace_seconds=2.0)
    t = int(T0.value // 1_000_000)
    b.on_trade(t + 1000, 100, 1)            # 첫 체결의 분은 부분 분 → 무시, 다음 분부터
    assert b.next_minute == ts_minute(T0) + 1 and b.ignored_trades == 1
    b.on_trade(t + 61_000, 101, 1)
    b.on_trade(t + 70_000, 103, 2)
    b.on_trade(t + 119_000, 99, 1)
    assert b.on_time(t / 1000 + 121.9) == []            # 분 종료 + grace 이전
    b.on_trade(t + 119_500, 100, 1)                      # 늦게 도착했지만 grace 안
    [bar] = b.on_time(t / 1000 + 122.0)
    assert bar == Bar(T0 + pd.Timedelta("1min"), 101, 103, 99, 100, 5, False, 4)
    b.on_trade(t + 110_000, 50, 1)                       # 이미 닫힌 분 → late
    assert b.late_trades == 1
    out = b.on_time(t / 1000 + 302.0)                    # 2..4분 체결 없음 → filled
    assert [x.ts for x in out] == [T0 + pd.Timedelta(minutes=k) for k in (2, 3, 4)]
    assert all(x.filled for x in out)


def test_pipeline_equals_regularize_clean_wicks():
    rng = np.random.default_rng(7)
    seed_raw = raw_from_trades(synth_trades(rng, T0 - pd.Timedelta(days=1), 1440))
    live = synth_trades(rng, T0, 600, wick_frac=0.01)
    hist = M1History()
    hist.seed(seed_raw)
    start_m = ts_minute(T0)
    b, hist, _ = build(live, start_minute=start_m, hist=hist, end_s=(T0 + pd.Timedelta(minutes=601)).timestamp())
    raw_all = pd.concat([seed_raw, raw_from_trades(live)])
    expect = quality.regularize(quality.clean_wicks(raw_all))
    got = hist.frame()
    # 마지막 실제 봉 이후 builder 가 낸 filled 꼬리는 regularize 범위 밖이므로 잘라서 비교
    got = got.loc[: expect.index[-1]]
    pd.testing.assert_index_equal(got.index.as_unit("ns"), expect.index.as_unit("ns"), check_names=False, check_freq=False)
    for c in ("open", "high", "low", "close"):
        np.testing.assert_array_equal(got[c].to_numpy(), expect[c].to_numpy(), err_msg=c)
    np.testing.assert_allclose(got["volume"].to_numpy(), expect["volume"].to_numpy(), rtol=1e-12)  # 합산 순서
    np.testing.assert_array_equal(got["filled"].to_numpy(), expect["filled"].to_numpy())
    assert expect["wick_cleaned"].fillna(False).any()          # 꼬리 정리가 실제로 일어난 데이터
    assert b.late_trades == 0


def test_history_gap_fill_and_window_alignment():
    h = M1History()
    h.append(Bar(T0, 10, 11, 9, 10.5, 1, False))
    added = h.append(Bar(T0 + pd.Timedelta(minutes=3), 11, 12, 10, 11, 1, False))
    assert [a.filled for a in added] == [True, True, False]
    assert h.bar_at(T0 + pd.Timedelta(minutes=1)).close == 10.5
    assert h.append(Bar(T0, 1, 1, 1, 1, 1, False)) == []          # 중복 무시
    w = h.window(T0 + pd.Timedelta(minutes=3), 2)
    assert w.index[0] == T0 and len(w) == 4                          # 정시 정렬
    assert list(w.columns) == ["open", "high", "low", "close", "volume", "filled"]
    empty = M1History()
    assert empty.append(Bar(T0, np.nan, np.nan, np.nan, np.nan, 0, True)) == []


def test_required_lookback():
    ex = ExitSpec(0.5, 0.5, 60)
    s = StrategySpec("a", BlockRef("random_entry", {"p": 0.1}), ex)
    assert required_lookback_bars(s) == 3 * 288                     # 기본 3일
    s = StrategySpec("b", BlockRef("donchian_breakout", {"lookback": 48}), ex,
                     (BlockRef("vol_regime", {"window": 24, "min_pct": 0.5}),))
    assert required_lookback_bars(s) == 24 + 7 * 288 + 2             # vol_regime 7일 순위 창
    s = StrategySpec("c", BlockRef("ema_cross_up", {"fast": 8, "slow": 96}), ex)
    n = required_lookback_bars(s)
    assert n > 1300 and (1 - 2 / 97) ** (n - 96 - 2) <= 1e-12
    s = StrategySpec("d", BlockRef("random_entry", {}), ex, (BlockRef("trend_regime_1h", {"span": 168}),))
    assert required_lookback_bars(s, max_days=30) == 30 * 288        # 상한
    assert window_minutes(10) == 110


def test_fetch_warmup_uses_fetch_and_bounds():
    calls = []

    def fake(market, unit, start, end, progress=False):
        calls.append((market, unit, start, end))
        idx = pd.date_range(start - pd.Timedelta("2min"), end, freq="1min", tz="UTC")
        return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
                             "quote_volume": 1.0}, index=idx)
    end = T0 + pd.Timedelta(seconds=30)
    df = fetch_warmup(end, 10, fetch=fake)
    assert calls[0][1] == 1 and calls[0][3] == T0
    assert df.index[0] == T0 - pd.Timedelta("10min") and df.index[-1] == T0 - pd.Timedelta("1min")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
