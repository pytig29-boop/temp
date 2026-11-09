"""ENGINE_SPEC 결정 주기 B=1 (timeframe="1m") 시나리오 — 빠른 엔진과 기준 엔진 양쪽으로 실행."""
import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest.fast import prepare, run_fast, simulate_many
from btc_quant.backtest.reference import ReferenceEngine, is_boundary, run_reference
from btc_quant.backtest.signals import boundary_mask, build_signals, to_decisions
from btc_quant.backtest.types import CostModel
from btc_quant.data import quality
from btc_quant.strategy.blocks.base import Registry
from btc_quant.strategy.features import to_5m, to_tf
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

T0 = pd.Timestamp("2026-01-01 00:00", tz="UTC")
ZERO = CostModel("zero", 0.0, 0.0, 0.0, 0.0)
COST = CostModel("test", 0.0005, 2.0, 1.0, 0.0)

REG = Registry()


def _mask(bars, at):
    want = pd.DatetimeIndex([pd.Timestamp(a, tz="UTC") for a in at])
    return pd.Series(bars.index.isin(want), index=bars.index)


@REG.register("entry", {"at": [[]]}, "test: True at given bar starts", name="_test_at")
def _test_at(bars, ctx, at=()):
    return _mask(bars, at)


@REG.register("entry", {}, "test: always True", name="_test_always")
def _test_always(bars, ctx):
    return pd.Series(True, index=bars.index)


@REG.register("exit_signal", {"at": [[]]}, "test exit signal at given bars", name="_test_exit_at")
def _test_exit_at(bars, ctx, at=()):
    return _mask(bars, at)


def t(hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"2026-01-01 {hhmm}", tz="UTC")


def d(hhmm: str) -> str:
    return f"2026-01-01 {hhmm}"


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


def spec1m(entry_at=None, exit_at=None, sl=1.0, tp=1.0, hold=60):
    entry = BlockRef("_test_always") if entry_at is None else BlockRef("_test_at", {"at": [d(a) for a in entry_at]})
    sig = BlockRef("_test_exit_at", {"at": [d(a) for a in exit_at]}) if exit_at else None
    s = StrategySpec("t1", entry, ExitSpec(sl, tp, hold, sig), timeframe="1m")
    s.validate()
    return s


def both(spec, m1, cost=ZERO, start=None, end=None):
    start = T0 if start is None else start
    end = m1.index[-1] + pd.Timedelta(minutes=1) if end is None else end
    rf = run_fast(spec, m1, cost, start, end, registry=REG)
    rr = run_reference(spec, m1, cost, start, end, registry=REG)
    pd.testing.assert_frame_equal(rf.trades, rr.trades, check_exact=True)
    pd.testing.assert_series_equal(rf.daily_returns, rr.daily_returns, check_exact=True)
    return rf.trades


# ---------------------------------------------------------------- 변환·매핑
def test_to_tf_generalizes_to_5m_and_1m_is_identity():
    rng = np.random.default_rng(3)
    m1 = flat_m1(200)
    m1[["open", "high", "low", "close"]] *= rng.uniform(0.99, 1.01, (200, 1))
    m1["high"] = m1[["open", "high", "close"]].max(axis=1)
    m1["low"] = m1[["open", "low", "close"]].min(axis=1)
    raw = m1.drop(columns="filled").drop(pd.date_range(t("00:31"), t("00:44"), freq="1min"))
    g = quality.regularize(raw)
    pd.testing.assert_frame_equal(to_tf(g, 5), to_5m(g))
    b1 = to_tf(g, 1)
    assert b1.index.equals(g.index)
    assert (b1["complete"] == ~g["filled"]).all()
    # 일반 resample 경로와 같은 값
    agg = g.resample("1min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    pd.testing.assert_frame_equal(b1.drop(columns="complete"), agg, check_freq=False)
    # filled 컬럼 없는 입력: 있는 봉 = complete
    b1n = to_tf(raw, 1)
    assert b1n["complete"].all() and len(b1n) == len(raw)


def test_build_signals_uses_spec_timeframe():
    m1 = flat_m1(30)
    bars, ok, _ = build_signals(spec1m(None), m1, registry=REG)
    assert bars.index.equals(m1.index) and ok.all()
    s5 = StrategySpec("t5", BlockRef("_test_always"), ExitSpec(1, 1, 5))
    bars5, _, _ = build_signals(s5, m1, registry=REG)
    assert len(bars5) == 6


def test_decision_mapping_1m():
    m1 = flat_m1(10)
    sig = pd.Series(False, index=m1.index)
    sig[t("00:03")] = True
    assert np.flatnonzero(to_decisions(sig, m1.index, 1)).tolist() == [4]
    assert boundary_mask(m1.index, 1).all()
    assert is_boundary(t("00:07"), 1) and not is_boundary(t("00:07"))


# ---------------------------------------------------------------- 진입
def test_entry_at_next_1m_open():
    m1 = flat_m1()
    setbar(m1, "00:05", c=100.5, h=100.5)            # 결정 봉(1분봉 00:05)의 종가
    setbar(m1, "00:06", o=101.0, h=101.0, l=101.0, c=101.0)
    r = both(spec1m(["00:05"], sl=5, tp=5, hold=7), m1, COST).iloc[0]
    assert r.entry_time == t("00:06")
    assert r.entry_price == COST.buy_fill(101.0)
    assert (r.exit_reason, r.exit_time, r.hold_min) == ("time", t("00:13"), 7)


def test_time_exit_then_cooldown_skips_exit_minute():
    m1 = flat_m1()
    tr = both(spec1m(None, sl=5, tp=5, hold=3), m1, end=t("00:12"))
    # 첫 결정 00:00 은 봉 23:59 가 없으므로 False → 00:01 진입, 00:04 만기 청산(결정 00:04 건너뜀) → 00:05 …
    assert list(tr.entry_time) == [t("00:01"), t("00:05"), t("00:09")]
    assert list(tr.exit_time) == [t("00:04"), t("00:08"), t("00:12")]
    assert list(tr.exit_reason) == ["time", "time", "time"]   # end 봉과 만기가 겹치면 time (해석 4)


def test_cooldown_after_intrabar_exit_skips_next_minute():
    m1 = flat_m1()
    setbar(m1, "00:03", l=98.0)                       # SL 봉 00:03 → t_eff 00:04 → 00:04 건너뛰고 00:05
    tr = both(spec1m(None, sl=1, tp=5, hold=30), m1, end=t("00:06"))
    assert list(tr.entry_time) == [t("00:01"), t("00:05")]
    assert (tr.exit_reason.iloc[0], tr.exit_time.iloc[0], tr.hold_min.iloc[0]) == ("sl", t("00:03"), 3)


def test_sl_on_entry_bar_then_reentry():
    m1 = flat_m1()
    setbar(m1, "00:01", l=98.0)
    tr = both(spec1m(None, sl=1, tp=5, hold=30), m1, end=t("00:04"))
    assert list(tr.entry_time) == [t("00:01"), t("00:03")]
    assert (tr.exit_reason.iloc[0], tr.hold_min.iloc[0]) == ("sl", 1)


def test_tp_and_reference_event_interface_cooldown():
    eng = ReferenceEngine(1.0, 1.0, 10, ZERO, timeframe_min=1)
    eng.on_decision(t("00:05"), True, False)
    assert [e.side for e in eng.on_bar_1m(t("00:05"), 100, 100, 100, 100)] == ["buy"]
    ev = eng.on_bar_1m(t("00:06"), 100, 101.5, 100, 101)
    assert [(e.reason, e.ref_price) for e in ev] == [("tp", pytest.approx(101.0))]
    assert eng.cooldown_until == t("00:08")
    eng.on_decision(t("00:07"), True, False)
    assert eng.on_bar_1m(t("00:07"), 100, 100, 100, 100) == []
    eng.on_decision(t("00:08"), True, False)
    assert [e.side for e in eng.on_bar_1m(t("00:08"), 100, 100, 100, 100)] == ["buy"]


# ---------------------------------------------------------------- 신호 청산
def test_signal_exit_any_minute():
    m1 = flat_m1()
    setbar(m1, "00:10", o=100.4, h=100.4, l=100.4, c=100.4)
    r = both(spec1m(["00:05"], exit_at=["00:09"], sl=5, tp=5, hold=60), m1).iloc[0]
    assert (r.entry_time, r.exit_reason, r.exit_time, r.hold_min) == (t("00:06"), "signal", t("00:10"), 4)
    assert r.gross_ret == pytest.approx(0.004)


def test_signal_exit_ignored_on_entry_bar_and_next_minute_exit():
    m1 = flat_m1()
    r = both(spec1m(["00:05"], exit_at=["00:05"], sl=5, tp=5, hold=20), m1).iloc[0]
    assert r.exit_reason == "time"
    r = both(spec1m(["00:05"], exit_at=["00:05", "00:06"], sl=5, tp=5, hold=20), m1).iloc[0]
    assert (r.exit_reason, r.exit_time, r.hold_min) == ("signal", t("00:07"), 1)


def test_time_priority_over_signal_1m():
    m1 = flat_m1()
    r = both(spec1m(["00:05"], exit_at=["00:08"], sl=5, tp=5, hold=3), m1).iloc[0]
    assert (r.exit_reason, r.exit_time) == ("time", t("00:09"))


def test_signal_exit_on_filled_bar_still_exits():
    m1 = flat_m1(60)
    raw = m1.drop(columns="filled").drop([t("00:09")])
    g = quality.regularize(raw)
    r = both(spec1m(["00:05"], exit_at=["00:09"], sl=5, tp=5, hold=30), g).iloc[0]
    assert (r.exit_reason, r.exit_time) == ("signal", t("00:10"))


# ---------------------------------------------------------------- filled 봉
def test_no_entry_when_decision_bar_or_entry_bar_filled():
    m1 = flat_m1(60)
    raw = m1.drop(columns="filled").drop(pd.date_range(t("00:10"), t("00:12"), freq="1min"))
    g = quality.regularize(raw)
    assert g["filled"].sum() == 3
    # 00:09 → 진입 봉 00:10 filled; 00:10~00:12 결정 봉 filled(불완전) → 첫 진입은 봉 00:13 기반 00:14
    tr = both(spec1m(["00:09", "00:10", "00:11", "00:12", "00:13"], sl=5, tp=5, hold=2), g)
    assert list(tr.entry_time) == [t("00:14")]


def test_gap_after_filled_bars_is_sl_gap_1m():
    m1 = flat_m1()
    raw = m1.drop(columns="filled").drop(pd.date_range(t("00:08"), t("00:10"), freq="1min"))
    raw.loc[t("00:11"), ["open", "high", "low", "close"]] = [97.0, 97.2, 96.8, 97.0]
    g = quality.regularize(raw)
    r = both(spec1m(["00:05"], sl=1, tp=5, hold=60), g).iloc[0]
    assert (r.entry_time, r.exit_reason, r.exit_time, r.hold_min) == (t("00:06"), "sl", t("00:11"), 6)
    assert r.gross_ret == pytest.approx(-0.03)


def test_sweep_api_1m_matches_run_fast():
    m1 = flat_m1(400)
    rng = np.random.default_rng(1)
    m1["close"] = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, 400)))
    m1["open"] = np.r_[100.0, m1["close"].to_numpy()[:-1]]
    m1["high"] = m1[["open", "close"]].max(axis=1) * 1.001
    m1["low"] = m1[["open", "close"]].min(axis=1) * 0.999
    spec = spec1m(None, sl=0.3, tp=0.4, hold=13)
    _, ent, exs = build_signals(spec, m1, registry=REG)
    p = prepare(m1, ent, exs, timeframe="1m")
    assert p.timeframe_min == 1
    many = simulate_many(p, [(0.3, 0.4, 13), (0.5, 0.5, 7)], ZERO, T0, m1.index[-1])
    rf = run_fast(spec, m1, ZERO, T0, m1.index[-1], registry=REG)
    assert list(m1.index[many[0].entry_idx]) == list(rf.trades.entry_time)
    np.testing.assert_array_equal(many[0].net_ret, rf.trades.net_ret.to_numpy())
    assert len(many[1].entry_idx) > 10


@pytest.mark.parametrize("seed", range(12))
def test_decision_mapping_fast_path_equals_reindex(seed):
    from btc_quant.backtest.signals import _shift_to_decisions
    rng = np.random.default_rng(seed)
    idx = pd.date_range(T0, periods=500, freq="1min", name="ts")
    if seed % 3 == 1:                                  # 공백이 있는 (regularize 안 된) index
        idx = idx[rng.random(500) > 0.2]
    b = 1 if seed % 2 else 5
    sidx = pd.date_range(T0 - pd.Timedelta(minutes=30), periods=120, freq=f"{b}min")
    if seed % 3 == 2:                                  # 중복·비정렬 신호 시각, 분 단위가 아닌 시각
        sidx = sidx.append(pd.DatetimeIndex([sidx[3], sidx[3] + pd.Timedelta(seconds=30)]))
    for vals, fill in ((rng.random(len(sidx)) < 0.5, False), (rng.normal(size=len(sidx)), np.nan)):
        sig = pd.Series(vals, index=sidx)
        shifted = pd.Series(vals, index=sidx + pd.Timedelta(minutes=b))
        want = shifted[~shifted.index.duplicated()].reindex(idx, fill_value=fill).to_numpy()
        got = _shift_to_decisions(sig, idx, b, fill)
        np.testing.assert_array_equal(got, want)
