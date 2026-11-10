"""params.proposals — 사람 승인 흐름: 비대화형 거부, 확인 불일치, 승인 적용, 낡은 제안·변조 거부."""
import json
from pathlib import Path

import pytest
import yaml

from btc_quant.params import calibrate as K
from btc_quant.params import costs as C
from btc_quant.params import proposals as P
from btc_quant.params import registry as R
from btc_quant.params.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[1]


def make_root(tmp_path: Path) -> Path:
    from _config_root import make_config_root
    return make_config_root(tmp_path)      # 프리셋 시드 비용 (+ 자산별 모델·ticks.yaml — 카탈로그 일치)


def cost_proposal(root: Path) -> str:
    from test_params_calibrate import samples
    res = K.apply_calibration(K.calibrate(("coinone",), root=root, samples={"coinone": samples(0.5, 1.0, days=8)}),
                              root=root, report_name="cal.md")
    return res.proposal_id


def ok_confirm(expected, what):
    assert expected in what or expected  # 화면 요약을 받는다


def test_non_tty_refused(tmp_path):
    root = make_root(tmp_path)
    pid = cost_proposal(root)
    before = (root / "config" / "costs.yaml").read_text()
    with pytest.raises(P.ConfirmationError, match="interactive terminal"):
        P.approve(pid, "tester", root=root)                 # pytest stdin 은 tty 가 아님
    assert (root / "config" / "costs.yaml").read_text() == before
    assert P.approval_record(pid, root) is None
    # CLI 도 같은 이유로 거부 (exit 2)
    assert cli_main(["--root", str(root), "approve", pid]) == 2


def test_confirmation_mismatch_aborts(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    pid = cost_proposal(root)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "wrong-id")
    with pytest.raises(P.ConfirmationError, match="did not match"):
        P.approve(pid, "tester", root=root)
    assert P.approval_record(pid, root) is None


def test_typed_confirmation_applies_cost_proposal(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    pid = cost_proposal(root)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": pid)
    rec = P.approve(pid, "tester", root=root)
    assert rec["approved_by"] == "tester"
    assert {a["name"] for a in rec["applied"]} == {"coinone_base", "coinone_conservative", "coinone_stress"}
    h = C.cost_history("coinone_stress", root)[-1]
    assert h["applied_by"] == "human:tester" and h["proposal_id"] == pid and h["direction"] == "looser"
    assert C.round_trip_bps(C.cost_model("coinone_stress", root=root)) < 21.0
    assert json.loads((root / "research" / "param_approvals" / f"{pid}.json").read_text())["id"] == pid
    assert [p["id"] for p in P.list_proposals(root, status="approved")] == [pid]
    with pytest.raises(P.ProposalError, match="already approved"):
        P.approve(pid, "tester", root=root, confirm=ok_confirm)


def test_stale_and_tampered_proposals_refused(tmp_path):
    root = make_root(tmp_path)
    pid = cost_proposal(root)
    f = root / "research" / "param_proposals" / f"{pid}.yaml"
    doc = yaml.safe_load(f.read_text())
    doc["changes"][0]["to"]["spread_bps"] = 0.0
    f.write_text(yaml.safe_dump(doc, allow_unicode=True))
    with pytest.raises(P.ProposalError, match="modified"):
        P.approve(pid, "t", root=root, confirm=ok_confirm)
    pid2 = cost_proposal(root)
    # 그 사이 비용 모델이 바뀜 → 낡은 제안
    from btc_quant.backtest.types import CostModel
    C.add_version("coinone_stress", CostModel("coinone_stress", 0.0, 6.0, 9.0), direction_="stricter",
                  applied_by="auto", source="t", root=root)
    with pytest.raises(P.ProposalError, match="stale"):
        P.approve(pid2, "t", root=root, confirm=ok_confirm)


def test_criterion_approval_is_prospective(tmp_path):
    root = make_root(tmp_path)
    from btc_quant.registry.ledger import Ledger
    led = Ledger(root / "research" / "ledger")
    h = led.register_hypothesis("h", "r", "c")
    for _ in range(3):
        led.create_campaign(h, {"a": [1]}, 1, ["coinone_conservative"], ("2021-01-01", "2025-11-01"))
    pid = P.write_proposal({"class": 2, "kind": "criterion", "title": "t",
                            "changes": [{"target": "yaml:config/gates.yaml#G5.min_dsr", "key": "gates.G5.min_dsr",
                                         "from": 0.95, "to": 0.9}]}, root)
    rec = P.approve(pid, "tester", root=root, confirm=ok_confirm)
    assert rec["applied"][0]["applies_from_campaign"] == 4
    assert yaml.safe_load((root / "config" / "gates.yaml").read_text())["G5"]["min_dsr"] == 0.9
    reg = R.load_registry(root)                           # 검증 통과 (drift 없음)
    p = reg.get("gates.G5.min_dsr")
    assert p.version == 2 and p.history[-1]["proposal_id"] == pid
    assert R.gate_config_for_campaign("camp-000003", root)["G5"]["min_dsr"] == 0.95
    assert R.gate_config_for_campaign("camp-000004", root)["G5"]["min_dsr"] == 0.9


def test_preference_requires_choice(tmp_path):
    root = make_root(tmp_path)
    pid = P.write_proposal({"class": 3, "kind": "preference", "title": "dd",
                            "options": [{"label": "-0.1", "changes": {"gates.risk.drawdown_floor": -0.1}},
                                        {"label": "-0.15", "changes": {"gates.risk.drawdown_floor": -0.15}}]}, root)
    with pytest.raises(P.ProposalError, match="--choice"):
        P.approve(pid, "t", root=root, confirm=ok_confirm)
    with pytest.raises(P.ProposalError, match="unknown choice"):
        P.approve(pid, "t", root=root, choice="-0.3", confirm=ok_confirm)
    P.approve(pid, "t", root=root, choice="-0.15", confirm=ok_confirm)
    assert yaml.safe_load((root / "config" / "gates.yaml").read_text())["risk"]["drawdown_floor"] == -0.15
    assert R.validate(R.load_registry(root, validate_=False)) == []


def test_reject(tmp_path):
    root = make_root(tmp_path)
    pid = cost_proposal(root)
    P.reject(pid, "t", "not convinced", root=root, confirm=ok_confirm)
    assert [p["id"] for p in P.list_proposals(root, status="rejected")] == [pid]
    with pytest.raises(P.ProposalError, match="rejected"):
        P.approve(pid, "t", root=root, confirm=ok_confirm)
