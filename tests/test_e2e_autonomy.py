"""총연습 축소판 (autonomous_research_plan Phase 3·§4-K): 합성 저장소에서 캠페인 → 레지스트리 validated → 서류 판정 →
규칙 잠금 → phase final → 결선(잠금 구간 자율 개봉·F1~F5) → 공개 결과 코드 + 사람용 수치 → 최종 보고 부록.
실제 잠금 구간 코드를 처음 실제 데이터에서 돌리지 않도록, 같은 코드 경로를 합성에서 끝까지 실행한다."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from test_research_synth import make_root, template_dict  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _git(r, *a):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=r, check=True, capture_output=True)


def test_final_round_end_to_end(tmp_path):
    from btc_quant.params.ruleset import lock
    from btc_quant.research import final_report as FR
    from btc_quant.research import final_verdict as FV
    from btc_quant.research.campaign import CampaignTemplate, ResearchEnv, run_campaign
    root = make_root(tmp_path / "r", planted=True, seed=7)
    for f in ("gates.yaml", "costs.yaml"):
        shutil.copy(ROOT / "config" / f, root / "config" / f)
    env = ResearchEnv.at(root)
    cr = run_campaign(CampaignTemplate.from_dict(template_dict(name="e2e", filters=[[]])), env=env)
    lh = cr.ranked()["logic_hash"].iloc[0]
    spec = cr.specs[lh]
    reg = env.registry
    reg.register(spec)
    for st in ("candidate", "validated"):
        reg.transition(spec.id, spec.version, st, "gate pass", evidence={"campaign_id": cr.campaign_id})
    cid = f"{spec.id}_v{spec.version}"
    d = root / "research" / "dossiers" / cid
    d.mkdir(parents=True)
    (d / "verdicts.json").write_text(json.dumps({"skeptic": "SURVIVES", "judge": "PASS", "paper": "PASS",
                                                "logic_hash": lh}))
    (root / "config" / "autonomy.yaml").write_text(yaml.safe_dump(
        {"phase": "final", "ruleset_lock_required": True, "verdict_budget": {"lockbox_openings": 5}, "program": {}}))
    _git(root, "init", "-q")
    _git(root, "add", "config")
    _git(root, "commit", "-qm", "cfg")
    lock("rs-001", None, root)
    _git(root, "add", "research/rule_locks")
    _git(root, "commit", "-qm", "lock")
    pub = FV.run(env, 1)
    assert [c["candidate_id"] for c in pub["candidates"]] == [cid]
    assert set(pub["candidates"][0]) == {"candidate_id", "final"}                     # 공개 = 결과 코드만
    human = json.loads((root / "research" / "reports" / "human" / "final_round1.json").read_text())
    rec = human["candidates"][0]["record"]
    for k in ("lb_sharpe", "lb_p", "oos_p", "full_mc_mdd95", "full_bh_sharpe", "val_p", "dev_worst_month"):
        assert rec.get(k) is not None, k
    assert set(human["candidates"][0]["flags"]) >= {"F1a", "F1b", "F1c", "F2", "F5", "final"}
    led = env.ledger
    opens = [r for r in led.records("lockbox") if r.get("event") == "open"]
    assert len(opens) == 1 and opens[0]["approval"]["auto"] is True
    assert any(r.get("event") == "result" for r in led.records("lockbox"))
    # 같은 후보를 다시 열 수 없다
    import pytest
    from btc_quant.registry.authorize import AuthorizationError
    from btc_quant.registry.splits import LockboxApprovalError
    with pytest.raises((AuthorizationError, LockboxApprovalError, Exception)):
        FV.run(env, 2)
    text = FR.render(FR.collect(root))
    assert cr.campaign_id in text and "잠금 개봉 1회" in text
