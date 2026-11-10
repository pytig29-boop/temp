"""부분 사이징·분할 매수 사다리 — 스펙 하위 호환·검증, 시나리오, 빠른/기준 엔진 차등(≥ 300 사례).

docs/ENGINE_SPEC.md "부분 사이징·분할 매수".
"""
import numpy as np
import pandas as pd
import pytest
from test_engine_differential import synth_m1
from test_engine_limit import F1, REG, ZERO, flat_m1, setbar

from btc_quant.backtest import fast
from btc_quant.backtest.fast import run_fast
from btc_quant.backtest.reference import ReferenceEngine, run_reference
from btc_quant.backtest.sizing import (equity_returns, frame_position_returns, inventory_profile,
                                       mtm_daily_returns, n_positions)
from btc_quant.backtest.types import TRADE_COLUMNS, CostModel, LimitFillModel
from btc_quant.research import universe as U
from btc_quant.strategy.spec import (
    MAX_PARAMS,
    BlockRef,
    ExecutionSpec,
    ExitSpec,
    LadderSpec,
    SizingSpec,
    StrategySpec,
    tf_minutes,
)

T0 = pd.Timestamp("2026-01-01 00:00", tz="UTC")
COST = CostModel("c", 0.0005, 2.0, 1.0, 0.0)


def S(k):
    return str(T0 + pd.Timedelta(minutes=k)).replace("+00:00", "")


def both(spec, m1, cost=ZERO, fill=F1, start=None, end=None):
    start = m1.index[0] if start is None else start
    end = m1.index[-1] + pd.Timedelta(minutes=1) if end is None else end
    rf = run_fast(spec, m1, cost, start, end, registry=REG, fill=fill)
    rr = run_reference(spec, m1, cost, start, end, registry=REG, fill=fill)
    pd.testing.assert_frame_equal(rf.trades, rr.trades, check_exact=True)
    pd.testing.assert_series_equal(rf.daily_returns, rr.daily_returns, check_exact=True)
    return rf


def lad_spec(at=(9,), n=3, step=100.0, order="market", exit="per_tranche", sl=1.0, tp=2.0, hold=120,
             entry=ExecutionSpec(), tf="1m", max_exposure=1.0, step_atr=None):
    lad = LadderSpec(n, step_bp=None if step_atr else step, step_atr=step_atr, order=order, exit=exit,
                     max_exposure=max_exposure)
    s = StrategySpec("s", BlockRef("_lm_at", {"at": [S(k) for k in at]}), ExitSpec(sl, tp, hold), timeframe=tf,
                     execution=entry, ladder=lad)
    s.validate()
    return s


# ================================================================ 스펙
def _base(**kw):
    return StrategySpec("a", BlockRef("bollinger_z_revert", {"window": 48, "z": 2.5}), ExitSpec(0.7, 1.0, 60), **kw)


def test_defaults_hash_compatible():
    a = _base()
    d = a.to_dict()
    assert "sizing" not in d and "ladder" not in d
    assert a.logic_hash() == _base(sizing=SizingSpec(1.0)).logic_hash()
    assert StrategySpec.from_dict({**d, "sizing": {}, "ladder": None}).logic_hash() == a.logic_hash()
    assert not a.is_sized and not a.needs_atr


def test_sizing_and_ladder_roundtrip_hash_and_params():
    import yaml
    a = _base()
    b = _base(sizing=SizingSpec(0.5))
    c = _base(ladder=LadderSpec(3, step_bp=150.0, order="limit", exit="average"))
    d = _base(ladder=LadderSpec(3, step_atr=1.0))
    e = _base(ladder=LadderSpec(3, step_bp=150.0, order="limit", exit="average", max_exposure=0.5))
    for s in (b, c, d, e):
        s.validate()
        assert StrategySpec.from_dict(s.to_dict()) == s
        assert StrategySpec.from_dict(yaml.safe_load(s.to_yaml())) == s
        assert s.is_sized
    assert len({x.logic_hash() for x in (a, b, c, d, e)}) == 5
    assert c.to_dict()["ladder"] == {"tranches": 3, "step_bp": 150.0, "order": "limit", "exit": "average"}
    # max_exposure 1.0 은 직렬화에서 생략 (명시해도 같은 해시)
    assert _base(ladder=LadderSpec(3, step_bp=150.0, order="limit", exit="average",
                                   max_exposure=1.0)).logic_hash() == c.logic_hash()
    assert b.n_params() == a.n_params()                 # entry_fraction: 크기 선택은 스펙 파라미터로 세지 않음
    assert c.n_params() == a.n_params() + 2             # 분할 수 + 간격 (모드·max_exposure 는 세지 않음)
    assert d.needs_atr and not c.needs_atr


@pytest.mark.parametrize("kw,msg", [
    (dict(sizing=SizingSpec(0.0)), "entry_fraction"),
    (dict(sizing=SizingSpec(1.5)), "entry_fraction"),
    (dict(ladder=LadderSpec(1, step_bp=100.0)), "tranches"),
    (dict(ladder=LadderSpec(6, step_bp=100.0)), "tranches"),
    (dict(ladder=LadderSpec(3)), "exactly one"),
    (dict(ladder=LadderSpec(3, step_bp=100.0, step_atr=1.0)), "exactly one"),
    (dict(ladder=LadderSpec(3, step_bp=6000.0)), "step_bp"),
    (dict(ladder=LadderSpec(3, step_atr=0.0)), "step_atr"),
    (dict(ladder=LadderSpec(3, step_bp=100.0, order="ioc")), "ladder.order"),
    (dict(ladder=LadderSpec(3, step_bp=100.0, exit="fifo")), "ladder.exit"),
    (dict(ladder=LadderSpec(3, step_bp=100.0, max_exposure=1.5)), "max_exposure"),
    (dict(ladder=LadderSpec(3, step_bp=100.0), sizing=SizingSpec(0.5)), "with a ladder"),
])
def test_validation(kw, msg):
    with pytest.raises(ValueError, match=msg):
        _base(**kw).validate()


def test_unknown_fields_rejected():
    with pytest.raises(ValueError, match="unknown ladder"):
        LadderSpec.from_dict({"tranches": 3, "step_bp": 1, "weights": [1, 2]})
    with pytest.raises(ValueError, match="unknown sizing"):
        SizingSpec.from_dict({"fraction": 0.5})


def test_max_params_binding_reported():
    """사다리 +2: 진입 2 + 필터 1 + 청산 3 = 6 → 사다리로 8 (= MAX_PARAMS). 하나 더면 거부."""
    s = StrategySpec("x", BlockRef("bollinger_z_revert", {"window": 48, "z": 2.5}), ExitSpec(0.7, 1.0, 60),
                     (BlockRef("vol_regime", {"lo": 0.2}),), ladder=LadderSpec(3, step_bp=100.0))
    assert s.n_params() == MAX_PARAMS
    s.validate()
    t = StrategySpec("x", s.entry, s.exit, s.filters, ladder=s.ladder,
                     execution=ExecutionSpec("limit", offset_bp=5.0))
    with pytest.raises(ValueError, match="params"):
        t.validate()


# ================================================================ 시나리오
def test_falling_knife_hits_all_tranches_then_stop():
    """시장가 트리거 3단 (1% 간격): 가격이 계속 하락 → 세 분할 모두 체결 후 공동 손절 (마지막 단 98 × (1 − 1%))."""
    m1 = flat_m1(200)
    spec = lad_spec(at=(9,), n=3, step=100.0, sl=1.0, tp=2.0)
    setbar(m1, S(12), o=100, l=98.95, c=99.0)          # 단 1 = 99 관통 (1 tick = 0.01) → 99 체결
    setbar(m1, S(13), o=99.0, h=99.0, l=99.0, c=99.0)
    for k in range(14, 200):
        setbar(m1, S(k), o=99.0, h=99.0, l=99.0, c=99.0)
    setbar(m1, S(20), o=97.5, h=97.5, l=96.0, c=96.5)   # 시가 97.5 ≤ 98 → 단 2 시가 체결, 저가 96 ≤ 손절 97.02 → 전량
    r = both(spec, m1)
    t = r.trades
    assert list(t.tranche) == [0, 1, 2] and (t.ladder == 0).all()
    assert list(t.entry_price) == [100.0, 99.0, 97.5]
    assert (t.exit_reason == "sl").all() and (t.exit_time == T0 + pd.Timedelta(minutes=20)).all()
    stop = (100.0 * (1.0 - 2 * 0.01)) * (1.0 - 0.01)
    assert np.allclose(t.exit_price, stop)
    assert np.allclose(t.weight, 1 / 3)
    # 슬리브 손익 = Σ w·r
    exp = sum((stop / p - 1) / 3 for p in (100.0, 99.0, 97.5))
    assert np.isclose(r.daily_returns.iloc[0], exp)


def test_gap_below_stop_fills_pending_triggers_at_open_then_stops_at_open():
    m1 = flat_m1(100)
    spec = lad_spec(at=(9,), n=3, step=100.0, sl=1.0)
    for k in range(11, 100):
        setbar(m1, S(k), o=90.0, h=90.0, l=90.0, c=90.0)
    t = both(spec, m1).trades
    assert list(t.tranche) == [0, 1, 2]
    assert list(t.entry_price) == [100.0, 90.0, 90.0] and (t.exit_price == 90.0).all()
    assert (t.exit_reason == "sl").all()


def test_grid_round_trips_per_tranche():
    """per_tranche: 하락으로 단 1·2 체결 → 반등하며 단 2, 단 1, 단 0 이 차례로 자기 익절가에서 청산 (그리드)."""
    m1 = flat_m1(200)
    spec = lad_spec(at=(9,), n=3, step=100.0, sl=5.0, tp=0.5)
    setbar(m1, S(12), o=100, l=97.9, c=98.0)            # 단 1(99)·단 2(98) 체결 (같은 봉)
    for k in range(13, 200):
        setbar(m1, S(k), o=98.0, h=98.0, l=98.0, c=98.0)
    setbar(m1, S(14), o=98.0, h=98.6, l=98.0, c=98.5)   # 단 2 익절 98.49
    setbar(m1, S(16), o=98.5, h=99.6, l=98.5, c=99.5)   # 단 1 익절 99.495
    setbar(m1, S(18), o=99.5, h=100.6, l=99.5, c=100.5)  # 단 0 익절 100.5
    t = both(spec, m1).trades
    assert list(t.tranche) == [0, 1, 2]
    assert list(t.exit_time) == [T0 + pd.Timedelta(minutes=m) for m in (18, 16, 14)]
    assert (t.exit_reason == "tp").all()
    assert np.allclose(t.gross_ret, 0.005)


def test_average_exit_uses_harmonic_average_cost_and_exits_all():
    m1 = flat_m1(200)
    spec = lad_spec(at=(9,), n=3, step=100.0, sl=5.0, tp=0.5, exit="average")
    setbar(m1, S(12), o=100, l=98.95, c=99.0)           # 단 1 = 99
    for k in range(13, 200):
        setbar(m1, S(k), o=99.0, h=99.0, l=99.0, c=99.0)
    avg = 2 / (1 / 100.0 + 1 / 99.0)
    tp = avg * 1.005
    setbar(m1, S(14), h=tp - 0.001)                     # 평균단가 익절가 미달
    setbar(m1, S(15), h=tp + 0.005)                     # 도달 (시장가 익절 = h ≥ tp + cost.tick, cost.tick = 0)
    t = both(spec, m1).trades
    assert len(t) == 2 and (t.exit_reason == "tp").all()
    assert (t.exit_time == T0 + pd.Timedelta(minutes=15)).all()
    assert np.allclose(t.exit_price, tp)


def test_no_tp_in_fill_minute_average_mode():
    m1 = flat_m1(100)
    spec = lad_spec(at=(9,), n=2, step=100.0, sl=5.0, tp=0.1, exit="average")
    setbar(m1, S(12), o=100, h=100.5, l=98.9, c=100.0)  # 단 1 체결 + 평균 익절 도달 → 같은 봉 익절 없음
    for k in range(13, 100):
        setbar(m1, S(k), o=99.2, h=99.2, l=99.2, c=99.2)
    setbar(m1, S(13), h=99.6)
    t = both(spec, m1).trades
    assert (t.exit_time == T0 + pd.Timedelta(minutes=13)).all() and (t.exit_reason == "tp").all()


def test_stop_before_tp_same_bar():
    m1 = flat_m1(100)
    spec = lad_spec(at=(9,), n=2, step=100.0, sl=1.0, tp=0.5)
    setbar(m1, S(12), o=100, h=101.0, l=97.0, c=98.0)   # 익절·단 1·손절(98.01) 모두 도달 → 손절 전량
    t = both(spec, m1).trades
    assert (t.exit_reason == "sl").all() and len(t) == 2


def test_partial_fills_ladder_ends_when_flat_and_cancels_pending():
    """단 0 만 체결 후 익절 → 보유 0 → 사다리 종료(걸린 단 주문 취소). 이후 하락해도 체결 없음, 쿨다운 후 새 사다리."""
    m1 = flat_m1(200)
    spec = lad_spec(at=(9, 40), n=3, step=100.0, sl=5.0, tp=0.5)
    setbar(m1, S(12), o=100, h=100.6, l=100, c=100.5)   # 단 0 익절
    for k in range(13, 200):
        setbar(m1, S(k), o=100.5, h=100.5, l=100.5, c=100.5)
    setbar(m1, S(20), o=100.5, l=98.0, c=98.0)         # 사다리 끝난 뒤 하락 — 체결 없음
    for k in range(21, 200):
        setbar(m1, S(k), o=98.0, h=98.0, l=98.0, c=98.0)
    eng_t = both(spec, m1).trades
    assert list(eng_t.ladder) == [0, 1]
    assert list(eng_t.tranche) == [0, 0]
    assert eng_t.entry_time.iloc[1] == T0 + pd.Timedelta(minutes=41)


def test_time_stop_on_oldest_tranche_exits_all():
    m1 = flat_m1(200)
    spec = lad_spec(at=(9,), n=3, step=100.0, sl=5.0, tp=5.0, hold=30)
    setbar(m1, S(25), o=100, l=98.9, c=99.0)
    for k in range(26, 200):
        setbar(m1, S(k), o=99.0, h=99.0, l=99.0, c=99.0)
    t = both(spec, m1).trades
    assert (t.exit_reason == "time").all() and (t.exit_time == T0 + pd.Timedelta(minutes=40)).all()
    assert list(t.hold_min) == [30, 15]


def test_limit_tranches_need_trade_through_and_pay_fee_only():
    m1 = flat_m1(100)
    spec = lad_spec(at=(9,), n=2, step=100.0, order="limit", sl=5.0, tp=5.0, hold=30)
    setbar(m1, S(12), o=100, l=99.0, c=99.5)            # 터치 = 체결 아님
    setbar(m1, S(13), o=99.5, l=98.99, c=99.5)          # 1 tick 관통 → 99 체결 (지정가)
    t = both(spec, m1, cost=COST).trades
    assert len(t) == 2
    assert t.entry_price.iloc[1] == 99.0 and t.entry_time.iloc[1] == T0 + pd.Timedelta(minutes=13)
    assert t.entry_price.iloc[0] == COST.buy_fill(100.0)


def test_market_trigger_pays_taker_cost():
    m1 = flat_m1(100)
    spec = lad_spec(at=(9,), n=2, step=100.0, sl=5.0, tp=5.0, hold=30)
    setbar(m1, S(13), o=99.5, l=98.99, c=99.5)
    t = both(spec, m1, cost=COST).trades
    assert t.entry_price.iloc[1] == COST.buy_fill(99.0)


def test_limit_first_entry_tranches_inactive_on_fill_bar():
    m1 = flat_m1(100)
    spec = lad_spec(at=(9,), n=2, step=100.0, entry=ExecutionSpec("limit", offset_bp=50.0), sl=5.0, tp=5.0,
                    hold=30)
    setbar(m1, S(10), o=100, l=98.0, c=98.5)            # 첫 분할 지정가 99.5 체결 · 단 1(98.505) 도 관통했지만 비활성
    for k in range(11, 100):
        setbar(m1, S(k), o=98.6, h=98.6, l=98.6, c=98.6)
    setbar(m1, S(11), l=98.4)                           # 다음 봉에서 체결
    t = both(spec, m1).trades
    assert list(t.entry_time) == [T0 + pd.Timedelta(minutes=10), T0 + pd.Timedelta(minutes=11)]


def test_cash_accounting_max_exposure():
    """max_exposure 0.6, 3분할 → 분할 0.2. 두 분할 체결 후 공동 손절: 슬리브 손익 = 0.2·(r0 + r1)."""
    m1 = flat_m1(100)
    spec = lad_spec(at=(9,), n=3, step=100.0, sl=0.5, tp=5.0, max_exposure=0.6)
    setbar(m1, S(12), o=100, l=98.95, c=99.0)
    for k in range(13, 100):
        setbar(m1, S(k), o=99.0, h=99.0, l=99.0, c=99.0)
    setbar(m1, S(14), o=99.0, l=97.4, c=97.5)          # 단 2(98) 체결 + 손절 97.51
    r = both(spec, m1)
    t = r.trades
    assert np.allclose(t.weight, 0.2)
    assert np.isclose(r.daily_returns.iloc[0], float((0.2 * t.net_ret).sum()))
    prof = inventory_profile(t)
    assert np.isclose(prof["max_inventory"], 0.6)
    assert n_positions(t) == 1 and np.isclose(frame_position_returns(t)[0], float((0.2 * t.net_ret).sum()))


def test_entry_fraction_scales_returns_linearly_for_single_entries():
    rng = np.random.default_rng(3)
    m1 = synth_m1(rng, 3000, start=T0, gaps=False)
    base = StrategySpec("x", BlockRef("_lm_rand_entry", {"seed": 1, "p": 0.3}), ExitSpec(0.4, 0.4, 60))
    r1 = run_fast(base, m1, COST, m1.index[0], m1.index[-1], registry=REG)
    assert list(r1.trades.columns) == TRADE_COLUMNS
    for f in (0.25, 0.5, 0.9):
        s = StrategySpec("x", base.entry, base.exit, sizing=SizingSpec(f))
        rf = both(s, m1, cost=COST, start=m1.index[0], end=m1.index[-1])
        assert len(rf.trades) == len(r1.trades)
        pd.testing.assert_series_equal(rf.trades.net_ret, r1.trades.net_ret)   # 거래 자체는 같다
        # 거래마다 슬리브 손익 = f × net_ret (일간 = Π(1 + f·r) − 1)
        exp = r1.trades.groupby(r1.trades.exit_time.dt.floor("D"))["net_ret"].apply(
            lambda x: np.prod(1 + f * x.to_numpy()) - 1)
        got = rf.daily_returns[rf.daily_returns != 0]
        assert np.allclose(got.to_numpy(), exp.reindex(got.index).to_numpy(), rtol=0, atol=1e-15)


def test_entry_fraction_one_identical_to_default():
    rng = np.random.default_rng(4)
    m1 = synth_m1(rng, 1500, start=T0)
    a = StrategySpec("x", BlockRef("_lm_rand_entry", {"seed": 2, "p": 0.3}), ExitSpec(0.3, 0.3, 30))
    p = fast.prepare_spec(a, m1, registry=REG)
    _, _, si, ei = p.bounds(m1.index[0], m1.index[-1])
    tr0 = fast.simulate_exit(p, a.exit, COST, si, ei)
    tr1 = fast.simulate_exit(p, a.exit, COST, si, ei, sizing=SizingSpec(1.0))
    assert tr0.weight is None and tr1.weight is None
    d0 = fast.daily_returns_matrix(p, [tr0], m1.index[0], m1.index[-1])[1]
    # 가중치 1, 거래마다 사다리 하나 → 자산 기여 = net_ret (비트 단위)
    c = equity_returns(np.ones(len(tr0.net_ret)), tr0.net_ret, np.arange(len(tr0.net_ret)),
                       np.zeros(len(tr0.net_ret)), p.tmin[tr0.exit_idx])
    assert np.array_equal(c, tr0.net_ret)
    d1 = fast.daily_returns_matrix(p, [tr0._replace(weight=np.ones(len(c)), ladder=np.arange(len(c)),
                                                    tranche=np.zeros(len(c), np.int64))], m1.index[0], m1.index[-1])[1]
    assert np.array_equal(d0, d1)


def test_ladder_never_reached_equals_weighted_single_entry():
    """간격이 커서 추가 분할이 체결되지 않고 손절도 닿지 않으면 = 크기 w 단일 진입(같은 익절·만기)과 같은 거래."""
    rng = np.random.default_rng(5)
    m1 = synth_m1(rng, 2000, start=T0, gaps=False)
    e = BlockRef("_lm_rand_entry", {"seed": 3, "p": 0.2})
    single = run_fast(StrategySpec("x", e, ExitSpec(90.0, 0.3, 60), sizing=SizingSpec(0.5)), m1, COST,
                      m1.index[0], m1.index[-1], registry=REG, fill=F1)
    lad = StrategySpec("x", e, ExitSpec(0.3, 0.3, 60), ladder=LadderSpec(2, step_bp=2000.0, exit="per_tranche"))
    rl = both(lad, m1, cost=COST, start=m1.index[0], end=m1.index[-1])
    cols = ["entry_time", "exit_time", "exit_reason", "net_ret", "weight"]
    assert len(rl.trades) > 10
    pd.testing.assert_frame_equal(rl.trades[cols], single.trades[cols])
    assert (rl.trades.tranche == 0).all()
    pd.testing.assert_series_equal(rl.daily_returns, single.daily_returns, check_exact=True)


def test_mtm_and_inventory_metrics_default_trades():
    m1 = flat_m1(3000)
    spec = lad_spec(at=(9,), n=2, step=100.0, sl=5.0, tp=50.0, hold=2900)
    setbar(m1, S(12), o=100, l=98.9, c=99.0)
    for k in range(13, 3000):
        setbar(m1, S(k), o=99.0, h=99.0, l=99.0, c=99.0)
    r = both(spec, m1)
    mtm = mtm_daily_returns(r.trades, m1["close"], r.daily_returns.index)
    # 첫날: 미실현 손실 = 0.5·(99/100 − 1); 실현 기준은 0 (청산 전)
    assert np.isclose(mtm.iloc[0], 0.5 * (99 / 100 - 1)) and r.daily_returns.iloc[0] == 0.0
    assert np.isclose((1 + mtm).prod(), (1 + r.daily_returns).prod())
    prof = inventory_profile(r.trades)
    assert prof["max_inventory"] == 1.0 and prof["max_inventory_minutes"] > 2800


def test_reference_engine_ladder_events_and_state():
    eng = ReferenceEngine.from_exit(ExitSpec(1.0, 2.0, 60), ZERO, timeframe="1m",
                                    ladder=LadderSpec(3, step_bp=100.0), fill=F1)
    t = T0
    eng.on_decision(t, True, False)
    evs = eng.on_bar_1m(t, 100, 100, 100, 100, False, 1.0, None)
    assert evs[0].side == "buy" and evs[0].tranche == 0 and evs[0].weight == pytest.approx(1 / 3)
    assert eng.in_position and eng.position is None and sorted(eng.lad.pending) == [1, 2]
    evs = eng.on_bar_1m(t + pd.Timedelta(minutes=1), 100, 100, 98.9, 99, False, 1.0, None)
    assert [e.reason for e in evs] == ["scale_in"] and evs[0].tranche == 1
    evs = eng.on_bar_1m(t + pd.Timedelta(minutes=2), 99, 99, 96, 96, False, 1.0, None)
    assert [e.side for e in evs] == ["buy", "sell", "sell", "sell"]
    assert all(e.trade is not None for e in evs[1:]) and not eng.in_position


# ================================================================ 차등 (빠른 = 기준, 비트 단위)
TFS = ("1m", "5m", "15m", "1h", "4h")
N_MIN = {1: (400, 1500), 5: (800, 3000), 15: (1500, 4000), 60: (3000, 7000), 240: (6000, 12000)}


def random_case_ladder(seed):
    rng = np.random.default_rng([seed, 31337])
    tf = TFS[seed % len(TFS)]
    b = tf_minutes(tf)
    lo, hi = N_MIN[b]
    m1 = synth_m1(rng, int(rng.integers(lo, hi)))
    from_data = rng.random() < 0.4
    if from_data:
        m1 = m1.copy()
        for k in ("open", "high", "low", "close"):
            m1[k] = np.round(m1[k].to_numpy() * 100) / 100
        m1["high"] = m1[["open", "high", "close"]].max(axis=1)
        m1["low"] = m1[["open", "low", "close"]].min(axis=1)
    entry = BlockRef("_lm_rand_entry", {"seed": seed, "p": float(rng.uniform(0.05, 0.3 if b == 1 else 0.8))})
    sig = BlockRef("_lm_rand_exit", {"seed": seed + 2, "p": float(rng.uniform(0.01, 0.2))}) \
        if rng.random() < 0.4 else None
    hold = int(rng.integers(5, 200)) if b == 1 else int(rng.integers(1, 12)) * b
    scale = {1: 1.0, 5: 1.0, 15: 1.5, 60: 2.5, 240: 4.0}[b]
    atr_exit = rng.random() < 0.35
    if atr_exit:
        ex = ExitSpec(stop_loss_atr=float(rng.uniform(0.2, 3)), take_profit_atr=float(rng.uniform(0.2, 4)),
                      max_hold_min=hold, signal_exit=sig, atr_bars=int(rng.choice([14, int(rng.integers(1, 10))])))
    else:
        ex = ExitSpec(float(rng.uniform(0.05, 1.0) * scale), float(rng.uniform(0.05, 1.0) * scale), hold, sig)
    tp_mode = "limit" if rng.random() < 0.35 else "market"
    em = rng.random()
    if em < 0.55:
        exe = ExecutionSpec(take_profit=tp_mode)
    elif em < 0.8:
        exe = ExecutionSpec("limit", offset_bp=float(rng.choice([0.0, rng.uniform(0, 40)])),
                            tif_bars=int(rng.integers(1, 4)), take_profit=tp_mode)
    else:
        exe = ExecutionSpec("limit", offset_atr=float(rng.uniform(0.05, 1.0)), tif_bars=int(rng.integers(1, 4)),
                            take_profit=tp_mode)
    kind = rng.random()
    sizing, ladder = SizingSpec(), None
    if kind < 0.12:
        sizing = SizingSpec(float(rng.choice([0.1, 0.5, rng.uniform(0.05, 1.0)])))
    else:
        n = int(rng.integers(2, 6))
        if rng.random() < 0.6:
            step = dict(step_bp=float(rng.uniform(3, 60) * scale))
        else:
            step = dict(step_atr=float(rng.uniform(0.1, 2.0)))
        ladder = LadderSpec(n, order=str(rng.choice(["market", "limit"])),
                            exit=str(rng.choice(["per_tranche", "average"])),
                            max_exposure=float(rng.choice([1.0, 1.0, 0.5, rng.uniform(0.1, 1.0)])), **step)
    spec = StrategySpec(f"lad{seed}", entry, ex, timeframe=tf, execution=exe, sizing=sizing, ladder=ladder)
    spec.exit.validate(b)
    spec.execution.validate()
    spec.sizing.validate()
    if ladder is not None:
        ladder.validate()
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


LAD_SEEDS = list(range(320))


@pytest.mark.parametrize("seed", LAD_SEEDS)
def test_fast_equals_reference_ladder(seed):
    spec, m1, cost, fill, start, end = random_case_ladder(seed)
    rf = run_fast(spec, m1, cost, start, end, registry=REG, fill=fill)
    rr = run_reference(spec, m1, cost, start, end, registry=REG, fill=fill)
    pd.testing.assert_frame_equal(rf.trades, rr.trades, check_exact=True)
    pd.testing.assert_series_equal(rf.daily_returns, rr.daily_returns, check_exact=True)
    t = rf.trades
    if len(t):
        assert (t.entry_time >= rf.start).all()
        if spec.ladder is not None:
            # 사다리는 겹치지 않는다: 다음 사다리 첫 체결 ≥ 이전 사다리 마지막 청산
            g = t.groupby("ladder").agg(first=("entry_time", "min"), last=("exit_time", "max"))
            assert (g["first"].iloc[1:].to_numpy() >= g["last"].iloc[:-1].to_numpy()).all()
            assert (t.groupby("ladder").size() <= spec.ladder.tranches).all()
            assert np.allclose(t.weight, spec.ladder.weight)


def test_ladder_differential_covers_modes_and_outcomes():
    seen = {"tranche>0": 0, "maker_tranche": 0, "trigger_tranche": 0, "avg": 0, "per": 0, "maker_tp": 0,
            "sizing_only": 0, "multi_tranche_sl": 0}
    reasons, tfs = set(), set()
    for seed in LAD_SEEDS[:160]:
        spec, m1, cost, fill, start, end = random_case_ladder(seed)
        p = fast.prepare_spec(spec, m1, registry=REG)
        _, _, si, ei = p.bounds(start, end)
        tr = fast.simulate_exit(p, spec.exit, cost, si, ei, fill=fill)
        tfs.add(spec.timeframe)
        if spec.ladder is None:
            seen["sizing_only"] += 1
            continue
        more = tr.tranche > 0
        seen["tranche>0"] += int(more.sum())
        seen["maker_tranche" if spec.ladder.limit_orders else "trigger_tranche"] += int(more.sum())
        seen["avg" if spec.ladder.average_exit else "per"] += 1
        seen["maker_tp"] += int(tr.exit_maker.sum())
        if len(tr.reason):
            sl = tr.reason == fast.R_SL
            seen["multi_tranche_sl"] += int((sl & more).sum())
            reasons |= set(fast.REASONS[tr.reason.astype(int)])
    assert min(seen.values()) > 0, seen
    assert {"sl", "tp", "time", "signal"} <= reasons
    assert tfs == set(TFS)


@pytest.mark.parametrize("seed", range(12))
def test_fast_equals_reference_ladder_universe_sleeves(seed):
    """다자산 슬리브: 자산마다 같은 사다리 스펙 → 슬리브별 일치 → 포트폴리오(평균)도 일치."""
    rng = np.random.default_rng([seed, 4242])
    spec, _, cost, fill, start, end = random_case_ladder(seed * 7 + 1)
    res_f, res_r = {}, {}
    for sym in ("BTC-KRW", "XRP-KRW", "ETH-KRW"):
        m1 = synth_m1(rng, 2500, start=pd.Timestamp("2025-06-01", tz="UTC"))
        s0, e0 = m1.index[0] + pd.Timedelta(minutes=30), m1.index[-1] - pd.Timedelta(minutes=30)
        res_f[sym] = run_fast(spec, m1, cost, s0, e0, registry=REG, fill=fill)
        res_r[sym] = run_reference(spec, m1, cost, s0, e0, registry=REG, fill=fill)
        pd.testing.assert_frame_equal(res_f[sym].trades, res_r[sym].trades, check_exact=True)
    pf, pr = U.portfolio_result(res_f), U.portfolio_result(res_r)
    pd.testing.assert_series_equal(pf.daily_returns, pr.daily_returns, check_exact=True)
    assert len(pf.trades) == sum(len(r.trades) for r in res_f.values())
