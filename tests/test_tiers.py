"""단계별 승격 (PLAN D23): T0/T1 기본 해석, 사람 전용 T2/T3 승격, 자본 비중 = risk_prefs.tier_capital,
명목 상한 = 총자본 × capital_fraction × position_scale, 리스크 사람-해제 정지 → 자동 강등, 원장 이력, guard 차단."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from btc_quant.registry import admin_cli
from btc_quant.registry.strategies import StrategyRegistry
from btc_quant.registry.tiers import (TierBook, TierError, allowed_fractions, check_order_notional,
                                      effective_fraction, live_notional_cap, tier_capital)

sys.path.insert(0, str(Path(__file__).parent))
from test_guard_hook import denied, run  # noqa: E402
from test_registry_strategies import spec, to_review  # noqa: E402


def test_tier_capital_is_user_decision():
    assert tier_capital() == {"T2": 0.1, "T3": [0.3, 1.0]}
    assert allowed_fractions("T2") == [0.1] and allowed_fractions("T3") == [0.3, 1.0]
    assert allowed_fractions("T1") == [0.0]
    assert effective_fraction(0.1, 0.4) == pytest.approx(0.04)
    assert live_notional_cap(10_000_000, 0.1, 0.5) == pytest.approx(500_000)
    assert live_notional_cap(10_000_000, 0.0, 1.0) == 0.0          # T1 = 실제 자본 0
    check_order_notional(500_000, 500_000)
    with pytest.raises(TierError):
        check_order_notional(500_001, 500_000)
    with pytest.raises(TierError):
        effective_fraction(1.2, 1.0)


def test_default_tiers_follow_registry_state(tmp_path):
    reg = StrategyRegistry(tmp_path)
    tb = TierBook(reg)
    reg.register(spec())
    assert tb.get("brk", 1)["tier"] == "T0"
    for st in ("candidate", "validated"):
        reg.transition("brk", 1, st, "gate pass")
    assert tb.get("brk", 1) == {"tier": "T1", "capital_fraction": 0.0, "state": "validated"}


def test_promotion_is_staged_and_human_only(tmp_path):
    reg = StrategyRegistry(tmp_path)
    tb = TierBook(reg)
    s = spec()
    reg.register(s)
    for st in ("candidate", "validated"):
        reg.transition(s.id, 1, st, "gate pass")
    with pytest.raises(TierError):                     # G8 전(review 아님) → T2 불가
        tb.promote(s.id, 1, "T2", approved_by="alice")
    reg.transition(s.id, 1, "challenger", "x")
    reg.transition(s.id, 1, "review", "G8 pass")
    with pytest.raises(TierError):
        tb.promote(s.id, 1, "T3", approved_by="alice")  # T2 를 건너뛸 수 없다
    for who in ("ai", "agent", "risk", ""):
        with pytest.raises(TierError):
            tb.promote(s.id, 1, "T2", approved_by=who)
    with pytest.raises(TierError):
        tb.promote(s.id, 1, "T2", approved_by="alice", capital_fraction=0.5)   # 사용자 결정 외 비중
    rec = tb.promote(s.id, 1, "T2", approved_by="alice")
    assert rec["tier"] == "T2" and rec["capital_fraction"] == 0.1 and rec["previous"] == "T1"
    assert tb.get(s.id, 1)["tier"] == "T2"
    rec = tb.promote(s.id, 1, "T3", approved_by="alice", capital_fraction=0.3)
    assert rec["capital_fraction"] == 0.3
    with pytest.raises(TierError):
        tb.promote(s.id, 1, "T3", approved_by="alice", capital_fraction=0.3)   # 증액만
    tb.promote(s.id, 1, "T3", approved_by="alice", capital_fraction=1.0)
    hist = tb.history(s.id, 1)
    assert [(h["from_state"], h["to_state"]) for h in hist] == [("tier:T1", "tier:T2"), ("tier:T2", "tier:T3"),
                                                                 ("tier:T3", "tier:T3")]
    assert all(h["event"] == "tier" for h in hist)
    # 강등 (자동/사람) — 멱등
    assert tb.demote(s.id, 1, "max_drawdown halt")["tier"] == "T1"
    assert tb.demote(s.id, 1, "again") is None
    assert tb.get(s.id, 1)["capital_fraction"] == 0.0
    # 상태가 rejected 로 가면 단계 기록은 의미를 잃는다(T0)
    tb.promote(s.id, 1, "T2", approved_by="alice")
    reg.reject(s.id, 1, "retired by human")
    assert tb.get(s.id, 1)["tier"] == "T0"


def test_registry_rollback_unaffected_by_tier_records(tmp_path):
    reg = StrategyRegistry(tmp_path)
    tb = TierBook(reg)
    a, b = spec("a"), spec("b")
    for s_ in (a, b):
        to_review(reg, s_)
    reg.write_activation_approval("a", 1, "alice")
    reg.activate("a", 1)
    tb.promote("a", 1, "T2", approved_by="alice")
    reg.write_activation_approval("b", 1, "alice")
    reg.activate("b", 1)
    tb.promote("b", 1, "T2", approved_by="alice")
    assert reg.rollback() == ("b_v1", "a_v1")


def test_risk_human_halt_demotes_automatically(tmp_path):
    from btc_quant.live.risk import RiskLimits, RiskManager
    reg = StrategyRegistry(tmp_path)
    tb = TierBook(reg)
    s = spec()
    to_review(reg, s)
    tb.promote(s.id, 1, "T2", approved_by="alice")
    risk = RiskManager(RiskLimits(daily_loss=-0.5, weekly_loss=-0.5, max_drawdown=-0.05, max_trades_per_day=100), s.id, "B", kill_dir=None)
    risk.on_human_halt = lambda h: tb.demote(s.id, 1, f"risk halt {h['code']}", actor="risk")
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    risk.on_equity(now, 1.0)
    risk.on_equity(now, 0.90)                           # 낙폭 10% > 5% → HUMAN 정지
    assert tb.get(s.id, 1)["tier"] == "T1"
    assert tb.history(s.id, 1)[-1]["actor"] == "risk"


def test_admin_cli_promote_requires_interactive_confirmation(tmp_path, monkeypatch, capsys):
    reg = StrategyRegistry(tmp_path)
    to_review(reg, spec())
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert admin_cli.main(["--root", str(tmp_path), "promote-tier", "brk", "1", "T2"]) == 3
    assert TierBook(reg).get("brk", 1)["tier"] == "T1"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "brk_v1 T2")
    assert admin_cli.main(["--root", str(tmp_path), "--by", "alice", "promote-tier", "brk", "1", "T2"]) == 0
    assert TierBook(reg).get("brk", 1)["tier"] == "T2"
    monkeypatch.setattr("builtins.input", lambda *_: "brk_v1")
    assert admin_cli.main(["--root", str(tmp_path), "--by", "alice", "demote-tier", "brk", "1",
                           "--reason", "test"]) == 0
    assert TierBook(reg).get("brk", 1)["tier"] == "T1"


@pytest.mark.parametrize("cmd", [
    "uv run btc-quant-admin promote-tier brk 1 T2",
    "python -m btc_quant.registry.admin_cli promote-tier brk 1 T3 --fraction 1.0",
    "python -c 'from btc_quant.registry.tiers import TierBook; TierBook(r).promote(\"brk\", 1, \"T2\", \"me\")'",
    "echo 'brk_v1: {tier: T3}' > strategies/tiers.yaml",
])
def test_guard_blocks_tier_changes(tmp_path, cmd):
    assert denied(run("Bash", {"command": cmd}, tmp_path=tmp_path)), cmd


def test_guard_blocks_tiers_file_write(tmp_path):
    assert denied(run("Write", {"file_path": str(tmp_path / "strategies" / "tiers.yaml"), "content": "x"},
                      tmp_path=tmp_path))
