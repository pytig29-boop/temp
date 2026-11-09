---
name: research-campaign
description: Run one pre-registered v4 research campaign end to end — knowledge base + lessons + similarity check → mechanism & counterparty → weekly budget → signal screening (event study) → power check → strategy campaign with principled ATR exits (≤ 6 variants) → gates G0–G5 (Dev) → ablation → robustness suite → G6 (Val, outcome code only) → lessons facts/interpretations → report, all logged in the ledger. Use when asked to test a strategy idea, explore a block family, or "run a campaign".
argument-hint: "[hypothesis or block family]"
---

# Research campaign (PLAN §5, v4 = §10b)

A campaign answers **one** pre-registered question with a **fixed** search budget. The result — pass or
fail — goes into the ledger, the lessons notebook and the report unchanged.

## Non-negotiable rules
- **Never relax gates.** Thresholds in `config/gates.yaml` are human-only. Nothing passes ⇒ "no candidate".
- **Never peek at the lockbox** (2026-05-01 .. 2026-11-08) — not via files, Python, or reasoning about recent prices.
- **Val is outcome codes only.** Never use Val numbers to choose Dev parameters, and never write Val metrics
  into reports' "what we learned", interpretations or hypotheses. Val usage is budgeted (ledger
  `val_evaluations`, cumulative Holm) by the pipeline.
- **A finished campaign is final.** Wider grid / extra filter / other exits = new campaign, same family.
- **Pre-register before running** (screens and exploratory one-offs count as trials).
- Data only via `DataAccess`; ledger only via `Ledger`; lessons only via `LessonBook` (never edit the JSONL).

## Stage 0 — Groundwork (no data touched yet)
1. Read `research/knowledge/*.md` (note `잠금:`/`Val:` exposure tags), `research/lessons/LESSONS.md`, and the
   ledger for the family (`Ledger().records("campaigns")`, `Ledger().trials_count(family=…)`).
2. Write the hypothesis: one sentence + **mechanism** + **counterparty** (who loses, why it persists, what
   kills it) + scope (venue, timeframe, cost model) + exposure of each cited evidence item. No mechanism ⇒
   do not register.
3. Repeat check: `LessonBook().find_similar(template=tpl, text=hypothesis_text)`. `exact` ⇒ stop or justify.
   If the idea comes from a lesson/failed campaign: `d = LessonBook().derive_hypothesis(parent, …)` then
   `tpl = LessonBook().apply_derivation(tpl, d["id"])` — family is inherited. `LessonBook().check_template(tpl)`
   must not raise.
4. **Weekly trial budget**: `LessonBook().trial_budget(family)`; the planned trials (screen configs + strategy
   specs + expected ablation reruns) must fit in `remaining`.
   Rule: global ≤ 2,000 new trials / 7 days; per family 600 (default), 300 (new family — screen first),
   900 (recent Dev survivor), 0 (frozen: last two campaigns failed as NO_SIGNAL/COST_EATEN — needs a met
   re-examine condition or human release). Budget is advisory in code but binding for you.

## Stage 1 — Signal screening (before any exits)
- Template per docs/SIGNAL_STUDY.md §7 (entry grid × filter sets, primary horizons ≤ 3, BH q=0.10, budget).
- `from btc_quant.research.signal_study import screen; res = screen("research/campaign_templates/<x>_screen.yaml")`
  (pre-registers itself, Dev only, one trial per signal config, family-wide multiple-testing correction).
- `LessonBook().record_facts_from_screen(res)`.
- Only configs with `passed=True` continue. None ⇒ report NO CANDIDATE (taxonomy NO_SIGNAL / COST_EATEN /
  LOW_SAMPLE is recorded automatically) and stop.
- Take from the passing config: information horizon → `max_hold_min`; MFE/MAE in ATR units → centre of
  the ATR-multiple SL/TP patterns.

## Stage 2 — Power check
`uv run python -m btc_quant.research.power --days <dev days> --grid <planned specs> --family <family> --target <S>`
(or `power.budget_report(...)`). If the Sharpe the screened signal can plausibly deliver (net mean ×
frequency) is below the required Sharpe at the estimated effective trials, shrink the grid or stop.

## Stage 3 — Strategy campaign (principled exits)
- Entry & filters **fixed** to the screened values (at most the screened neighbours).
- Exits: ATR multiples (`stop_loss_atr`, `take_profit_atr`, `atr_bars` default) + `max_hold_min` from the
  information horizon. **≤ 6 exit variants in total** (e.g. SL {1.0, 1.5} × TP {1.5, 2.5} + 2 hold variants).
  Fixed-% exit grids ×80 are not allowed (PM `dsr-power-exit-grid`).
- Template format: `research/campaigns/README.md`. Budget = grid size.
- Run: `uv run python -m btc_quant.research.cli run <template.yaml> --max-candidates 5`
  (registers hypothesis+campaign before running, records every run, runs the pipeline and the report).

## Stage 4 — Gates, ablation, robustness, Val
- Pipeline (`btc_quant.research.pipeline.run_pipeline`): G0 → G1 → G2 (trade-count-matched random-entry null)
  → G3 → G4 → G5 (DSR with family effective trials, PBO) → ablation → G6 on Val (budgeted, outcome only) → grade.
- Robustness on Dev survivors: `btc_quant.research.robustness.robustness_for_spec(spec)` (latency, outliers,
  leave-year-out, cost ×1.5, regime share, alpha vs BTC, cross-venue). Attach the result to the candidate
  record you pass to the lessons notebook (`candidate["robustness"]`) — failures map to OUTLIER_DEPENDENT,
  LATENCY_SENSITIVE, REGIME_DEPENDENT, COST_EATEN, NO_SIGNAL (alpha).
- Survivors → `skeptic` subagent (`candidate-review`; it writes predicted failure modes first). Fatal ⇒ reject.
- Lockbox request only after skeptic SURVIVES; a human approves. Never open it yourself.

## Stage 5 — Lessons & report
1. `LessonBook().record_facts_from_pipeline("research/campaigns/<cmp>/pipeline.json")` (idempotent).
2. `lessons-review` skill §A: 0–3 interpretations (unverified, evidence, falsifying test); mark earlier
   interpretations that this campaign verifies/refutes (`mark(..., by_campaign=<this cmp>)`); post-mortem any
   process mistake.
3. `LessonBook().render_markdown()`.
4. Report `research/reports/campaign_<cmp>.md` (the CLI writes the base report; add the sections below).

## Interface map
| Step | MCP tool (planned) | Python today |
|---|---|---|
| knowledge / lessons | – | `research/knowledge/*.md`, `btc_quant.research.lessons.LessonBook` |
| list blocks | `list_blocks` | `from btc_quant.strategy.blocks import REGISTRY; REGISTRY.all("entry")`, docs/BLOCKS.md |
| ① hypothesis + ② campaign | `register_hypothesis`, `create_campaign` | done by `research.cli run` / `signal_study.screen` from the template; manual: `Ledger().register_hypothesis`, `Ledger().create_campaign(..., family=…)` |
| screen | – | `btc_quant.research.signal_study.screen` |
| power | – | `btc_quant.research.power.budget_report`, `required_sharpe` |
| ③ run + ④ gates | `run_campaign`, `run_gates` | `btc_quant.research.campaign.run_campaign`, `btc_quant.research.pipeline.run_pipeline` |
| robustness | – | `btc_quant.research.robustness.robustness_for_spec` |
| ledger query | `query_ledger` / `compare` | `Ledger().records(...)`, `Ledger().query("SELECT …")`, `Ledger().trials_count(family=…)` |
| lockbox | `request_lockbox_eval` | write the request in the report; a **human** runs `admin_cli approve-lockbox` |
| report | `write_report` | `btc_quant.research.report.write_report` |

## Report template additions (`research/reports/campaign_<cmp>.md`)
```
## Mechanism & counterparty
## Evidence used (knowledge items + lockbox/Val exposure) · similar prior work (find_similar) · derived from
## Budget: family trials before/after · weekly remaining · power: required Sharpe at N_eff
## Signal screen: <screen cmp> passed configs, info horizon, ATR-unit MFE/MAE
## Funnel G0 → … → G5 → ablation → G6 (pass/fail only)
## Robustness (Dev) table
## Lessons: fact id, taxonomy, interpretations (unverified), marks, post-mortems
## Requests (lockbox / human) and next hypothesis (derive_hypothesis, same family)
```
