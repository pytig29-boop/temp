---
name: daily-report
description: Write the morning research report — overnight campaigns and their gate funnels, ledger trial counts, lockbox status, strategy registry states, data quality, and pending human decisions. Use for the scheduled nightly-campaign → morning-report cycle or when asked for a daily/status summary.
argument-hint: "[YYYY-MM-DD]"
---

# Daily report (PLAN §8 예약 실행: 야간 캠페인 → 아침 리포트)

Report facts from the ledger and registry; do not run new campaigns or change anything while writing it.
Never include lockbox performance numbers except results already recorded by an approved opening.

Intended MCP tools (later milestone): `query_ledger`, `compare`, `write_report`.
Python equivalents today:
- Ledger: `from btc_quant.registry.ledger import Ledger; L = Ledger()` →
  `L.records("hypotheses" | "campaigns" | "runs" | "lockbox" | "transitions")`, `L.query("SELECT …")`
  (duckdb over the JSONL tables), `L.trials_count(family=…)`, `L.lockbox_history()`.
- Registry: `from btc_quant.registry.strategies import StrategyRegistry; StrategyRegistry().status()`.
- Lockbox: `from btc_quant.registry.splits import DataAccess; DataAccess().lockbox_status()`.
- Data quality: delegate to the `data-steward` subagent (Dev/Val/Forward only).
- Do **not** run `btc_quant.registry.admin_cli` (human-only; the research guard denies it, even `status`).

## Steps
1. Window = previous 24 h (UTC) unless a date is given.
2. Collect: campaigns created/finished, gate funnel per campaign, survivors, skeptic verdicts
   (`research/reports/review_*.md`), new block proposals, ledger cumulative trials per family.
3. Collect registry states (active, challengers, review queue) and lockbox openings used / cap.
4. Data section from `data-steward` (coverage of the last day, gaps, collector health).
5. List decisions waiting for a human (lockbox approval requests, block proposals to merge,
   promotion reviews). Never make or imply those decisions.
6. Save to `research/reports/daily_<YYYYMMDD>.md`.

## Template
```
# Daily research report — <YYYY-MM-DD> (UTC)
## Summary (3 lines max)
## Campaigns
| campaign | hypothesis | family | grid/budget | funnel G0→G6 | survivors | status |
## Candidates & reviews
## Ledger
cumulative trials per family · effective trials · new hypotheses
## Registry & lockbox
active / challengers / review · lockbox <id>: openings k / cap
## Data quality
## Waiting for human
- [ ] …
## Next planned campaigns (pre-registered ids only)
```
