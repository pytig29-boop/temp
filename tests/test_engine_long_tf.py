"""긴 결정 주기(15m/1h/4h, PLAN §10 스윙 연구) — 엔진 의미론·차등·시나리오·해시 하위 호환.

ENGINE_SPEC "결정 주기 B": 봉은 UTC epoch 기준 B분 정렬(4h = 00/04/08/12/16/20 UTC), 결정은 B분 경계,
진입은 다음 1분봉 시가, 쿨다운 = ceilB(t_eff) + B, max_hold_min 은 B 의 배수, ATR 은 결정 주기 봉.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest.fast import run_fast
from btc_quant.backtest.reference import ReferenceEngine, is_boundary, run_reference
from btc_quant.backtest.signals import atr_series, boundary_mask
from btc_quant.backtest.types import CostModel
from btc_quant.strategy.blocks.base import Registry
from btc_quant.strategy.features import to_tf
from btc_quant.strategy.spec import TIMEFRAMES, BlockRef, ExitSpec, StrategySpec, tf_minutes, tf_name

from test_engine_differential import synth_m1

REG = Registry()
ZERO = CostModel("zero", 0.0, 0.0, 0.0, 0.0)
T0 = pd.Timestamp("2026-01-01 00:00", tz="UTC")


def _rand(bars, seed, p):
    r = np.random.default_rng([int(seed), len(bars)]).random(len(bars))
    return pd.Series(r < p, index=bars.index)


@REG.register("entry", {"seed": [0], "p": [0.3]}, "test random entry", name="_lt_rand_entry")
def _lt_rand_entry(bars, ctx, seed=0, p=0.3):
    return _rand(bars, seed, p)


@REG.register("filter", {"seed": [0], "p": [0.7]}, "test random filter", name="_lt_rand_filter")
def _lt_rand_filter(bars, ctx, seed=0, p=0.7):
    return _rand(bars, seed, p)


@REG.register("exit_signal", {"seed": [0], "p": [0.1]}, "test random exit", name="_lt_rand_exit")
def _lt_rand_exit(bars, ctx, seed=0, p=0.1):
    return _rand(bars, seed, p)


def _mask(bars, at):
    want = pd.DatetimeIndex([pd.Timestamp(a, tz="UTC") for a in at])
    return pd.Series(bars.index.isin(want), index=bars.index)


@REG.register("entry", {"at": [[]]}, "test: True at given bar starts", name="_lt_at")
def _lt_at(bars, ctx, at=()):
    return _mask(bars, at)


@REG.register("entry", {}, "test: always True", name="_lt_always")
def _lt_always(bars, ctx):
    return pd.Series(True, index=bars.index)


@REG.register("exit_signal", {"at": [[]]}, "test exit at given bars", name="_lt_exit_at")
def _lt_exit_at(bars, ctx, at=()):
    return _mask(bars, at)


# ================================================================ 기본 규약
def test_timeframes_and_names():
    assert TIMEFRAMES == {"5m": 5, "1m": 1, "15m": 15, "1h": 60, "4h": 240}
    for k, v in TIMEFRAMES.items():
        assert tf_minutes(k) == v and tf_name(v) == k
        assert 1440 % v == 0                       # UTC 자정(= epoch) 정렬
    with pytest.raises(ValueError):
        tf_name(30)


@pytest.mark.parametrize("b", [15, 60, 240])
def test_bars_utc_epoch_aligned_even_when_data_starts_mid_bar(b):
    rng = np.random.default_rng(b)
    m1 = synth_m1(rng, 3 * 1440, start=pd.Timestamp("2025-06-01 01:37", tz="UTC"), gaps=False)
    bars = to_tf(m1, b)
    mins = bars.index.as_unit("ns").asi8 // 60_000_000_000
    assert (mins % b == 0).all()
    if b == 240:
        assert set(bars.index.hour) <= {0, 4, 8, 12, 16, 20}
        # KST 로는 09/13/17/21/01/05시 — 한국 거래소 일봉 기준(09:00 KST = 00:00 UTC)과 같은 경계
        assert set((bars.index + pd.Timedelta(hours=9)).hour) <= {9, 13, 17, 21, 1, 5}
    # 빠른 엔진 경계 == 기준 엔진 경계
    idx = m1.index
    assert np.array_equal(boundary_mask(idx, b), np.array([is_boundary(t, b) for t in idx]))


def test_is_boundary_epoch_based():
    t = pd.Timestamp("2026-01-01 04:00", tz="UTC")
    assert is_boundary(t, 240) and not is_boundary(t + pd.Timedelta("1h"), 240)
    assert is_boundary(t + pd.Timedelta("1h"), 60) and not is_boundary(t + pd.Timedelta("15min"), 60)
    assert is_boundary(t + pd.Timedelta("15min"), 15) and is_boundary(t + pd.Timedelta("5min"), 5)
    assert not is_boundary(t + pd.Timedelta("30s"), 1)
    # 시간대와 무관 (KST 로 표시해도 epoch 기준)
    assert is_boundary(t.tz_convert("Asia/Seoul"), 240)


def test_max_hold_multiple_of_b():
    for tf, b in (("15m", 15), ("1h", 60), ("4h", 240)):
        StrategySpec("a", BlockRef("e"), ExitSpec(0.7, 1.0, 2 * b), timeframe=tf).validate()
        with pytest.raises(ValueError, match=f"multiple of {b}"):
            StrategySpec("a", BlockRef("e"), ExitSpec(0.7, 1.0, b + 5), timeframe=tf).validate()


# ================================================================ 해시 하위 호환
def test_hash_backward_compat_and_new_timeframes():
    a = StrategySpec("a", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(0.7, 1.0, 60))
    assert "timeframe" not in a.to_dict()
    assert a.logic_hash() == StrategySpec.from_dict(a.to_dict()).logic_hash()
    hashes = {a.logic_hash()}
    for tf in ("15m", "1h", "4h"):
        b = StrategySpec("a", a.entry, ExitSpec(0.7, 1.0, 240), timeframe=tf)
        assert b.to_dict()["timeframe"] == tf and StrategySpec.from_dict(b.to_dict()) == b
        hashes.add(b.logic_hash())
    assert len(hashes) == 4


def test_recorded_ledger_hashes_unchanged():
    """원장에 기록된 실제 스펙(5m/1m)의 logic_hash 가 이번 변경 후에도 그대로 재현된다."""
    p = Path(__file__).resolve().parents[1] / "research" / "ledger" / "runs.jsonl"
    if not p.exists():
        pytest.skip("no ledger")
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        sp = r.get("spec")
        if not sp or "logic_hash" not in r:
            continue
        assert StrategySpec.from_dict(sp).logic_hash() == r["logic_hash"], r.get("id")
        n += 1
        if n >= 3000:
            break
    if n == 0:
        pytest.skip("no runs with specs")


# ================================================================ 차등: {1m, 5m, 15m, 1h, 4h} × {%, ATR}
N_MIN = {1: (500, 3000), 5: (800, 4000), 15: (1500, 6000), 60: (3000, 12000), 240: (6000, 20000)}


def random_case_long(seed):
    rng = np.random.default_rng([seed, 4242])
    tf = list(TIMEFRAMES)[seed % len(TIMEFRAMES)]
    b = tf_minutes(tf)
    lo, hi = N_MIN[b]
    m1 = synth_m1(rng, int(rng.integers(lo, hi)))
    p_hi = 0.3 if b == 1 else 0.8
    entry = BlockRef("_lt_rand_entry", {"seed": seed, "p": float(rng.uniform(0.02, p_hi))})
    filters = ()
    if rng.random() < 0.4:
        filters = (BlockRef("_lt_rand_filter", {"seed": seed + 1, "p": float(rng.uniform(0.3, 1))}),)
    sig = None
    if rng.random() < 0.6:
        sig = BlockRef("_lt_rand_exit", {"seed": seed + 2, "p": float(rng.uniform(0.01, 0.4))})
    hold = int(rng.integers(1, 200)) if b == 1 else int(rng.integers(1, 12)) * b
    # 긴 봉일수록 넓은 % 레벨 (시간·신호 청산만 나오지 않게)
    scale = {1: 1.0, 5: 1.0, 15: 1.5, 60: 2.5, 240: 4.0}[b]
    if rng.random() < 0.5:
        atr_bars = int(rng.choice([14, int(rng.integers(1, 10))]))
        ex = ExitSpec(stop_loss_atr=float(rng.uniform(0.2, 3)), take_profit_atr=float(rng.uniform(0.2, 4)),
                      max_hold_min=hold, signal_exit=sig, atr_bars=atr_bars)
    else:
        ex = ExitSpec(float(rng.uniform(0.03, 1.0) * scale), float(rng.uniform(0.03, 1.0) * scale), hold, sig)
    spec = StrategySpec(f"lt{seed}", entry, ex, filters, timeframe=tf)
    spec.exit.validate(b)
    cost = CostModel("rand", float(rng.choice([0.0, 0.0005])), float(rng.uniform(0, 5)),
                     float(rng.uniform(0, 8)), float(rng.choice([0.0, 0.001, 0.01])))
    i0 = m1.index[0]
    span = len(m1)
    start = i0 + pd.Timedelta(minutes=int(rng.integers(-30, span // 3)))
    if rng.random() < 0.3:
        end = m1.index[-1] + pd.Timedelta(minutes=int(rng.integers(-3, 30)))
    else:
        end = i0 + pd.Timedelta(minutes=int(rng.integers(span // 2, span)))
    return spec, m1, cost, start, end


LT_SEEDS = list(range(250))


def _identical(rf, rr):
    pd.testing.assert_frame_equal(rf.trades, rr.trades, check_exact=True)
    pd.testing.assert_series_equal(rf.daily_returns, rr.daily_returns, check_exact=True)


@pytest.mark.parametrize("seed", LT_SEEDS)
def test_fast_equals_reference_all_timeframes(seed):
    spec, m1, cost, start, end = random_case_long(seed)
    rf = run_fast(spec, m1, cost, start, end, registry=REG)
    rr = run_reference(spec, m1, cost, start, end, registry=REG)
    _identical(rf, rr)
    if len(rf.trades):
        t = rf.trades
        b = spec.tf_min
        em = t.entry_time.astype("int64") // 60_000_000_000 if t.entry_time.dtype.kind == "M" else None
        em = pd.DatetimeIndex(t.entry_time).as_unit("ns").asi8 // 60_000_000_000
        assert (em % b == 0).all()                                     # 진입은 B분 경계
        assert (t.entry_time >= rf.start).all() and (t.entry_time < rf.end).all()
        tm = t[t.exit_reason == "time"]
        assert (tm.hold_min >= spec.exit.max_hold_min).all()


def test_long_tf_differential_covers_timeframes_modes_reasons():
    seen: dict = {}
    for seed in LT_SEEDS[:150]:
        spec, m1, cost, start, end = random_case_long(seed)
        key = (spec.timeframe, spec.exit.mode)
        seen.setdefault(key, set())
        seen[key] |= set(run_fast(spec, m1, cost, start, end, registry=REG).trades.exit_reason)
    assert {tf for tf, _ in seen} == set(TIMEFRAMES)
    assert {m for _, m in seen} == {"pct", "atr"}
    for tf in ("15m", "1h", "4h"):
        reasons = set().union(*(v for (t, _), v in seen.items() if t == tf))
        assert {"sl", "tp", "time", "signal"} <= reasons, (tf, reasons)


# ================================================================ 시나리오 (양 엔진)
def flat_m1(days=2, price=100.0, start=T0):
    idx = pd.date_range(start, periods=int(days * 1440), freq="1min", name="ts")
    df = pd.DataFrame({"open": price, "high": price, "low": price, "close": price, "volume": 1.0}, index=idx)
    df["filled"] = False
    return df


def setbar(m1, ts, o=None, h=None, l=None, c=None):
    ts = pd.Timestamp(ts, tz="UTC")
    for k, v in (("open", o), ("high", h), ("low", l), ("close", c)):
        if v is not None:
            m1.loc[ts, k] = v
    r = m1.loc[ts]
    m1.loc[ts, "high"] = max(r["high"], r["open"], r["close"])
    m1.loc[ts, "low"] = min(r["low"], r["open"], r["close"])


def both(spec, m1, cost=ZERO, start=T0, end=None):
    end = m1.index[-1] + pd.Timedelta(minutes=1) if end is None else end
    rf = run_fast(spec, m1, cost, start, end, registry=REG)
    rr = run_reference(spec, m1, cost, start, end, registry=REG)
    _identical(rf, rr)
    return rf.trades


def mkspec(tf, entry_at=None, exit_at=None, sl=5.0, tp=5.0, hold=None, **atr):
    b = tf_minutes(tf)
    entry = BlockRef("_lt_always") if entry_at is None else BlockRef("_lt_at", {"at": list(entry_at)})
    sig = BlockRef("_lt_exit_at", {"at": list(exit_at)}) if exit_at else None
    hold = hold or 10 * b
    ex = (ExitSpec(max_hold_min=hold, signal_exit=sig, **atr) if atr
          else ExitSpec(sl, tp, hold, sig))
    s = StrategySpec("s", entry, ex, timeframe=tf)
    s.validate()
    return s


def T(s):
    return pd.Timestamp(f"2026-01-01 {s}", tz="UTC") if len(s) <= 5 else pd.Timestamp(s, tz="UTC")


def test_4h_entry_at_next_boundary_not_next_hour():
    m1 = flat_m1()
    tr = both(mkspec("4h", entry_at=["2026-01-01 00:00"], hold=480), m1)
    assert list(tr.entry_time) == [T("04:00")]                  # 봉 00:00 확정(04:00) → 04:00 시가 진입
    assert list(tr.exit_time) == [T("12:00")] and list(tr.exit_reason) == ["time"]
    assert list(tr.hold_min) == [480]


def test_4h_cooldown_skips_one_full_bar_after_intrabar_exit():
    m1 = flat_m1()
    setbar(m1, "2026-01-01 05:10", l=94.0)                     # 04:00 진입 → 05:10 손절 (5%)
    tr = both(mkspec("4h", sl=5.0, tp=50.0, hold=2400), m1)
    assert list(tr.entry_time[:2]) == [T("04:00"), T("12:00")]  # t_eff 05:11 → ceil 08:00 건너뜀 → 12:00
    assert tr.exit_reason.iloc[0] == "sl" and tr.hold_min.iloc[0] == 71


def test_4h_cooldown_after_open_exit_at_boundary():
    m1 = flat_m1()
    tr = both(mkspec("4h", hold=240), m1)                       # 04:00 진입 → 08:00 time 청산
    assert list(tr.entry_time[:3]) == [T("04:00"), T("12:00"), T("20:00")]
    assert list(tr.exit_time[:2]) == [T("08:00"), T("16:00")]


def test_4h_signal_exit_at_boundary():
    m1 = flat_m1()
    tr = both(mkspec("4h", entry_at=["2026-01-01 00:00"], exit_at=["2026-01-01 08:00"], hold=2400), m1)
    assert list(tr.exit_time) == [T("12:00")] and list(tr.exit_reason) == ["signal"]


def test_1h_entry_cooldown_and_time_exit():
    m1 = flat_m1(1)
    tr = both(mkspec("1h", entry_at=["2026-01-01 10:00", "2026-01-01 12:00", "2026-01-01 13:00"],
                     hold=120), m1)
    # 10:00 봉 → 11:00 진입 → 13:00 time 청산 → 13:00 건너뜀(쿨다운) → 13:00 봉 신호로 14:00 진입
    assert list(tr.entry_time) == [T("11:00"), T("14:00")]
    assert list(tr.exit_time) == [T("13:00"), T("16:00")]


def test_1h_intrabar_tp_and_gap_sl():
    m1 = flat_m1(1)
    setbar(m1, "2026-01-01 03:20", h=103.0)                     # TP 3%
    setbar(m1, "2026-01-01 05:00", o=96.0)                      # 다음 거래(05:00 진입 직전?) — 갭
    tr = both(mkspec("1h", entry_at=["2026-01-01 02:00", "2026-01-01 05:00"], sl=2.0, tp=3.0, hold=600), m1)
    assert list(tr.entry_time) == [T("03:00"), T("06:00")]
    assert tr.exit_reason.iloc[0] == "tp" and tr.exit_time.iloc[0] == T("03:20")


def test_4h_atr_on_decision_bars():
    rng = np.random.default_rng(3)
    m1 = synth_m1(rng, 6 * 1440, start=T0, gaps=False)
    spec = mkspec("4h", entry_at=["2026-01-04 08:00"], hold=960, stop_loss_atr=1.0, take_profit_atr=1.5,
                  atr_bars=5)
    ev = []
    eng = ReferenceEngine.from_exit(spec.exit, ZERO, T0, m1.index[-1], spec.timeframe)
    bars = to_tf(m1, 240)
    atr = atr_series(bars, 5)
    t_dec = T("2026-01-04 12:00")
    eng.on_decision(t_dec, True, False, float(atr.loc[T("2026-01-04 08:00")]))
    o = float(m1.loc[t_dec, "open"])
    ev = eng.on_bar_1m(t_dec, o, o, o, o)
    a = float(atr.loc[T("2026-01-04 08:00")]) / o
    assert ev[0].sl == o * (1 - 1.0 * a) and ev[0].tp == o * (1 + 1.5 * a)
    # 4h ATR 은 1시간 ATR 보다 크다 (결정 주기 봉으로 계산됨을 확인)
    assert a > float(atr_series(to_tf(m1, 60), 5).loc[T("2026-01-04 11:00")]) / o
    tr = both(spec, m1)
    assert list(tr.entry_time) == [t_dec]


# ================================================================ 긴 봉 complete 규칙 (데이터 공백만 배제)
def test_long_bar_complete_rule():
    m1 = flat_m1(1)
    f = m1["filled"].to_numpy().copy()
    f[[65, 70, 100]] = True                                    # 01:05… 조용한 분 3개 (1h 봉 01:00)
    f[180:210] = True                                          # 03:00~03:29 공백 30분 (1h 봉 03:00)
    f[290:300] = True                                          # 04:50~04:59 10분 + 05:00~05:24 25분 = 35분 연속
    f[300:325] = True
    m1["filled"] = f
    b = to_tf(m1, 60)["complete"]
    assert b[T("01:00")] and not b[T("03:00")] and b[T("04:00")] and not b[T("05:00")]
    assert to_tf(m1, 240)["complete"].tolist()[:2] == [False, False]
    # 5m 는 기존 규칙(모두 실제 봉) 그대로
    b5 = to_tf(m1, 5)["complete"]
    assert not b5[T("01:05")] and not b5[T("01:10")] and b5[T("01:15")]
    # 부분 봉(데이터가 봉 중간에서 시작) 은 complete 아님 (B ≥ 15)
    part = to_tf(flat_m1(1, start=T0 + pd.Timedelta("37min")), 60)["complete"]
    assert not part.iloc[0] and part.iloc[1:-1].all()


def test_long_bar_entry_blocked_only_by_outage():
    m1 = flat_m1(1)
    m1.loc[T("01:05"):T("01:07"), "filled"] = True             # 조용한 분 → 01:00 봉 complete → 02:00 진입
    m1.loc[T("05:00"):T("05:40"), "filled"] = True             # 공백 → 05:00 봉 incomplete → 06:00 진입 없음
    tr = both(mkspec("1h", entry_at=["2026-01-01 01:00", "2026-01-01 05:00"], hold=60), m1)
    assert list(tr.entry_time) == [T("02:00")]
