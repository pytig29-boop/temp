"""지정가(메이커) 집행 — 스펙 하위 호환·검증, 1분봉 관통 체결 규칙 시나리오, 빠른/기준 엔진 차등(≥ 200 사례).

docs/ENGINE_SPEC.md "지정가 집행".
"""
import numpy as np
import pandas as pd
import pytest
from test_engine_differential import synth_m1

from btc_quant.backtest import fast
from btc_quant.backtest.fast import _hash_u01, run_fast
from btc_quant.backtest.reference import ReferenceEngine, run_reference
from btc_quant.backtest.types import (
    LIMIT_FILL_DEFAULT,
    CostModel,
    LimitFillModel,
    hash_u01,
)
from btc_quant.strategy.blocks.base import Registry
from btc_quant.strategy.spec import (
    MAX_PARAMS,
    BlockRef,
    ExecutionSpec,
    ExitSpec,
    StrategySpec,
    tf_minutes,
)

REG = Registry()
ZERO = CostModel("zero", 0.0, 0.0, 0.0, 0.0)
COST = CostModel("c", 0.0005, 2.0, 1.0, 0.0)
T0 = pd.Timestamp("2026-01-01 00:00", tz="UTC")
# 시나리오용: 관통 1 tick (tick 0.01 = 1bp at 100), bp 조건 없음
F1 = LimitFillModel("t", through_ticks=1, tick=0.01, through_bp=0.0, tick_from_data=False)


def _rand(bars, seed, p):
    r = np.random.default_rng([int(seed), len(bars), 77]).random(len(bars))
    return pd.Series(r < p, index=bars.index)


@REG.register("entry", {"seed": [0], "p": [0.3]}, "test random entry", name="_lm_rand_entry")
def _lm_rand_entry(bars, ctx, seed=0, p=0.3):
    return _rand(bars, seed, p)


@REG.register("exit_signal", {"seed": [0], "p": [0.1]}, "test random exit", name="_lm_rand_exit")
def _lm_rand_exit(bars, ctx, seed=0, p=0.1):
    return _rand(bars, seed, p)


@REG.register("entry", {"at": [[]]}, "test: True at given bar starts", name="_lm_at")
def _lm_at(bars, ctx, at=()):
    want = pd.DatetimeIndex([pd.Timestamp(a, tz="UTC") for a in at])
    return pd.Series(bars.index.isin(want), index=bars.index)


# ================================================================ 스펙
def _base():
    return StrategySpec("a", BlockRef("bollinger_z_revert", {"window": 48, "z": 2.5}), ExitSpec(0.7, 1.0, 60))


def test_default_execution_is_hash_compatible():
    a = _base()
    assert "execution" not in a.to_dict()
    assert a.execution.is_default
    b = StrategySpec.from_dict(a.to_dict())
    assert b == a and b.logic_hash() == a.logic_hash()
    # 명시적 기본값도 같은 해시
    c = StrategySpec(a.id, a.entry, a.exit, execution=ExecutionSpec("market", take_profit="market"))
    assert c.logic_hash() == a.logic_hash()
    assert StrategySpec.from_dict({**a.to_dict(), "execution": {}}).logic_hash() == a.logic_hash()


def test_limit_execution_roundtrip_hash_and_params():
    a = _base()
    ex = ExecutionSpec("limit", offset_bp=5.0, tif_bars=3, take_profit="limit")
    b = StrategySpec(a.id, a.entry, a.exit, execution=ex)
    b.validate()
    d = b.to_dict()
    assert d["execution"] == {"entry": "limit", "offset_bp": 5.0, "tif_bars": 3, "take_profit": "limit"}
    assert StrategySpec.from_dict(d) == b
    hashes = {a.logic_hash(), b.logic_hash(),
              StrategySpec(a.id, a.entry, a.exit, execution=ExecutionSpec("limit", offset_bp=5.0)).logic_hash(),
              StrategySpec(a.id, a.entry, a.exit, execution=ExecutionSpec(take_profit="limit")).logic_hash(),
              StrategySpec(a.id, a.entry, a.exit,
                           execution=ExecutionSpec("limit", offset_atr=0.5)).logic_hash()}
    assert len(hashes) == 5
    # 파라미터 수: 오프셋 +1, tif ≠ 1 이면 +1, 익절 방식은 모드
    assert b.n_params() == a.n_params() + 2
    assert StrategySpec(a.id, a.entry, a.exit, execution=ExecutionSpec(take_profit="limit")).n_params() == a.n_params()


def test_yaml_roundtrip_limit():
    import yaml
    b = StrategySpec("x", BlockRef("e"), ExitSpec(stop_loss_atr=2, take_profit_atr=3, max_hold_min=60),
                     execution=ExecutionSpec("limit", offset_atr=0.25, tif_bars=2))
    assert StrategySpec.from_dict(yaml.safe_load(b.to_yaml())) == b


@pytest.mark.parametrize("ex,msg", [
    (ExecutionSpec("limit"), "exactly one"),
    (ExecutionSpec("limit", offset_bp=1.0, offset_atr=1.0), "exactly one"),
    (ExecutionSpec("market", offset_bp=1.0), "only valid"),
    (ExecutionSpec("market", tif_bars=2), "only valid"),
    (ExecutionSpec("limit", offset_bp=-1.0), "offset_bp"),
    (ExecutionSpec("limit", offset_atr=0.0), "offset_atr"),
    (ExecutionSpec("limit", offset_bp=1.0, tif_bars=0), "tif_bars"),
    (ExecutionSpec("ioc"), "entry must be"),
    (ExecutionSpec(take_profit="post"), "take_profit"),
])
def test_execution_validation(ex, msg):
    s = StrategySpec("a", BlockRef("e"), ExitSpec(0.7, 1.0, 60), execution=ex)
    with pytest.raises(ValueError, match=msg):
        s.validate()


def test_unknown_execution_field_rejected():
    with pytest.raises(ValueError, match="unknown execution"):
        ExecutionSpec.from_dict({"entry": "limit", "offset": 3})


def test_max_params_counts_limit():
    e = BlockRef("e", {"a": 1, "b": 2, "c": 3})
    s = StrategySpec("a", e, ExitSpec(0.7, 1.0, 60), execution=ExecutionSpec("limit", offset_bp=1, tif_bars=2))
    assert s.n_params() == 3 + 3 + 2 == MAX_PARAMS
    s.validate()
    s2 = StrategySpec("a", BlockRef("e", {"a": 1, "b": 2, "c": 3, "d": 4}), ExitSpec(0.7, 1.0, 60),
                      execution=ExecutionSpec("limit", offset_bp=1, tif_bars=2))
    with pytest.raises(ValueError, match="params"):
        s2.validate()


# ================================================================ 체결 모델
def test_fill_model_validation_and_threshold():
    LIMIT_FILL_DEFAULT.validate()
    with pytest.raises(ValueError, match="trade-through"):
        LimitFillModel(tick=0.0, through_bp=0.0, tick_from_data=False).validate()
    with pytest.raises(ValueError, match="through_ticks"):
        LimitFillModel(through_ticks=0).validate()
    with pytest.raises(ValueError, match="fill_prob"):
        LimitFillModel(fill_prob=0.0).validate()
    m = LimitFillModel(through_ticks=2, tick=0.01, through_bp=1.0)
    assert m.threshold(100.0) == pytest.approx(0.02)          # 2 tick > 1bp(0.01)
    assert LimitFillModel(through_ticks=1, tick=0.01, through_bp=3.0).threshold(100.0) == pytest.approx(0.03)
    assert m.buy_price(99.987) == pytest.approx(99.98) and m.sell_price(100.001) == pytest.approx(100.01)
    assert LimitFillModel().buy_price(99.987) == 99.987        # tick 0 = 연속 가격
    assert m.buy_price(99.987, 0.05) == pytest.approx(99.95)
    assert LIMIT_FILL_DEFAULT.tick_from_data and LIMIT_FILL_DEFAULT.through_ticks == 1
    assert np.isnan(LIMIT_FILL_DEFAULT.order_tick(None)) and np.isnan(LIMIT_FILL_DEFAULT.order_tick(0.0))
    assert LIMIT_FILL_DEFAULT.order_tick(10.0) == 10.0
    assert LimitFillModel(tick=0.01, tick_from_data=False).order_tick(5.0) == 0.01


def test_hash_u01_python_equals_numba_and_uniform():
    keys = np.arange(29_000_000, 29_000_000 + 4000, dtype=np.int64)
    for salt in (0, 1, 2, 7, 2 ** 20):
        a = np.array([hash_u01(int(k), salt) for k in keys])
        b = np.array([_hash_u01(np.int64(k), np.int64(salt)) for k in keys])
        assert np.array_equal(a, b)
        assert ((a >= 0) & (a < 1)).all() and abs(a.mean() - 0.5) < 0.03


# ================================================================ 시나리오 (양 엔진 동일 + 기대값)
def flat_m1(n=200, price=100.0, start=T0):
    idx = pd.date_range(start, periods=n, freq="1min", name="ts")
    df = pd.DataFrame({"open": price, "high": price, "low": price, "close": price, "volume": 1.0}, index=idx)
    df["filled"] = False
    return df


def setbar(m1, ts, o=None, h=None, l=None, c=None, v=None, filled=None):
    ts = pd.Timestamp(ts, tz="UTC")
    for k, x in (("open", o), ("high", h), ("low", l), ("close", c), ("volume", v)):
        if x is not None:
            m1.loc[ts, k] = x
    if filled is not None:
        m1.loc[ts, "filled"] = filled
    r = m1.loc[ts]
    m1.loc[ts, "high"] = max(r["high"], r["open"], r["close"])
    m1.loc[ts, "low"] = min(r["low"], r["open"], r["close"])


def both(spec, m1, cost=ZERO, fill=F1, start=None, end=None):
    start = m1.index[0] if start is None else start
    end = m1.index[-1] + pd.Timedelta(minutes=1) if end is None else end
    rf = run_fast(spec, m1, cost, start, end, registry=REG, fill=fill)
    rr = run_reference(spec, m1, cost, start, end, registry=REG, fill=fill)
    pd.testing.assert_frame_equal(rf.trades, rr.trades, check_exact=True)
    pd.testing.assert_series_equal(rf.daily_returns, rr.daily_returns, check_exact=True)
    return rf.trades


def lspec(entry_at, off=100.0, tif=1, tp="market", sl=5.0, tpp=5.0, hold=60, tf="1m", entry="limit"):
    ex = (ExecutionSpec("limit", offset_bp=off, tif_bars=tif, take_profit=tp) if entry == "limit"
          else ExecutionSpec(take_profit=tp))
    s = StrategySpec("s", BlockRef("_lm_at", {"at": list(entry_at)}), ExitSpec(sl, tpp, hold), timeframe=tf,
                     execution=ex)
    s.validate()
    return s


def test_touch_is_not_enough_trade_through_fills_at_limit():
    # 결정 00:10 (1m: 봉 00:09 신호) → 00:10 시가 100 기준 지정가 L = 99 (100bp)
    m1 = flat_m1()
    setbar(m1, "2026-01-01 00:11", l=99.0)                 # 터치만 → 미체결
    setbar(m1, "2026-01-01 00:12", l=99.0 - 0.005)         # 관통 0.5 tick → 미체결 (1 tick 필요)
    t = both(lspec(["2026-01-01 00:09"], tif=10), m1)
    assert t.empty
    setbar(m1, "2026-01-01 00:13", l=99.0 - 0.01)          # 정확히 1 tick 관통 → 체결
    t = both(lspec(["2026-01-01 00:09"], tif=10), m1)
    assert len(t) == 1
    r = t.iloc[0]
    assert r.entry_time == pd.Timestamp("2026-01-01 00:13", tz="UTC")
    assert r.entry_price == 99.0                           # 체결가 = 지정가 (비용 없음, 가격 개선 없음)


def test_maker_entry_no_spread_slippage_fee_only():
    m1 = flat_m1()
    setbar(m1, "2026-01-01 00:10", l=98.9)
    t = both(lspec(["2026-01-01 00:09"], sl=50, tpp=50, hold=5), m1, cost=COST)
    r = t.iloc[0]
    assert r.entry_price == 99.0 and r.exit_reason == "time"
    assert r.exit_price == pytest.approx(100.0 * (1 - COST.side_cost))      # 시장가 청산은 비용
    assert r.net_ret == pytest.approx(COST.net_return(99.0, 100.0 * (1 - COST.side_cost)))


def test_tif_expiry_then_new_order():
    # 5m, tif 1 봉: 00:05 주문 (00:05..00:09 유효), 00:10 에 만료 → 00:10 결정으로 새 주문
    m1 = flat_m1()
    setbar(m1, "2026-01-01 00:10", l=98.98)    # 첫 주문은 이미 만료, 두 번째 주문이 이 봉에서 체결
    s = lspec(["2026-01-01 00:00", "2026-01-01 00:05"], tf="5m", sl=50, tpp=50, hold=20)
    t = both(s, m1)
    assert list(t.entry_time) == [pd.Timestamp("2026-01-01 00:10", tz="UTC")]
    # tif 2 봉이면 첫 주문(00:05)이 00:10 에 체결, 00:10 결정은 무시
    s2 = lspec(["2026-01-01 00:00", "2026-01-01 00:05"], tf="5m", tif=2, sl=50, tpp=50, hold=20)
    t2 = both(s2, m1)
    assert list(t2.entry_time) == [pd.Timestamp("2026-01-01 00:10", tz="UTC")] and len(t2) == 1


def test_order_resting_ignores_new_decisions_and_expires():
    m1 = flat_m1()
    s = lspec(["2026-01-01 00:00", "2026-01-01 00:05"], tf="5m", tif=3)
    setbar(m1, "2026-01-01 00:16", l=98.0)     # 00:05 주문 만료(00:20)? 아님: 00:05 결정 주문은 무시됨,
    t = both(s, m1)                            # 00:05 봉 신호 → 00:10 결정... 주문(00:05 결정)은 00:20 까지
    assert list(t.entry_time) == [pd.Timestamp("2026-01-01 00:16", tz="UTC")]
    m2 = flat_m1()
    setbar(m2, "2026-01-01 00:21", l=98.0)     # 00:05 주문은 00:20 만료 → 00:21 하락에도 미체결
    assert both(s, m2).empty


def test_gap_open_below_limit_fills_at_limit_and_gap_stop():
    m1 = flat_m1()
    setbar(m1, "2026-01-01 00:11", o=98.5, h=98.6, l=98.4, c=98.5)    # 갭 하락: L=99 체결, 손절 99×0.995=98.505
    t = both(lspec(["2026-01-01 00:09"], tif=5, sl=0.5, tpp=1.0), m1)
    r = t.iloc[0]
    assert r.entry_price == 99.0 and r.entry_time == pd.Timestamp("2026-01-01 00:11", tz="UTC")
    assert r.exit_reason == "sl" and r.exit_time == r.entry_time
    assert r.gross_ret == pytest.approx(98.5 / 99.0 - 1)                 # 시가(갭)로 손절
    assert r.hold_min == 1


def test_no_take_profit_on_fill_bar_but_stop_yes():
    m1 = flat_m1()
    # 체결 봉에서 고가가 익절 이상이어도 익절 없음 (고가가 체결 전일 수 있음)
    setbar(m1, "2026-01-01 00:10", h=101.0, l=98.9)       # L = 99, 익절 99 × 1.015 = 100.485 < 101
    t = both(lspec(["2026-01-01 00:09"], sl=1.0, tpp=1.5, hold=5), m1)
    assert t.iloc[0].exit_reason == "time"
    # 체결 봉에서 손절 터치는 손절
    m2 = flat_m1()
    setbar(m2, "2026-01-01 00:10", h=101.0, l=97.0)
    t2 = both(lspec(["2026-01-01 00:09"], sl=1.0, tpp=1.0, hold=5), m2)
    assert t2.iloc[0].exit_reason == "sl" and t2.iloc[0].gross_ret == pytest.approx(-0.01)
    # 다음 봉의 익절은 정상
    m3 = flat_m1()
    setbar(m3, "2026-01-01 00:10", l=98.9)
    setbar(m3, "2026-01-01 00:11", h=100.0)
    t3 = both(lspec(["2026-01-01 00:09"], sl=1.0, tpp=1.0, hold=5), m3)
    assert t3.iloc[0].exit_reason == "tp" and t3.iloc[0].gross_ret == pytest.approx(0.01)


def test_limit_take_profit_needs_trade_through_and_has_no_cost():
    m1 = flat_m1()
    # 시장가 진입 00:10 시가 100, 익절 지정가 T = 101 (1%); 101 터치 → 미체결, 101.01 → 체결
    setbar(m1, "2026-01-01 00:12", h=101.0)
    s = lspec(["2026-01-01 00:09"], entry="market", tp="limit", sl=2.0, tpp=1.0, hold=10)
    t = both(s, m1, cost=COST)
    assert t.iloc[0].exit_reason == "time"
    setbar(m1, "2026-01-01 00:13", h=101.01)
    t = both(s, m1, cost=COST)
    r = t.iloc[0]
    assert r.exit_reason == "tp" and r.exit_price == pytest.approx(101.0)             # 비용 없음
    assert r.entry_price == pytest.approx(100.0 * (1 + COST.side_cost))               # 시장가 진입은 비용
    assert r.net_ret == pytest.approx(COST.net_return(r.entry_price, 101.0))
    # 시장가 익절(기존)은 터치 + cost.tick 에서 비용 반영 체결
    s_m = lspec(["2026-01-01 00:09"], entry="market", tp="market", sl=2.0, tpp=1.0, hold=10)
    r2 = both(s_m, m1, cost=COST).iloc[0]
    assert r2.exit_reason == "tp" and r2.exit_time == pd.Timestamp("2026-01-01 00:12", tz="UTC")


def test_market_entry_limit_tp_can_fill_on_entry_bar():
    m1 = flat_m1()
    setbar(m1, "2026-01-01 00:10", h=101.5)
    s = lspec(["2026-01-01 00:09"], entry="market", tp="limit", sl=2.0, tpp=1.0, hold=10)
    r = both(s, m1).iloc[0]
    assert r.exit_reason == "tp" and r.exit_time == r.entry_time and r.hold_min == 1


def test_filled_bars_and_min_volume_never_fill():
    m1 = flat_m1()
    setbar(m1, "2026-01-01 00:11", l=98.0, filled=True)
    s = lspec(["2026-01-01 00:09"], tif=5)
    assert both(s, m1).empty
    m2 = flat_m1()
    setbar(m2, "2026-01-01 00:11", l=98.0, v=0.5)
    fv = LimitFillModel("v", through_ticks=1, tick=0.01, through_bp=0.0, min_volume=1.0, tick_from_data=False)
    assert both(s, m2, fill=fv).empty
    setbar(m2, "2026-01-01 00:12", l=98.0, v=1.0)
    assert len(both(s, m2, fill=fv)) == 1


def test_fill_prob_deterministic_thinning():
    rng = np.random.default_rng(3)
    m1 = synth_m1(rng, 4000, gaps=False)
    s = StrategySpec("s", BlockRef("_lm_rand_entry", {"seed": 1, "p": 0.5}), ExitSpec(0.5, 0.5, 30),
                     timeframe="5m", execution=ExecutionSpec("limit", offset_bp=5.0, tif_bars=2,
                                                             take_profit="limit"))
    full = both(s, m1, fill=LimitFillModel(tick_from_data=False, through_bp=1.0))
    half = both(s, m1, fill=LimitFillModel(tick_from_data=False, through_bp=1.0, fill_prob=0.5))
    again = both(s, m1, fill=LimitFillModel(tick_from_data=False, through_bp=1.0, fill_prob=0.5))
    other = both(s, m1, fill=LimitFillModel(tick_from_data=False, through_bp=1.0, fill_prob=0.5, seed=1))
    pd.testing.assert_frame_equal(half, again)                     # 재현 가능
    assert len(half) < len(full) and not half.equals(other)
    assert both(s, m1, fill=LimitFillModel(tick_from_data=False, through_bp=1.0, fill_prob=1e-9)).empty


def test_tick_rounding_buy_down_sell_up():
    m1 = flat_m1(price=100.003)
    setbar(m1, "2026-01-01 00:10", l=98.97)
    s = lspec(["2026-01-01 00:09"], off=100.0, tif=1, sl=50, tpp=50, hold=5)
    r = both(s, m1).iloc[0]
    assert r.entry_price == pytest.approx(np.floor(100.003 * 0.99 / 0.01) * 0.01)


def test_atr_offset_with_pct_exits_both_engines():
    rng = np.random.default_rng(11)
    m1 = synth_m1(rng, 3000)
    s = StrategySpec("s", BlockRef("_lm_rand_entry", {"seed": 5, "p": 0.4}), ExitSpec(0.5, 0.6, 60),
                     timeframe="5m", execution=ExecutionSpec("limit", offset_atr=0.5, tif_bars=2))
    t = both(s, m1, fill=LimitFillModel(tick_from_data=False, through_bp=1.0))
    assert len(t) > 5


def test_prepared_carries_execution_and_reprice_keeps_maker_prices():
    rng = np.random.default_rng(12)
    m1 = synth_m1(rng, 3000, gaps=False)
    s = StrategySpec("s", BlockRef("_lm_rand_entry", {"seed": 2, "p": 0.5}), ExitSpec(0.4, 0.4, 30),
                     timeframe="5m", execution=ExecutionSpec("limit", offset_bp=3.0, take_profit="limit"))
    start, end = m1.index[0], m1.index[-1]
    p = fast.prepare_spec(s, m1, registry=REG)
    assert p.execution == s.execution
    _, _, si, ei = p.bounds(start, end)
    fb = LimitFillModel(tick_from_data=False, through_bp=1.0)
    tr = fast.simulate_exit(p, s.exit, COST, si, ei, fill=fb)
    res = fast.to_result(p, tr, COST, start, end)
    ref = run_reference(s, m1, COST, start, end, registry=REG, fill=fb)
    pd.testing.assert_frame_equal(res.trades, ref.trades, check_exact=True)
    assert tr.entry_maker.all() and tr.exit_maker.any()
    z = fast.reprice(tr, ZERO)
    assert np.array_equal(z.net_ret, z.gross_ret)
    assert np.array_equal(z.entry_price, tr.entry_ref)
    # 시장가로 강제 (execution 인자 우선)
    trm = fast.simulate_exit(p, s.exit, COST, si, ei, execution=ExecutionSpec())
    assert not trm.entry_maker.any()


def test_default_execution_unchanged_vs_core():
    """시장가(기본) 집행은 기존 _core 경로 그대로 — _core_x 로 강제해도 같은 결과."""
    rng = np.random.default_rng(21)
    for seed in range(15):
        m1 = synth_m1(rng, 2000)
        s = StrategySpec("s", BlockRef("_lm_rand_entry", {"seed": seed, "p": 0.3}),
                         ExitSpec(0.3, 0.4, 25, BlockRef("_lm_rand_exit", {"seed": seed, "p": 0.1})))
        p = fast.prepare_spec(s, m1, registry=REG)
        _, _, si, ei = p.bounds(m1.index[0], m1.index[-1])
        a = fast.simulate_exit(p, s.exit, COST, si, ei)
        raw = fast._core_x(p.o, p.h, p.l, p.c, p.v, p.filled, p.tmin, p.cand, p.exit_dec, 0.3, 0.4, 25,
                           COST.tick, si, ei, 5, False, fast._NO_FRAC, False, False, 0.0, 5, False,
                           1.0, 0.0, 1.0, 1.0, 0.0, 0, False, fast._NO_FRAC)
        b = fast._finish(raw, COST.side_cost, COST.fee_rate)
        for x, y in zip(a, b):
            assert np.array_equal(np.asarray(x), np.asarray(y))


def test_reference_engine_fill_oracle_overrides_candle_rule():
    eng = ReferenceEngine(5.0, 5.0, 10, ZERO, timeframe_min=1,
                          execution=ExecutionSpec("limit", offset_bp=100.0, tif_bars=5), fill=F1)
    asked = []

    def oracle(order, ts, bar):
        asked.append((order.kind, ts))
        return ts == pd.Timestamp("2026-01-01 00:02", tz="UTC")

    eng.fill_oracle = oracle
    t = [T0 + pd.Timedelta(minutes=k) for k in range(5)]
    eng.on_decision(t[0], True, False)
    assert eng.on_bar_1m(t[0], 100, 100, 50, 100) == []          # 1분봉 규칙이면 체결이지만 오라클은 아니라고 함
    assert eng.order is not None and eng.order.price == 99.0
    assert eng.on_bar_1m(t[1], 100, 100, 100, 100) == []
    ev = eng.on_bar_1m(t[2], 100, 100, 100, 100)
    assert len(ev) == 1 and ev[0].side == "buy" and ev[0].fill_price == 99.0
    assert eng.position.maker and eng.order is None
    assert [a[0] for a in asked] == ["entry"] * 3
    assert [o["status"] for o in eng.orders_log] == ["placed", "filled"]


# ================================================================ 차등: 무작위 지정가 사례 ≥ 200
N_MIN = {1: (400, 2500), 5: (800, 4000), 15: (1500, 6000), 60: (3000, 9000)}
TFS = ("1m", "5m", "15m", "1h")


def random_case_limit(seed):
    rng = np.random.default_rng([seed, 9191])
    tf = TFS[seed % len(TFS)]
    b = tf_minutes(tf)
    lo, hi = N_MIN[b]
    m1 = synth_m1(rng, int(rng.integers(lo, hi)))
    from_data = rng.random() < 0.4
    if from_data:   # 호가 격자 위 가격 (0.01) → 데이터 추정 호가 단위
        m1 = m1.copy()
        for k in ("open", "high", "low", "close"):
            m1[k] = np.round(m1[k].to_numpy() * 100) / 100
        m1["high"] = m1[["open", "high", "close"]].max(axis=1)
        m1["low"] = m1[["open", "low", "close"]].min(axis=1)
    entry = BlockRef("_lm_rand_entry", {"seed": seed, "p": float(rng.uniform(0.05, 0.3 if b == 1 else 0.8))})
    sig = BlockRef("_lm_rand_exit", {"seed": seed + 2, "p": float(rng.uniform(0.01, 0.3))}) \
        if rng.random() < 0.5 else None
    hold = int(rng.integers(1, 120)) if b == 1 else int(rng.integers(1, 10)) * b
    scale = {1: 1.0, 5: 1.0, 15: 1.5, 60: 2.5}[b]
    atr_exit = rng.random() < 0.4
    if atr_exit:
        ex = ExitSpec(stop_loss_atr=float(rng.uniform(0.2, 3)), take_profit_atr=float(rng.uniform(0.2, 4)),
                      max_hold_min=hold, signal_exit=sig, atr_bars=int(rng.choice([14, int(rng.integers(1, 10))])))
    else:
        ex = ExitSpec(float(rng.uniform(0.05, 1.0) * scale), float(rng.uniform(0.05, 1.0) * scale), hold, sig)
    mode = rng.random()
    tp = "limit" if rng.random() < 0.5 else "market"
    if mode < 0.15:
        exe = ExecutionSpec(take_profit="limit")                             # 시장가 진입 + 익절 지정가
    elif mode < 0.6:
        exe = ExecutionSpec("limit", offset_bp=float(rng.choice([0.0, rng.uniform(0, 40)])),
                            tif_bars=int(rng.integers(1, 5)), take_profit=tp)
    else:
        exe = ExecutionSpec("limit", offset_atr=float(rng.uniform(0.05, 1.5)), tif_bars=int(rng.integers(1, 5)),
                            take_profit=tp)
    spec = StrategySpec(f"lm{seed}", entry, ex, timeframe=tf, execution=exe)
    spec.exit.validate(b)          # 테스트 블록 파라미터 때문에 MAX_PARAMS 는 검사하지 않는다
    spec.execution.validate()
    tick = float(rng.choice([0.0, 0.001, 0.01]))
    fill = LimitFillModel("r", through_ticks=int(rng.integers(1, 4)), tick=tick,
                          through_bp=float(rng.choice([0.0, 0.5, 1.0, 3.0])) if tick > 0
                          else float(rng.choice([0.5, 1.0, 3.0])),
                          fill_prob=float(rng.choice([1.0, 1.0, rng.uniform(0.2, 1.0)])),
                          min_volume=float(rng.choice([0.0, 0.0, rng.uniform(0, 3)])),
                          seed=int(rng.integers(0, 5)), tick_from_data=from_data)
    cost = CostModel("rand", float(rng.choice([0.0, 0.0005])), float(rng.uniform(0, 5)),
                     float(rng.uniform(0, 8)), float(rng.choice([0.0, 0.001, 0.01])))
    i0 = m1.index[0]
    span = len(m1)
    start = i0 + pd.Timedelta(minutes=int(rng.integers(-30, span // 3)))
    if rng.random() < 0.3:
        end = m1.index[-1] + pd.Timedelta(minutes=int(rng.integers(-3, 30)))
    else:
        end = i0 + pd.Timedelta(minutes=int(rng.integers(span // 2, span)))
    return spec, m1, cost, fill, start, end


LM_SEEDS = list(range(240))


@pytest.mark.parametrize("seed", LM_SEEDS)
def test_fast_equals_reference_limit(seed):
    spec, m1, cost, fill, start, end = random_case_limit(seed)
    rf = run_fast(spec, m1, cost, start, end, registry=REG, fill=fill)
    rr = run_reference(spec, m1, cost, start, end, registry=REG, fill=fill)
    pd.testing.assert_frame_equal(rf.trades, rr.trades, check_exact=True)
    pd.testing.assert_series_equal(rf.daily_returns, rr.daily_returns, check_exact=True)
    t = rf.trades
    if len(t) and spec.execution.limit_entry:
        assert (t.entry_time >= rf.start).all() and (t.entry_time < rf.end).all()
        # 지정가 진입 체결가 = 지정가 ≤ 그 봉 저가 + 관통 폭 이하로 거래된 가격
        lows = m1["low"].reindex(t.entry_time).to_numpy()
        assert (lows <= t.entry_price.to_numpy() + 1e-12).all()


def test_limit_differential_covers_modes_and_outcomes():
    seen = {"maker_entry": 0, "maker_tp": 0, "market_entry": 0, "unfilled": 0, "atr_off": 0, "bp_off": 0}
    reasons = set()
    for seed in LM_SEEDS[:120]:
        spec, m1, cost, fill, start, end = random_case_limit(seed)
        p = fast.prepare_spec(spec, m1, registry=REG)
        _, _, si, ei = p.bounds(start, end)
        tr = fast.simulate_exit(p, spec.exit, cost, si, ei, fill=fill)
        seen["maker_entry"] += int(tr.entry_maker.sum())
        seen["maker_tp"] += int(tr.exit_maker.sum())
        seen["market_entry"] += int((~tr.entry_maker).sum())
        if spec.execution.limit_entry:
            seen["atr_off" if spec.execution.offset_mode == "atr" else "bp_off"] += 1
            seen["unfilled"] += int(len(tr.entry_idx) < len(fast.simulate_exit(
                p, spec.exit, cost, si, ei, execution=ExecutionSpec()).entry_idx))
        reasons |= set(fast.REASONS[tr.reason.astype(int)]) if len(tr.reason) else set()
    assert min(seen.values()) > 0, seen
    assert {"sl", "tp", "time", "signal"} <= reasons


def test_inferred_ticks_used_for_default_model():
    """기본 모델(tick_from_data): 격자 가격에서 추정한 tick 으로 1 tick 관통, 지정가는 격자로 내림."""
    from btc_quant.backtest.signals import infer_ticks
    m1 = flat_m1(price=100.0)
    rng = np.random.default_rng(0)
    m1["close"] = np.round(100 + rng.normal(0, 0.05, len(m1)), 2)
    m1["open"] = m1["close"].shift(1).fillna(100.0)
    m1["high"] = m1[["open", "close"]].max(axis=1)
    m1["low"] = m1[["open", "close"]].min(axis=1)
    ticks = infer_ticks(m1)
    assert (ticks == 0.01).all()
    o = float(m1.loc["2026-01-01 00:10", "open"])
    L = np.floor(o * (1 - 30e-4) / 0.01) * 0.01
    setbar(m1, "2026-01-01 00:11", l=L)              # 터치 → 미체결
    setbar(m1, "2026-01-01 00:12", l=L - 0.01)       # 1 tick 관통 → 체결
    s = lspec(["2026-01-01 00:09"], off=30.0, tif=5, sl=50, tpp=50, hold=5)
    t = both(s, m1, fill=LIMIT_FILL_DEFAULT)
    assert len(t) == 1 and t.iloc[0].entry_time == pd.Timestamp("2026-01-01 00:12", tz="UTC")
    assert t.iloc[0].entry_price == pytest.approx(L)
    # 호가 단위를 추정할 수 없는 가격(연속값)이면 지정가를 걸지 않는다
    m2 = synth_m1(np.random.default_rng(1), 400, start=T0, gaps=False)
    assert (infer_ticks(m2) == 0).all()
    s2 = StrategySpec("s", BlockRef("_lm_rand_entry", {"seed": 1, "p": 0.9}), ExitSpec(0.5, 0.5, 10),
                      timeframe="1m", execution=ExecutionSpec("limit", offset_bp=0.0, tif_bars=5))
    assert both(s2, m2, fill=LIMIT_FILL_DEFAULT).empty


def test_coinone_tick_switch_inferred():
    from btc_quant.backtest.signals import infer_ticks
    idx = pd.date_range("2024-09-28", periods=3 * 1440, freq="1min", tz="UTC", name="ts")
    rng = np.random.default_rng(2)
    px = 85e6 + np.cumsum(rng.normal(0, 3e4, len(idx)))
    grid = np.where(idx < pd.Timestamp("2024-09-30", tz="UTC"), 1000.0, 10000.0)
    c = np.round(px / grid) * grid
    m1 = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0, "filled": False}, index=idx)
    t = pd.Series(infer_ticks(m1), index=idx).resample("D").first()
    assert list(t) == [1000.0, 1000.0, 10000.0]
