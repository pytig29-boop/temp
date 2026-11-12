"""자율 프로그램 시도 예산 (§4-E·§7.3): 주 150·계열 60·전체 400 (작은 쪽), AI 의 계열 해제 기록."""
import yaml

from btc_quant.registry.ledger import Ledger
from btc_quant.research.lessons import LessonBook


def _book(tmp_path, start="2000-01-01T00:00:00+00:00", fams=None):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "autonomy.yaml").write_text(yaml.safe_dump(
        {"program_start": start, "program": {"weekly_trials": 150, "family_weekly_trials": 60, "max_trials": 400}}))
    if fams is not None:
        (tmp_path / "research" / "program").mkdir(parents=True, exist_ok=True)
        (tmp_path / "research" / "program" / "families.yaml").write_text(yaml.safe_dump(fams))
    led = Ledger(tmp_path / "research" / "ledger")
    return LessonBook(tmp_path / "research" / "lessons", ledger=led)


def test_program_caps(tmp_path):
    b = _book(tmp_path).trial_budget("newfam")
    assert b["allowance"] == 60 and b["global_cap"] == 150 and b["remaining"] == 60
    assert b["program_cap"] == 400 and b["program_used"] == 0


def test_no_program_keeps_defaults(tmp_path):
    led = Ledger(tmp_path / "research" / "ledger")
    b = LessonBook(tmp_path / "research" / "lessons", ledger=led).trial_budget("x")
    assert b["global_cap"] == 2000 and "program_cap" not in b


def test_pre_program_trials_do_not_count(tmp_path):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    book = _book(tmp_path, start=now.isoformat(timespec="seconds"))
    old = (now - timedelta(days=1)).isoformat(timespec="seconds")
    for i in range(200):
        book.ledger.append("runs", {"campaign_id": "camp-old", "logic_hash": f"h{i}", "family": "f"})
    recs = book.ledger.path("runs").read_text().splitlines()
    import json
    book.ledger.path("runs").write_text("\n".join(json.dumps({**json.loads(x), "ts": old}) for x in recs) + "\n")
    b = book.trial_budget("newfam")
    assert b["remaining"] == 60 and b["global_used_7d"] == 0
