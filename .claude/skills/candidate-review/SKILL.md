---
name: candidate-review
description: Adversarial review checklist and report template for a strategy candidate that survived a campaign (lookahead, engine agreement, data snooping, cost sensitivity, regime dependence, ablation, sample size, live feasibility). Use before any lockbox request or registry promotion; the skeptic subagent runs it.
argument-hint: "<candidate_id> <campaign_id>"
---

# Candidate review (반대 논리 심사)

Goal: find the reason the candidate fails live. Default verdict is REJECT unless every item below has
evidence. Do **not** modify the candidate: any change is a new campaign. Use Dev/Val only via
`btc_quant.registry.splits.DataAccess`; the lockbox is off-limits.

Intended MCP tools (later milestone): `query_ledger`, `compare`, `run_gates`, `run_ablation`.
Python equivalents today: `btc_quant.registry.ledger.Ledger`, `btc_quant.backtest.{fast,reference}`,
`btc_quant.evaluation.{metrics,gates,selection,inference,montecarlo}`.

## Step 0 — Predicted failure modes (before looking at any result numbers)
From the hypothesis (mechanism, counterparty), the spec, the family's lessons facts
(`btc_quant.research.lessons.LessonBook().facts(family=…)`, taxonomy codes) and `research/knowledge/pitfalls.md`,
write 3–6 numbered predictions, each = taxonomy code + the measurement that would confirm it. Timestamp it and
never edit it afterwards. The verdict table reports, per row, whether a prediction was confirmed.

## Automated robustness suite (evidence for items 5–7)
`from btc_quant.research.robustness import robustness_for_spec; r = robustness_for_spec(spec)` (Dev, DataAccess)
→ `r["checks"]`: latency (retention ≥ 0.5 at 1 min / 1 bar delay), outliers (Sharpe > 0 after removing top 1 % and
top 5 trades), leave_year_out (min Sharpe > 0), cost (Sharpe > 0 at ×1.5), regime (no regime > 70 % of PnL),
alpha (Newey–West t ≥ 2 vs BTC), cross_venue (Upbit Sharpe > 0 and ≥ 0.5× target). `passed=None` = skipped
(not shown ⇒ not a pass). Quote the numbers in the table.

## Checklist
1. **Pre-registration integrity** — hypothesis and campaign records exist and their timestamps precede
   the first run of the campaign; grid in the ledger equals grid actually run; budget not exceeded;
   no earlier campaign in the family with an overlapping grid that was silently "re-tried"
   (`LessonBook().find_similar(template=…)` → `exact`), no family renaming to reset trials
   (derived hypotheses must carry `[derived-from drv-…]` and the parent family; facts show `family_check`).
2. **Lookahead / causality** — `uv run pytest -q tests/test_blocks.py` passes for every block used;
   future-perturbation check: corrupt data after t, signals ≤ t unchanged; entry on next 1m open after
   5m close; HTF filters (e.g. `trend_regime_1h`) use only completed bars.
3. **Engine agreement** — `run_fast` vs `run_reference` on Dev: identical trade list (entry/exit ts,
   reason). Any mismatch is fatal until explained.
4. **Selection bias** — effective trials (`Ledger().trials_count(family=…)`, clustered with
   `selection.effective_trials_cluster`), DSR ≥ 0.95, campaign PBO < 0.3, null percentile vs
   `random_entry` with same filters/exits.
5. **Costs** — Sharpe and MDD under Coinone/Upbit × base/conservative/stress; break-even extra cost (bp);
   cost grade A (passes with Upbit costs) or B (0 % only).
6. **Regimes** — yearly table; 6-month rolling Sharpe (≥ 60 % positive); last-12-month Sharpe > 0;
   by volatility tercile, by KST session, bull vs bear years; PnL concentration (top 5 days, top 10 trades).
7. **Ablation & plateau** — drop each filter; neighbours in the grid (G3 median ratio); random entry
   with same exits.
8. **Sample** — trades total / last 12 months (G1), trades/day, exposure %, overlap with data-quality
   gaps (ask `data-steward` if unsure).
9. **Live feasibility** — requires live Binance (`live_ready=False`)? Min order / tick rounding, gap
   handling, latency sensitivity (delay entry by +1 minute).

## Report template
```
# Candidate review — <candidate_id> (campaign <cmp_id>)
VERDICT: REJECT | WEAK | SURVIVES   Confidence: low | medium | high
| # | Check | Result | Evidence (numbers / command) | Severity |
|---|-------|--------|------------------------------|----------|
| 1 | Pre-registration | | | |
| 2 | Lookahead | | | |
| 3 | Engine agreement | | | |
| 4 | Selection bias (DSR/PBO/null) | | | |
| 5 | Costs (grade A/B) | | | |
| 6 | Regimes / robustness suite (latency, outliers, LOYO, regime, alpha, cross-venue) | | | |
| 7 | Ablation / plateau | | | |
| 8 | Sample | | | |
| 9 | Live feasibility | | | |
Fatal issues:
What would change the verdict (pre-registrable test):
Recommendation: reject | new campaign (<describe>) | request lockbox evaluation
```
Severity: fatal ⇒ REJECT. "Not shown" counts as failed, never as passed. Every row cites numbers.
Val appears only as the G6 outcome code (pass/fail); do not request or quote Val metrics.
Add to the report: "Predicted failure modes (pre-registered)" list and "Predictions confirmed k/n", and one
suggested interpretation (unverified, with evidence) for `LessonBook().add_interpretation`.
The researcher (not the skeptic) saves it to `research/reports/review_<candidate_id>.md`.
