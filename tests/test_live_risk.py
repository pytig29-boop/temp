"""RiskManager (PLAN §7) — 순수 로직 테스트, KST 경계 포함."""
from datetime import datetime, timedelta, timezone

import pytest

from btc_quant.live.risk import KST, HaltLevel, RiskLimits, RiskManager, kst_day_key, kst_week_key

LIM = RiskLimits(daily_loss=-0.03, weekly_loss=-0.06, max_drawdown=-0.12, max_trades_per_day=5,
                 stale_seconds=120, max_api_errors=3, price_deviation_threshold=0.01,
                 expected_fee_rate=0.0)


def kst(*a):
    return datetime(*a, tzinfo=KST)


def mk(tmp_path, **kw):
    kw.setdefault("kill_dir", tmp_path)
    rm = RiskManager(LIM, strategy_id="s1", **kw)
    return rm


def fresh(rm, now, equity=1_000_000):
    rm.on_market_data(now, price=100.0, ref_price=100.0)
    rm.on_equity(now, equity)


def test_keys_kst_boundaries():
    # 2026-11-08 (일) 23:59 KST = 14:59 UTC; 2026-11-09 (월) 00:00 KST = 2026-11-08 15:00 UTC
    sun = datetime(2026, 11, 8, 14, 59, 59, tzinfo=timezone.utc)
    mon = datetime(2026, 11, 8, 15, 0, 0, tzinfo=timezone.utc)
    assert kst_day_key(sun) == "2026-11-08" and kst_day_key(mon) == "2026-11-09"
    assert kst_week_key(sun) != kst_week_key(mon)
    assert kst_week_key(mon) == kst_week_key(kst(2026, 11, 15, 23, 59))  # 일요일까지 같은 주


def test_ok_when_fresh(tmp_path):
    rm = mk(tmp_path)
    now = kst(2026, 11, 10, 10)
    assert rm.can_enter(now)[0] is False  # 시세 미수신 → 진입 금지
    fresh(rm, now)
    assert rm.can_enter(now) == (True, "ok")
    assert rm.should_flatten(now) == (False, "ok")
    assert rm.halt_level(now) == HaltLevel.NONE


def test_daily_loss_halts_until_kst_midnight(tmp_path):
    rm = mk(tmp_path)
    t = kst(2026, 11, 10, 9)
    fresh(rm, t)
    rm.on_equity(t + timedelta(hours=1), 975_000)          # −2.5%: 아직 허용
    rm.on_market_data(t + timedelta(hours=1))
    assert rm.can_enter(t + timedelta(hours=1))[0]
    t2 = kst(2026, 11, 10, 23, 59, 30)
    rm.on_market_data(t2)
    rm.on_equity(t2, 970_000)                              # −3.0% 정확히 한도 → 발동(≤)
    ok, why = rm.can_enter(t2)
    assert not ok and why.startswith("daily_loss")
    assert rm.halt_level(t2) == HaltLevel.DAY
    assert rm.should_flatten(t2)[0] is False               # 일일 한도는 청산하지 않음
    ev = rm.drain_events()
    assert any(e["code"] == "daily_loss" for e in ev) and rm.drain_events() == []
    # KST 자정 직후: 해제, 일간 기준 자산 = 직전 자산
    t3 = kst(2026, 11, 11, 0, 0, 1)
    rm.on_market_data(t3)
    assert rm.can_enter(t3) == (True, "ok")
    assert rm.s.day_start_equity == 970_000 and rm.daily_return() == 0
    # UTC 자정(KST 09:00)은 경계가 아님
    rm.on_equity(kst(2026, 11, 11, 8, 59), 960_000)
    rm.on_equity(kst(2026, 11, 11, 9, 1), 960_000)
    assert rm.s.day_start_equity == 970_000


def test_weekly_loss_halts_until_monday_kst(tmp_path):
    rm = mk(tmp_path)
    fresh(rm, kst(2026, 11, 9, 9))                         # 월요일
    eq = 1_000_000
    for d in range(1, 4):                                  # 화~목 매일 −2.1% (일일 한도 미만)
        t = kst(2026, 11, 9 + d, 12)
        eq *= 0.979
        rm.on_market_data(t)
        rm.on_equity(t, eq)
    t = kst(2026, 11, 12, 12)
    assert rm.weekly_return() <= -0.06
    ok, why = rm.can_enter(t)
    assert not ok and why.startswith("weekly_loss") and rm.halt_level(t) == HaltLevel.WEEK
    # 다음 날(금): 일 경계는 지났지만 주 정지는 유지
    t = kst(2026, 11, 13, 0, 0, 1)
    rm.on_market_data(t)
    assert rm.can_enter(t)[0] is False
    # 일요일 23:59:59 KST 여전히 정지
    t = kst(2026, 11, 15, 23, 59, 59)
    rm.on_market_data(t)
    assert rm.can_enter(t)[0] is False
    # 월요일 00:00:00 KST 해제
    t = kst(2026, 11, 16, 0, 0, 0)
    rm.on_market_data(t)
    assert rm.can_enter(t) == (True, "ok")
    assert rm.s.week_start_equity == pytest.approx(eq)


def test_max_drawdown_needs_human_and_flattens(tmp_path):
    rm = mk(tmp_path)
    fresh(rm, kst(2026, 11, 1, 9), 1_000_000)
    rm.on_equity(kst(2026, 11, 2, 9), 1_100_000)           # 고점
    eq = 1_100_000
    for d in range(3, 10):                                 # 매일 −1.9% 씩 → 일/주 경계 넘기며 누적
        eq *= 0.981
        t = kst(2026, 11, d, 9)
        rm.on_market_data(t)
        rm.on_equity(t, eq)
    t = kst(2026, 11, 9, 9)
    assert rm.drawdown() <= -0.12
    assert rm.halt_level(t) == HaltLevel.HUMAN
    assert rm.should_flatten(t)[0] is True
    # 날짜·주가 바뀌어도 HUMAN 은 유지
    t = kst(2026, 11, 20, 9)
    rm.on_market_data(t)
    assert rm.can_enter(t)[0] is False
    assert rm.clear_human_halt("max_drawdown", operator="me") == ["max_drawdown"]
    assert rm.can_enter(t) == (True, "ok") and rm.s.peak == rm.s.equity


def test_no_flatten_option(tmp_path):
    rm = mk(tmp_path, flatten_on_human_halt=False)
    t = kst(2026, 11, 10, 9)
    fresh(rm, t)
    rm.flag_degradation(t, "CUSUM 이탈")
    assert rm.can_enter(t)[0] is False and rm.should_flatten(t)[0] is False


def test_trade_limit_resets_next_day(tmp_path):
    rm = mk(tmp_path)
    t = kst(2026, 11, 10, 9)
    fresh(rm, t)
    for i in range(4):
        rm.on_fill(t, "buy", 0.01, 1e8)
        rm.on_fill(t, "sell", 0.01, 1e8)
    assert rm.can_enter(t)[0]                               # 4회
    rm.on_fill(t, "buy", 0.01, 1e8)                         # 5회 = 한도 → 더 이상 진입 금지
    ok, why = rm.can_enter(t)
    assert not ok and why.startswith("trade_limit")
    t2 = kst(2026, 11, 11, 0, 0)
    rm.on_market_data(t2)
    assert rm.can_enter(t2)[0] and rm.s.trades_today == 0


def test_stale_data_boundary(tmp_path):
    rm = mk(tmp_path)
    t = kst(2026, 11, 10, 9)
    fresh(rm, t)
    assert rm.can_enter(t + timedelta(seconds=119.9))[0]
    ok, why = rm.can_enter(t + timedelta(seconds=120))
    assert not ok and why.startswith("stale_data")
    assert rm.should_flatten(t + timedelta(seconds=600))[0] is False
    rm.on_market_data(t + timedelta(seconds=130))
    assert rm.can_enter(t + timedelta(seconds=130))[0]


def test_api_errors_three_consecutive(tmp_path):
    rm = mk(tmp_path)
    t = kst(2026, 11, 10, 9)
    fresh(rm, t)
    rm.on_api_error(t, "timeout")
    rm.on_api_error(t, "timeout")
    rm.on_api_success(t)
    rm.on_api_error(t)
    rm.on_api_error(t)
    assert rm.can_enter(t)[0]
    rm.on_api_error(t, "500")
    ok, why = rm.can_enter(t)
    assert not ok and why.startswith("api_errors")
    assert any(e["code"] == "api_errors" for e in rm.drain_events())
    rm.on_api_success(t)
    assert rm.can_enter(t)[0]


def test_price_deviation(tmp_path):
    rm = mk(tmp_path)
    t = kst(2026, 11, 10, 9)
    fresh(rm, t)
    rm.on_market_data(t, price=101.0, ref_price=100.0)     # 1.0% = 한도 (초과 아님)
    assert rm.can_enter(t)[0]
    rm.on_market_data(t, price=101.5)
    ok, why = rm.can_enter(t)
    assert not ok and why.startswith("price_deviation")
    rm.on_market_data(t, ref_price=101.4)
    assert rm.can_enter(t)[0]


def test_fee_change_grade_b_halts_grade_a_flags(tmp_path):
    t = kst(2026, 11, 10, 9)
    b = mk(tmp_path, grade="B")
    fresh(b, t)
    b.on_fee_observed(t, 0.0)
    assert b.can_enter(t)[0]
    b.on_fill(t, "buy", 0.01, 100_000_000, fee=500.0)      # 0.05% 관측
    assert b.s.observed_fee_rate == pytest.approx(0.0005)
    assert b.halt_level(t) == HaltLevel.HUMAN and b.should_flatten(t)[0]
    a = mk(tmp_path, grade="A")
    fresh(a, t)
    a.on_fee_observed(t, 0.0005)
    assert a.can_enter(t)[0] and a.status(t)["flags"][0]["code"] == "fee_change"
    assert any(e["type"] == "flag" for e in a.drain_events())


def test_kill_and_flatten_switch(tmp_path):
    rm = mk(tmp_path)
    t = kst(2026, 11, 10, 9)
    fresh(rm, t)
    (tmp_path / "KILL").write_text("x")
    assert rm.can_enter(t)[0] is False and rm.halt_level(t) == HaltLevel.KILL
    assert rm.should_flatten(t)[0] is False
    (tmp_path / "FLATTEN").write_text("x")
    assert rm.should_flatten(t)[0] is True
    (tmp_path / "KILL").unlink()
    (tmp_path / "FLATTEN").unlink()
    assert rm.can_enter(t)[0]


def test_persistence_roundtrip(tmp_path):
    rm = mk(tmp_path, grade="B")
    t = kst(2026, 11, 10, 9)
    fresh(rm, t)
    rm.on_equity(t, 960_000)
    rm.on_fill(t, "buy", 0.01, 1e8)
    rm.on_api_error(t)
    p = tmp_path / "state" / "risk.json"
    rm.save(p)
    r2 = RiskManager.load(p, kill_dir=tmp_path)
    assert r2.grade == "B" and r2.strategy_id == "s1" and r2.limits == LIM
    assert r2.s.trades_today == 1 and r2.s.consecutive_api_errors == 1
    assert r2.s.peak == 1_000_000 and r2.s.equity == 960_000
    assert r2.can_enter(t)[0] is False and "daily_loss" in r2.s.halts
    # 재시작 후 다음 날로 넘어가면 DAY 정지 해제
    t2 = kst(2026, 11, 11, 0, 1)
    r2.on_market_data(t2)
    assert r2.can_enter(t2)[0]
    # 새 한도로 로드
    r3 = RiskManager.load(p, limits=RiskLimits(-0.05, -0.1, -0.2, 10), kill_dir=tmp_path)
    assert r3.limits.daily_loss == -0.05


def test_positive_limits_interpreted_as_losses(tmp_path):
    rm = RiskManager(RiskLimits(0.03, 0.06, 0.12, 5), kill_dir=tmp_path)
    t = kst(2026, 11, 10, 9)
    fresh(rm, t)
    rm.on_equity(t, 990_000)
    assert rm.can_enter(t)[0]


def test_naive_datetime_rejected(tmp_path):
    rm = mk(tmp_path)
    with pytest.raises(ValueError):
        rm.on_market_data(datetime(2026, 11, 10))
