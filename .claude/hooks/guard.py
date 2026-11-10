#!/usr/bin/env python3
"""PreToolUse guard for AI research sessions (stdlib only).

Active ONLY when the environment variable BTCQ_RESEARCH_MODE=1 is set for the
Claude Code process. In any other session (normal development) it exits 0
immediately with no output, so the normal permission flow applies untouched.

In research mode every evaluated tool call is appended to
logs/research_tool_calls.jsonl and the call is denied when it:
  1. writes to a protected path (approved strategies, gates/splits/risk config,
     the ledger, the lessons notebook records, approval records, the parameter
     registry config/params.yaml + config/costs.yaml and research/param_approvals,
     the human-only forward watchlist config/forward_watchlist.yaml,
     engine/evaluation/registry/data code,
     raw data, the Claude config itself, the audit log);
  2. runs the human-only admin CLI (approve-lockbox / activate / rollback, and
     `python -m btc_quant.params approve|reject`) or
     `git push` / other tree-mutating git commands;
  3. reads lockbox data (data/raw/**/year=2026/month=05..11/**) or an unscoped
     data/raw path that could cover the lockbox;
  4. calls an MCP tool whose name implies a human-only capability (approve /
     activate / rollback / promote / open lockbox / orders / keys) — the btcq
     research MCP server exposes none of these (docs/MCP.md);
  5. reads human-only Val/lockbox detail (Read/Grep/Glob/Bash): the full campaign
     reports research/reports/human/**, the full research/campaigns/<id>/pipeline.json
     (agents use pipeline_public.json / MCP get_campaign_report), the ledger tables
     that carry Val/lockbox numbers (runs / val_evaluations / lockbox / transitions
     .jsonl — use MCP query_ledger) and the Val-derived strategies/**/*.risk.yaml;
     broad recursive searches (Grep / grep -r / rg over the repo root, research/,
     strategies/) that would include those files are denied unless narrowed;
  6. runs research entry points that write the ledger directly, bypassing the MCP
     checks (weekly budget, screen gate, strict power, repeat check):
     `btc_quant.research.cli run|screen|gates`, Python that calls run_campaign /
     run_pipeline / run_screen / run_forward / signal_study.screen /
     Ledger().record_* (incl. record_exploration) / research.exploration.log_exploration /
     register_hypothesis / create_campaign / LessonBook
     add_interpretation / mark / derive_hypothesis / apply_derivation — including
     the contents of a `python <script.py>` / `python -m <local module>` target.
     Use the btcq MCP tools instead (docs/MCP.md §4.3).

Hook protocol (https://code.claude.com/docs/en/hooks): JSON on stdin with
tool_name / tool_input / cwd; to deny we print
  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                          "permissionDecision": "deny",
                          "permissionDecisionReason": "..."}}
and exit 0. Printing nothing and exiting 0 means "no decision".

This is a best-effort tripwire, not a sandbox. Bash parsing is heuristic
(regex over the command text); `cd` + relative paths, variables, generated
paths inside Python code etc. can evade it. The real lockbox enforcement lives
in the Python DataAccess layer; registry transitions are enforced in
btc_quant.registry. See docs/AI_RESEARCH.md.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

MODE_ENV = "BTCQ_RESEARCH_MODE"
LOG_REL = "logs/research_tool_calls.jsonl"

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob"}

# ---------------------------------------------------------------------------
# Protected (non-AI-writable) paths, as regexes over a POSIX path relative to
# the project root. Bash checks search for the same fragments anywhere in the
# command text.
# ---------------------------------------------------------------------------
PROTECTED_WRITE = [
    (r"strategies/(active|review|retired)(/|$)", "approved/retired strategies are changed only by registry code + human approval"),
    (r"strategies/tiers\.yaml$", "promotion tiers (real-capital stages, PLAN D23) are changed only by the human admin CLI"),
    (r"config/gates\.ya?ml", "gate thresholds are fixed (PLAN §5.2)"),
    (r"config/splits\.ya?ml", "data splits are human-only (PLAN §2.1)"),
    (r"config/risk[^/\s'\"]*\.ya?ml", "risk formulas are fixed (PLAN §7)"),
    (r"research/ledger(/|$)", "the ledger is append-only via the Python API (btc_quant.registry.ledger)"),
    # 오답노트: 사실·해석·표시·사후분석·파생 JSONL, 잠금 파일, 생성된 LESSONS.md, 디렉터리 자체.
    # README.md 만 편집 가능. 추가는 btc_quant.research.lessons Python API 로만.
    (r"research/lessons(?:/+(?!README\.md(?![\w.]))|/*$|(?=[\s'\";|&)]))",
     "lessons-notebook records are append-only via the Python API (btc_quant.research.lessons); "
     "facts are immutable and interpretations are added with add_interpretation()"),
    (r"research/lockbox_approvals(/|$)", "lockbox approvals are human-only (PLAN §5.3)"),
    (r"research/activation_approvals(/|$)", "activation approvals are human-only (PLAN §6)"),
    # 잠금 평가 요청은 MCP 도구 request_lockbox_eval(자격 검사: validated + skeptic SURVIVES)로만 기록
    (r"research/lockbox_requests(/|$)", "lockbox evaluation requests are filed only via the MCP tool request_lockbox_eval (eligibility checks)"),
    # 파라미터 거버넌스 (docs/PARAMS.md): 레지스트리·비용 모델은 btc_quant.params 규칙으로만, 승인은 사람만
    (r"config/params\.ya?ml", "the parameter registry changes only through btc_quant.params class rules + human approval (docs/PARAMS.md)"),
    (r"config/costs\.ya?ml", "cost models are versioned only by btc_quant.params calibrate (stricter) or human approval (looser)"),
    (r"research/param_approvals(/|$)", "parameter-change approvals are human-only (docs/PARAMS.md)"),
    # 전진 관찰 목록 (사용자 결정 2026-11-10): 사람만 전략을 올리고 내린다. 관찰 전용 — 승격·리스크·페이퍼와 무관
    (r"config/forward_watchlist", "the forward watchlist (config/forward_watchlist*) is human-only: a human places a "
     "strategy under observation; it is never promotion (research/forward.py)"),
    (r"src/btc_quant/(backtest|evaluation|registry|data)(/|$)", "engine/gates/registry/data code is not AI-editable (PLAN §3.3)"),
    (r"data/raw(/|$)", "raw market data is written only by the collectors"),
    (r"\.claude/(settings[^/\s'\"]*\.json|hooks|agents|skills)(/|$)", "the research guard configuration cannot be changed from a research session"),
    (r"logs/research_tool_calls\.jsonl", "the audit log is append-only by this hook"),
]

# Lockbox = 2026-05-01 .. 2026-11-08 -> month partitions 05..11 of 2026
# (month=11 also holds forward days; we block the whole partition).
LOCKBOX_PART_RE = re.compile(r"year=2026[/\\]+month=(0?[5-9]|1[01])(?!\d)")
YEAR2026_RE = re.compile(r"year=2026(?:[/\\]+month=([^/\\\s'\";|&()<>]*))?")
RAW_REF_RE = re.compile(r"data[/\\]+raw[^\s'\";|&()<>]*")
GLOB_CHARS = set("*?[{")
SNAP_DATE_RE = re.compile(r"_(\d{8})T\d")

ADMIN_RES = [
    re.compile(r"\badmin_cli\b"),
    re.compile(r"\bapprove[-_]lockbox\b"),
    re.compile(r"\bapprove[-_]activation\b"),
    re.compile(r"(?:^|[\s;&|])(activate|rollback)(?=\s|$|;|&|\|)"),
    re.compile(r"\b(activate|rollback|approve_lockbox|approve_activation)\s*\("),
    re.compile(r"\b(write_lockbox_approval|write_activation_approval)\b"),
    # 파라미터 제안 승인/거부 (사람 전용) 와 검증 우회 쓰기 함수
    re.compile(r"btc_quant[./]params\b[^;&|\n]*?\s(approve|reject)\b"),
    re.compile(r"btc_quant\.params(?:\.\w+)?\s+import\s+[^;\n]*\b(approve|reject|add_versions?|record_change)\b"),
    re.compile(r"\b(add_versions?|record_change|approve_proposal)\s*\("),
    # 단계별 승격 (PLAN D23): 실제 자본 투입 단계 변경은 사람 전용
    re.compile(r"\b(promote[-_]tier|demote[-_]tier)\b"),
    re.compile(r"\bTierBook\b[^\n]*\.(promote|demote)\s*\("),
    re.compile(r"\.promote\s*\("),
]

WRITE_VERB_RE = re.compile(
    r"(\bsed\s+(?:-\w+\s+)*-\w*i|\bperl\s+-\w*i|\b(tee|mv|cp|rm|rmdir|touch|truncate|ln|chmod|chown|dd|install|rsync|unlink|shred|mkdir)\b"
    r"|write_text|write_bytes|to_parquet|to_csv|to_json|\.unlink\(|\bshutil\.|os\.remove|os\.rename|os\.replace"
    r"|open\([^)]*['\"][wax]\+?b?['\"])"
)
REDIRECT_RE = re.compile(r"(?<![<>&\d])\d?>>?\|?\s*([^\s;&|<>]+)")
GIT_RE = re.compile(r"\bgit\s+(?:-[Cc]\s+\S+\s+|--?[\w-]+(?:=\S+)?\s+)*([a-z][a-z-]*)")
GIT_SAFE = {
    "status", "log", "diff", "show", "blame", "rev-parse", "ls-files", "ls-tree",
    "grep", "describe", "shortlog", "branch", "add", "commit", "fetch", "cat-file",
    "reflog", "rev-list", "help", "version",
}
SECRET_RE = re.compile(r"(^|[\s'\"/=])\.env(\.[\w.-]+)?(?=$|[\s'\";|&)])|runtime/secrets")


# Human-only READ paths (Val / lockbox detail). Regexes over the project-relative path.
HUMAN_ONLY_READ = [
    (r"research/reports/human(/|$)",
     "full campaign reports contain Val (G6) detail and are human-only; read the agent-safe "
     "research/reports/campaign_<id>.md or MCP get_campaign_report (Val = outcome codes only)"),
    (r"research/campaigns/[^/]+/pipeline\.json$",
     "pipeline.json holds G6/Val detail and is human-only; use research/campaigns/<id>/pipeline_public.json "
     "or MCP get_campaign_report"),
    (r"research/ledger/(runs|val_evaluations|lockbox|transitions)\.jsonl$",
     "this ledger table carries Val/lockbox numbers; use MCP query_ledger (dev metrics, outcome codes only)"),
    (r"strategies/.+\.risk\.ya?ml$", "risk.yaml limits are derived from Val (human-only)"),
]
# Sample human-only files, used to decide whether a broad search / glob could reach them.
HUMAN_ONLY_SAMPLES = (
    "research/reports/human/campaign_camp-000001.md",
    "research/campaigns/camp-000001/pipeline.json",
    "research/ledger/runs.jsonl", "research/ledger/val_evaluations.jsonl",
    "research/ledger/lockbox.jsonl", "research/ledger/transitions.jsonl",
    "strategies/validated/x_v1.risk.yaml",
)
# Fragments that name a human-only file anywhere in a Bash command.
HUMAN_ONLY_BASH_RE = re.compile(
    r"reports[/\\]+human(?![\w-])|(?<![\w-])pipeline\.json\b|"
    r"(?<![\w-])(runs|val_evaluations|lockbox|transitions)\.jsonl\b|\.risk\.ya?ml\b")
RECURSIVE_SEARCH_RE = re.compile(
    r"(?:^|[\s;&|(`])(rg|ag|ack|grep\s+(?:[^|;&]*\s)?-[a-zA-Z]*[rR][a-zA-Z]*|grep\s+(?:[^|;&]*\s)?--recursive"
    r"|git\s+grep)(?=\s|$)")

# Research entry points that write the ledger / lessons directly (bypass of the MCP checks).
RESEARCH_BYPASS_RES = [
    re.compile(r"(btc_quant[./]research[./]cli|btc-quant-research)\b[^;&|\n]*?\s(run|screen|gates)(?=\s|$|;|&|\|)"),
    re.compile(r"\b(run_campaign|run_pipeline|run_screen|run_forward|record_runs_batch)\b"),
    re.compile(r"signal_study\s+import\s+[^\n;]*(?<![\w.])screen\b(?!_)|signal_study\.screen\s*\("),
    re.compile(r"\.(register_hypothesis|create_campaign|record_runs?|record_lockbox_\w+|open_val_evaluation|"
               r"record_val_result|record_forward_result|record_transition|record_exploration)\s*\("),
    # 탐색적 시도 기록(research.exploration.log_exploration) — 연구 모드에서는 MCP log_exploration 으로만
    re.compile(r"\blog_exploration\s*\(|exploration\s+import\s+[^\n;]*\blog_exploration\b"),
    re.compile(r"\bLedger\s*\([^)]*\)\s*\.\s*append\s*\(|\bledger\s*\.\s*append\s*\("),
    re.compile(r"\b(add_interpretation|derive_hypothesis|apply_derivation)\s*\("),
]
LESSONS_MARK_RE = re.compile(r"\.mark\s*\(")
LESSONS_CTX_RE = re.compile(r"\b(LessonBook|lessons)\b")
PY_SCRIPT_RE = re.compile(r"\bpython[\d.]*\s+(?:-[^\s-]\S*\s+)*([^\s;&|<>'\"]+\.py)(?=\s|$|;|&|\|)")
PY_MODULE_RE = re.compile(r"\bpython[\d.]*\s+(?:-[^\sm]\S*\s+)*-m\s+([A-Za-z_][\w.]*)")
BYPASS_REASON = ("runs a research entry point that writes the ledger/lessons directly ({what}); in research mode "
                 "campaigns, screens, pipelines and ledger/lessons writes go through the btcq MCP tools "
                 "(screen_signals, create_and_run_campaign, register_hypothesis, lessons_*), which enforce the "
                 "weekly budget, screen gate, strict power and repeat checks (docs/MCP.md §4.3)")


# ---------------------------------------------------------------------------
def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def rel_path(p: str, root: Path, cwd: str | None) -> str:
    """Path relative to the project root, POSIX style; absolute if outside."""
    if not p:
        return ""
    path = Path(p).expanduser()
    if not path.is_absolute():
        path = Path(cwd or root) / path
    path = Path(os.path.normpath(path))
    for base in (Path(os.path.normpath(root)), Path(os.path.realpath(root))):
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def protected_reason(rel: str) -> str | None:
    for pat, why in PROTECTED_WRITE:
        if re.match(pat, rel):
            return why
    return None


def human_only_reason(rel: str) -> str | None:
    for pat, why in HUMAN_ONLY_READ:
        if re.match(pat, rel):
            return why
    return None


def _glob_rx(glob: str) -> re.Pattern:
    """Path-aware glob → regex: ** = any (incl. /), * and ? stay within one path segment."""
    out, i = [], 0
    while i < len(glob):
        ch = glob[i]
        if glob.startswith("**/", i):
            out.append(r"(?:.*/)?"); i += 3; continue
        if glob.startswith("**", i):
            out.append(r".*"); i += 2; continue
        if ch == "*":
            out.append(r"[^/]*")
        elif ch == "?":
            out.append(r"[^/]")
        elif ch == "[":
            j = glob.find("]", i)
            if j > i:
                out.append(glob[i:j + 1]); i = j + 1; continue
            out.append(r"\[")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out) + r"$")


def _expand_braces(glob: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", glob)
    if not m:
        return [glob]
    out = []
    for alt in m.group(1).split(","):
        out += _expand_braces(glob[:m.start()] + alt + glob[m.end():])
    return out


def _under(base: str, sample: str) -> str | None:
    """sample relative to base if base covers it (base '' / '.' = repo root), else None."""
    b = base.strip("/")
    if b in ("", "."):
        return sample
    if sample == b or sample.startswith(b + "/"):
        return sample[len(b):].lstrip("/") or sample.rsplit("/", 1)[-1]
    return None


def search_reaches_human_only(base: str, glob: str | None = None, ftype: str | None = None) -> str | None:
    """Would a recursive search rooted at `base` (optionally filtered by a rg-style glob / file type) read a
    human-only file? Returns the matching sample path or None."""
    if ftype and ftype.lower() not in ("md", "markdown", "json", "jsonl", "yaml", "yml", "txt", "all"):
        return None
    for sample in HUMAN_ONLY_SAMPLES:
        sub = _under(base, sample)
        if sub is None:
            if human_only_reason(base.strip("/")):      # searching inside a human-only dir / file
                return base
            continue
        if not glob:
            return sample
        for g in _expand_braces(glob):
            neg = g.startswith("!")
            if neg:
                continue
            target = sub if "/" in g.strip("/") else sub.rsplit("/", 1)[-1]
            if _glob_rx(g.lstrip("/")).match(target) or _glob_rx(g.lstrip("/")).match(sub):
                return sample
    return None


def bash_human_only_reason(cmd: str) -> str | None:
    m = HUMAN_ONLY_BASH_RE.search(cmd)
    if m:
        return (f"command references human-only Val/lockbox detail ('{m.group(0)}'): full campaign reports "
                "(research/reports/human/), pipeline.json, ledger runs/val_evaluations/lockbox/transitions, "
                "*.risk.yaml. Use research/reports/campaign_<id>.md, pipeline_public.json or the MCP tools "
                "(get_campaign_report, query_ledger)")
    # shell globs that expand into human-only files
    for tok in re.findall(r"[^\s'\";|&<>()]+", cmd):
        if GLOB_CHARS & set(tok) and not tok.startswith("-"):
            t = tok.lstrip("./")
            for g in _expand_braces(t):
                rx = _glob_rx(g)
                hit = next((x for x in HUMAN_ONLY_SAMPLES if rx.match(x)), None)
                if hit:
                    return f"glob '{tok}' can expand to human-only Val/lockbox file(s) such as {hit}"
    # recursive searches over broad scopes
    for seg in re.split(r"\|\||&&|[;|&\n]", cmd):
        if not RECURSIVE_SEARCH_RE.search(seg):
            continue
        try:
            import shlex
            toks = shlex.split(seg, posix=True)
        except ValueError:
            toks = seg.split()
        incl = [t.split("=", 1)[1] for t in toks if t.startswith(("--include=", "--glob=", "--iglob="))]
        for i, t in enumerate(toks):
            if t in ("-g", "--glob", "--iglob", "--include") and i + 1 < len(toks):
                incl.append(toks[i + 1])
        ftype = None
        for i, t in enumerate(toks):
            if t in ("-t", "--type") and i + 1 < len(toks):
                ftype = toks[i + 1]
            elif t.startswith("--type="):
                ftype = t.split("=", 1)[1]
        # positional args after the tool: first = pattern (unless -e/-f), rest = paths
        start = next((i for i, t in enumerate(toks) if t in ("rg", "ag", "ack", "grep")), None)
        if start is None:
            continue
        pos, skip = [], False
        has_e = False
        for t in toks[start + 1:]:
            if skip:
                skip = False
                continue
            if t in ("-e", "-f", "--regexp", "--file"):
                has_e, skip = True, True
                continue
            if t in ("-g", "--glob", "--iglob", "--include", "-t", "--type", "-A", "-B", "-C", "-m",
                     "--max-count", "--exclude", "--exclude-dir", "-T", "--type-not"):
                skip = True
                continue
            if t.startswith("-"):
                continue
            pos.append(t)
        paths = pos if has_e else pos[1:]
        for base in (paths or ["."]):
            b = os.path.normpath(base).replace("\\", "/")
            globs = incl or [None]
            for g in globs:
                hit = search_reaches_human_only("" if b == "." else b, g, ftype)
                if hit:
                    return (f"recursive search over '{base}' would read human-only Val/lockbox detail ({hit}); "
                            "narrow it (e.g. src/, docs/, research/knowledge/, research/lessons/) or add an "
                            "--include/-g/-t filter that excludes *.md/*.json/*.jsonl/*.yaml")
    return None


def _scan_python_targets(cmd: str, root: Path, cwd: str | None) -> str | None:
    """`python x.py` / `python -m local.module`: scan the target file for ledger-writing entry points."""
    files: list[Path] = []
    base = Path(cwd or root)
    for m in PY_SCRIPT_RE.finditer(cmd):
        p = Path(m.group(1)).expanduser()
        files.append(p if p.is_absolute() else base / p)
    for m in PY_MODULE_RE.finditer(cmd):
        mod = m.group(1)
        if mod.startswith("btc_quant.") and not mod.startswith("btc_quant.strategy.blocks"):
            continue          # project code (reviewed); only the CLI patterns above apply
        rel = Path(*mod.split("."))
        for b in (base, root, root / "src"):
            files += [b / rel.with_suffix(".py"), b / rel / "__main__.py"]
    for f in files:
        try:
            if f.is_file() and f.stat().st_size < 2_000_000:
                why = research_bypass_reason(f.read_text(encoding="utf-8", errors="replace"), scan_files=False)
                if why:
                    return f"script {f.name}: {why}"
        except OSError:
            continue
    return None


def research_bypass_reason(text: str, root: Path | None = None, cwd: str | None = None,
                           scan_files: bool = True) -> str | None:
    for rx in RESEARCH_BYPASS_RES:
        m = rx.search(text)
        if m:
            return BYPASS_REASON.format(what=m.group(0).strip()[:80])
    if LESSONS_MARK_RE.search(text) and LESSONS_CTX_RE.search(text):
        return BYPASS_REASON.format(what="LessonBook.mark(...)")
    if scan_files and root is not None:
        return _scan_python_targets(text, root, cwd)
    return None


def raw_ref_is_safe(ref: str) -> bool:
    """True if a data/raw reference is pinned to a non-lockbox partition/file."""
    snap = SNAP_DATE_RE.search(ref)
    if snap and not GLOB_CHARS & set(ref):
        return not ("20260501" <= snap.group(1) <= "20261108")
    m = re.search(r"year=(\d{4})(?![\d*?\[])", ref)
    if not m:
        return False  # no concrete year -> may cover lockbox
    if m.group(1) != "2026":
        return True
    mm = re.search(r"year=2026[/\\]+month=(\d{1,2})(?![\d*?\[])", ref)
    return bool(mm) and not (5 <= int(mm.group(1)) <= 11)


def lockbox_reason(text: str) -> str | None:
    if not text:
        return None
    if LOCKBOX_PART_RE.search(text):
        return "reads lockbox partition (2026-05..11). Lockbox evaluation only via request_lockbox_eval + human approval (PLAN §5.3)"
    for m in YEAR2026_RE.finditer(text):
        month = m.group(1)
        if not (month and month.isdigit() and int(month) in (1, 2, 3, 4, 12)):
            return (f"'{m.group(0)}' is not pinned to a non-lockbox month; 2026-05..11 is the lockbox "
                    "(PLAN §5.3)")
    for ref in RAW_REF_RE.findall(text):
        if not raw_ref_is_safe(ref):
            return (f"raw-data path '{ref}' is not pinned to a non-lockbox year/month partition and may include "
                    "the lockbox. Load data through the DataAccess layer (it enforces splits) instead of raw files")
    return None


# ---------------------------------------------------------------------------
def check_file_tool(tool: str, ti: dict, root: Path, cwd: str | None) -> str | None:
    if tool in WRITE_TOOLS:
        raw = ti.get("file_path") or ti.get("notebook_path") or ""
        rel = rel_path(raw, root, cwd)
        why = protected_reason(rel)
        if why:
            return f"write to protected path '{rel}' denied: {why}"
        return None
    # read tools
    if tool == "Read":
        target = rel_path(ti.get("file_path", ""), root, cwd)
        why = human_only_reason(target)
        if why:
            return f"Read of '{target}' denied: {why}"
    else:  # Grep / Glob: directory scope + pattern/glob
        base = rel_path(ti.get("path", "") or "", root, cwd) if ti.get("path") else ""
        if base == ".":
            base = ""
        extra = ti.get("glob") if tool == "Grep" else ti.get("pattern")
        if tool == "Grep":
            hit = search_reaches_human_only(base, ti.get("glob"), ti.get("type"))
            if hit:
                return (f"Grep over '{base or '.'}' would read human-only Val/lockbox detail ({hit}); narrow "
                        "`path` (e.g. src, docs, research/knowledge, research/lessons) or set `glob`/`type` to "
                        "exclude *.md/*.json/*.jsonl/*.yaml")
        else:
            tgt = "/".join(x for x in (base, extra or "") if x)
            for g in _expand_braces(tgt):
                if human_only_reason(g) or (GLOB_CHARS & set(g) and re.search(r"reports/+human|pipeline\.json"
                                                                              r"|\.risk\.ya?ml", g)):
                    return f"Glob '{tgt}' targets human-only Val/lockbox files ({HUMAN_ONLY_READ[0][1]})"
        if re.fullmatch(r"data/?", base):
            base = "data/raw"  # searching all of data/ covers data/raw
        target = "/".join(x for x in (base, extra or "") if x)
    why = lockbox_reason(target)
    return f"{tool} denied: {why}" if why else None


def check_bash(cmd: str, root: Path | None = None, cwd: str | None = None) -> str | None:
    if not cmd:
        return None
    for rx in ADMIN_RES:
        if rx.search(cmd):
            return "human-only admin command (lockbox approval / activation / rollback / parameter approval) is not available to AI research sessions (PLAN §6, docs/PARAMS.md)"
    why = research_bypass_reason(cmd, root, cwd)
    if why:
        return why
    why = bash_human_only_reason(cmd)
    if why:
        return why
    for m in GIT_RE.finditer(cmd):
        sub = m.group(1)
        if sub == "push":
            return "git push is not allowed in research mode; a human reviews and pushes"
        if sub not in GIT_SAFE:
            return f"'git {sub}' can overwrite or rewrite tracked files; only read-only git plus add/commit is allowed in research mode"
    if SECRET_RE.search(cmd):
        return "command references secrets (.env / runtime/secrets)"
    # writes: redirection targets
    for target in REDIRECT_RE.findall(cmd):
        if target.startswith("&") or target == "/dev/null":
            continue
        for pat, why in PROTECTED_WRITE:
            if re.search(pat.replace("(/|$)", r"(/|\b|$)"), target):
                return f"shell redirection into protected path '{target}' denied: {why}"
    # writes: write verb + protected path mentioned anywhere (coarse)
    if WRITE_VERB_RE.search(cmd):
        for pat, why in PROTECTED_WRITE:
            if re.search(pat.replace("(/|$)", r"(/|\b|$)"), cmd):
                return f"command appears to modify a protected path ({pat}): {why}"
    why = lockbox_reason(cmd)
    return f"Bash denied: {why}" if why else None


# MCP tools (any server) whose name implies a human-only capability. The btcq research server has none
# (docs/MCP.md); this is a tripwire against other servers / future additions.
MCP_FORBIDDEN_RE = re.compile(
    r"approv|activat|roll_?back|promot|open_lockbox|lockbox_open|unlock|"
    r"(place|submit|cancel|create)_?order|(api_)?keys?$|secret|credential", re.IGNORECASE)


def check_mcp_name(tool: str) -> str | None:
    parts = tool.split("__", 2)
    name = parts[2] if len(parts) == 3 else tool
    if MCP_FORBIDDEN_RE.search(name):
        return (f"MCP tool '{tool}' looks like a human-only capability (approval / activation / rollback / "
                "lockbox opening / orders / keys); not available to AI research sessions (PLAN §6, §8)")
    return None


def check_generic(ti: dict) -> str | None:
    """MCP / other tools: only scan arguments for lockbox paths."""
    try:
        why = lockbox_reason(json.dumps(ti, ensure_ascii=False))
    except (TypeError, ValueError):
        return None
    return f"tool arguments reference lockbox data: {why}" if why else None


def evaluate(payload: dict, root: Path) -> str | None:
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}
    cwd = payload.get("cwd")
    if tool in WRITE_TOOLS or tool in READ_TOOLS:
        return check_file_tool(tool, ti, root, cwd)
    if tool == "Bash":
        return check_bash(ti.get("command", ""), root, cwd)
    if tool.startswith("mcp__"):
        why = check_mcp_name(tool)
        if why:
            return why
    return check_generic(ti)


# ---------------------------------------------------------------------------
def summarize_input(ti: dict) -> dict:
    out = {}
    for k, v in (ti or {}).items():
        if isinstance(v, str) and len(v) > 400:
            v = v[:400] + f"...<{len(v)} chars>"
        elif not isinstance(v, (str, int, float, bool, type(None))):
            s = json.dumps(v, ensure_ascii=False, default=str)
            v = s[:400] + ("..." if len(s) > 400 else "")
        out[k] = v
    return out


def log_call(root: Path, payload: dict, decision: str, reason: str | None) -> None:
    rec = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "session_id": payload.get("session_id"),
        "agent_type": payload.get("agent_type"),
        "tool_name": payload.get("tool_name"),
        "tool_input": summarize_input(payload.get("tool_input") or {}),
        "decision": decision,
        "reason": reason,
    }
    try:
        path = root / LOG_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:  # logging must never decide the outcome
        print(f"guard: could not write log: {e}", file=sys.stderr)


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"[research guard] {reason}",
    }}, ensure_ascii=False))


def main() -> int:
    if os.environ.get(MODE_ENV) != "1":
        return 0  # development session: no-op
    root = project_root()
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        deny(f"could not parse hook input ({e}); failing closed")
        return 0
    try:
        reason = evaluate(payload, root)
    except Exception as e:  # fail closed in research mode
        reason = f"guard internal error ({type(e).__name__}: {e}); failing closed"
    log_call(root, payload, "deny" if reason else "allow", reason)
    if reason:
        deny(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
