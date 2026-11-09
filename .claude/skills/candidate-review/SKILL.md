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

## Checklist
1. **Pre-registration integrity** — hypothesis and campaign records exist and their timestamps precede
   the first run of the campaign; grid in the ledger equals grid actually run; budget not exceeded;
   no earlier campaign in the family with an overlapping grid that was silently "re-tried".
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
| 6 | Regimes | | | |
| 7 | Ablation / plateau | | | |
| 8 | Sample | | | |
| 9 | Live feasibility | | | |
Fatal issues:
What would change the verdict (pre-registrable test):
Recommendation: reject | new campaign (<describe>) | request lockbox evaluation
```
Severity: fatal ⇒ REJECT. "Not shown" counts as failed, never as passed.
The researcher (not the skeptic) saves it to `research/reports/review_<candidate_id>.md`.
