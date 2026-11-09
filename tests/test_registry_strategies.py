import pytest
import yaml

from btc_quant.registry import admin_cli
from btc_quant.registry.strategies import (ActivationApprovalError, RegistryError, StrategyRegistry,
                                           TransitionError)
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec


def spec(sid="brk", version=1, lb=48):
    return StrategySpec(id=sid, version=version, entry=BlockRef("donchian_breakout", {"lookback": lb}),
                        exit=ExitSpec(0.7, 0.9, 90))


def to_review(reg, s):
    reg.register(s)
    for st in ("candidate", "validated", "challenger", "review"):
        reg.transition(s.id, s.version, st, "gate pass")


def test_forward_path_and_illegal(tmp_path):
    reg = StrategyRegistry(tmp_path)
    reg.register(spec())
    with pytest.raises(RegistryError):
        reg.register(spec())
    with pytest.raises(TransitionError):
        reg.transition("brk", 1, "validated", "skip")
    with pytest.raises(TransitionError):
        reg.transition("brk", 1, "active", "sneak")
    reg.transition("brk", 1, "candidate", "G0")
    assert reg.state_of("brk", 1) == "candidate"
    assert reg.load("brk", 1) == spec()
    reg.reject("brk", 1, "failed G2")
    assert reg.state_of("brk", 1) == "rejected"
    with pytest.raises(TransitionError):
        reg.transition("brk", 1, "validated", "zombie")
    with pytest.raises(ValueError):
        reg.reject("brk", 1, "")
    states = [(t["from_state"], t["to_state"]) for t in reg.ledger.transitions("brk")]
    assert states == [(None, "draft"), ("draft", "candidate"), ("candidate", "rejected")]


def test_challenger_cap(tmp_path):
    reg = StrategyRegistry(tmp_path)
    for i in range(4):
        reg.register(spec(f"s{i}"))
        for st in ("candidate", "validated"):
            reg.transition(f"s{i}", 1, st, "ok")
    for i in range(3):
        reg.transition(f"s{i}", 1, "challenger", "ok")
    with pytest.raises(TransitionError):
        reg.transition("s3", 1, "challenger", "ok")


def test_activation_requires_human_approval_and_rollback(tmp_path):
    reg = StrategyRegistry(tmp_path)
    to_review(reg, spec("a"))
    with pytest.raises(ActivationApprovalError):
        reg.activate("a", 1)
    # 위조 승인(logic_hash 불일치)
    p = reg.approval_path("a", 1)
    p.parent.mkdir(parents=True)
    p.write_text(yaml.safe_dump({"strategy_id": "a", "version": 1, "logic_hash": "x"}))
    with pytest.raises(ActivationApprovalError):
        reg.activate("a", 1)
    reg.write_activation_approval("a", 1, "human")
    assert reg.activate("a", 1) is None
    assert not p.exists()  # 소비됨
    assert reg.active()["id"] == "a"

    to_review(reg, spec("b"))
    reg.write_activation_approval("b", 1, "human")
    prev = reg.activate("b", 1)
    assert prev["id"] == "a" and reg.state_of("a", 1) == "retired"
    assert len(reg.list("active")) == 1 and reg.active()["id"] == "b"

    assert reg.rollback() == ("b_v1", "a_v1")
    assert reg.active()["id"] == "a" and reg.state_of("b", 1) == "retired"
    assert len(reg.list("active")) == 1


def test_rollback_without_previous(tmp_path):
    reg = StrategyRegistry(tmp_path)
    with pytest.raises(RegistryError):
        reg.rollback()
    to_review(reg, spec("a"))
    reg.write_activation_approval("a", 1, "human")
    reg.activate("a", 1)
    with pytest.raises(RegistryError):
        reg.rollback()


# ---------------------------------------------------------------- admin CLI
def _tty(monkeypatch, answers, tty=True):
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *_: next(it))


def _cfg(tmp_path):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "splits.yaml").write_text(yaml.safe_dump({
        "lockbox_id": "lb1", "lockbox_cap": 10, "segments": {
            "dev": {"start": "2021-01-01", "end": "2025-11-01"},
            "val": {"start": "2025-11-01", "end": "2026-05-01"},
            "lockbox": {"start": "2026-05-01", "end": "2026-11-09"},
            "forward": {"start": "2026-11-09", "end": None}}}))


def test_admin_cli_confirmation(tmp_path, monkeypatch, capsys):
    _cfg(tmp_path)
    root = ["--root", str(tmp_path), "--by", "tester"]
    _tty(monkeypatch, ["wrong"])
    assert admin_cli.main(root + ["approve-lockbox", "cand1"]) == 3
    assert not (tmp_path / "research" / "lockbox_approvals" / "cand1.yaml").exists()
    _tty(monkeypatch, ["cand1"], tty=False)
    assert admin_cli.main(root + ["approve-lockbox", "cand1"]) == 3
    _tty(monkeypatch, ["cand1"])
    assert admin_cli.main(root + ["approve-lockbox", "cand1"]) == 0
    ap = yaml.safe_load((tmp_path / "research" / "lockbox_approvals" / "cand1.yaml").read_text())
    assert ap["approved_by"] == "tester" and ap["lockbox_id"] == "lb1"

    reg = StrategyRegistry(tmp_path)
    to_review(reg, spec("a"))
    to_review(reg, spec("b"))
    _tty(monkeypatch, ["a_v1"])
    assert admin_cli.main(root + ["activate", "a", "1"]) == 0
    _tty(monkeypatch, ["nope"])
    assert admin_cli.main(root + ["activate", "b", "1"]) == 3
    assert reg.state_of("b", 1) == "review"
    _tty(monkeypatch, ["b_v1"])
    assert admin_cli.main(root + ["activate", "b", "1"]) == 0
    _tty(monkeypatch, ["rollback"])
    assert admin_cli.main(root + ["rollback"]) == 0
    assert reg.active()["id"] == "a"
    _tty(monkeypatch, ["b_v1"])
    assert admin_cli.main(root + ["reject", "b", "1", "--reason", "bad"]) == 0
    assert reg.state_of("b", 1) == "rejected"
    capsys.readouterr()
    assert admin_cli.main(root + ["status"]) == 0
    out = capsys.readouterr().out
    assert '"a_v1"' in out and '"pending_approvals"' in out


# ---------------------------------------------------------------- 리스크 사이드카 이동
def _write_sidecar(reg, state, key, grade="A"):
    p = reg.dir / state / f"{key}.risk.yaml"
    p.write_text(yaml.safe_dump({"grade": grade, "limits": {"daily_loss": -0.02}}))
    return p


def test_risk_sidecar_moves_with_spec(tmp_path):
    reg = StrategyRegistry(tmp_path)
    reg.register(spec("a"))
    for st in ("candidate", "validated"):
        reg.transition("a", 1, st, "ok")
    side = _write_sidecar(reg, "validated", "a_v1")             # pipeline.write_risk_yaml 위치
    for st in ("challenger", "review"):
        reg.transition("a", 1, st, "ok")
        assert (reg.dir / st / "a_v1.risk.yaml").exists() and not side.exists()
    assert yaml.safe_load((reg.dir / "review" / "a_v1.risk.yaml").read_text())["grade"] == "A"
    # risk.yaml 은 목록에 스펙으로 잡히지 않는다
    assert [x["id"] for x in reg.list("review")] == ["a"]

    reg.write_activation_approval("a", 1, "human")              # activate: review → active
    reg.activate("a", 1)
    assert (reg.dir / "active" / "a_v1.risk.yaml").exists()
    assert not (reg.dir / "review" / "a_v1.risk.yaml").exists()

    to_review(reg, spec("b"))                                    # b 는 사이드카 없음 (그대로 동작)
    _write_sidecar(reg, "review", "b_v1", grade="B")
    reg.write_activation_approval("b", 1, "human")
    reg.activate("b", 1)                                         # a: active → retired (사이드카 함께)
    assert (reg.dir / "retired" / "a_v1.risk.yaml").exists()
    assert (reg.dir / "active" / "b_v1.risk.yaml").exists()

    reg.rollback()                                               # b → retired, a → active
    assert (reg.dir / "active" / "a_v1.risk.yaml").exists()
    assert (reg.dir / "retired" / "b_v1.risk.yaml").exists()
    assert not (reg.dir / "retired" / "a_v1.risk.yaml").exists()

    reg.register(spec("c"))
    reg.transition("c", 1, "candidate", "ok")
    reg.transition("c", 1, "validated", "ok")
    _write_sidecar(reg, "validated", "c_v1")
    reg.reject("c", 1, "skeptic review failed")                  # reject 도 함께 이동
    assert (reg.dir / "rejected" / "c_v1.risk.yaml").exists()
    assert not (reg.dir / "validated" / "c_v1.risk.yaml").exists()


def test_risk_sidecar_conflict_leaves_files_untouched(tmp_path):
    reg = StrategyRegistry(tmp_path)
    reg.register(spec("a"))
    reg.transition("a", 1, "candidate", "ok")
    reg.transition("a", 1, "validated", "ok")
    _write_sidecar(reg, "validated", "a_v1")
    stale = _write_sidecar(reg, "challenger", "a_v1", grade="C")   # 목적지에 남은 사이드카
    with pytest.raises(RegistryError, match="risk.yaml"):
        reg.transition("a", 1, "challenger", "ok")
    assert reg.state_of("a", 1) == "validated"
    assert (reg.dir / "validated" / "a_v1.risk.yaml").exists() and stale.exists()
