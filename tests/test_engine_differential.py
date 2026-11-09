"""차등 테스트: 무작위 데이터·신호·청산 파라미터·비용에서 빠른 엔진 == 기준 엔진."""
import time

import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest import fast
from btc_quant.backtest.fast import Prepared, prepare, run_fast, simulate, simulate_many
from btc_quant.backtest.reference import run_reference
from btc_quant.backtest.signals import minutes, period_bounds
from btc_quant.backtest.types import CostModel
from btc_quant.data import quality
from btc_quant.strategy.blocks.base import Registry
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

REG = Registry()


def _rand(bars, seed, p):
    r = np.random.default_rng([int(seed), len(bars)]).random(len(bars))
    return pd.Series(r < p, index=bars.index)


@REG.register("entry", {"seed": [0], "p": [0.3]}, "test random entry", name="_test_rand_entry")
def _test_rand_entry(bars, ctx, seed=0, p=0.3):
    return _rand(bars, seed, p)


@REG.register("filter", {"seed": [0], "p": [0.7]}, "test random filter", name="_test_rand_filter")
def _test_rand_filter(bars, ctx, seed=0, p=0.7):
    return _rand(bars, seed, p)


@REG.register("exit_signal", {"seed": [0], "p": [0.1]}, "test random exit", name="_test_rand_exit")
def _test_rand_exit(bars, ctx, seed=0, p=0.1):
    return _rand(bars, seed, p)


def synth_m1(rng, n, start=None, gaps=True):
    """기하 랜덤워크 1분봉. 가끔 시가 갭, 누락 구간(→ regularize 로 filled 평탄 봉)."""
    start = start or pd.Timestamp("2025-06-01", tz="UTC") + pd.Timedelta(minutes=int(rng.integers(0, 2880)))
    idx = pd.date_range(start, periods=n, freq="1min", name="ts")
    sig = rng.uniform(0.0005, 0.003)
    lr = rng.normal(0, sig, n)
    c = 100.0 * np.exp(np.cumsum(lr))
    o = np.empty(n)
    o[0] = 100.0
    o[1:] = c[:-1]
    jump = rng.random(n) < 0.01
    o[jump] *= np.exp(rng.normal(0, 4 * sig, jump.sum()))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, sig, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, sig, n)))
    raw = pd.DataFrame({"open": o, "high": h, "low": l, "close": c,
                        "volume": rng.uniform(0, 5, n)}, index=idx)
    if gaps:
        drop = np.zeros(n, dtype=bool)
        for _ in range(int(rng.integers(0, 6))):
            a = int(rng.integers(1, n - 2))
            drop[a:min(n - 1, a + int(rng.integers(1, 40)))] = True
        raw = raw[~drop]
    return quality.regularize(raw)


def random_case(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(500, 3000))
    m1 = synth_m1(rng, n)
    entry = BlockRef("_test_rand_entry", {"seed": seed, "p": float(rng.uniform(0.02, 0.8))})
    filters = ()
    if rng.random() < 0.4:
        filters = (BlockRef("_test_rand_filter", {"seed": seed + 1, "p": float(rng.uniform(0.3, 1))}),)
    sig = None
    if rng.random() < 0.6:
        sig = BlockRef("_test_rand_exit", {"seed": seed + 2, "p": float(rng.uniform(0.01, 0.4))})
    ex = ExitSpec(float(rng.uniform(0.03, 1.5)), float(rng.uniform(0.03, 1.5)),
                  int(rng.integers(1, 40)) * 5, sig)
    spec = StrategySpec(f"rand{seed}", entry, ex, filters)
    cost = CostModel("rand", float(rng.choice([0.0, 0.0005, 0.001])), float(rng.uniform(0, 5)),
                     float(rng.uniform(0, 8)), float(rng.choice([0.0, 0.001, 0.01, 0.05])))
    i0 = m1.index[0]
    span = len(m1)
    start = i0 + pd.Timedelta(minutes=int(rng.integers(-30, span // 3)))
    if rng.random() < 0.3:
        end = m1.index[-1] + pd.Timedelta(minutes=int(rng.integers(-3, 30)))  # end 이후 데이터 없음
    else:
        end = i0 + pd.Timedelta(minutes=int(rng.integers(span // 2, span)))
    return spec, m1, cost, start, end


def assert_same(rf, rr):
    a, b = rf.trades, rr.trades
    assert len(a) == len(b)
    for col in ("entry_time", "exit_time", "exit_reason", "hold_min"):
        assert list(a[col]) == list(b[col]), col
    for col in ("entry_price", "exit_price", "gross_ret", "net_ret"):
        np.testing.assert_allclose(a[col].to_numpy(float), b[col].to_numpy(float), rtol=1e-12, atol=1e-15)
    assert rf.daily_returns.index.equals(rr.daily_returns.index)
    np.testing.assert_allclose(rf.daily_returns.to_numpy(), rr.daily_returns.to_numpy(),
                               rtol=1e-12, atol=1e-15)


SEEDS = list(range(240))


@pytest.mark.parametrize("seed", SEEDS)
def test_fast_equals_reference(seed):
    spec, m1, cost, start, end = random_case(seed)
    rf = run_fast(spec, m1, cost, start, end, registry=REG)
    rr = run_reference(spec, m1, cost, start, end, registry=REG)
    assert_same(rf, rr)
    if len(rf.trades):
        t = rf.trades
        assert (t.entry_time >= rf.start).all() and (t.entry_time < rf.end).all()
        assert (t.exit_time >= t.entry_time).all() and (t.entry_time.diff().dropna() > pd.Timedelta(0)).all()
        assert set(t.exit_reason) <= {"sl", "tp", "signal", "time", "end"}


def test_differential_exercises_all_reasons():
    reasons = set()
    for seed in SEEDS[:80]:
        spec, m1, cost, start, end = random_case(seed)
        reasons |= set(run_fast(spec, m1, cost, start, end, registry=REG).trades.exit_reason)
    assert reasons == {"sl", "tp", "signal", "time", "end"}


def test_low_level_simulate_and_sweep_match_run_fast():
    spec, m1, cost, start, end = random_case(7)
    from btc_quant.backtest.signals import build_signals
    _, ent, exs = build_signals(spec, m1, registry=REG)
    p = prepare(m1, ent, exs)
    _, _, si, ei = period_bounds(m1.index, start, end)
    combos = [(0.2, 0.3, 30), (spec.exit.stop_loss_pct, spec.exit.take_profit_pct, spec.exit.max_hold_min),
              (1.0, 0.1, 5)]
    many = simulate_many(p, combos, cost, start, end)
    rf = run_fast(spec, m1, cost, start, end, registry=REG)
    assert list(m1.index[many[1].entry_idx]) == list(rf.trades.entry_time)
    entry_dec = np.zeros(len(m1), dtype=bool)
    entry_dec[p.cand] = True
    tr = simulate(m1.open, m1.high, m1.low, m1.close, minutes(m1.index), entry_dec, p.exit_dec,
                  *combos[1], cost.side_cost, cost.fee_rate, cost.tick, si, ei)
    np.testing.assert_array_equal(tr.net_ret, many[1].net_ret)
    days, mat = fast.daily_returns_matrix(p, many, start, end)
    np.testing.assert_allclose(mat[1], rf.daily_returns.to_numpy(), rtol=1e-12, atol=1e-15)
    # 각 조합은 기준 엔진과도 일치
    from btc_quant.backtest.reference import run_reference_signals
    for (sl, tp, hold), trs in zip(combos, many):
        rr = run_reference_signals(m1, ent, exs, sl, tp, hold, cost, start, end)
        assert list(m1.index[trs.exit_idx]) == list(rr.trades.exit_time)
        np.testing.assert_allclose(trs.net_ret, rr.trades.net_ret.to_numpy(float), rtol=1e-12)


def test_fast_engine_timing_3m_bars(capsys):
    """성능 측정(하드 단언 아님): 1분봉 ~3,000,000개."""
    rng = np.random.default_rng(0)
    n = 3_000_000
    idx = pd.date_range("2021-01-01", periods=n, freq="1min", tz="UTC", name="ts")
    c = 1e8 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    o = np.r_[1e8, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0005, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0005, n)))
    m1 = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0,
                       "filled": False}, index=idx)
    spec = StrategySpec("timing", BlockRef("_test_rand_entry", {"seed": 1, "p": 0.3}),
                        ExitSpec(0.7, 1.0, 120, BlockRef("_test_rand_exit", {"seed": 2, "p": 0.05})))
    cost = CostModel("t", 0.0005, 2.0, 3.0, 1000.0)
    run_fast(spec, m1.iloc[:10000], cost, idx[0], idx[9999], registry=REG)  # JIT 워밍업
    t0 = time.perf_counter()
    res = run_fast(spec, m1, cost, idx[0], idx[-1], registry=REG)
    t_full = time.perf_counter() - t0
    from btc_quant.backtest.signals import build_signals
    _, ent, exs = build_signals(spec, m1, registry=REG)
    p: Prepared = prepare(m1, ent, exs)
    combos = [(sl, tp, hd) for sl in (0.3, 0.5, 0.7, 1.0, 1.5) for tp in (0.5, 1.0, 1.5, 2.0)
              for hd in (30, 60, 120, 240, 480)]
    t0 = time.perf_counter()
    simulate_many(p, combos, cost, idx[0], idx[-1])
    t_sweep = time.perf_counter() - t0
    with capsys.disabled():
        print(f"\n[timing] run_fast 3M bars: {t_full:.2f}s ({len(res.trades)} trades); "
              f"simulate_many {len(combos)} combos: {t_sweep:.2f}s "
              f"({t_sweep / len(combos) * 1e3:.1f} ms/combo)")
    assert len(res.trades) > 0
