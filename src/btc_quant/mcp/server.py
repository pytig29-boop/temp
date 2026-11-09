"""연구 MCP 서버 등록 (공식 MCP Python SDK 2.x, `mcp.server.mcpserver.MCPServer`).

    uv run python -m btc_quant.mcp            # stdio (Claude Code 가 .mcp.json 으로 띄운다)

도구 구현은 `tools.ResearchTools` (모든 도구는 JSON 요약 dict 를 반환, 오류도 {"ok": false, "error": …}).
사람 전용 기능(승인·활성화·롤백·잠금 개봉·관문/비용/분할/원장 편집·주문·키)은 등록하지 않는다 — docs/MCP.md.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .tools import TOOL_NAMES, ResearchTools

SERVER_NAME = "btcq"

INSTRUCTIONS = """BTC quant research tools (PLAN §8/§10b). Rules enforced by these tools:
- Pre-registration happens inside screen_signals / create_and_run_campaign (ledger first, then data).
- Data comes only through the split-enforcing DataAccess layer; the lockbox is never read or summarised.
- Val (G6) and lockbox information is returned as outcome codes only (pass/fail, counts). Never quote or infer Val numbers.
- Weekly trial budgets, screen gating and strict power checks are enforced before any ledger write.
- There are no tools to approve, activate, roll back, open the lockbox, edit gates/costs/splits/ledger files,
  place orders or touch keys: those are human-only. request_lockbox_eval only files a request for a human.
Every result is JSON: {"ok": true, ...} or {"ok": false, "error": {"code", "message", ...}}."""

DESCRIPTIONS = {
    "list_blocks": "List registered strategy blocks (entry/filter/exit_signal) with status official/experimental, "
                   "parameter grids and defaults, required aux data and live_ready.",
    "get_data_summary": "Data coverage per exchange/symbol for the dev, val and forward segments (rows, expected "
                        "bars, gaps, filled share), read through DataAccess. The lockbox is sealed: only its "
                        "opening count is reported.",
    "register_hypothesis": "Pre-register a hypothesis in the ledger (text + mechanism + counterparty + success "
                           "criteria, optional scope / evidence exposure). Returns similar prior work. Val/lockbox "
                           "metrics in the text are refused.",
    "find_similar": "Repeat check before proposing: similar prior campaigns/hypotheses (exact/same_blocks/"
                    "same_entry/fuzzy) with outcomes and family trial counts, plus the family's weekly trial budget "
                    "and template checks (derived-family inheritance).",
    "screen_signals": "Run a pre-registered signal screen (event study vs matched null, no exits) on Dev from a "
                      "screen template (mapping or YAML path in the repo). Enforces the weekly trial budget; records "
                      "ledger runs and lessons facts; returns per-config pass/fail.",
    "power_check": "Power calculator: minimum annual Sharpe that can pass G5 (DSR) for a planned grid size or "
                   "campaign template, given the family's accumulated trials; compares with target_sharpe.",
    "create_and_run_campaign": "Pre-register and run a screen-gated strategy campaign from a template, then the "
                               "gate pipeline G0–G6 (Val as outcome code only), robustness suite and base report. "
                               "Refuses before any ledger write if the grid exceeds the template budget or the "
                               "weekly family budget, a signal config did not pass a recorded screen, the strict "
                               "power check fails (power.target_sharpe required), or the grid exactly repeats a "
                               "prior campaign without repeat_justification.",
    "list_campaigns": "List campaigns and screens in the ledger (newest first) with family, budget and verdict.",
    "get_campaign_report": "Structured summary of a campaign or screen: grid/power/screen linkage, Dev funnel and "
                           "per-candidate Dev gates, robustness and ablation, G6 as pass/fail only, lessons facts.",
    "query_ledger": "Read-only ledger query over whitelisted tables/columns (hypotheses, campaigns, runs, "
                    "transitions, val_evaluations, lockbox, forward_results, or 'trials' counts). Equality filters "
                    "on allowed columns. Val/lockbox rows carry outcome codes only; non-dev run metrics are withheld.",
    "lessons_view": "Lessons notebook view: facts (auto, immutable) and interpretations with current status for a "
                    "family or campaign, integrity check, and the family's weekly budget.",
    "lessons_add_interpretation": "Add an AI interpretation to a lessons fact (always starts unverified; needs "
                                  "confidence and cited evidence; Val/lockbox metrics refused).",
    "lessons_mark": "Mark an interpretation verified/refuted by a follow-up campaign whose facts are recorded "
                    "(must differ from and postdate the interpreted campaign).",
    "lessons_derive_hypothesis": "Derive a follow-up hypothesis from a fact/interpretation/campaign; the parent "
                                 "family is inherited (no override). Optionally applies it to a template and checks it.",
    "propose_block": "Run the automatic acceptance check (metadata, signature, purity, causality at 5m/1m, "
                     "non-degenerate, docs row) on an experimental block module the agent already wrote directly "
                     "under src/btc_quant/strategy/blocks/experimental/. Writes no code; returns the report.",
    "request_lockbox_eval": "File a lockbox (G7) evaluation REQUEST for a human (validated/challenger strategy + "
                            "skeptic review with VERDICT: SURVIVES). Never opens the lockbox and writes no approval.",
    "registry_status": "Strategy registry states, lockbox openings used/cap, pending lockbox requests and the Val "
                       "usage budget (counts only).",
    "params_status": "Parameter registry status: params_version, validation, current cost model versions, pending "
                     "proposals (read-only).",
    "propose_criterion": "Propose a class-② gate criterion change from synthetic calibration only (anti-goalpost: "
                         "references to real campaigns/candidates are refused). Writes a proposal that needs human "
                         "approval and applies from the next campaign. Can take a long time.",
    "forward_status": "Forward incubation status of validated/challenger strategies from the ledger (latest daily "
                      "record, ok days, within-band share). Read-only.",
}

READ_ONLY = {"list_blocks", "get_data_summary", "find_similar", "power_check", "list_campaigns",
             "get_campaign_report", "query_ledger", "lessons_view", "registry_status", "params_status",
             "forward_status", "propose_block"}


def build_server(tools: ResearchTools | None = None) -> MCPServer:
    tools = tools or ResearchTools()
    srv = MCPServer(SERVER_NAME, title="BTC quant research", instructions=INSTRUCTIONS, version="1.0.0",
                    log_level="WARNING")
    for name in TOOL_NAMES:
        ro = name in READ_ONLY
        srv.add_tool(getattr(tools, name), name=name, description=DESCRIPTIONS[name],
                     annotations=ToolAnnotations(title=name.replace("_", " "), read_only_hint=ro,
                                                 destructive_hint=False, idempotent_hint=ro, open_world_hint=False),
                     structured_output=True)
    return srv
