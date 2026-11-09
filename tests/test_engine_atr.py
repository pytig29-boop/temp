"""ATR 배수 청산 (ENGINE_SPEC "ATR 청산") — 레벨 계산, 워밍업, 인과성, 스윕 API, 두 엔진 일치."""
import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest import fast
from btc_quant.backtest.fast import (
    prepare,
    prepare_spec,
    run_fast,
    simulate_exit,
    simulate_many,
    with_atr,
)
from btc_quant.backtest.reference import (
    ReferenceEngine,
    is_boundary,
    run_reference,
    run_reference_signals,
)
from btc_quant.backtest.signals import (
    atr_frac_decisions,
    atr_series,
    atr_values,
    build_signals,
    true_range,
)
from btc_quant.backtest.types import CostModel
from btc_quant.data import quality
from btc_quant.strategy.blocks.base import Registry
from btc_quant.strategy.features import to_tf
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

T0 = pd.Timestamp("2026-01-01 00:00", tz="UTC")
ZERO = CostModel("zero", 0.0, 0.0, 0.0, 0.0)
COST = CostModel("test", 0.0005, 2.0, 1.0, 0.0)
REG = Registry()


@REG.register("entry", {}, "test: always True", name="_test_always")
def _test_always(bars, ctx):
    return pd.Series(True, index=bars.index)


@REG.register("entry", {"at": [[]]}, "test: True at given bar starts", name="_test_at")
def _test_at(bars, ctx, at=()):
    want = pd.DatetimeIndex([pd.Timestamp(a, tz="UTC") for a in at])
    return pd.Series(bars.index.isin(want), index=bars.index)


@REG.register("entry", {"seed": [0], "p": [0.3]}, "test random entry", name="_test_rand_entry")
def _test_rand_entry(bars, ctx, seed=0, p=0.3):
    r = np.random.default_rng([int(seed), len(bars)]).random(len(bars))
    return pd.Series(r < p, index=bars.index)


@REG.register("exit_signal", {"seed": [0], "p": [0.1]}, "test random exit", name="_test_rand_exit")
def _test_rand_exit(bars, ctx, seed=0, p=0.1):
    r = np.random.default_rng([int(seed), len(bars), 7]).random(len(bars))
    return pd.Series(r < p, index=bars.index)


def t(hhmm):
    return pd.Timestamp(f"2026-01-01 {hhmm}", tz="UTC")


def wiggle_m1(n=200, wiggle_minute=None, h=100.5, l=99.5):
    """시가=종가=100. wiggle_minute=None 이면 모든 봉 (h,l), 아니면 분 % 5 == wiggle_minute 인 봉만."""
    idx = pd.date_range(T0, periods=n, freq="1min", name="ts")
    m1 = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0,
                       "filled": False}, index=idx)
    sel = np.ones(n, bool) if wiggle_minute is None else (idx.minute % 5 == wiggle_minute)
    m1.loc[sel, "high"] = h
    m1.loc[sel, "low"] = l
    return m1


def atr_spec(sl, tp, hold, tf="1m", n=14, entry=None, sig=None):
    s = StrategySpec("atr", entry or BlockRef("_test_always"),
                     ExitSpec(stop_loss_atr=sl, take_profit_atr=tp, max_hold_min=hold, atr_bars=n,
                              signal_exit=sig), timeframe=tf)
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


# ---------------------------------------------------------------- ATR 정의
def test_atr_definition_matches_textbook():
    rng = np.random.default_rng(0)
    n = 300
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    bars = pd.DataFrame({"open": np.r_[100, c[:-1]], "close": c}, index=pd.RangeIndex(n))
    bars["high"] = bars[["open", "close"]].max(axis=1) * (1 + rng.uniform(0, 0.002, n))
    bars["low"] = bars[["open", "close"]].min(axis=1) * (1 - rng.uniform(0, 0.002, n))
    pc = bars["close"].shift(1)
    tr = pd.concat([bars.high - bars.low, (bars.high - pc).abs(), (bars.low - pc).abs()], axis=1).max(axis=1)
    tr[0] = np.nan
    np.testing.assert_allclose(true_range(bars), tr.to_numpy(), rtol=0, atol=0)
    for k in (1, 5, 14):
        want = tr.rolling(k, min_periods=k).mean().to_numpy()
        got = atr_series(bars, k).to_numpy()
        assert np.isnan(got[:k]).all() and not np.isnan(got[k:]).any()
        np.testing.assert_allclose(got[k:], want[k:], rtol=1e-12)
    # 창마다 독립 계산: 후행 창에서 다시 계산해도 비트 동일 (페이퍼 엔진 재현성)
    full = atr_values(true_range(bars), 14)
    win = atr_values(true_range(bars.iloc[150:]), 14)
    np.testing.assert_array_equal(full[150 + 14:], win[14:])


def test_atr_levels_1m_by_hand():
    m1 = wiggle_m1()                                 # 모든 봉 TR = 1.0 → ATR = 1, a = 0.01
    m1.loc[t("00:09"), "high"] = 101.25
    tr = both(atr_spec(1.0, 1.2, 60, n=5), m1, COST, end=t("00:10"))
    r = tr.iloc[0]
    # ATR_5 은 봉 00:05 부터 정의 → 첫 진입 00:06. tp = 100·(1 + 1.2·0.01) = 101.2 < 101.25
    assert (r.entry_time, r.exit_reason, r.exit_time, r.hold_min) == (t("00:06"), "tp", t("00:09"), 4)
    tp = 100.0 * (1.0 + 1.2 * (1.0 / 100.0))
    assert r.gross_ret == tp / 100.0 - 1
    assert r.exit_price == COST.sell_fill(tp)
    # SL 도: sl = 100·(1 − 0.5·0.01) = 99.5 → 모든 봉 저가 99.5 가 닿는다 → 진입 봉에서 SL
    r = both(atr_spec(0.5, 3.0, 60, n=5), m1, end=t("00:07")).iloc[0]
    assert (r.entry_time, r.exit_reason, r.hold_min) == (t("00:06"), "sl", 1)
    assert r.gross_ret == 100.0 * (1.0 - 0.5 * (1.0 / 100.0)) / 100.0 - 1


def test_atr_levels_5m_by_hand():
    m1 = wiggle_m1(300, wiggle_minute=2, h=100.6, l=99.4)   # 5분봉 TR = 1.2 → a = 0.012
    bars = to_tf(m1, 5)
    a = atr_series(bars, 3)
    assert a.iloc[3:].to_numpy() == pytest.approx(1.2, abs=1e-12) and np.isnan(a.iloc[:3]).all()
    m1.loc[t("00:33"), "high"] = 101.3
    r = both(atr_spec(1.0, 1.0, 60, tf="5m", n=3), m1, end=t("00:35")).iloc[0]
    # ATR_3 은 5분봉 00:15 부터 → 결정 00:20 진입. tp = 100·(1 + a) ≈ 101.2 → 00:33 고가 101.3 에서 TP
    assert (r.entry_time, r.exit_reason, r.exit_time) == (t("00:20"), "tp", t("00:33"))
    frac = float(a[t("00:15")]) / 100.0
    assert r.gross_ret == 100.0 * (1.0 + 1.0 * frac) / 100.0 - 1


def test_no_entry_during_warmup_or_zero_atr():
    m1 = wiggle_m1()
    tr = both(atr_spec(1.0, 1.0, 3, n=14), m1, end=t("00:16"))
    assert tr.entry_time.iloc[0] == t("00:15")       # ATR_14 은 봉 00:14 부터 정의
    flat = wiggle_m1(h=100.0, l=100.0)               # ATR = 0 → 진입 불가
    assert len(both(atr_spec(1.0, 1.0, 3), flat)) == 0
    pct = StrategySpec("p", BlockRef("_test_always"), ExitSpec(1.0, 1.0, 3), timeframe="1m")
    assert len(both(pct, flat)) > 0                  # 같은 데이터, % 모드는 진입


def test_reference_engine_needs_atr_in_atr_mode():
    eng = ReferenceEngine.from_exit(ExitSpec(stop_loss_atr=1.0, take_profit_atr=2.0, max_hold_min=10),
                                    ZERO, timeframe="1m")
    eng.on_decision(t("00:05"), True, False)          # atr 없음 → 진입 안 함
    assert eng.on_bar_1m(t("00:05"), 100, 100, 100, 100) == []
    eng.on_decision(t("00:06"), True, False, float("nan"))
    assert eng.on_bar_1m(t("00:06"), 100, 100, 100, 100) == []
    eng.on_decision(t("00:07"), True, False, 0.5)
    assert [e.side for e in eng.on_bar_1m(t("00:07"), 100, 100, 100, 100)] == ["buy"]
    pos = eng.position
    assert (pos.atr_frac, pos.sl, pos.tp) == (0.005, 100 * (1 - 0.005), 100 * (1 + 2 * 0.005))


# ---------------------------------------------------------------- 인과성
def synth(seed, n=2000):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(T0, periods=n, freq="1min", name="ts")
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0015, n)))
    o = np.r_[100.0, c[:-1]]
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.001, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.001, n)))
    raw = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0}, index=idx)
    return quality.regularize(raw.drop(idx[700:712]))


def perturb_after(m1, ts, seed):
    """ts 이후 봉(ts 봉은 시가만 유지) 가격을 무작위로 바꾼다."""
    rng = np.random.default_rng(seed)
    out = m1.copy()
    sel = out.index >= ts
    k = int(sel.sum())
    f = np.exp(rng.normal(0, 0.01, (k, 4)))
    vals = out.loc[sel, ["open", "high", "low", "close"]].to_numpy() * f
    vals[0, 0] = out.loc[ts, "open"]
    vals[:, 1] = vals.max(axis=1)
    vals[:, 2] = vals[:, [0, 2, 3]].min(axis=1)
    out.loc[sel, ["open", "high", "low", "close"]] = vals
    return out


class _Recorder(ReferenceEngine):
    """진입 직후의 Position 을 기록 (같은 봉에서 청산돼도 레벨을 볼 수 있게)."""
    def _open(self, ts, o, frac=None):
        ev = super()._open(ts, o, frac)
        self.opened = self.position
        return ev


def first_position(spec, m1, after):
    bars, ok, ex = build_signals(spec, m1, registry=REG)
    atr = atr_series(bars, spec.exit.atr_bars)
    b = spec.tf_min
    step = pd.Timedelta(minutes=b)
    ex_ = spec.exit
    eng = _Recorder(None, None, ex_.max_hold_min, ZERO, after, m1.index[-1], timeframe_min=b,
                    sl_atr=ex_.stop_loss_atr, tp_atr=ex_.take_profit_atr)
    eng.opened = None
    for ts, o, h, l, c, f in zip(m1.index, m1.open, m1.high, m1.low, m1.close, m1.filled):
        if is_boundary(ts, b):
            eng.on_decision(ts, ok.get(ts - step, False), ex.get(ts - step, False), atr.get(ts - step))
        eng.on_bar_1m(ts, o, h, l, c, f)
        if eng.opened is not None:
            return eng.opened
    raise AssertionError("no entry")


@pytest.mark.parametrize("tf,seed", [("1m", 1), ("1m", 2), ("5m", 3), ("5m", 4)])
def test_levels_causal_perturbing_bars_after_entry(tf, seed):
    m1 = synth(seed)
    spec = atr_spec(1.5, 2.5, 60, tf=tf, n=10, entry=BlockRef("_test_rand_entry", {"seed": seed, "p": 0.05}))
    after = m1.index[900]
    pos = first_position(spec, m1, after)
    m2 = perturb_after(m1, pos.entry_time, seed + 100)
    pos2 = first_position(spec, m2, after)
    assert (pos2.entry_time, pos2.ref, pos2.atr_frac, pos2.sl, pos2.tp) == \
        (pos.entry_time, pos.ref, pos.atr_frac, pos.sl, pos.tp)
    # 빠른 엔진: 진입 행까지의 ATR 비율 불변, 첫 거래 진입 동일
    i = m1.index.get_loc(pos.entry_time)
    f1 = prepare_spec(spec, m1, registry=REG).atr_frac[10]
    f2 = prepare_spec(spec, m2, registry=REG).atr_frac[10]
    np.testing.assert_array_equal(f1[: i + 1], f2[: i + 1])
    assert f1[i] == pos.atr_frac
    r1 = run_fast(spec, m1, ZERO, after, m1.index[-1], registry=REG).trades.iloc[0]
    r2 = run_fast(spec, m2, ZERO, after, m1.index[-1], registry=REG).trades.iloc[0]
    assert r1.entry_time == r2.entry_time == pos.entry_time
    # 결정 봉 이후(진입 봉 포함)를 바꾸면 거래 결과는 달라질 수 있지만, 결정 봉을 바꾸면 레벨이 바뀐다 (대조)
    m3 = perturb_after(m1, pos.entry_time - pd.Timedelta(minutes=spec.tf_min), seed + 200)
    m3.loc[pos.entry_time:, ["open", "high", "low", "close"]] = m1.loc[pos.entry_time:, ["open", "high", "low", "close"]]
    pos3 = first_position(spec, m3, after)
    assert pos3.entry_time != pos.entry_time or pos3.atr_frac != pos.atr_frac


# ---------------------------------------------------------------- 스윕 API
@pytest.mark.parametrize("tf", ["1m", "5m"])
def test_atr_sweep_matches_reference(tf):
    m1 = synth(11, 3000)
    entry = BlockRef("_test_rand_entry", {"seed": 5, "p": 0.1 if tf == "1m" else 0.4})
    sig = BlockRef("_test_rand_exit", {"seed": 6, "p": 0.05})
    spec = atr_spec(1.0, 2.0, 30, tf=tf, n=14, entry=entry, sig=sig)
    bars, ent, exs = build_signals(spec, m1, registry=REG)
    start, end = m1.index[100], m1.index[2900]
    p = prepare(m1, ent, exs, timeframe=tf, atr_bars=[7, 14])
    assert sorted(p.atr_frac) == [7, 14] and p.timeframe_min == spec.tf_min
    combos = [(1.0, 2.0, 30), (0.5, 0.8, 60), (3.0, 1.0, 15)]
    for n in (7, 14):
        many = simulate_many(p, combos, COST, start, end, atr_bars=n)
        atr = atr_series(bars, n)
        for (k_sl, k_tp, hold), tr in zip(combos, many):
            rr = run_reference_signals(m1, ent, exs, None, None, hold, COST, start, end,
                                       timeframe=tf, atr=atr, sl_atr=k_sl, tp_atr=k_tp)
            res = fast.to_result(p, tr, COST, start, end)
            pd.testing.assert_frame_equal(res.trades, rr.trades, check_exact=True)
            assert len(rr.trades) > 3
    # ExitSpec 경로 · run_fast 와 같은 결과
    rf = run_fast(spec, m1, COST, start, end, registry=REG)
    _, _, si, ei = p.bounds(start, end)
    tr = simulate_exit(p, spec.exit, COST, si, ei)
    np.testing.assert_array_equal(tr.net_ret, rf.trades.net_ret.to_numpy())
    # % 모드 스윕은 같은 Prepared 에서 그대로 동작
    pct = simulate_many(p, [(0.5, 0.5, 30)], COST, start, end)[0]
    rr = run_reference_signals(m1, ent, exs, 0.5, 0.5, 30, COST, start, end, timeframe=tf)
    np.testing.assert_array_equal(pct.net_ret, rr.trades.net_ret.to_numpy())


def test_with_atr_and_missing_window_error():
    m1 = synth(12, 600)
    spec = atr_spec(1.0, 2.0, 30, tf="5m", n=14)
    _, ent, exs = build_signals(spec, m1, registry=REG)
    p = prepare(m1, ent, exs)
    with pytest.raises(KeyError, match="ATR window 14"):
        simulate_many(p, [(1.0, 2.0, 30)], ZERO, m1.index[0], m1.index[-1], atr_bars=14)
    p2 = with_atr(p, m1, 14)
    assert 14 in p2.atr_frac and not p.atr_frac
    np.testing.assert_array_equal(
        p2.atr_frac[14], atr_frac_decisions(atr_series(to_tf(m1, 5), 14), m1.open, m1.index, 5))
    # 결정 행이 아닌 곳은 NaN
    assert np.isnan(p2.atr_frac[14][m1.index.minute % 5 != 0]).all()
