"""잠금 자율 개봉 승인 (D31): phase·잠금·서류 판정·상태·예산 검사, open_lockbox 재검사."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from btc_quant.registry import authorize as A
from btc_quant.registry.splits import DataAccess, LockboxApprovalError
from btc_quant.registry.strategies import StrategyRegistry

sys.path.insert(0, str(Path(__file__).parent))
from test_registry_strategies import spec  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _git(r, *a):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=r, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "r"
    for f in ("config/gates.yaml", "config/floor.yaml", "config/splits.yaml", "config/costs.yaml"):
        (r / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, r / f)
    (r / "config" / "autonomy.yaml").write_text(yaml.safe_dump(
        {"phase": "final", "ruleset_lock_required": True, "verdict_budget": {"lockbox_openings": 2}, "program": {}}))
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    from btc_quant.params.ruleset import lock
    lock("rs-001", None, r)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "lock")
    reg = StrategyRegistry(r)
    for sid in ("a", "b", "c"):
        reg.register(spec(sid))
        for st in ("candidate", "validated"):
            reg.transition(sid, 1, st, "gate pass")
    return r


def _dossier(r, cid, **v):
    d = r / "research" / "dossiers" / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "verdicts.json").write_text(json.dumps({"skeptic": "SURVIVES", "judge": "PASS", "paper": "PASS", **v}))


def test_authorization_conditions_and_open(repo):
    lh = spec("a").logic_hash()
    with pytest.raises(A.AuthorizationError, match="verdicts"):
        A.lockbox_authorization("a_v1", lh, "a", 1, repo)
    _dossier(repo, "a_v1", judge="FAIL")
    with pytest.raises(A.AuthorizationError):
        A.lockbox_authorization("a_v1", lh, "a", 1, repo)
    _dossier(repo, "a_v1")
    p = A.lockbox_authorization("a_v1", lh, "a", 1, repo)
    assert yaml.safe_load(p.read_text())["auto"] is True
    da = DataAccess(root=repo)
    key = da.open_lockbox("a_v1", lh)
    assert key.candidate_id == "a_v1" and A.auto_openings(repo) == 1
    # 기록을 직접 만들어도 조건이 실제로 성립하지 않으면 열리지 않는다 (서류 판정 실패)
    _dossier(repo, "b_v1", paper="FAIL")
    (repo / "research" / "lockbox_approvals" / "b_v1.yaml").write_text(yaml.safe_dump(
        {"candidate_id": "b_v1", "logic_hash": spec("b").logic_hash(), "auto": True, "strategy_id": "b", "version": 1}))
    with pytest.raises(LockboxApprovalError, match="no longer valid"):
        da.open_lockbox("b_v1", spec("b").logic_hash())
    # 예산 2
    _dossier(repo, "c_v1")
    A.lockbox_authorization("c_v1", spec("c").logic_hash(), "c", 1, repo)
    da.open_lockbox("c_v1", spec("c").logic_hash())
    _dossier(repo, "b_v1")
    with pytest.raises(A.AuthorizationError, match="budget"):
        A.lockbox_authorization("b_v1", spec("b").logic_hash(), "b", 1, repo)


def test_phase_must_be_final(repo):
    ap = repo / "config" / "autonomy.yaml"
    d = yaml.safe_load(ap.read_text())
    d["phase"] = "research"
    ap.write_text(yaml.safe_dump(d))
    _dossier(repo, "a_v1")
    with pytest.raises(A.AuthorizationError, match="phase"):
        A.lockbox_authorization("a_v1", spec("a").logic_hash(), "a", 1, repo)
