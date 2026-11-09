"""ENGINE_SPEC 규칙별 시나리오 — 모든 시나리오를 빠른 엔진과 기준 엔진 양쪽으로 실행한다."""
import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest.fast import run_fast
from btc_quant.backtest.reference import ReferenceEngine, run_reference
from btc_quant.backtest.signals import build_signals, to_decisions
from btc_quant.backtest.types import CostModel
from btc_quant.data import quality
from btc_quant.strategy.blocks.base import Registry
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

T0 = pd.Timestamp("2026-01-01 00:00", tz="UTC")
ZERO = CostModel("zero", 0.0, 0.0, 0.0, 0.0)
COST = CostModel("test", 0.0005, 2.0, 1.0, 0.0)

REG = Registry()


def _mask(bars, at):
    want = pd.DatetimeIndex([pd.Timestamp(a, tz="UTC") for a in at])
    return pd.Series(bars.index.isin(want), index=bars.index)


@REG.register("entry", {"at": [[]]}, "test: True at given 5m bar starts", name="_test_at")
def _test_at(bars, ctx, at=()):
    return _mask(bars, at)


@REG.register("entry", {}, "test: always True", name="_test_always")
def _test_always(bars, ctx):
    return pd.Series(True, index=bars.index)


@REG.register("filter", {"at": [[]]}, "test filter: True except at given bars", name="_test_not_at")
def _test_not_at(bars, ctx, at=()):
    return ~_mask(bars, at)


@REG.register("exit_signal", {"at": [[]]}, "test exit signal at given bars", name="_test_exit_at")
def _test_exit_at(bars, ctx, at=()):
    return _mask(bars, at)


def t(hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"2026-01-01 {hhmm}", tz="UTC")


def flat_m1(n=120, price=100.0):
    idx = pd.date_range(T0, periods=n, freq="1min", name="ts")
    df = pd.DataFrame({"open": price, "high": price, "low": price, "close": price,
                       "volume": 1.0}, index=idx)
    df["filled"] = False
    return df


def setbar(m1, hhmm, o=None, h=None, l=None, c=None):
    ts = t(hhmm)
    for k, v in (("open", o), ("high", h), ("low", l), ("close", c)):
        if v is not None:
            m1.loc[ts, k] = v
    r = m1.loc[ts]
    m1.loc[ts, "high"] = max(r["high"], r["open"], r["close"])
    m1.loc[ts, "low"] = min(r["low"], r["open"], r["close"])


def make_spec(entry_at=None, exit_at=None, sl=1.0, tp=1.0, hold=60, filter_not_at=None):
    entry = BlockRef("_test_always") if entry_at is None else BlockRef("_test_at", {"at": list(entry_at)})
    filters = (BlockRef("_test_not_at", {"at": list(filter_not_at)}),) if filter_not_at else ()
    sig = BlockRef("_test_exit_at", {"at": list(exit_at)}) if exit_at else None
    return StrategySpec("t", entry, ExitSpec(sl, tp, hold, sig), filters)


def both(spec, m1, cost=ZERO, start=None, end=None):
    start = T0 if start is None else start
    end = m1.index[-1] + pd.Timedelta(minutes=1) if end is None else end
    rf = run_fast(spec, m1, cost, start, end, registry=REG)
    rr = run_reference(spec, m1, cost, start, end, registry=REG)
    pd.testing.assert_frame_equal(rf.trades, rr.trades, check_exact=False, rtol=1e-12, atol=0)
    pd.testing.assert_series_equal(rf.daily_returns, rr.daily_returns, check_exact=False,
                                   rtol=1e-12, atol=1e-15)
    return rf.trades


def one(tr):
    assert len(tr) >= 1
    return tr.iloc[0]


# ---------------------------------------------------------------- 진입
def test_entry_fills_at_next_1m_open_not_decision_bar_close():
    m1 = flat_m1()
    setbar(m1, "00:09", c=100.5, h=100.5)       # 결정 5분봉(00:05)의 종가
    setbar(m1, "00:10", o=101.0, h=101.0, l=101.0, c=101.0)
    tr = both(make_spec(["2026-01-01 00:05"], sl=5, tp=5, hold=10), m1, COST)
    r = one(tr)
    assert r.entry_time == t("00:10")
    assert r.entry_price == pytest.approx(COST.buy_fill(101.0), rel=1e-15)
    assert r.exit_reason == "time" and r.exit_time == t("00:20")
    assert r.gross_ret == pytest.approx(100.0 / 101.0 - 1)
    assert r.net_ret == pytest.approx(COST.net_return(COST.buy_fill(101.0), COST.sell_fill(100.0)))


def test_entry_only_on_complete_5m_bars():
    m1 = flat_m1(60)
    m1.loc[t("00:02"), "filled"] = True          # 5분봉 00:00 불완전 → 00:05 결정 진입 불가
    tr = both(make_spec(None, sl=5, tp=5, hold=5), m1, end=t("00:12"))
    assert one(tr).entry_time == t("00:10")
    _, entry_ok, _ = build_signals(make_spec(None), m1, registry=REG)
    assert not entry_ok[t("00:00")] and entry_ok[t("00:05")]


def test_filter_blocks_entry():
    m1 = flat_m1(60)
    tr = both(make_spec(None, sl=5, tp=5, hold=5, filter_not_at=["2026-01-01 00:00"]), m1,
              end=t("00:12"))
    assert one(tr).entry_time == t("00:10")


def test_no_entry_before_start_or_at_end():
    m1 = flat_m1(60)
    tr = both(make_spec(["2026-01-01 00:00", "2026-01-01 00:20"], sl=5, tp=5, hold=5), m1,
              start=t("00:06"), end=t("00:25"))
    assert len(tr) == 0


# ---------------------------------------------------------------- 청산 규칙
def test_sl_touch():
    m1 = flat_m1()
    setbar(m1, "00:13", l=98.5)
    r = one(both(make_spec(["2026-01-01 00:05"], sl=1, tp=5, hold=60), m1, COST))
    assert (r.exit_reason, r.exit_time, r.hold_min) == ("sl", t("00:13"), 4)
    assert r.gross_ret == pytest.approx(99.0 / 100.0 - 1)
    assert r.exit_price == pytest.approx(COST.sell_fill(100.0 * (1 - 0.01)), rel=1e-15)


def test_sl_touch_on_entry_bar():
    m1 = flat_m1()
    setbar(m1, "00:10", l=98.0)
    r = one(both(make_spec(["2026-01-01 00:05"], sl=1, tp=5, hold=60), m1))
    assert (r.exit_reason, r.exit_time, r.hold_min) == ("sl", t("00:10"), 1)


def test_sl_gap_uses_open():
    m1 = flat_m1()
    setbar(m1, "00:12", c=99.5, l=99.5)
    setbar(m1, "00:13", o=98.0, h=98.2, l=97.5, c=98.0)
    r = one(both(make_spec(["2026-01-01 00:05"], sl=1, tp=5, hold=60), m1))
    assert (r.exit_reason, r.exit_time, r.hold_min) == ("sl", t("00:13"), 4)
    assert r.gross_ret == pytest.approx(98.0 / 100.0 - 1)


def test_tp_needs_tick():
    cost = CostModel("tick", 0.0, 0.0, 0.0, tick=0.5)
    m1 = flat_m1()
    setbar(m1, "00:12", h=101.4)                 # tp=101, tp+tick=101.5 미달
    setbar(m1, "00:15", h=101.5)
    r = one(both(make_spec(["2026-01-01 00:05"], sl=5, tp=1, hold=60), m1, cost))
    assert (r.exit_reason, r.exit_time, r.hold_min) == ("tp", t("00:15"), 6)
    assert r.gross_ret == pytest.approx(0.01)


def test_tp_gap_up_still_tp_level():
    m1 = flat_m1()
    setbar(m1, "00:12", o=103.0, h=103.0, l=103.0, c=103.0)
    r = one(both(make_spec(["2026-01-01 00:05"], sl=5, tp=1, hold=60), m1))
    assert r.exit_reason == "tp" and r.gross_ret == pytest.approx(0.01)


def test_same_bar_sl_and_tp_is_sl():
    m1 = flat_m1()
    setbar(m1, "00:12", h=102.0, l=98.0)
    r = one(both(make_spec(["2026-01-01 00:05"], sl=1, tp=1, hold=60), m1))
    assert (r.exit_reason, r.exit_time) == ("sl", t("00:12"))
    assert r.gross_ret == pytest.approx(-0.01)


def test_time_exit_at_open():
    m1 = flat_m1()
    setbar(m1, "00:24", c=100.2, h=100.2)
    setbar(m1, "00:25", o=100.3, h=100.3, l=100.3, c=100.3)
    r = one(both(make_spec(["2026-01-01 00:05"], sl=5, tp=5, hold=15), m1))
    assert (r.exit_reason, r.exit_time, r.hold_min) == ("time", t("00:25"), 15)
    assert r.gross_ret == pytest.approx(0.003)


def test_time_exit_before_sl_check_of_that_bar():
    m1 = flat_m1()
    setbar(m1, "00:25", l=90.0)                  # 만기 봉의 저가는 무시 (시가에 이미 청산)
    r = one(both(make_spec(["2026-01-01 00:05"], sl=1, tp=5, hold=15), m1))
    assert (r.exit_reason, r.gross_ret) == ("time", 0.0)


def test_signal_exit_at_open():
    m1 = flat_m1()
    setbar(m1, "00:20", o=100.4, h=100.4, l=100.4, c=100.4)
    r = one(both(make_spec(["2026-01-01 00:05"], exit_at=["2026-01-01 00:15"], sl=5, tp=5,
                           hold=60), m1))
    assert (r.exit_reason, r.exit_time, r.hold_min) == ("signal", t("00:20"), 10)
    assert r.gross_ret == pytest.approx(0.004)


def test_signal_exit_ignored_on_entry_bar():
    m1 = flat_m1()
    r = one(both(make_spec(["2026-01-01 00:05"], exit_at=["2026-01-01 00:05"], sl=5, tp=5,
                           hold=60), m1))
    assert r.exit_reason == "time"


def test_signal_exit_before_sl_check_of_that_bar():
    m1 = flat_m1()
    setbar(m1, "00:20", l=90.0)
    r = one(both(make_spec(["2026-01-01 00:05"], exit_at=["2026-01-01 00:15"], sl=1, tp=5,
                           hold=60), m1))
    assert (r.exit_reason, r.gross_ret) == ("signal", 0.0)


def test_time_has_priority_over_signal():
    m1 = flat_m1()
    r = one(both(make_spec(["2026-01-01 00:05"], exit_at=["2026-01-01 00:15"], sl=5, tp=5,
                           hold=10), m1))
    assert (r.exit_reason, r.exit_time) == ("time", t("00:20"))


# ---------------------------------------------------------------- 쿨다운
def test_cooldown_after_intrabar_exit_skips_one_decision():
    m1 = flat_m1()
    setbar(m1, "00:12", l=98.0)                  # 진입 00:05, SL 00:12 (t_eff 00:13)
    tr = both(make_spec(None, sl=1, tp=5, hold=10), m1, end=t("00:21"))
    assert list(tr.entry_time[:2]) == [t("00:05"), t("00:20")]   # 00:15 건너뜀


def test_cooldown_intrabar_exit_on_last_minute_before_boundary():
    m1 = flat_m1()
    setbar(m1, "00:14", l=98.0)                  # t_eff = 00:15 → 00:15 건너뛰고 00:20
    tr = both(make_spec(None, sl=1, tp=5, hold=10), m1, end=t("00:21"))
    assert list(tr.entry_time[:2]) == [t("00:05"), t("00:20")]


def test_cooldown_after_open_exit_skips_one_decision():
    m1 = flat_m1()
    tr = both(make_spec(None, sl=5, tp=5, hold=15), m1, end=t("01:00"))
    # 00:05 진입 → 00:20 만기 청산(그 시점 결정은 건너뜀) → 00:25 재진입
    assert list(tr.entry_time) == [t("00:05"), t("00:25"), t("00:45")]
    assert list(tr.exit_time[:2]) == [t("00:20"), t("00:40")]


# ---------------------------------------------------------------- 기간 종료
def test_end_of_period_exits_at_next_open():
    m1 = flat_m1()
    setbar(m1, "00:30", o=100.7, h=100.7, l=100.7, c=100.7)
    r = one(both(make_spec(["2026-01-01 00:05"], sl=5, tp=5, hold=100), m1, end=t("00:30")))
    assert (r.exit_reason, r.exit_time, r.hold_min) == ("end", t("00:30"), 20)
    assert r.gross_ret == pytest.approx(0.007)


def test_end_without_data_uses_last_close():
    m1 = flat_m1(30)                             # 마지막 봉 00:29
    setbar(m1, "00:29", c=100.9, h=100.9)
    r = one(both(make_spec(["2026-01-01 00:05"], sl=5, tp=5, hold=100), m1, end=t("01:00")))
    assert (r.exit_reason, r.exit_time, r.hold_min) == ("end", t("00:29"), 20)
    assert r.gross_ret == pytest.approx(0.009)


# ---------------------------------------------------------------- 데이터 공백
def test_filled_gap_then_sl_gap():
    m1 = flat_m1()
    raw = m1.drop(columns="filled").drop(pd.date_range(t("00:14"), t("00:20"), freq="1min"))
    raw.loc[t("00:21"), ["open", "high", "low", "close"]] = [97.0, 97.2, 96.8, 97.0]
    m1g = quality.regularize(raw)
    assert m1g["filled"].sum() == 7
    r = one(both(make_spec(["2026-01-01 00:05"], sl=1, tp=5, hold=60), m1g))
    assert (r.exit_reason, r.exit_time, r.hold_min) == ("sl", t("00:21"), 12)
    assert r.gross_ret == pytest.approx(-0.03)


def test_no_entry_on_incomplete_bar_or_filled_bar():
    m1 = flat_m1(80)
    raw = m1.drop(columns="filled").drop(pd.date_range(t("00:15"), t("00:21"), freq="1min"))
    m1g = quality.regularize(raw)
    # 5분봉 00:10 은 완전하지만 진입 봉 00:15 는 filled(합성 가격) → 진입 안 함
    # 5분봉 00:15, 00:20 불완전 → 00:20, 00:25 진입 안 함 → 00:30 진입
    tr = both(make_spec(["2026-01-01 00:10", "2026-01-01 00:15", "2026-01-01 00:20",
                         "2026-01-01 00:25"], sl=5, tp=5, hold=5), m1g)
    assert list(tr.entry_time) == [t("00:30")]


# ---------------------------------------------------------------- 기타
def test_to_decisions_mapping():
    m1 = flat_m1(30)
    bars, _, _ = build_signals(make_spec(None), m1, registry=REG)
    sig = pd.Series(False, index=bars.index)
    sig[t("00:05")] = True
    d = to_decisions(sig, m1.index)
    assert np.flatnonzero(d).tolist() == [10]


def test_reference_engine_event_interface():
    eng = ReferenceEngine(1.0, 1.0, 10, ZERO)
    eng.on_decision(t("00:05"), True, False)
    ev = eng.on_bar_1m(t("00:05"), 100, 100, 100, 100)
    assert [e.side for e in ev] == ["buy"] and eng.position is not None
    ev = eng.on_bar_1m(t("00:06"), 100, 101.5, 100, 101)
    assert [(e.side, e.reason, e.ref_price) for e in ev] == [("sell", "tp", pytest.approx(101.0))]
    assert eng.cooldown_until == t("00:15")
    eng.on_decision(t("00:10"), True, False)
    assert eng.on_bar_1m(t("00:10"), 100, 100, 100, 100) == []


def test_daily_returns_include_empty_days():
    idx = pd.date_range("2026-01-01", periods=3 * 1440, freq="1min", tz="UTC", name="ts")
    m1 = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
                       "volume": 1.0, "filled": False}, index=idx)
    m1.loc[pd.Timestamp("2026-01-02 00:13", tz="UTC"), "high"] = 102.0
    spec = make_spec(["2026-01-02 00:05"], sl=5, tp=1, hold=60)
    tr = both(spec, m1, COST, start=idx[0], end=pd.Timestamp("2026-01-04", tz="UTC"))
    rf = run_fast(spec, m1, COST, idx[0], pd.Timestamp("2026-01-04", tz="UTC"), registry=REG)
    assert len(rf.daily_returns) == 3
    assert rf.daily_returns.iloc[0] == 0 and rf.daily_returns.iloc[2] == 0
    assert rf.daily_returns.iloc[1] == pytest.approx(tr.net_ret.iloc[0])
