"""합성 데이터로 로직만 검증한다(시장 결과를 의미하지 않음)."""
import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from btc_quant.analysis import cost_floor as cf
from btc_quant.analysis import lead_lag
from btc_quant.data import binance_vision, orderbook, quality, store, upbit


def flat_m1(n=60, price=100.0, start="2026-01-01 00:00"):
    idx = pd.date_range(start, periods=n, freq="1min", tz="UTC", name="ts")
    df = pd.DataFrame({"open": price, "high": price, "low": price, "close": price,
                       "volume": 1.0, "quote_volume": price}, index=idx)
    df["filled"] = False
    return df


def test_barrier_tp_first():
    m1 = flat_m1()
    m1.iloc[7, m1.columns.get_loc("high")] = 100.6  # 5분 경계(i=5) 진입 후 3번째 분
    b = cf.barrier_outcomes(m1, horizon=10, x=0.005)
    row = b.loc[m1.index[5]]
    assert row.outcome == 1 and row.gross == pytest.approx(0.005) and row.hold_min == 3


def test_barrier_same_bar_counts_as_loss():
    m1 = flat_m1()
    m1.iloc[6, m1.columns.get_loc("high")] = 101
    m1.iloc[6, m1.columns.get_loc("low")] = 99
    b = cf.barrier_outcomes(m1, horizon=10, x=0.005)
    assert b.loc[m1.index[5]].outcome == -1


def test_barrier_timeout_uses_close():
    m1 = flat_m1()
    m1.iloc[14, m1.columns.get_loc("close")] = 100.2
    m1.iloc[14, m1.columns.get_loc("high")] = 100.2
    b = cf.barrier_outcomes(m1, horizon=10, x=0.005)
    row = b.loc[m1.index[5]]
    assert row.outcome == 0 and row.gross == pytest.approx(0.002)


def test_entries_skip_filled_windows_and_need_warmup():
    m1 = flat_m1()
    m1.iloc[12, m1.columns.get_loc("filled")] = True
    idx = cf._entries(m1, horizon=5)
    starts = m1.index[idx].minute.tolist()
    assert 0 not in starts           # 워밍업 부족
    assert 10 not in starts and 15 not in starts   # 12분 누락이 [i-5,i+5) 에 걸림
    assert 20 in starts


def test_forward_return_enters_next_open():
    m1 = flat_m1()
    m1.iloc[4, m1.columns.get_loc("close")] = 200  # 결정 봉 종가는 진입가가 아님
    r = cf.forward_returns(m1, 5)
    assert r.loc[m1.index[5]] == pytest.approx(0.0)


def test_required_edge_formula():
    m1 = flat_m1(n=300)
    s = cf.CostScenario("t", 0.0005, 0.0, 0.0)  # 왕복 10bp
    t = cf.barrier_table(m1, [5], [0.005], [s])
    # 평탄 시장: gross=0 → Δ = 0.001 / 0.01 = 10%p
    assert t.iloc[0]["필요 승률개선 %p t"] == pytest.approx(10.0)


def test_quality_detects_issues():
    m1 = flat_m1(n=10).drop(columns="filled")
    bad = pd.concat([m1, m1.iloc[[3]]])
    bad.iloc[1, bad.columns.get_loc("low")] = 101
    bad = bad.drop(index=m1.index[6])
    r = quality.check_ohlcv(bad)
    assert r.duplicates == 1 and r.ohlc_violations == 1 and r.missing_bars == 1 and not r.ok


def test_regularize_marks_filled():
    m1 = flat_m1(n=10).drop(columns="filled").drop(index=pd.Timestamp("2026-01-01 00:04", tz="UTC"))
    out = quality.regularize(m1)
    assert len(out) == 10 and out["filled"].sum() == 1 and out["volume"].iloc[4] == 0


def test_upbit_parse():
    payload = [{"market": "KRW-BTC", "candle_date_time_utc": "2026-01-01T00:01:00",
                "opening_price": 2, "high_price": 3, "low_price": 1, "trade_price": 2.5,
                "candle_acc_trade_volume": 0.1, "candle_acc_trade_price": 0.25},
               {"market": "KRW-BTC", "candle_date_time_utc": "2026-01-01T00:00:00",
                "opening_price": 1, "high_price": 2, "low_price": 1, "trade_price": 2,
                "candle_acc_trade_volume": 0.2, "candle_acc_trade_price": 0.4}]
    df = upbit.parse_candles(payload)
    assert df.index.is_monotonic_increasing and str(df.index.tz) == "UTC"
    assert df["close"].tolist() == [2, 2.5]


@pytest.mark.parametrize("ts,expected", [(1735689600000, "2025-01-01"),
                                          (1735689600000000, "2025-01-01")])
def test_binance_ts_units(ts, expected):
    line = f"{ts},1,2,0.5,1.5,10,{ts + 59999},15,3,5,7,0\n".encode()
    df = binance_vision.parse_kline_csv(line)
    assert str(df.index[0].date()) == expected


def test_orderbook_impact():
    units = [{"ask_price": 101, "bid_price": 99, "ask_size": 1, "bid_size": 1},
             {"ask_price": 102, "bid_price": 98, "ask_size": 10, "bid_size": 1}]
    assert orderbook.market_buy_cost(units, 101) == pytest.approx(0.01)
    assert np.isnan(orderbook.market_buy_cost(units, 1e6))


def test_store_roundtrip(tmp_path):
    m1 = flat_m1(n=5).drop(columns="filled")
    store.write_ohlcv(m1, "x", "BTC-KRW", "1m", root=tmp_path)
    store.write_ohlcv(m1.iloc[:2], "x", "BTC-KRW", "1m", root=tmp_path)  # 재기록 병합
    back = store.read_ohlcv("x", "BTC-KRW", "1m", root=tmp_path)
    assert len(back) == 5


def test_lead_lag_detects_lead():
    rng = np.random.default_rng(0)
    rb = rng.normal(0, 1e-3, 2000)
    ra = np.roll(rb, 1) * 0.8 + rng.normal(0, 3e-4, 2000)
    idx = pd.date_range("2026-01-01", periods=2000, freq="1min", tz="UTC")
    a = pd.Series(100 * np.exp(np.cumsum(ra)), idx)
    b = pd.Series(100 * np.exp(np.cumsum(rb)), idx)
    t = lead_lag.lead_lag(a, b, 2)
    assert t["corr"].idxmax() == 1


def test_report_builds():
    rng = np.random.default_rng(1)
    n = 3 * 24 * 60
    c = 100 * np.exp(np.cumsum(rng.normal(0, 5e-4, n)))
    m1 = flat_m1(n=n)
    m1["open"] = np.r_[100, c[:-1]]
    m1["close"] = c
    m1["high"] = np.maximum(m1.open, m1.close) * 1.0002
    m1["low"] = np.minimum(m1.open, m1.close) * 0.9998
    md = cf.build_report(m1, "synthetic", horizons=(5, 30), xs=(0.003,))
    assert "필요 승률개선" in md and "hour(KST)" in md


def test_format_to_per_exchange():
    from datetime import datetime, timezone
    t = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    assert upbit.format_to("upbit", t) == "2026-11-01T00:00:00Z"
    assert upbit.format_to("bithumb", t) == "2026-11-01T09:00:00"


# --- Korbit (문서 예시 응답 기반) ---
from btc_quant.data import korbit

KORBIT_CANDLES = {"success": True, "data": [
    {"timestamp": 1708041600000, "open": "71211000", "high": "9999990000", "low": "300000",
     "close": "71392000", "volume": "1.93"},
    {"timestamp": 1708041660000, "open": "71392000", "high": "71400000", "low": "71300000",
     "close": "71350000", "volume": "0.5"}]}


def test_korbit_symbol():
    assert korbit.to_symbol("KRW-BTC") == "btc_krw" == korbit.to_symbol("BTC-KRW")


def test_korbit_parse_candles():
    df = korbit.parse_candles(KORBIT_CANDLES)
    assert str(df.index.tz) == "UTC" and df["close"].iloc[1] == 71350000.0


def test_korbit_parse_orderbook():
    ob = korbit.parse_orderbook({"success": True, "data": {
        "timestamp": 1700000000000,
        "bids": [{"price": "99", "qty": "1"}], "asks": [{"price": "101", "qty": "2"}]}})
    u = ob["orderbook_units"][0]
    assert (u["bid_price"], u["ask_price"], u["ask_size"]) == (99.0, 101.0, 2.0)


def test_korbit_error_raises():
    with pytest.raises(RuntimeError):
        korbit.parse_candles({"success": False, "error": "x"})


def test_wick_outlier_flag_and_clean():
    df = korbit.parse_candles(KORBIT_CANDLES)
    m = quality.flag_wick_outliers(df)
    assert m.iloc[0] and not m.iloc[1]
    c = quality.clean_wicks(df)
    assert c["high"].iloc[0] == 71392000.0 and c["low"].iloc[0] == 71211000.0
    assert df["high"].iloc[0] == 9999990000.0  # 원본 불변
