"""Research-mode guard hook (.claude/hooks/guard.py) — run as a subprocess with sample payloads."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".claude" / "hooks" / "guard.py"
LOCK = "data/raw/exchange=upbit/symbol=BTC-KRW/timeframe=1m/year=2026/month=06/part.parquet"
DEV = "data/raw/exchange=upbit/symbol=BTC-KRW/timeframe=1m/year=2024/month=06/part.parquet"
VAL = "data/raw/exchange=upbit/symbol=BTC-KRW/timeframe=1m/year=2026/month=03/part.parquet"


def run(tool, tool_input, research=True, tmp_path=None, raw_stdin=None):
    env = {k: v for k, v in os.environ.items() if k != "BTCQ_RESEARCH_MODE"}
    if research:
        env["BTCQ_RESEARCH_MODE"] = "1"
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    payload = {
        "session_id": "s1", "cwd": str(tmp_path), "hook_event_name": "PreToolUse",
        "permission_mode": "default", "tool_name": tool, "tool_input": tool_input,
        "tool_use_id": "toolu_1",
    }
    p = subprocess.run([sys.executable, str(GUARD)], input=raw_stdin or json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout) if p.stdout.strip() else None
    return out


def denied(out):
    return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"


def abs_(tmp_path, rel):
    return str(tmp_path / rel)


# ---- research mode OFF: no-op for everything -------------------------------
@pytest.mark.parametrize("tool,ti", [
    ("Write", {"file_path": "config/gates.yaml", "content": "x"}),
    ("Edit", {"file_path": "src/btc_quant/backtest/fast.py", "old_string": "a", "new_string": "b"}),
    ("Read", {"file_path": LOCK}),
    ("Bash", {"command": "uv run python -m btc_quant.registry.admin_cli activate s1"}),
    ("Bash", {"command": "git push origin main"}),
])
def test_dev_mode_is_noop(tmp_path, tool, ti):
    assert run(tool, ti, research=False, tmp_path=tmp_path) is None
    assert not (tmp_path / "logs" / "research_tool_calls.jsonl").exists()


# ---- writes -----------------------------------------------------------------
@pytest.mark.parametrize("rel", [
    "strategies/active/brk_donchian_v1.yaml",
    "strategies/review/x.yaml",
    "strategies/retired/x.yaml",
    "config/gates.yaml",
    "config/splits.yaml",
    "config/risk.yaml",
    "config/risk_limits.yaml",
    "research/ledger/experiments.jsonl",
    "research/lockbox_approvals/c1.json",
    "research/activation_approvals/c1.json",
    "src/btc_quant/backtest/fast.py",
    "src/btc_quant/evaluation/gates.py",
    "src/btc_quant/registry/ledger.py",
    "src/btc_quant/data/store.py",
    ".claude/hooks/guard.py",
    ".claude/settings.json",
    "logs/research_tool_calls.jsonl",
])
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_protected_writes_denied(tmp_path, rel, tool):
    assert denied(run(tool, {"file_path": abs_(tmp_path, rel)}, tmp_path=tmp_path))
    assert denied(run(tool, {"file_path": rel}, tmp_path=tmp_path))  # relative to cwd


def test_notebook_edit_denied(tmp_path):
    assert denied(run("NotebookEdit", {"notebook_path": abs_(tmp_path, "src/btc_quant/evaluation/x.ipynb")},
                      tmp_path=tmp_path))


def test_dotdot_path_normalized(tmp_path):
    p = str(tmp_path / "research" / "reports" / ".." / "ledger" / "x.jsonl")
    assert denied(run("Write", {"file_path": p}, tmp_path=tmp_path))


@pytest.mark.parametrize("rel", [
    "src/btc_quant/strategy/blocks/my_new_block.py",
    "tests/test_my_new_block.py",
    "strategies/draft/brk_v2.yaml",
    "research/reports/campaign_001.md",
    "research/campaigns/c001.yaml",
    "docs/BLOCKS.md",
    "config/exchanges.yaml",
])
def test_allowed_writes(tmp_path, rel):
    assert run("Write", {"file_path": abs_(tmp_path, rel), "content": "x"}, tmp_path=tmp_path) is None


# ---- bash -------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "echo 'gates: {}' > config/gates.yaml",
    "echo x >> research/ledger/experiments.jsonl",
    "cat foo | tee strategies/active/x.yaml",
    "sed -i 's/0.95/0.5/' config/gates.yaml",
    "mv /tmp/x.yaml strategies/review/x.yaml",
    "cp mine.py src/btc_quant/evaluation/gates.py",
    "rm -rf research/ledger",
    "python -c \"open('research/ledger/e.jsonl','a').write('x')\"",
    "git checkout -- config/gates.yaml",
    "git reset --hard HEAD",
    "git push origin main",
    "git -C . push",
    "uv run python -m btc_quant.registry.admin_cli approve-lockbox c1",
    "uv run btc-quant activate brk_donchian",
    "uv run btc-quant rollback",
    "python -c 'from btc_quant.registry import strategies; strategies.activate(\"x\")'",
    "python -c 'from btc_quant.registry.splits import write_lockbox_approval as w; w(None, \"c1\", \"me\")'",
    "uv run python -m btc_quant.registry.admin_cli status",
    "cat .env",
    f"cat {LOCK}",
    f"duckdb -c \"select count(*) from '{LOCK}'\"",
    "duckdb -c \"select * from 'data/raw/**/*.parquet'\"",
    "ls data/raw/exchange=upbit/symbol=BTC-KRW/timeframe=1m/year=2026/",
    "python -c \"import pandas as pd; pd.read_parquet('data/raw/exchange=upbit')\"",
])
def test_bash_denied(tmp_path, cmd):
    assert denied(run("Bash", {"command": cmd}, tmp_path=tmp_path)), cmd


@pytest.mark.parametrize("cmd", [
    "uv run pytest -q tests/test_blocks.py",
    "cat config/gates.yaml",
    "cat research/ledger/experiments.jsonl | tail -5",
    "git status && git diff",
    "git add src/btc_quant/strategy/blocks/new.py && git commit -m 'proposal'",
    "uv run python -m btc_quant.cli quality --exchange upbit",
    "source .venv/bin/activate && python -V",
    "ls 2>&1 | head",
    f"head -c 100 {DEV}",
    f"duckdb -c \"select count(*) from '{VAL}'\"",
    "echo hi > research/reports/note.md",
])
def test_bash_allowed(tmp_path, cmd):
    assert run("Bash", {"command": cmd}, tmp_path=tmp_path) is None, cmd


# ---- read tools -------------------------------------------------------------
def test_read_lockbox_denied(tmp_path):
    assert denied(run("Read", {"file_path": abs_(tmp_path, LOCK)}, tmp_path=tmp_path))
    nov = LOCK.replace("month=06", "month=11")
    assert denied(run("Read", {"file_path": nov}, tmp_path=tmp_path))


def test_read_dev_val_allowed(tmp_path):
    assert run("Read", {"file_path": abs_(tmp_path, DEV)}, tmp_path=tmp_path) is None
    assert run("Read", {"file_path": VAL}, tmp_path=tmp_path) is None
    assert run("Read", {"file_path": "config/gates.yaml"}, tmp_path=tmp_path) is None


@pytest.mark.parametrize("tool,ti", [
    ("Grep", {"pattern": "x", "path": "data/raw"}),
    ("Grep", {"pattern": "x", "path": "data"}),
    ("Grep", {"pattern": "x", "path": "data/raw/exchange=upbit", "glob": "**/year=2026/**"}),
    ("Glob", {"pattern": "data/raw/**/*.parquet"}),
    ("Glob", {"pattern": "**/year=2026/month=07/*.parquet"}),
])
def test_search_lockbox_denied(tmp_path, tool, ti):
    assert denied(run(tool, ti, tmp_path=tmp_path))


def test_search_elsewhere_allowed(tmp_path):
    assert run("Grep", {"pattern": "def ", "path": "src"}, tmp_path=tmp_path) is None
    assert run("Glob", {"pattern": "**/*.py"}, tmp_path=tmp_path) is None
    assert run("Glob", {"pattern": "data/raw/**/year=2024/**/*.parquet"}, tmp_path=tmp_path) is None


def test_mcp_tool_logged_and_lockbox_arg_denied(tmp_path):
    assert run("mcp__btcq__query_ledger", {"campaign": "c1"}, tmp_path=tmp_path) is None
    assert denied(run("mcp__btcq__x", {"path": LOCK}, tmp_path=tmp_path))


# ---- logging & robustness ---------------------------------------------------
def test_every_call_logged(tmp_path):
    run("Read", {"file_path": "README.md"}, tmp_path=tmp_path)
    run("Write", {"file_path": "config/gates.yaml", "content": "x" * 5000}, tmp_path=tmp_path)
    lines = (tmp_path / "logs" / "research_tool_calls.jsonl").read_text().splitlines()
    recs = [json.loads(x) for x in lines]
    assert [r["decision"] for r in recs] == ["allow", "deny"]
    assert recs[1]["reason"] and len(recs[1]["tool_input"]["content"]) < 500


def test_bad_input_fails_closed(tmp_path):
    assert denied(run("Bash", {}, tmp_path=tmp_path, raw_stdin="not json"))


# ---- lessons notebook (오답노트): records only via the Python API ----------
@pytest.mark.parametrize("rel", [
    "research/lessons/facts.jsonl",
    "research/lessons/interpretations.jsonl",
    "research/lessons/marks.jsonl",
    "research/lessons/postmortems.jsonl",
    "research/lessons/derivations.jsonl",
    "research/lessons/LESSONS.md",
    "research/lessons/.lock",
    "research/lessons/README.md.bak",
])
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_lessons_records_write_denied(tmp_path, rel, tool):
    assert denied(run(tool, {"file_path": abs_(tmp_path, rel)}, tmp_path=tmp_path))
    assert denied(run(tool, {"file_path": rel}, tmp_path=tmp_path))


@pytest.mark.parametrize("cmd", [
    "echo '{}' >> research/lessons/facts.jsonl",
    "sed -i 's/unverified/verified/' research/lessons/interpretations.jsonl",
    "rm -rf research/lessons",
    "cp /tmp/x research/lessons/LESSONS.md",
    "python -c \"open('research/lessons/facts.jsonl','w').write('')\"",
])
def test_lessons_bash_write_denied(tmp_path, cmd):
    assert denied(run("Bash", {"command": cmd}, tmp_path=tmp_path)), cmd


@pytest.mark.parametrize("tool,ti", [
    ("Write", {"file_path": "research/lessons/README.md", "content": "x"}),
    ("Read", {"file_path": "research/lessons/facts.jsonl"}),
    ("Write", {"file_path": "research/knowledge/mechanisms.md", "content": "x"}),
    ("Bash", {"command": "cat research/lessons/facts.jsonl | tail -3"}),
    ("Bash", {"command": "uv run python -c 'from btc_quant.research.lessons import LessonBook; "
                         "LessonBook().render_markdown()'"}),
])
def test_lessons_reads_and_api_allowed(tmp_path, tool, ti):
    assert run(tool, ti, tmp_path=tmp_path) is None


# ---- parameter governance (docs/PARAMS.md) ---------------------------------
@pytest.mark.parametrize("rel", [
    "config/params.yaml",
    "config/costs.yaml",
    "research/param_approvals/pp-20261109-x-abc123.json",
])
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_param_files_protected(tmp_path, rel, tool):
    assert denied(run(tool, {"file_path": abs_(tmp_path, rel)}, tmp_path=tmp_path))


@pytest.mark.parametrize("cmd", [
    "uv run python -m btc_quant.params approve pp-20261109-cost-abc123",
    "python -m btc_quant.params --root . approve pp-1 --choice -0.12",
    "uv run python -m btc_quant.params reject pp-1 --reason x",
    "python -c 'from btc_quant.params.proposals import approve; approve(\"pp-1\", \"me\", confirm=lambda *a: None)'",
    "python -c 'from btc_quant.params.costs import add_versions'",
    "python -c 'import btc_quant.params.costs as c; c.add_version(\"coinone_stress\", m)'",
    "python -c 'from btc_quant.params import registry; registry.record_change(\"k\", 1, by=\"x\")'",
    "sed -i 's/5.0/1.0/' config/costs.yaml",
    "echo x > config/params.yaml",
    "cp x.json research/param_approvals/pp-1.json",
])
def test_param_admin_bash_denied(tmp_path, cmd):
    assert denied(run("Bash", {"command": cmd}, tmp_path=tmp_path)), cmd


@pytest.mark.parametrize("cmd", [
    "uv run python -m btc_quant.params status",
    "uv run python -m btc_quant.params calibrate --dry-run",
    "uv run python -m btc_quant.params calibrate",
    "uv run python -m btc_quant.params propose-criteria gates.G5.min_dsr --values 0.9 --motivation 'synthetic FPR check'",
    "uv run python -m btc_quant.params history costs",
    "cat config/costs.yaml config/params.yaml",
    "ls research/param_proposals",
])
def test_param_research_commands_allowed(tmp_path, cmd):
    assert run("Bash", {"command": cmd}, tmp_path=tmp_path) is None, cmd


def test_param_proposals_writable(tmp_path):
    assert run("Write", {"file_path": abs_(tmp_path, "research/param_proposals/pp-x.yaml"), "content": "x"},
               tmp_path=tmp_path) is None
