"""자율 연구 프로그램 상태 (Phase 0.4): 단계는 앞으로만, 대기열, 재개 점검."""
import json

import pytest

from btc_quant.research import program as P


def test_phase_forward_only_and_queue(tmp_path):
    assert P.load_state(tmp_path)["phase"] == "build"
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "autonomy.yaml").write_text("phase: build\n")
    P.set_phase("research", tmp_path, "test")
    assert P.load_state(tmp_path)["phase"] == "research"
    assert "phase: research" in (tmp_path / "config" / "autonomy.yaml").read_text()
    for bad in ("build", "research"):
        with pytest.raises(P.ProgramError):
            P.set_phase(bad, tmp_path)
    assert P.main(["--root", str(tmp_path), "queue", "add", "do X"]) == 0
    assert P.load_state(tmp_path)["queue"][0]["task"] == "do X"
    assert P.main(["--root", str(tmp_path), "queue", "done", "0"]) == 0
    st = P.load_state(tmp_path)
    assert st["queue"] == [] and st["done"][0]["task"] == "do X"
    ev = [json.loads(x) for x in (tmp_path / "research" / "program" / "events.jsonl").read_text().splitlines()]
    assert ev[0]["kind"] == "phase" and ev[0]["to"] == "research"


def test_resume_reports_missing_data(tmp_path):
    out = P.resume(tmp_path)
    assert out["ops"]["data_present"] is False
    assert any("restore_data" in a for a in out["actions"])
