"""페이퍼 서비스: 슬롯 로딩(레지스트리/스펙+리스크 파일), 워밍업→실시간 봉→트레이더, 하트비트·요약·보고."""
import asyncio
import json

import numpy as np
import pandas as pd
import pytest

from btc_quant.live.risk import RiskLimits
from btc_quant.paper import service as S
from btc_quant.registry.ledger import Ledger
from btc_quant.registry.strategies import StrategyRegistry
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

from test_paper_bars import synth_trades
from test_paper_trader import book_at, synth_raw

T0 = pd.Timestamp("2026-11-12 23:00", tz="UTC")      # KST 08:00 → 1시간 뒤 일일 요약 시각 통과
SPEC = StrategySpec("svc_rand", BlockRef("random_entry", {"p": 0.5, "seed": 1}), ExitSpec(0.1, 0.1, 10))


class FakeCollector:
    def __init__(self):
        self.listeners = []
        self.stats = {"coinone": None, "upbit": None}

    def add_listener(self, fn):
        self.listeners.append(fn)

    def last_message_age(self, ex=None, kind=None, now=None):
        return 0.5


class FakeNotifier:
    def __init__(self):
        self.msgs = []

    def send(self, text, level="info"):
        self.msgs.append((level, text))
        return True

    async def asend(self, text, level="info"):
        return self.send(text, level)


class Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


def test_limits_and_slots_from_specs(tmp_path):
    lim = S.limits_from_dict({"daily_loss_limit": -0.05, "drawdown_limit": -0.2, "max_trades_per_day": 7.5,
                              "stale_seconds": 60, "unknown": 1})
    assert lim.daily_loss == -0.05 and lim.max_drawdown == -0.2 and lim.max_trades_per_day == 8
    assert lim.weekly_loss == S.DEFAULT_LIMITS.weekly_loss and lim.stale_seconds == 60
    p = tmp_path / "a_v1.yaml"
    p.write_text(SPEC.to_yaml())
    q = tmp_path / "b_v1.yaml"
    q.write_text(StrategySpec("b", SPEC.entry, SPEC.exit).to_yaml())
    (tmp_path / "a_v1.risk.yaml").write_text("grade: A\nlimits: {max_trades_per_day: 50}\n")
    common = tmp_path / "common.yaml"
    common.write_text("grade: B\nlimits: {daily_loss: -0.1}\n")
    a, b = S.slots_from_specs([p, q], risk_path=common)
    assert (a.grade, a.limits.max_trades_per_day, a.limits.daily_loss) == ("A", 50, -0.1)
    assert (b.grade, b.limits.max_trades_per_day) == ("B", 5)
    assert a.key == "svc_rand_v1" and a.role == "spec"


def test_slots_from_registry(tmp_path):
    reg = StrategyRegistry(tmp_path, Ledger(tmp_path / "research" / "ledger"))
    for i in range(4):
        s = StrategySpec(f"c{i}", SPEC.entry, SPEC.exit)
        reg.register(s)
        for st in ("candidate", "validated", "challenger") if i < 3 else ("candidate",):
            reg.transition(s.id, 1, st, "test")
    slots = S.slots_from_registry(tmp_path)
    assert [x.role for x in slots] == ["challenger"] * 3
    assert {x.key for x in slots} == {"c0_v1", "c1_v1", "c2_v1"}
    (tmp_path / "strategies" / "challenger" / "c0_v1.risk.yaml").write_text("grade: A\n")
    assert S.slots_from_registry(tmp_path)[0].grade == "A"


def _service(tmp_path, clock, notifier=None, fetch=None, spec=SPEC, limits=None):
    slot = S.StrategySlot(spec, "spec", "B", limits or RiskLimits(-0.5, -0.5, -0.5, 1000))
    cfg = S.ServiceConfig(runtime_dir=tmp_path / "rt", heartbeat_seconds=1, summary_hour_kst=9)
    return S.PaperService([slot], cfg, notifier=notifier, collector=FakeCollector(), fetch=fetch, clock=clock)


def test_service_end_to_end_with_report(tmp_path):
    rng = np.random.default_rng(3)
    raw = synth_raw(rng, T0 - pd.Timedelta(days=4), 4 * 1440 + 10)
    calls = []

    def fetch(market, unit, start, end, progress=False):
        calls.append((start, end))
        return raw[(raw.index >= start) & (raw.index < end)]

    clock = Clock(T0.timestamp() - 30)
    nt = FakeNotifier()
    svc = _service(tmp_path, clock, nt, fetch)
    trades = synth_trades(rng, T0 - pd.Timedelta(seconds=30), 150, p0=float(raw["close"].iloc[-1]))
    svc.on_event("coinone", "orderbook", book_at(1e8, ts_recv=clock.t))
    svc.on_event("upbit", "trades", {"price": 1e8, "ts_exchange": 0})
    first = True
    for ts_ms, p, q in trades:
        clock.t = ts_ms / 1000
        svc.on_event("coinone", "orderbook", book_at(p, ts_recv=clock.t))
        svc.on_event("upbit", "trades", {"price": p * 1.001, "ts_exchange": ts_ms})
        svc.on_event("coinone", "trades", {"ts_exchange": ts_ms, "price": p, "qty": q})
        if first:
            assert svc.builder.next_minute * 60 == T0.timestamp()     # 다음 분부터
            first = False
        if not svc.ready and clock.t >= T0.timestamp() + 5:
            asyncio.run(svc.warmup())                              # REST 워밍업 → attach → 대기 봉 처리
            assert calls and calls[0][1] == T0
        svc.process(clock.t)
    clock.t = trades[-1][0] / 1000 + 120
    svc.process(clock.t)
    tr = svc.traders["svc_rand_v1"]
    assert svc.ready and svc.bars_processed >= 140 and tr.n_decisions >= 28
    assert tr.n_trades >= 3
    dec = S.read_jsonl(svc.cfg.out / "svc_rand_v1" / "decisions.jsonl")
    reasons = {d["risk_reason"].split(":")[0] for d in dec if not d["risk_ok"]}
    assert reasons <= {"stale_data"}, reasons          # 합성 데이터의 체결 공백(최대 8분)만 진입을 막는다
    # 하트비트
    svc.write_heartbeat()
    hb = json.loads((svc.cfg.out / "service.heartbeat").read_text())
    assert hb["ready"] and hb["traders"]["svc_rand_v1"]["n_trades"] == tr.n_trades
    ok, _ = S.check_health(svc.cfg.out / "service.heartbeat", now=clock.t + 5)
    assert ok
    assert not S.check_health(svc.cfg.out / "service.heartbeat", now=clock.t + 500)[0]
    # 알림: 시작, 진입/청산; 일일 요약은 KST 09:00 이후 하루 한 번
    assert any("시작" in m for _, m in nt.msgs) and any("진입" in m for _, m in nt.msgs)
    assert svc.maybe_daily_summary() and not svc.maybe_daily_summary()
    assert any("일일 요약" in m for _, m in nt.msgs)
    # 상태 / 보고 (같은 1분봉으로 백테스트 → 완전 일치)
    st = S.status_report(svc.cfg.out, now=clock.t)
    assert st["strategies"]["svc_rand_v1"]["trades"] == tr.n_trades
    rep = S.build_report(svc.cfg.out / "svc_rand_v1", m1=svc.hist.frame())
    t = rep["trades"]
    assert t["n_paper"] == tr.n_trades and t["n_identical"] == t["n_paper"]
    assert t["n_backtest"] == t["n_paper"] and not t["paper_only"] and not t["backtest_only"]
    assert rep["slippage"]["n"] == 2 * tr.n_trades
    assert "G8" in S.format_report(rep)


def test_service_restart_restores(tmp_path):
    rng = np.random.default_rng(4)
    raw = synth_raw(rng, T0 - pd.Timedelta(days=4), 4 * 1440 + 200)

    def fetch(market, unit, start, end, progress=False):
        return raw[(raw.index >= start) & (raw.index < end)]

    def run_segment(t_start, minutes, svc):
        clock = svc.clock
        clock.t = t_start - 30
        trades = synth_trades(rng, pd.Timestamp(t_start - 30, unit="s", tz="UTC"), minutes,
                              p0=float(raw["close"].iloc[-1]), n_gaps=0, empty_frac=0)
        for ts_ms, p, q in trades:
            clock.t = ts_ms / 1000
            svc.on_event("coinone", "orderbook", book_at(p, ts_recv=clock.t))
            svc.on_event("coinone", "trades", {"ts_exchange": ts_ms, "price": p, "qty": q})
            if not svc.ready and clock.t >= svc.builder.next_minute * 60 + 5:
                asyncio.run(svc.warmup())
            svc.process(clock.t)

    svc = _service(tmp_path, Clock(0), fetch=fetch)
    run_segment(T0.timestamp(), 60, svc)
    tr = svc.traders["svc_rand_v1"]
    last, n = tr.last_ts, tr.n_decisions
    # 재시작: 30분 뒤. 공백은 REST 이력으로 재생된다
    svc2 = _service(tmp_path, Clock(0), fetch=fetch)
    tr2 = svc2.traders["svc_rand_v1"]
    assert tr2.last_ts == last and tr2.n_decisions == n
    run_segment(T0.timestamp() + 90 * 60, 20, svc2)
    assert tr2.last_ts >= last + pd.Timedelta(minutes=45)                # 공백 30분+ 재생 후 실시간 진행
    dec = S.read_jsonl(svc2.cfg.out / "svc_rand_v1" / "decisions.jsonl")
    ts = [pd.Timestamp(d["ts"]) for d in dec]
    assert ts == sorted(set(ts))                                   # 중복·역행 없음
    assert any(not d["live"] for d in dec)                         # 재생 결정 존재
    meta = json.loads((svc2.cfg.out / "svc_rand_v1" / "meta.json").read_text())
    assert meta["restored"] is True


def test_price_deviation_vs_reference_gates_entries(tmp_path):
    clock = Clock(T0.timestamp())
    svc = _service(tmp_path, clock)
    tr = svc.traders["svc_rand_v1"]
    svc.on_event("upbit", "trades", {"price": 1.0e8, "ts_exchange": 0})
    svc.on_event("coinone", "trades", {"ts_exchange": int(clock.t * 1000), "price": 1.02e8, "qty": 0.1})
    ok, why = tr.risk.can_enter(pd.Timestamp(clock.t, unit="s", tz="UTC").to_pydatetime())
    assert not ok and why.startswith("price_deviation")
    clock.t += 120                                     # 참조가 60초 이상 갱신 없음 → 괴리 판정 해제
    svc.on_event("coinone", "trades", {"ts_exchange": int(clock.t * 1000), "price": 1.02e8, "qty": 0.1})
    ok, why = tr.risk.can_enter(pd.Timestamp(clock.t, unit="s", tz="UTC").to_pydatetime())
    assert ok, why


def test_warmup_retries_on_fetch_failure(tmp_path, monkeypatch):
    rng = np.random.default_rng(8)
    raw = synth_raw(rng, T0 - pd.Timedelta(days=4), 4 * 1440)
    calls = []

    def fetch(market, unit, start, end, progress=False):
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("boom")
        return raw[(raw.index >= start) & (raw.index < end)]

    async def no_sleep(_):
        return None
    clock = Clock(T0.timestamp() + 10)
    svc = _service(tmp_path, clock, fetch=fetch)
    svc.on_event("coinone", "trades", {"ts_exchange": int(clock.t * 1000), "price": 1e8, "qty": 0.1})
    clock.t = T0.timestamp() + 70
    monkeypatch.setattr(S.asyncio, "sleep", no_sleep)
    asyncio.run(svc.warmup())
    assert len(calls) == 3 and svc.ready and svc.hist.last_ts == raw.index[-1]


def test_costs_from_param_registry(tmp_path):
    from btc_quant.params import all_cost_models
    from btc_quant.params import costs as C
    from btc_quant.backtest.types import CostModel
    # 이름은 그대로 (기존 5개 + upbit_stress), 값은 레지스트리 현재 버전
    assert set(S.COSTS) >= {"coinone_base", "coinone_conservative", "coinone_stress", "upbit_base",
                            "upbit_conservative"}
    assert S.COSTS == all_cost_models() == S.load_costs()
    # 새 버전이 생기면 서비스가 시작할 때 그 버전을 쓴다
    (tmp_path / "config").mkdir()
    C.ensure_costs_file(tmp_path)
    new = CostModel("coinone_conservative", 0.0, 3.0, 4.0)
    C.add_version("coinone_conservative", new, direction_="stricter", applied_by="auto", source="test",
                  root=tmp_path)
    assert S.load_costs(tmp_path)["coinone_conservative"] == new
    assert S.load_costs(tmp_path)["coinone_conservative"].name == "coinone_conservative"


def test_service_uses_registry_cost(tmp_path, monkeypatch):
    from btc_quant.backtest.types import CostModel
    m = CostModel("coinone_conservative", 0.0, 2.5, 3.5)
    monkeypatch.setattr(S, "load_costs", lambda root=None: {"coinone_conservative": m})
    svc = _service(tmp_path, lambda: pd.Timestamp("2024-01-01", tz="UTC"))
    assert svc.cost == m
    monkeypatch.setattr(S, "load_costs", lambda root=None: {})
    with pytest.raises(ValueError, match="unknown cost model"):
        _service(tmp_path, lambda: pd.Timestamp("2024-01-01", tz="UTC"))
