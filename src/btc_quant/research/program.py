"""자율 연구 프로그램 상태·재개 (docs/prompts/autonomous_research_plan.md §9.1, Phase 0.4).

    python -m btc_quant.research.program status          # 단계·주기·예산·다음 할 일
    python -m btc_quant.research.program resume          # 멱등: 운영 점검 + 대기열의 다음 작업 출력
    python -m btc_quant.research.program phase <다음>     # build → research → final → report → closed (되돌리기 불가)
    python -m btc_quant.research.program queue add "<작업>" | done <번호>

상태는 git 에 있는 `research/program/state.json` 하나(+ 사건 기록 `events.jsonl`). 컨테이너가 사라져도 푸시만 돼 있으면
새 세션에서 `resume` 으로 이어간다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from btc_quant.registry.ledger import repo_root as _repo_root
from btc_quant.registry.ledger import utcnow


def repo_root(root=None) -> Path:
    return Path(root) if root is not None else _repo_root()

PHASES = ("build", "research", "final", "report", "closed")
DEFAULT_STATE = {
    "version": 1,
    "plan": "docs/prompts/autonomous_research_plan.md",
    "phase": "build",
    "plan_phase": "0",
    "cycle": None,
    "queue": [],
    "done": [],
    "background": [],
    "notes": [],
}


class ProgramError(RuntimeError):
    pass


def program_dir(root=None) -> Path:
    return Path(repo_root(root)) / "research" / "program"


def load_state(root=None) -> dict:
    p = program_dir(root) / "state.json"
    if not p.exists():
        return json.loads(json.dumps(DEFAULT_STATE))
    return {**DEFAULT_STATE, **json.loads(p.read_text(encoding="utf-8"))}


def save_state(st: dict, root=None) -> Path:
    d = program_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    st = {**st, "updated": utcnow()}
    p = d / "state.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def log_event(kind: str, root=None, **data) -> dict:
    d = program_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    rec = {"ts": utcnow(), "kind": kind, **data}
    with open(d / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def set_phase(new: str, root=None, reason: str = "") -> dict:
    """단계 전환 — 앞으로만 (되돌리기 불가). config/autonomy.yaml 이 있으면 phase 도 같이 쓴다(guard 가 읽음)."""
    st = load_state(root)
    if new not in PHASES:
        raise ProgramError(f"phase must be one of {PHASES}")
    if PHASES.index(new) <= PHASES.index(st["phase"]):
        raise ProgramError(f"phase can only move forward ({st['phase']} → {new} refused)")
    old = st["phase"]
    st["phase"] = new
    save_state(st, root)
    ap = Path(repo_root(root)) / "config" / "autonomy.yaml"
    if ap.exists():
        import yaml
        doc = yaml.safe_load(ap.read_text(encoding="utf-8")) or {}
        doc["phase"] = new
        ap.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    log_event("phase", root, frm=old, to=new, reason=reason)
    return st


def _alive(pattern: str) -> bool:
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def ops_check(root=None) -> dict:
    r = Path(repo_root(root))
    hb = r / "runtime" / "collector.heartbeat"
    age = time.time() - hb.stat().st_mtime if hb.exists() else None
    raw = r / "data" / "raw" / "exchange=coinone"
    return {"data_present": raw.exists() and any(raw.iterdir()),
            "collector_running": _alive("btc_quant.live collect"),
            "collector_heartbeat_age_s": None if age is None else round(age),
            "collector_watchdog": _alive("collector_watchdog.sh"),
            "paper_running": _alive("btc_quant.paper run")}


def status(root=None) -> dict:
    st = load_state(root)
    return {"phase": st["phase"], "plan_phase": st.get("plan_phase"), "cycle": st.get("cycle"),
            "next": st["queue"][:5], "n_done": len(st["done"]), "background": st.get("background", []),
            "ops": ops_check(root), "updated": st.get("updated")}


def resume(root=None) -> dict:
    """멱등 재개 점검: 데이터·수집기 상태와 다음 작업. 고치는 일(복원·재기동)은 scripts/session_start.sh 가 한다."""
    out = status(root)
    out["actions"] = []
    if not out["ops"]["data_present"]:
        out["actions"].append("run scripts/restore_data.sh (data/raw empty)")
    if not out["ops"]["collector_watchdog"]:
        out["actions"].append("start scripts/collector_watchdog.sh")
    log_event("resume", root, ops=out["ops"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="btc_quant.research.program")
    ap.add_argument("--root", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("resume")
    p = sub.add_parser("phase")
    p.add_argument("to", choices=PHASES)
    p.add_argument("--reason", default="")
    q = sub.add_parser("queue")
    q.add_argument("op", choices=["add", "done", "list"])
    q.add_argument("arg", nargs="?")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(status(a.root), ensure_ascii=False, indent=2))
    elif a.cmd == "resume":
        print(json.dumps(resume(a.root), ensure_ascii=False, indent=2))
    elif a.cmd == "phase":
        try:
            set_phase(a.to, a.root, a.reason)
        except ProgramError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"phase → {a.to}")
    else:
        st = load_state(a.root)
        if a.op == "add":
            st["queue"].append({"task": a.arg, "added": utcnow()})
        elif a.op == "done":
            i = int(a.arg)
            st["done"].append({**st["queue"].pop(i), "done": utcnow()})
        if a.op != "list":
            save_state(st, a.root)
        print(json.dumps(st["queue"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
