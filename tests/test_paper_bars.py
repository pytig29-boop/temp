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


def _parity(exchange, seed_raw, live, wick_check=True):
    """M1History(exchange) seed + 실시간 append == research.campaign.prepare_m1(전체 원본, exchange)."""
    from btc_quant.research.campaign import prepare_m1
    hist = M1History(exchange=exchange)
    hist.seed(seed_raw)
    T = seed_raw.index[-1] + pd.Timedelta(minutes=1)
    end_m = max(ts_minute(pd.Timestamp(t, unit="ms", tz="UTC")) for t, *_ in live) + 2
    b, hist, _ = build(live, start_minute=ts_minute(T), hist=hist, end_s=(minute_ts(end_m) + pd.Timedelta("5s")).timestamp())
    raw_all = pd.concat([seed_raw, raw_from_trades(live)])
    expect = prepare_m1(raw_all, exchange)
    got = hist.frame().loc[: expect.index[-1]]
    pd.testing.assert_index_equal(got.index.as_unit("ns"), expect.index.as_unit("ns"), check_names=False,
                                  check_freq=False)
    for c in ("open", "high", "low", "close", "filled"):
        np.testing.assert_array_equal(got[c].to_numpy(), expect[c].to_numpy(), err_msg=c)
    np.testing.assert_allclose(got["volume"].to_numpy(), expect["volume"].to_numpy(), rtol=1e-12)
    return hist, expect, raw_all


@pytest.mark.parametrize("exchange", ["coinone", "upbit", None])
def test_venue_wick_k_parity_with_research(exchange):
    """거래소별 꼬리 폭(코인원 k=0.01): 페이퍼 이력 == 연구 prepare_m1 (비트 단위)."""
    rng = np.random.default_rng(11)
    seed_raw = raw_from_trades(synth_trades(rng, T0 - pd.Timedelta(days=1), 1440))
    live = synth_trades(rng, T0, 600, wick_frac=0.0)
    # 1~3% 꼬리 체결 (코인원 k=0.01 에서만 잘리는 크기)
    t0 = int(T0.value // 1_000_000)
    p0 = live[0][1]
    for m in (40, 140, 260, 380):
        live.append((t0 + m * 60_000 + 30_000, round(p0 * (1.02 if m % 80 else 0.98)), 0.001))
    live.sort()
    hist, expect, raw_all = _parity(exchange, seed_raw, live)
    k, w = quality.wick_params(exchange)
    assert (k, w) == ((0.01, 15) if exchange == "coinone" else (0.03, 15))
    assert (hist.wick_k, hist.wick_window) == (k, w)
    n_cut = int(quality.flag_wick_outliers(raw_all, k, w).sum())
    n_cut_default = int(quality.flag_wick_outliers(raw_all, 0.03, 15).sum())
    if exchange == "coinone":
        assert n_cut > n_cut_default                 # 더 엄격(보수적)한 정리가 실제로 일어남
    else:
        assert n_cut == n_cut_default


def test_gap_warmup_parity_with_research():
    """30분 이상 공백 직후 첫 실제 봉 = filled (가격은 실제), seed 경계를 넘어도 페이퍼 == 연구."""
    rng = np.random.default_rng(5)
    seed_tr = synth_trades(rng, T0 - pd.Timedelta(days=1), 1440, n_gaps=0, empty_frac=0.0)
    cut0 = int((T0 - pd.Timedelta(minutes=40)).value // 1_000_000)
    seed_tr = [x for x in seed_tr if not (cut0 <= x[0] < cut0 + 35 * 60_000)]      # seed 끝 근처 35분 공백
    seed_raw = raw_from_trades(seed_tr)
    live = synth_trades(rng, T0, 300, n_gaps=0, empty_frac=0.0)
    a = int((T0 + pd.Timedelta(minutes=100)).value // 1_000_000)
    live = [x for x in live if not (a <= x[0] < a + 45 * 60_000)]                  # 실시간 45분 공백
    b = int((T0 + pd.Timedelta(minutes=200)).value // 1_000_000)
    live = [x for x in live if not (b <= x[0] < b + 10 * 60_000)]                  # 짧은 공백 (워밍업 없음)
    hist, expect, _ = _parity("coinone", seed_raw, live)
    base = quality.regularize(quality.clean_wicks_for(pd.concat([seed_raw, raw_from_trades(live)]), "coinone"))
    added = expect["filled"].to_numpy() & ~base["filled"].to_numpy()
    assert added.sum() == 2                                   # 긴 공백 2개 → 첫 실제 봉 각 1개
    assert not expect.loc[added, "volume"].eq(0).any()        # 실제 봉(가격 유지)
    # seed 경계 직후 상태: seed 가 긴 공백으로 끝나면 첫 실시간 실제 봉이 워밍업
    h2 = M1History(exchange="coinone")
    h2.seed(seed_raw.loc[: T0 - pd.Timedelta(minutes=41)])
    for g in range(35):
        h2.append(Bar(T0 - pd.Timedelta(minutes=40 - g), np.nan, np.nan, np.nan, np.nan, 0, True))
    out = h2.append(Bar(T0, 100.0, 101.0, 99.0, 100.5, 1.0, False, 3))
    assert out[-1].filled and out[-1].close == 100.5
    assert not h2.append(Bar(T0 + pd.Timedelta(minutes=1), 100.0, 101.0, 99.0, 100.5, 1.0, False, 3))[-1].filled


def test_gap_warmup_mask_definition():
    f = np.array([0] + [1] * 30 + [0, 0] + [1] * 3 + [0] + [1] * 29 + [0], bool)
    assert np.flatnonzero(quality.gap_warmup_mask(f, 30, 1)).tolist() == [31]
    assert np.flatnonzero(quality.gap_warmup_mask(f, 30, 3)).tolist() == [31, 32, 36]   # 짧은 공백은 건너뜀
    assert not quality.gap_warmup_mask(f, 30, 0).any()


def test_exclusion_windows_optional():
    idx = pd.date_range("2024-12-03 13:00", "2024-12-03 16:00", freq="1min", tz="UTC")
    m1 = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0, "filled": False},
                      index=idx)
    out = quality.apply_exclusions(m1, "coinone")
    assert out["excluded"].sum() == 131 and out["filled"].sum() == 131      # 13:20..15:30 양끝 포함
    assert not m1["filled"].any()                                           # 원본 불변, 기본 미적용
    bidx = pd.date_range("2021-02-11 02:00", "2021-02-11 05:30", freq="1min", tz="UTC")
    assert quality.exclusion_mask(bidx, "binance").sum() == 160
    assert quality.exclusion_mask(bidx, "upbit").sum() == 0


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
