---
name: research-campaign
description: Run one pre-registered research campaign end to end — hypothesis → fixed grid & budget → fast-engine run → gates G0–G5 (Dev) → ablation → G6 (Val) → report, all logged in the ledger. Use when asked to test a strategy idea, explore a block family, or "run a campaign".
argument-hint: "[hypothesis or block family]"
---

# Research campaign (PLAN §5)

A campaign answers **one** pre-registered question with a **fixed** search budget. The result —
pass or fail — goes into the ledger and the report unchanged. Read `docs/PLAN.md` §2, §3, §5 and
the ledger before starting.

## Non-negotiable rules
- **Never relax gates.** Thresholds live in `config/gates.yaml` and are fixed (human-only). If nothing
  passes, report "no candidate" (PLAN §10: "기준은 낮추지 않는다").
- **Never peek at the lockbox** (2026-05-01 .. 2026-11-08): not via raw files, not via Python,
  not by reasoning about recent prices. Lockbox evaluation = `request_lockbox_eval` + human approval.
- **Never re-run a failed campaign with a widened grid, extra filter, or different exits.** That
  is a *new* campaign (new ledger record, trials accumulate in the family) with its own hypothesis.
- **Never look at Val results to choose Dev parameters.** Val (G6) is run only for the few Dev survivors,
  once, with parameters frozen.
- Pre-registration happens **before** any backtest of the grid. Exploratory one-offs also count as
  trials — record them.
- Data only via `DataAccess`; ledger only via `Ledger` methods (never edit `research/ledger/*.jsonl`).

## Interface
MCP tools (PLAN §8) are the intended interface; they arrive in a later milestone (M5). Until then use
the Python equivalents (run with `uv run python -c ...` or a script under `research/scripts/`):

| Step | MCP tool (planned) | Python equivalent today |
|---|---|---|
| list blocks | `list_blocks` | `from btc_quant.strategy.blocks import REGISTRY; REGISTRY.all("entry")` (see `docs/BLOCKS.md`) |
| ① hypothesis | `register_hypothesis` | `Ledger().register_hypothesis(text, rationale, success_criteria)` → `hyp-…` |
| ② campaign | `create_campaign` | `Ledger().create_campaign(hyp_id, grid, budget, cost_models, period, family=…)` → `cmp-…` |
| ③ run | `run_campaign` | `DataAccess().read("dev", "upbit", "BTC-KRW", "1m")`; `btc_quant.backtest.fast.prepare_spec` / `simulate_many` / `run_fast`; `Ledger().record_run(cmp_id, spec, cost_name, period, data_snapshot, code_commit, metrics, family)` |
| ④ gates | `run_gates` | `btc_quant.evaluation.gates.check_g1 … check_g5`; metrics via `btc_quant.evaluation.metrics.summarize`; DSR/PBO via `btc_quant.evaluation.selection` (`dsr_from_returns`, `effective_trials_cluster`, `pbo_cscv`); null via `random_entry` + `btc_quant.evaluation.inference.null_percentile` |
| ablation | `run_ablation` | rerun with each filter removed (counts as +1 trial each, recorded with `record_run`) |
| ⑤ Val | `run_gates` (G6) | `DataAccess().read("val", …)`; `check_g6`; MC MDD via `btc_quant.evaluation.montecarlo.worst_case_mdd95` |
| ledger query | `query_ledger` / `compare` | `Ledger().records("campaigns")`, `Ledger().query("SELECT …")`, `Ledger().trials_count(family=…)` |
| ⑥ lockbox | `request_lockbox_eval` | write a request in the report; a **human** runs `python -m btc_quant.registry.admin_cli approve-lockbox <candidate_id>` |
| ⑦ report | `write_report` | write `research/reports/campaign_<cmp_id>.md` |

Snapshot ids: `btc_quant.registry.snapshot.snapshot_id(exchange, symbol, timeframe, start, end)`;
code commit: `git rev-parse HEAD` (commit nothing yourself unless asked; uncommitted code ⇒ note "dirty").

## Procedure
1. **Read the ledger.** Prior hypotheses/campaigns in the same family, their outcomes, cumulative
   trial count. State what is new about this hypothesis.
2. **① Register the hypothesis**: one sentence, rationale (e.g. Phase 0 cost-floor / lead-lag
   reports in `research/reports/`), success criterion = "≥1 spec passes G0–G6".
3. **② Fix grid & budget**: entry block + ≤ 2 filters + exits (SL/TP/max-hold), 3–5 values per
   parameter, ≤ 8 parameters total. Budget = grid size (≤ 2,000 by default). Cost models: Coinone and
   Upbit, base/conservative/stress. Period = Dev. Record with `create_campaign` **before running**.
4. **③ Run** the full grid on Dev with the fast engine. Record every run. Do not stop early because
   something "looks good"; do not add combinations.
5. **④ Gates G0–G5** on Dev in order; keep the funnel counts. G2 null = same filters/exits with
   `random_entry` (1,000 runs). G5 uses the ledger's *effective* trial count for the family.
6. **Ablation** for each survivor: drop each filter; if not significantly worse, keep the simpler spec
   (+1 trial).
7. **⑤ G6 on Val** for at most the top few survivors, parameters frozen. Re-check on the reference
   engine (`btc_quant.backtest.reference.run_reference`) — trade lists must match the fast engine.
8. **Skeptic**: hand any survivor to the `skeptic` subagent (`candidate-review` skill). Fatal finding ⇒
   reject.
9. **⑥ Lockbox request** (only after skeptic SURVIVES): state candidate id, logic hash, why; stop there.
   Never open it yourself.
10. **⑦ Report** using the template below; failures are reported in full.

## Report template (`research/reports/campaign_<cmp_id>.md`)
```
# Campaign <cmp_id> — <hypothesis>
Date (UTC) · hypothesis <hyp_id> · family <family> · data snapshot <id> · code <sha>
## Pre-registration
Grid (verbatim) · budget · cost models · period · success criterion
## Funnel
G0 → G1 → G2 → G3 → G4 → G5 → ablation → G6 (counts)
## Survivors
table: spec | weighted Sharpe cons. | recent-12m Sharpe | trades | DSR | PBO | null pct | cost grade
yearly table per survivor; rolling-6m chart/table
## Failures & what we learned
## Requests (lockbox / human) and next hypothesis
```
