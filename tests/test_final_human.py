"""결선 사람 승인 경로 (final_human): 페이퍼 판정 없이, 사람 승인 파일로만 연다. 합성 저장소에서 끝까지."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from test_research_synth import make_root, template_dict  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _git(r, *a):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=r, check=True, capture_output=True)


def test_final_human_path(tmp_path):
    from btc_quant.params.ruleset import lock
    from btc_quant.registry.splits import LockboxApprovalError, write_lockbox_approval
    from btc_quant.research import final_human as FH
    from btc_quant.research.campaign import CampaignTemplate, ResearchEnv, run_campaign
    root = make_root(tmp_path / "r", planted=True, seed=7)
    for f in ("gates.yaml", "costs.yaml"):
        shutil.copy(ROOT / "config" / f, root / "config" / f)
    env = ResearchEnv.at(root)
    cr = run_campaign(CampaignTemplate.from_dict(template_dict(name="e2eh", filters=[[]])), env=env)
    lh = cr.ranked()["logic_hash"].iloc[0]
    spec = cr.specs[lh]
    env.registry.register(spec)
    for st in ("candidate", "validated"):
        env.registry.transition(spec.id, spec.version, st, "gate pass", evidence={"campaign_id": cr.campaign_id})
    cid = f"{spec.id}_v{spec.version}"
    d = root / "research" / "dossiers" / cid
    d.mkdir(parents=True)
    (d / "verdicts.json").write_text(json.dumps({"skeptic": "SURVIVES", "judge": "PASS", "logic_hash": lh}))
    (root / "config" / "autonomy.yaml").write_text(yaml.safe_dump(
        {"phase": "research", "ruleset_lock_required": True, "verdict_budget": {"lockbox_openings": 5}, "program": {}}))
    _git(root, "init", "-q")
    _git(root, "add", "config")
    _git(root, "commit", "-qm", "cfg")
    lock("rs-001", None, root)
    _git(root, "add", "research/rule_locks")
    _git(root, "commit", "-qm", "lock")
    with pytest.raises(LockboxApprovalError):          # 사람 승인 없으면 못 연다
        FH.run(env, cid, 1)
    write_lockbox_approval(root, cid, "human:test", logic_hash=lh)
    pub = FH.run(env, cid, 1)
    assert pub["paper_required"] is True and set(pub["candidates"][0]) == {"candidate_id", "final"}
    human = json.loads((root / "research" / "reports" / "human" / "final_round1.json").read_text())
    assert set(human["candidates"][0]["flags"]) >= {"F1a", "F1b", "F1c", "F2", "F5", "final"}
    opens = [r for r in env.ledger.records("lockbox") if r.get("event") == "open"]
    assert len(opens) == 1 and not opens[0]["approval"].get("auto")
    write_lockbox_approval(root, cid, "human:test", logic_hash=lh)
    with pytest.raises(LockboxApprovalError):          # 같은 후보 재개봉 불가
        FH.run(env, cid, 2)
