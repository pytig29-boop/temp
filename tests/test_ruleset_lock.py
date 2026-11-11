"""사전 잠금 (autonomous_research_plan §4-C): 잠금 없이 거부, 잠금 뒤 설정 변경 거부, 미커밋 거부, 정비 창구,
구간 소진(연 뒤 느슨화), 엄격화는 소진 아님."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from btc_quant.params import ruleset as R
from btc_quant.registry.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]


def _git(root, *a):
    subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "r"
    for f in ("config/gates.yaml", "config/floor.yaml", "config/splits.yaml", "config/costs.yaml",
              "config/params.yaml"):
        (r / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, r / f)
    (r / "config" / "autonomy.yaml").write_text("phase: build\nruleset_lock_required: true\n"
                                                "verdict_budget: {val_new_uses: 10}\nprogram: {}\n")
    (r / "src" / "btc_quant" / "evaluation").mkdir(parents=True)
    (r / "src" / "btc_quant" / "evaluation" / "g.py").write_text("X = 1\n")
    _git(r, "init", "-q")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return r


def _commit(r, msg="c"):
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", msg)


def test_lock_lifecycle(repo):
    with pytest.raises(R.RulesetError, match="no ruleset lock"):
        R.require_locked_ruleset(repo, "x")
    R.lock("rs-001", None, repo)
    with pytest.raises(R.RulesetError, match="not committed"):
        R.require_locked_ruleset(repo, "x")
    _commit(repo)
    assert R.require_locked_ruleset(repo, "x") == "rs-001"
    # 판정 코드 한 글자 변경 → 해시 불일치
    (repo / "src/btc_quant/evaluation/g.py").write_text("X = 2\n")
    with pytest.raises(R.RulesetError, match="hash"):
        R.require_locked_ruleset(repo, "x")
    _commit(repo)
    with pytest.raises(R.RulesetError):
        R.require_locked_ruleset(repo, "x")
    with pytest.raises(R.RulesetError):                     # 같은 id 재사용 금지
        R.lock("rs-001", None, repo)
    R.lock("rs-002", None, repo)
    _commit(repo)
    assert R.require_locked_ruleset(repo, "x") == "rs-002"
    # 정비 창구
    st = repo / "research" / "program" / "state.json"
    st.parent.mkdir(parents=True, exist_ok=True)
    st.write_text(json.dumps({"maintenance": {"open": True}}))
    with pytest.raises(R.RulesetError, match="maintenance"):
        R.require_locked_ruleset(repo, "x")
    st.write_text(json.dumps({"maintenance": {"open": False}}))
    assert R.require_locked_ruleset(repo, "x") == "rs-002"
    # 인증 id 가 있으면 통과한 인증 파일 필요
    with pytest.raises(R.RulesetError):
        R.lock("bad", None, repo)


def test_lock_refuses_uncommitted_and_cert_required(repo):
    (repo / "config" / "gates.yaml").write_text((repo / "config" / "gates.yaml").read_text() + "\n# x\n")
    with pytest.raises(R.RulesetError, match="commit"):
        R.lock("rs-001", None, repo)
    _commit(repo)
    R.lock("rs-001", "cert-abc", repo)
    _commit(repo)
    with pytest.raises(R.RulesetError, match="certification"):
        R.require_locked_ruleset(repo, "x")
    cd = repo / "research" / "certifications" / "cert-abc"
    cd.mkdir(parents=True)
    (cd / "cert.json").write_text(json.dumps({"passed": True}))
    assert R.require_locked_ruleset(repo, "x") == "rs-001"


def test_not_required_without_autonomy(tmp_path):
    assert R.require_locked_ruleset(tmp_path, "x") is None


def test_segment_consumed(tmp_path):
    led = Ledger(tmp_path)
    assert not R.segment_consumed(led, "val")
    led.record_criteria_revision("looser", ["G6.x"], {}, {}, cert_id="c", ruleset_before="a", ruleset_after="b",
                                 reason="before use", scope="val")
    import time
    time.sleep(1.1)
    led.open_val_evaluation("a", "camp-1", "v1", 20)
    assert not R.segment_consumed(led, "val")                # 연기 전의 느슨화는 소진 아님
    led.record_criteria_revision("stricter", ["G6.x"], {}, {}, cert_id="c", ruleset_before="b", ruleset_after="c",
                                 reason="tighten", scope="val")
    assert not R.segment_consumed(led, "val")
    led.record_criteria_revision("looser", ["G3"], {}, {}, cert_id="c", ruleset_before="c", ruleset_after="d",
                                 reason="dev only", scope="dev")
    assert not R.segment_consumed(led, "val")                # 개발 구간 규칙 느슨화는 무관
    led.record_criteria_revision("looser", ["G6.x"], {}, {}, cert_id="c", ruleset_before="d", ruleset_after="e",
                                 reason="after use", scope="val")
    assert R.segment_consumed(led, "val")
