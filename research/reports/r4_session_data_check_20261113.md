# Data quality — R4 step 1.4: Coinone BTC-KRW dawn / 09h session data and cost check (2026-11-13 UTC)

Status: **WARN** (data usable for both configs; no pre-registered rule triggered; forward cost evidence for the target windows is very thin)

Scope: R4 plan `docs/prompts/opus_r4_plan.md` §4 step 1.4. Data quality and execution cost only. **No returns, PnL or
signal performance were computed.** Ledger trials: 0. No Val or lockbox data were read. The lockbox
(2026-05-01 .. 2026-11-08) was never requested.

## Pre-registered rule outcomes

| config | rule (i) LOW_SAMPLE (usable events < 300 over Dev) | rule (ii) measured one-way cost median > coinone_conservative (2.00 bp) |
|---|---|---|
| **E1** 21:00 UTC (06:00 KST), 1h bars, primary hold 120 min | **Not triggered.** 1,630 usable days under the engine/screen rule; 409 under the strictest per-1m reading (decision bar and next bar have no filled 1m bar); 372 if the full 120-min hold also has no filled bar. All are ≥ 300 | **Not triggered.** Forward KST 06–08 median 0.49–0.52 bp (block-bootstrap 95% upper bound 0.52 bp), p90 0.78 bp. No stress-cost Sharpe criterion is required by the rule |
| **E2** 00:05 UTC (09:05 KST), 5m bars, primary hold 60 min | **Not triggered.** 1,616 usable (engine rule); 1,522 to 1,523 strict; 1,006 if the 60-min hold also has no filled bar | **Not triggered.** Forward KST 09–10 median 0.50 bp (upper bound 0.52 bp), p90 0.98 bp. No stress-cost Sharpe criterion is required by the rule |

Caveat on rule (ii): the KST 06–08 forward evidence covers only about 17 minutes of order-book data from 2 hour buckets
(2026-11-09 22:47–22:59 UTC and 2026-11-12 21:07–21:12 UTC). That is far below the calibration "final" standard
(72 hours, 24 KST hours, 7 days). The outcome is a fact about the data we have now, not evidence that dawn
costs on Coinone are low. Forward data say nothing about 2021–2025 dawn spreads. See Issues 1–2.

## Data used

| item | path / access | segment | snapshot id |
|---|---|---|---|
| Coinone BTC-KRW 1m | `DataAccess().read("dev", "coinone", "BTC-KRW", "1m")` (`data/raw/exchange=coinone/symbol=BTC-KRW/timeframe=1m/year=2021..2025`) | dev 2021-01-01 .. 2025-11-01 | `snap-93b066dd27ee41f0a0ec` |
| Upbit BTC-KRW 1m | `DataAccess().read("dev", "upbit", "BTC-KRW", "1m")` | dev | `snap-469cadfe5ed6b08ed5a0` |
| Coinone BTC-KRW order book | `btc_quant.params.calibrate.load_orderbook_costs("coinone", notional=300000, since=forward_start)` reading `data/raw/exchange=coinone/symbol=BTC-KRW/orderbook/` (WS `date=2026-11-09..13/HH-*.parquet`, `rest_20261109T*.parquet`, `snap_2026110[9]/[10]T*.parquet`); rows with ts < 2026-11-09 00:00 UTC dropped | forward 2026-11-09 .. 2026-11-13 08:46 UTC | n/a |
| cost assumption | `btc_quant.params.costs.cost_model("coinone_conservative")` (config/costs.yaml v2: spread 2.0, slip 1.0, fee 0) → one-way = spread/2 + slip = **2.00 bp** (base 1.00, stress 4.00) | | |

Scripts (scratch, not committed): `/tmp/claude-0/-home-user-temp/6c68d243-afba-544a-81d3-998fbfe8910d/scratchpad/task1.py`,
`task1b.py`, `task2.py` in the same directory.

## Method

- **Filled / no-trade definition.** This is the same as the research and paper path: `research.campaign.prepare_m1` = `data.quality.prepare_venue_m1`
  = `clean_wicks_for(coinone)` → `regularize` (rows absent from the store are filled, and zero-volume flat bars
  O=H=L=C are filled) → `mark_gap_warmup` (the first real bar after a filled run of ≥ 30 min is filled). Reported separately:
  minutes absent from the store ("missing"), zero-volume flat bars present in the store (Coinone's own no-trade fill),
  any zero-volume bar, and the final `filled` flag after `prepare_m1`.
- **Windows.** KST 06:00–08:00 = UTC 21:00–23:00 of the previous UTC day. KST 09:00–10:00 = UTC 00:00–01:00. "other" = all
  remaining hours. Year = KST calendar year. Dev starts 2021-01-01 09:00 KST and ends 2025-11-01 09:00 KST,
  so 2021 and 2025 are partial.
- **Upbit maintenance gap.** A run of ≥ 30 consecutive Upbit minutes absent from the store, starting in KST 02:00–07:00.
  Overlap is counted per gap if any gap minute falls in the stated KST hour range.
- **Usable events (rule i).** Clock triggers were rebuilt with the screen's own code path (`signal_study.decision_bars` on the
  `prepare_m1` frame: 1h = `features.to_tf`, 5m = `features.to_5m`). A trigger is the decision bar whose end time is 21:00 UTC (E1)
  or 00:05 UTC (E2). Only the eligibility masks of `signal_study._event_arrays` were evaluated; no price differences were taken.
  Four definitions are reported, from loosest to strictest:
  1. *engine*: decision bar `complete` (1h: no filled run ≥ 30 min and 60 bars present; 5m: all 5 1m bars real), entry 1m bar
     at t not filled, max-horizon window inside data, trailing vol available. This is exactly what the screen counts before
     declustering.
  2. *engine + next decision bar complete*: the task's wording "entry bar and next bar exist and are not filled" under the engine's
     bar-completeness rule.
  3. *strict*: the decision bar and the next decision bar contain **no** filled 1m bar at all, and the entry 1m bar is not filled.
  4. *strict + hold*: (3) and no filled 1m bar over the primary hold (E1 120 min, E2 60 min).
- **Execution cost (task 2).** Effective one-way cost of a 300,000 KRW market order (`notional_krw` from params settings) walked
  through the displayed book (`paper.fills.walk_book`) = half-spread + impact vs mid, fee excluded. Buy and sell sides are
  pooled. There is one sample per side per snapshot. Uncertainty comes from a 2,000× hour-bucket block bootstrap. The collector stores no separate
  slippage field. Impact beyond the touch is the only slippage proxy, and it is 0 at this notional (see the table).

## 1. Coinone BTC-KRW Dev 1m: filled / no-trade share by KST window and year

Missing minutes (absent from the store) are **0 in every cell**. The Dev store is a complete 1-minute grid
(2,541,600 rows, 0 duplicates, 0 gaps). Coinone writes no-trade minutes as zero-volume flat bars. "zero-vol flat" is
the share of those. "filled" is the final `prepare_m1` flag (zero-vol flat + post-outage warm-up bar).

| KST year | window | minutes | missing % | zero-vol flat % | zero-vol any % | filled (prepare_m1) % |
|---|---|---|---|---|---|---|
| 2021 | KST06-08 | 43,680 | 0.00 | 1.60 | 1.60 | 1.61 |
| 2021 | KST09-10 | 21,900 | 0.00 | 0.21 | 0.21 | 0.21 |
| 2021 | other | 459,480 | 0.00 | 1.28 | 1.28 | 1.28 |
| 2022 | KST06-08 | 43,800 | 0.00 | 6.16 | 6.16 | 6.16 |
| 2022 | KST09-10 | 21,900 | 0.00 | 1.21 | 1.21 | 1.21 |
| 2022 | other | 459,900 | 0.00 | 4.03 | 4.03 | 4.03 |
| 2023 | KST06-08 | 43,800 | 0.00 | 18.43 | 18.43 | 18.43 |
| 2023 | KST09-10 | 21,900 | 0.00 | 6.42 | 6.42 | 6.42 |
| 2023 | other | 459,900 | 0.00 | 12.92 | 12.92 | 12.92 |
| 2024 | KST06-08 | 43,920 | 0.00 | 10.64 | 10.64 | 10.66 |
| 2024 | KST09-10 | 21,960 | 0.00 | 3.93 | 3.93 | 3.93 |
| 2024 | other | 461,160 | 0.00 | 8.24 | 8.24 | 8.24 |
| 2025* | KST06-08 | 36,600 | 0.00 | 15.19 | 15.19 | 15.20 |
| 2025* | KST09-10 | 18,240 | 0.00 | 4.92 | 4.92 | 4.92 |
| 2025* | other | 383,460 | 0.00 | 11.79 | 11.79 | 11.79 |
| **all Dev** | KST06-08 | | 0.00 | 10.25 | 10.25 | **10.25** |
| **all Dev** | KST09-10 | | 0.00 | 3.29 | 3.29 | **3.29** |
| **all Dev** | other | | 0.00 | 7.51 | 7.51 | **7.51** |

\* 2025 = 2025-01-01 .. 2025-11-01 09:00 KST.

Filled share by KST hour (all Dev; the E1 decision bar is KST 05:00–06:00):

| KST hour | 04 | 05 | 06 | 07 | 08 | 09 | 10 | 11 |
|---|---|---|---|---|---|---|---|---|
| filled % | 15.38 | 13.95 | 11.67 | 8.84 | 7.32 | 3.29 | 4.04 | 4.97 |

Long filled runs (≥ 30 min, Coinone maintenance-like): 32 in Dev, max 471 min. The top 10 all start between KST 00:31 and 01:01
(e.g. 2022-11-28 21:00 KST 471 min; 2023-06-21 01:00 KST 431 min; 2024-12-02 01:00 KST 371 min). None of the top 10
reaches KST 09–10. Some reach into KST 06–08 (a 430-min run from 01:00 KST ends about 08:10 KST).

### Upbit BTC-KRW maintenance gaps (≥ 30 missing min starting KST 02–07), Dev

Upbit Dev missing share overall 0.32%.

| KST year | gaps | days | median len (min) | max len (min) | days overlapping KST 05–06 (E1 decision bar) | days overlapping KST 06–08 | days overlapping KST 09–10 |
|---|---|---|---|---|---|---|---|
| 2021 | 14 | 13 | 133 | 267 | 10 | 5 | 0 |
| 2022 | 5 | 5 | 148 | 237 | 3 | 0 | 0 |
| 2023 | 5 | 5 | 243 | 357 | 5 | 3 | 0 |
| 2024 | 4 | 4 | 270 | 336 | 4 | 4 | 0 |
| 2025 | 5 | 5 | 333 | 414 | 4 | 5 | 0 |
| **total** | 33 | 32 | | | 26 | **17** | **0** |

Days with any Upbit missing minute (any length) inside KST 06–08: 2021 5, 2022 15, 2023 11, 2024 5, 2025 5 (41 total;
≥ 30 missing minutes: 11). Inside KST 09–10: 0 in every year. E1 and E2 templates use `aux: []`, so Upbit gaps do not
remove any events. They matter only if an Upbit reference or grade feature is added later.

## 2. Rule (i): usable clock events over Dev (counts per UTC year of entry time)

**E1: decision bar 20:00–21:00 UTC (1h), entry 21:00 UTC; horizons 60/120/240**

| year | trigger days | dec bar complete | entry 1m not filled | (1) engine usable | (2) + next 1h bar complete | dec bar no filled 1m | next bar no filled 1m | (3) strict | hold 120m no filled | (4) strict + hold |
|---|---|---|---|---|---|---|---|---|---|---|
| 2021 | 365 | 363 | 358 | 358 | 358 | 257 | 273 | 228 | 255 | 222 |
| 2022 | 365 | 362 | 350 | 348 | 348 | 119 | 92 | 65 | 72 | 55 |
| 2023 | 365 | 362 | 314 | 314 | 314 | 59 | 62 | 39 | 34 | 27 |
| 2024 | 366 | 359 | 355 | 354 | 354 | 91 | 100 | 68 | 77 | 59 |
| 2025 | 304 | 302 | 256 | 256 | 256 | 18 | 16 | 9 | 15 | 9 |
| **Dev** | 1,765 | 1,748 | 1,633 | **1,630** | **1,630** | 544 | 543 | **409** | 453 | **372** |

**E2: decision bar 00:00–00:05 UTC (5m), entry 00:05 UTC; horizons 30/60/120**

| year | trigger days | dec bar complete | entry 1m not filled | (1) engine usable | (2) + next 5m bar complete | next bar no filled 1m | (3) strict | hold 60m no filled | (4) strict + hold |
|---|---|---|---|---|---|---|---|---|---|
| 2021 | 365 | 364 | 365 | 363 | 360 | 360 | 359 | 351 | 350 |
| 2022 | 365 | 355 | 359 | 352 | 345 | 350 | 345 | 258 | 256 |
| 2023 | 365 | 316 | 349 | 311 | 281 | 308 | 281 | 145 | 142 |
| 2024 | 366 | 324 | 350 | 313 | 286 | 313 | 286 | 181 | 177 |
| 2025 | 304 | 279 | 298 | 277 | 251 | 269 | 251 | 82 | 81 |
| **Dev** | 1,765 | 1,638 | 1,721 | **1,616** | **1,523** | 1,600 | **1,522** | 1,017 | **1,006** |

(For 5m bars "complete" already means all five 1m bars are real, so (2) and (3) coincide up to the horizon/vol masks.)

Outcome: **E1 not LOW_SAMPLE, E2 not LOW_SAMPLE** under every definition. Declustering cannot reduce one trigger per day
further (min gap < 24 h).

## 3. Forward-period execution cost (Coinone BTC-KRW, 300,000 KRW market order, one-way, bp, fee excluded)

Forward sample: 2026-11-09 11:46 .. 2026-11-13 08:46 UTC, 118,105 one-way samples (WS 116,608; REST 924; old snap 573),
0 incomplete book walks. Collector coverage is uneven: 34 hour buckets, 4.9 days. KST hours 01, 02, 04 and 05 have no
samples.

| window | one-way samples | snapshots | hour buckets | days | cost p50 | cost p90 | cost p99 | half-spread p50 | half-spread p90 | impact p50 | impact p90 | p50 95% block-boot CI | p90 95% CI |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KST06-08 | 1,748 | 874 | **2** | 2 | **0.49** | **0.78** | 1.41 | 0.49 | 0.52 | 0.00 | 0.00 | 0.49–0.52 | 0.49–1.03 |
| KST09-10 | 10,688 | 3,526 | 3 | 3 | **0.50** | **0.98** | 1.44 | 0.50 | 0.52 | 0.00 | 0.00 | 0.49–0.52 | 0.49–1.04 |
| other | 105,669 | 43,238 | 29 | 4 | 0.50 | 0.99 | 1.46 | 0.50 | 0.50 | 0.00 | 0.00 | 0.495–0.498 | 0.98–0.995 |
| assumption: coinone_conservative v2 | | | | | **2.00** | | | 1.00 | | 1.00 | | | |
| (coinone_base / stress v2) | | | | | 1.00 / 4.00 | | | | | | | | |

Hour buckets inside the target windows:

| window | UTC hour bucket | samples | first – last sample (UTC) | p50 | p90 |
|---|---|---|---|---|---|
| KST06-08 | 2026-11-09 22:00 | 1,216 | 22:47:55 – 22:59:58 | 0.49 | 0.49 |
| KST06-08 | 2026-11-12 21:00 | 532 | 21:07:05 – 21:12:12 | 0.52 | 1.03 |
| KST09-10 | 2026-11-10 00:00 | 3,634 | 00:24:49 – 00:59:59 | 0.49 | 0.49 |
| KST09-10 | 2026-11-12 00:00 | 6,524 | 00:00:00 – 00:59:59 | 0.50 | 1.00 |
| KST09-10 | 2026-11-13 00:00 | 530 | 00:07:34 – 00:12:41 | 0.52 | 1.04 |

By side: KST06-08 buy p50/p90 0.49/0.98, sell 0.49/0.52. KST09-10 buy 0.50/0.98, sell 0.50/0.98.
Cost = one tick of half-spread (about 0.5 bp) or two ticks (about 1.0 bp). At 300k KRW the order never walks past the touch.

Outcome: measured median ≤ 0.52 bp in both windows. That is below the 2.00 bp coinone_conservative one-way assumption (and below base 1.00 bp)
→ **rule (ii) not triggered for E1 or E2**.

## Issues (ordered by impact on research)

1. **Dawn cost evidence is too thin to support the rule (ii) outcome strongly.** KST 06–08 has about 17 minutes of order-book data
   on 2 days. The 21:00 UTC entry minute itself was sampled once (2026-11-12 21:07–21:12, p90 1.03 bp). KST 05:00–06:00 (E1
   decision bar) and KST 01–05 were never sampled. The rule outcome is "not triggered on current evidence". It should be
   re-checked once the collector has ≥ 7 days of dawn coverage (calibration final standard).
2. **No order-book history for Dev.** The 2021–2025 dawn spread cannot be measured. Dev filled share at KST 06–08 rose from 1.6%
   (2021) to 15–18% (2023, 2025). It is the thinnest trading in the windows studied, so Dev-era dawn spreads were plausibly
   wider than the Nov-2026 forward book suggests. Forward cost numbers do not test this.
3. **E1 strict-definition sample is concentrated in 2021.** Engine-rule usable days are 1,630 and spread evenly. Under the strict
   "no filled 1m in decision bar + next bar" reading, 228 of 409 events are from 2021 and only 9 from 2025. The 1h engine
   `complete` rule (filled run < 30 min) admits bars with many quiet minutes. KST 05:00–06:00 is 14% filled over Dev. The
   screen will run on the engine definition (1,630). Any stratified-by-year reading of E1 should note that 2023–2025 dawn
   bars are frequently partly no-trade (stale prices inside the decision bar).
4. **E2 hold window is often partly no-trade in later years.** Only 82 of 277 engine-usable days in 2025 have a fully traded
   60-min hold. Exit prices at quiet minutes are stale closes. This is not a sample problem (engine count 1,616), but a fill-realism note.
5. **Upbit maintenance overlaps KST 06–08 on 17 Dev days and KST 05–06 on 26 days. It never overlaps 09–10.** This is irrelevant for
   the current `aux: []` templates.
6. Coinone Dev store: complete grid, 0 missing minutes, 0 duplicates. Long Coinone filled runs (≤ 471 min) cluster at KST
   00:30–01:00 starts and sometimes extend to about 08:10 KST. They are already filled and the warm-up bar is masked.

## Windows to exclude from trading

None new. The existing `prepare_m1` filled + warm-up masking already blocks entries on all no-trade/outage bars in both windows.
The optional `AUDIT_EXCLUSION_WINDOWS` (2024-12-03 13:20–15:30 UTC) does not touch 21:00 or 00:05 UTC.

## Recommended action (for a human or collector, not executed here)

- Keep the Coinone WS order-book collector running continuously through KST 01:00–08:00 for ≥ 7 days. Then re-run this cost
  table and re-evaluate rule (ii) for E1 before any E1 campaign (not just the screen) relies on conservative cost.
- Optional, as a voluntary extra disclosure (not required by rule ii): report E1/E2 screen net mean at coinone_stress next to
  coinone_conservative, given Issues 1–2.
- No post-mortem draft is needed: no data problem affected a campaign.
