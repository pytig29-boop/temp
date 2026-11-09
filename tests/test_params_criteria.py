"""params.criteria — anti-goalpost 거부, 합성 보정 결과 → 제안 (가짜 runner, 네트워크·장시간 실행 없음)."""
import shutil
from pathlib import Path

import pytest

from btc_quant.params import costs as C
from btc_quant.params import criteria as Q
from btc_quant.params import proposals as P

ROOT = Path(__file__).resolve().parents[1]
K_FOR = {2.0: 0.03}


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    for f in ("gates.yaml", "splits.yaml", "exchanges.yaml", "params.yaml"):
        shutil.copy(ROOT / "config" / f, tmp_path / "config" / f)
    C.ensure_costs_file(tmp_path)
    return tmp_path


def fake_runner(fpr_by_value: dict, power_by_value: dict):
    """값마다 정해진 비율로 validated 를 돌려주는 runner (seed 순서대로 결정적)."""
    def run(tasks):
        out, cnt = [], {}
        for t in tasks:
            v, kind = t["value"], t["kind"]
            i = cnt[(v, kind)] = cnt.get((v, kind), 0) + 1
            n = sum(1 for x in tasks if x["value"] == v and x["kind"] == kind)
            rate = fpr_by_value[v] if kind == "null" else power_by_value[v]
            hit = i <= round(rate * n)
            out.append({**t, "any_validated": hit if kind == "null" else True,
                        "star_validated": hit if kind == "planted" else False, "star_dev_passed": True})
        return out
    return run


@pytest.mark.parametrize("motivation,refs,match", [
    ("candidate from camp-000012 just missed DSR", [], "ledger"),
    ("loosen DSR", ["research/campaigns/camp_12/report.md"], "artefacts"),
    ("loosen DSR", ["strategies/review/brk_x.yaml"], "artefacts"),
    ("loosen DSR", ["https://my-notes/val-run"], "not synthetic"),
    ("", [], "motivation"),
])
def test_anti_goalpost_refusals(tmp_path, motivation, refs, match):
    root = make_root(tmp_path)
    called = []
    with pytest.raises(Q.GoalpostError, match=match):
        Q.propose_criterion("gates.G5.min_dsr", [0.9], Q.Targets(), motivation, refs, root=root,
                            runner=lambda t: called.append(t) or [], k_for=K_FOR)
    assert not called                                     # 거부면 아무것도 실행하지 않음
    assert not P.list_proposals(root)


def test_anti_goalpost_known_real_ids(tmp_path):
    root = make_root(tmp_path)
    from btc_quant.registry.ledger import Ledger
    from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec
    led = Ledger(root / "research" / "ledger")
    h = led.register_hypothesis("h", "r", "c")
    cid = led.create_campaign(h, {"a": [1]}, 5, ["coinone_conservative"], ("2021-01-01", "2025-11-01"))
    spec = StrategySpec("brk_failed_cand", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(1.0, 1.2, 60))
    led.record_run(cid, spec, "coinone_conservative", ("2021-01-01", "2025-11-01"), "snap", "abc", {"sharpe": 1})
    for text in ("brk_failed_cand failed G5 by a hair", f"see {spec.logic_hash()}"):
        with pytest.raises(Q.GoalpostError, match="anti-goalpost"):
            Q.check_anti_goalpost("gates.G5.min_dsr", text, [], root)
    Q.check_anti_goalpost("gates.G5.min_dsr", "synthetic FPR study of DSR threshold", ["synthetic:null-markets"], root)


@pytest.mark.parametrize("key,match", [
    ("gates.risk.drawdown_floor", "class 3"),            # class ③ 는 위험 선호
    ("costs.coinone_stress", "class 1"),
    ("robustness.latency_min_retention", "code"),         # 코드 상수는 사람이 코드로
])
def test_only_gates_class2(tmp_path, key, match):
    root = make_root(tmp_path)
    with pytest.raises(Q.GoalpostError, match=match):
        Q.check_anti_goalpost(key, "synthetic study", [], root)


def test_proposal_from_synthetic_evidence(tmp_path):
    root = make_root(tmp_path)
    runner = fake_runner({0.95: 0.0, 0.9: 0.05, 0.8: 0.3}, {0.95: 0.5, 0.9: 0.8, 0.8: 0.9})
    st = Q.propose_criterion("gates.G5.min_dsr", [0.9, 0.8], Q.Targets(0.05, 0.7, 2.0),
                             "DSR threshold power check on synthetic markets", ["synthetic:calibration_study"],
                             mode="bounded", n_null_markets=20, n_planted_markets=10, root=root, runner=runner,
                             k_for=K_FOR, progress=lambda *a: None)
    assert st.status == "proposed" and st.recommended == 0.9
    row = {r["value"]: r for r in st.rows}
    assert row[0.95]["power"] == 0.5 and row[0.8]["fpr"] == 0.3
    doc = P.load_proposal(st.proposal_id, root)
    assert doc["class"] == 2 and doc["kind"] == "criterion"
    assert doc["changes"] == [{"target": "yaml:config/gates.yaml#G5.min_dsr", "key": "gates.G5.min_dsr",
                               "from": 0.95, "to": 0.9}]
    assert "next campaign" in doc["applies_from"]
    assert (root / st.report_path).exists()
    # 같은 seed(공통 난수) 가 값마다 쓰였는지: build_tasks 확인
    tasks, _ = Q.build_tasks("G5.min_dsr", [0.95, 0.9], "bounded", 2.0, 3, 2, k_for=K_FOR)
    seeds = {v: [t["seed"] for t in tasks if t["value"] == v] for v in (0.95, 0.9)}
    assert seeds[0.95] == seeds[0.9]
    assert all(t["gate_overrides"] == {"G5.min_dsr": t["value"]} for t in tasks)


def test_no_proposal_when_current_best_or_none_meet(tmp_path):
    root = make_root(tmp_path)
    kw = dict(mode="bounded", n_null_markets=10, n_planted_markets=10, root=root, k_for=K_FOR,
              progress=lambda *a: None)
    st = Q.propose_criterion("gates.G5.min_dsr", [0.9], Q.Targets(), "synthetic check",
                             runner=fake_runner({0.95: 0.0, 0.9: 0.2}, {0.95: 0.8, 0.9: 0.9}), **kw)
    assert st.status == "no_change" and st.proposal_id is None
    st = Q.propose_criterion("gates.G5.min_dsr", [0.9], Q.Targets(), "synthetic check",
                             runner=fake_runner({0.95: 0.0, 0.9: 0.2}, {0.95: 0.3, 0.9: 0.9}), **kw)
    assert st.status == "no_candidate_meets_targets" and not P.list_proposals(root)


def test_quick_mode_is_smoke_only(tmp_path):
    root = make_root(tmp_path)
    st = Q.propose_criterion("gates.G5.min_dsr", [0.9], Q.Targets(), "synthetic check", mode="quick",
                             n_null_markets=2, n_planted_markets=2, root=root, k_for=K_FOR,
                             runner=fake_runner({0.95: 0.0, 0.9: 0.0}, {0.95: 0.0, 0.9: 1.0}),
                             progress=lambda *a: None)
    assert st.recommended == 0.9 and st.status == "smoke_only" and st.proposal_id is None
