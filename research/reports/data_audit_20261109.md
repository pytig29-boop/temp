# Data quality — 1m OHLCV, Coinone / Upbit / Binance / Bithumb, Dev + Val (2026-11-09 UTC)
Status: **WARN** (Coinone 1m: **FAIL for direct engine use** until zero-volume bars are treated as `filled`, see Issue 1)

Scope: read only via `DataAccess.read("dev"|"val", …)`. Dev = 2021-01-01..2025-10-31, Val = 2025-11-01..2026-04-30 (UTC, end-exclusive).
Lockbox (2026-05-01..2026-11-08) not read. Val: aggregate coverage/quality only. Bithumb has no Dev data (starts 2025-11).
All numbers are `잠금: 없음`; Val rows are `Val: 노출 (집계 품질 지표만)`.

| exchange | tf | segment | coverage % | missing min | max gap | dupes* | bad OHLC | wick>3% (>1%) | \|r\|>5% / >2% | zero-vol % | flat % | stale runs≥10 | snapshot |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| coinone | 1m | dev | 100.000 (pre-filled) | 0 | 0 (zero-vol run 470) | 0 | 0 | 13 (106) | 0 / 37 | 7.56 | 17.90 | 799 (16,882 bars, max 471) | snap-93b066dd27ee41f0a0ec |
| coinone | 1m | val | 100.000 (pre-filled) | 0 | 0 (zero-vol run 371) | 0 | 0 | 5 (18) | 0 / 0 | 14.71 | 38.76 | 156 (2,528 bars) | snap-3c57011d43a87a4b2e9d |
| upbit | 1m | dev | 99.677 | 8,221 | 414 | 0 | 0 | 2 (38) | 6 / 56 | 0 | 0.45 | 10 (101 bars) | snap-469cadfe5ed6b08ed5a0 |
| upbit | 1m | val | 99.754 | 640 | 334 | 0 | 0 | 0 (0) | 0 / 0 | 0 | 0.18 | 4 | snap-f4c55d2a20d363a88eaf |
| binance | 1m | dev | 99.958 | 1,073 | 284 | 0 | 0 | 3 (174) | 2 / 113 | 0.006 | 0.006 | 29 (480 bars, max 82) | snap-71eea6e5caf1f378faa3 |
| binance | 1m | val | 100.000 | 0 | 0 | 0 | 0 | 0 (1) | 0 / 3 | 0 | 0 | 0 | snap-1b6010a91e40352cc5ed |
| bithumb | 1m | val | 99.569 | 1,124 | 420 | 0 | 0 | 2 (6) | 0 / 4 | 0 | 0.58 | 3 | snap-65e9e42bd3027e0017f5 |

\* `store.normalize_ohlcv` drops duplicate ts (keep last) and sorts **before** DataAccess returns, so dupes = 0 / sorted = True by construction; raw-file duplicates cannot be counted through the sanctioned interface.
Other integrity checks (all series, both segments): 0 OHLC violations, 0 non-positive prices, 0 negative volumes, 0 NaN rows, all timestamps tz-aware UTC on exact minute boundaries.
`|r|` = contiguous 1m close-to-close log return. No return spanning a gap exceeds 2%. Snapshot ids are new: `research/ledger/` is empty, so there was nothing to compare them with. Record them with the first campaign.

## Issues (ordered by impact on research)

1. **Coinone 1m is pre-regularised: no-trade minutes are stored as flat bars with volume = 0, and the store has no `filled` column.**
   Dev has exactly 1765×1440 rows and Val is also 100%. `regularize()` therefore marks none of these bars `filled`. The engine (`fast.py` `~filled_mask`) and `signal_study` (`complete`) would enter at the open of stale synthetic bars, including during Coinone maintenance windows of up to 7.8 h. All 7.56% (Dev) / 14.7% (Val) zero-volume bars are exactly flat, i.e. synthesised (0 zero-volume bars have a non-flat range).
   Maintenance signature: 32 zero-volume runs of ≥30 min in Dev (7,407 min). They start at KST 00:00–01:00 and usually end at UTC hh:09, on Wednesdays in 2021–23 and Mondays in 2024–25. Only 1 of the 32 overlaps an Upbit gap, so the two schedules differ. Pitfall P-D2's `filled` check is blind on Coinone.
2. **Coinone liquidity declined throughout Dev.** It does not improve over time, and 2021 is the best year, not the worst:

   | year | zero-vol % | flat % | median quote vol / traded min (M KRW) | Coinone/Upbit KRW volume | last trade ≥5 min old % |
   |---|---|---|---|---|---|
   | 2021 | 1.3 | 2.8 | 46.2 | 19.7% | 0.38 |
   | 2022 | 4.1 | 10.2 | 13.0 | 16.3% | 0.30 |
   | 2023 | **13.1** | 27.6 | 5.4 | 10.0% | **0.97** |
   | 2024 | 8.3 | 18.9 | 6.2 | 5.8% | 0.66 |
   | 2025 (Jan–Oct) | **11.8** | **32.5** | **3.0** | **4.6%** | 0.43 |
   | Val (aggregate) | 14.7 | 38.8 | – | – | – |

   Worst months: 2023-08 (29.8% zero-vol), 2023-09 (24.5%), 2023-10 (21.7%), 2023-07 (19.0%), 2024-09 (17.1%), 2024-06 (16.6%).
   Intraday pattern: zero-vol share at KST 02–06 is 15–28% in 2023–2025, against 4–9% at KST 09–23.
   Even in 2025, half of the traded minutes carry less than about 3M KRW (about 0.02 BTC). A third of all bars have a range of 0, so 1m ATR and high/low-based features on Coinone are degenerate.
   Volume contamination (P-D1) cannot be checked in Dev because Coinone has no trade-count column. Dev volume looks thin rather than inflated.
3. **Coinone has venue-specific bad prints in the wicks.** All 13 Coinone wick outliers at k=3% (Dev) are venue-specific: Coinone's low or high falls outside Upbit's same-minute range by more than 1.5%. At k=1%, 103 of 106 are venue-specific (2021: 26, 2024: 47). Examples (Coinone vs Upbit same minute):
   - 2024-01-02 03:48: low 56.80M vs 60.33M (−5.9%)
   - 2021-02-22 14:15: low 54.14M vs 57.68M
   - 2023-10-23 22:05: low 40.85M vs 42.58M
   - 2024-07-21 21:10: low 91.2M vs 94.44M
   - 2024-12-18 04:19: high 158.0M vs 152.39M
   - 2025-02-25 16:02: high 131.79M vs 126.51M
   - 2025-10-10 21:38: high 178.75M vs 170.71M

   Val has 5 wick outliers at k=3%, all venue-specific. The default `clean_wicks(k=0.03)` misses most of them. These are thin-book sweeps, and a TP/SL simulator would treat them as touches that could not really be filled.
4. **The 2024-12-03 martial-law crash (13:20–15:00 UTC) is a real but extreme cross-venue dislocation.** Upbit fell to 88.27M (1m returns −6.0%, −8.7%, −10.1%, then +21.9% at 13:58). Coinone's low was only about 113M at the same time and 110M at 14:35. The Coinone−Upbit gap reached 2,439bp. This window accounts for all 6 of Upbit's Dev |r|>5% minutes and the top 8 cross-venue gaps. The data is genuine and should not be cleaned away, but it dominates any tail statistic.
5. **Upbit missing bars are maintenance windows.** There are 41 gaps of ≥30 min in Dev (7,460 min), plus 2 in Val. Most start at KST 02:01 and recur on quarter-end and year-end days: 03-31, 06-30, 09-30 and 12-31 every year from 2022 to 2025. In 2021 there are extra KST 03:01 and Tuesday KST 23:xx gaps. Missing minutes by KST hour: 02–05 h hold 6,389 of 8,221. Coinone kept trading in 77.9% of the minutes Upbit was missing.
6. **Binance has 7 Dev gaps (1,073 min), 6 of them in 2021.** Each is a known exchange maintenance or outage (2021-04-25, 2021-08-13, 2023-03-24 …). Before two of them (2021-02-11 02:20–03:40 and 2023-03-24 11:28–12:39 UTC) there are 81- and 72-min runs of zero-volume flat bars where trading was frozen; treat these as missing too. The 29 stale runs (up to 82 bars) are concentrated in 2023–25.
7. **Bithumb (Val only, aggregate):** 99.57% coverage. There are 4 gaps of ≥30 min, all starting Monday KST 01:00 (weekly maintenance). Missing minutes sit at KST 01–07. Bithumb cannot be used in Dev at all.
8. **Minor items.**
   - Partitions: year=/month= exist for every month. Binance has no 2026-11 partition (outside Dev/Val, noted from the directory listing only).
   - The orderbook files are all dated 2026-11-09 (Forward) and were not audited.
   - `store.read_ohlcv` globs every partition, including lockbox months, and filters by `WHERE ts`. DataAccess blocks lockbox ranges correctly, but the reader still opens those files. If you want defence in depth, the reader could restrict the glob to the selected partitions.

## Cross-market sanity (Dev only)
- **Timestamp alignment is good.** For Coinone/Upbit and Upbit/Binance, 1m return cross-correlation peaks at lag 0, so all series use the same UTC, left-labelled convention, with no 9 h KST shift and no off-by-one.
- **Coinone − Upbit log close gap, in bp, using minutes where both venues traded (n = 2.34M):**
  - Whole Dev: median −4.4, p1 −65.1, p99 +12.9, p0.1 −128.6, p99.9 +27.5. Mean |gap| 9.7. |gap| >20bp in 11% of minutes, >50bp in 2%.
  - The distribution is **negatively skewed**: Coinone trades at a discount, mainly in fast markets.
  - By year:

    | year | median | p1 | p99 | mean \|gap\| | share \|gap\| > 20bp |
    |---|---|---|---|---|---|
    | 2021 | −10.5 | −83.0 | 16.2 | 16.8 | 29% |
    | 2022 | −5.0 | −62.4 | 10.5 | 9.1 | 7% |
    | 2023 | −2.8 | −31.4 | 10.0 | 5.8 | 3% |
    | 2024 | −5.7 | −69.0 | 14.4 | 10.6 | 12% |
    | 2025 | −0.9 | −13.3 | 13.7 | 4.5 | 0.4% |

  - Drift: monthly medians run from −32bp (2021-03) to +3.6bp (2025-07), with a standard deviation of 7bp. The level is not stationary, so spread signals need a causal rolling baseline.
- **1m return correlation, Coinone vs Upbit (both traded in t and t−1):**
  - 1m: 0.79 / 0.78 / 0.72 / 0.66 / 0.72 for 2021–2025.
  - 5m: 0.92 / 0.93 / 0.89 / 0.82 / 0.90.
  - Lead-lag: Upbit(t−1)→Coinone(t) is 0.06–0.12, and Coinone(t−1)→Upbit(t) is 0.05–0.11. Upbit leads more strongly in 2024–25 (0.12 / 0.11 vs 0.05 / 0.07).
  - Coinone lag-1 autocorrelation is between −0.02 and +0.05. The Coinone/Upbit 1m volatility ratio is 0.81–0.97, so Coinone prices are smoother and slower.
- **Upbit vs Binance:** 1m correlation at lag 0 is 0.62–0.72, and Binance leads by 1 min at 0.12–0.17. These figures reproduce K-P0-5's direction in Dev. Upbit/Binance price ratio (KRW/USDT × premium), yearly median: 1186, 1309, 1334, 1397, 1431. This is plausible: no unit or scaling errors.

## Coinone 2021–2023 adequacy for 1m-decision research
- **2021: adequate.** It is the most liquid year: 1.3% zero-volume and a median of 46M KRW per minute. The caveat is a large, volatile Coinone–Upbit gap (premium era, p1 −83bp). Keep 2021 for price and lead-lag research, but cross-venue spread levels from 2021 should not be used to calibrate thresholds for today.
- **2022: adequate**, with a moderate caveat: 4.1% zero-volume, up to 9% at KST 02–06.
- **2023: down-weight.** It is the thinnest year: 13.1% zero-volume, 27.6% flat bars, 0.97% of minutes with a last trade ≥5 min old. Jul–Oct 2023 has 19–30% zero-volume, so exclude those months from Coinone-execution 1m studies, or run them only with Upbit as the decision venue.
- **2024–2025 (and Val) are not better than 2023.** Liquidity in 2025 is worse by median volume and flat share. So the answer to "2021–2023 thin" is: the problem is 2023 and later, not 2021. Any Coinone-execution 1m result should be reported by year and by KST hour, and should be re-checked against forward orderbook depth.

## Windows to exclude from trading (start, end, reason) — Dev, UTC, inclusive minutes
- Every minute in an Upbit, Binance or Bithumb gap, plus the first bar after each gap of ≥30 min (warm-up).
- Every Coinone zero-volume run of ≥30 min, treated as maintenance; the windows are listed below. Mask **every** Coinone zero-volume bar as `filled` for entry and exit fills.
- 2024-12-03 13:20 .. 15:30: martial-law dislocation. Exclude it from cross-venue model fitting and tail statistics, or report results with and without it.
- 2021-02-11 02:20 .. 04:59 and 2023-03-24 11:28 .. 13:59: Binance frozen trading plus gap.

upbit missing>=30m (41 windows, 7460 min)
- 2021-01-06 20:00 .. 2021-01-06 20:48 UTC (49 min, KST start Thu 05:00)
- 2021-02-02 19:01 .. 2021-02-02 21:08 UTC (128 min, KST start Wed 04:01)
- 2021-02-02 21:18 .. 2021-02-02 21:49 UTC (32 min, KST start Wed 06:18)
- 2021-02-14 19:31 .. 2021-02-14 20:29 UTC (59 min, KST start Mon 04:31)
- 2021-02-19 18:01 .. 2021-02-19 22:27 UTC (267 min, KST start Sat 03:01)
- 2021-03-02 18:31 .. 2021-03-02 21:16 UTC (166 min, KST start Wed 03:31)
- 2021-03-13 18:01 .. 2021-03-13 20:12 UTC (132 min, KST start Sun 03:01)
- 2021-03-22 18:01 .. 2021-03-22 21:42 UTC (222 min, KST start Tue 03:01)
- 2021-03-31 18:01 .. 2021-03-31 19:39 UTC (99 min, KST start Thu 03:01)
- 2021-04-18 18:01 .. 2021-04-18 20:42 UTC (162 min, KST start Mon 03:01)
- 2021-05-31 18:01 .. 2021-05-31 20:14 UTC (134 min, KST start Tue 03:01)
- 2021-06-30 15:01 .. 2021-06-30 18:13 UTC (193 min, KST start Thu 00:01)
- 2021-08-16 17:01 .. 2021-08-16 19:41 UTC (161 min, KST start Tue 02:01)
- 2021-08-25 18:01 .. 2021-08-25 18:45 UTC (45 min, KST start Thu 03:01)
- 2021-10-05 14:31 .. 2021-10-05 15:32 UTC (62 min, KST start Tue 23:31)
- 2021-10-12 14:01 .. 2021-10-12 16:57 UTC (177 min, KST start Tue 23:01)
- 2021-10-19 14:32 .. 2021-10-19 15:01 UTC (30 min, KST start Tue 23:32)
- 2021-11-14 18:01 .. 2021-11-14 20:25 UTC (145 min, KST start Mon 03:01)
- 2021-12-31 17:01 .. 2021-12-31 20:57 UTC (237 min, KST start Sat 02:01)
- 2022-02-13 18:00 .. 2022-02-13 20:08 UTC (129 min, KST start Mon 03:00)
- 2022-05-23 17:00 .. 2022-05-23 18:49 UTC (110 min, KST start Tue 02:00)
- 2022-06-30 17:00 .. 2022-06-30 20:00 UTC (181 min, KST start Fri 02:00)
- 2022-09-30 17:01 .. 2022-09-30 19:28 UTC (148 min, KST start Sat 02:01)
- 2022-12-31 17:01 .. 2022-12-31 20:37 UTC (217 min, KST start Sun 02:01)
- 2023-03-31 17:01 .. 2023-03-31 21:03 UTC (243 min, KST start Sat 02:01)
- 2023-04-03 08:06 .. 2023-04-03 08:54 UTC (49 min, KST start Mon 17:06)
- 2023-04-23 17:01 .. 2023-04-23 22:57 UTC (357 min, KST start Mon 02:01)
- 2023-04-23 23:00 .. 2023-04-23 23:29 UTC (30 min, KST start Mon 08:00)
- 2023-07-11 17:01 .. 2023-07-11 20:40 UTC (220 min, KST start Wed 02:01)
- 2023-08-08 17:01 .. 2023-08-08 21:40 UTC (280 min, KST start Wed 02:01)
- 2023-12-03 15:31 .. 2023-12-03 20:59 UTC (329 min, KST start Mon 00:31)
- 2023-12-31 17:00 .. 2023-12-31 21:03 UTC (244 min, KST start Mon 02:00)
- 2024-01-28 15:01 .. 2024-01-28 15:53 UTC (53 min, KST start Mon 00:01)
- 2024-03-31 17:01 .. 2024-03-31 21:43 UTC (283 min, KST start Mon 02:01)
- 2024-06-30 17:01 .. 2024-06-30 21:17 UTC (257 min, KST start Mon 02:01)
- 2024-10-13 17:01 .. 2024-10-13 22:36 UTC (336 min, KST start Mon 02:01)
- 2024-12-31 17:01 .. 2024-12-31 21:19 UTC (259 min, KST start Wed 02:01)
- 2025-01-02 21:28 .. 2025-01-02 23:38 UTC (131 min, KST start Fri 06:28)
- 2025-03-31 17:01 .. 2025-03-31 22:33 UTC (333 min, KST start Tue 02:01)
- 2025-06-30 17:01 .. 2025-06-30 22:57 UTC (357 min, KST start Tue 02:01)
- 2025-10-25 17:01 .. 2025-10-25 23:54 UTC (414 min, KST start Sun 02:01)

coinone zero-volume flat run>=30m (32 windows, 7407 min)
- 2021-02-17 15:34 .. 2021-02-17 16:48 UTC (75 min, KST start Thu 00:34)
- 2021-03-03 16:00 .. 2021-03-03 20:59 UTC (300 min, KST start Thu 01:00)
- 2021-03-03 22:11 .. 2021-03-03 22:51 UTC (41 min, KST start Thu 07:11)
- 2021-04-02 15:30 .. 2021-04-02 18:40 UTC (191 min, KST start Sat 00:30)
- 2021-06-08 17:01 .. 2021-06-08 17:44 UTC (44 min, KST start Wed 02:01)
- 2021-07-07 15:31 .. 2021-07-07 22:39 UTC (429 min, KST start Thu 00:31)
- 2021-08-02 15:10 .. 2021-08-02 15:39 UTC (30 min, KST start Tue 00:10)
- 2021-08-12 06:31 .. 2021-08-12 07:01 UTC (31 min, KST start Thu 15:31)
- 2021-08-17 16:00 .. 2021-08-17 16:39 UTC (40 min, KST start Wed 01:00)
- 2021-09-09 16:01 .. 2021-09-09 17:54 UTC (114 min, KST start Fri 01:01)
- 2021-10-26 16:00 .. 2021-10-26 18:09 UTC (130 min, KST start Wed 01:00)
- 2021-11-09 16:00 .. 2021-11-09 17:09 UTC (70 min, KST start Wed 01:00)
- 2021-11-24 15:00 .. 2021-11-24 17:29 UTC (150 min, KST start Thu 00:00)
- 2021-12-01 15:00 .. 2021-12-01 16:59 UTC (120 min, KST start Thu 00:00)
- 2021-12-14 16:00 .. 2021-12-14 17:09 UTC (70 min, KST start Wed 01:00)
- 2022-03-22 16:00 .. 2022-03-22 20:09 UTC (250 min, KST start Wed 01:00)
- 2022-06-28 16:00 .. 2022-06-28 19:59 UTC (240 min, KST start Wed 01:00)
- 2022-09-27 16:01 .. 2022-09-27 21:09 UTC (309 min, KST start Wed 01:01)
- 2022-11-28 12:00 .. 2022-11-28 19:49 UTC (470 min, KST start Mon 21:00)
- 2023-02-14 16:00 .. 2023-02-14 21:09 UTC (310 min, KST start Wed 01:00)
- 2023-06-20 16:00 .. 2023-06-20 23:09 UTC (430 min, KST start Wed 01:00)
- 2023-11-07 16:00 .. 2023-11-07 23:09 UTC (430 min, KST start Wed 01:00)
- 2024-03-31 16:01 .. 2024-03-31 22:09 UTC (369 min, KST start Mon 01:01)
- 2024-04-28 15:59 .. 2024-04-28 19:09 UTC (191 min, KST start Mon 00:59)
- 2024-05-26 16:00 .. 2024-05-26 21:09 UTC (310 min, KST start Mon 01:00)
- 2024-07-14 16:00 .. 2024-07-14 22:09 UTC (370 min, KST start Mon 01:00)
- 2024-09-08 16:00 .. 2024-09-08 21:09 UTC (310 min, KST start Mon 01:00)
- 2024-09-29 16:00 .. 2024-09-29 20:09 UTC (250 min, KST start Mon 01:00)
- 2024-11-03 16:00 .. 2024-11-03 21:09 UTC (310 min, KST start Mon 01:00)
- 2024-12-01 16:00 .. 2024-12-01 22:09 UTC (370 min, KST start Mon 01:00)
- 2025-02-27 15:59 .. 2025-02-27 21:09 UTC (311 min, KST start Fri 00:59)
- 2025-07-13 15:58 .. 2025-07-13 21:39 UTC (342 min, KST start Mon 00:58)

binance missing>=30m (7 windows, 1073 min)
- 2021-02-11 03:41 .. 2021-02-11 04:59 UTC (79 min, KST start Thu 12:41)
- 2021-03-06 02:00 .. 2021-03-06 03:29 UTC (90 min, KST start Sat 11:00)
- 2021-04-20 02:00 .. 2021-04-20 04:29 UTC (150 min, KST start Tue 11:00)
- 2021-04-25 04:01 .. 2021-04-25 08:44 UTC (284 min, KST start Sun 13:01)
- 2021-08-13 02:00 .. 2021-08-13 06:29 UTC (270 min, KST start Fri 11:00)
- 2021-09-29 07:00 .. 2021-09-29 08:59 UTC (120 min, KST start Wed 16:00)
- 2023-03-24 12:40 .. 2023-03-24 13:59 UTC (80 min, KST start Fri 21:40)


## Recommended action (for a human or collector, not for me to execute)
1. **Coinone filled flag (blocking for Coinone campaigns).** In the research loader (`campaign.load_segment` / `aux_context` / `signal_study`), set `filled = filled | (volume == 0)` after `regularize` for Coinone. Use the same rule for the Binance frozen windows. Optionally also flag bars whose last trade is ≥N min old. The collector should keep no-trade minutes absent, or write an explicit `filled` column, rather than synthesising them silently.
2. **Wick cleaning.** For Coinone use `clean_wicks(k=0.01, window=15)`, preferably with cross-venue confirmation: clip only when Upbit's same-minute range does not confirm the move. Keep k=0.03 for Upbit and Binance; at 1%, Upbit flags real moves (31 of its 38 flags are 2021 market moves).
3. **Coinone features.** Do not use Coinone volume, trade-count, range or ATR features at 1m. Compute volatility and ATR on Upbit, or on Coinone at ≥5m. Use Upbit volume for volume features (P-D1).
4. **Cross-venue research.**
   - Condition on both venues having traded in t and t−1.
   - Use a causal rolling median for the Coinone−Upbit basis. Its level drifts by tens of bp across years, and its tails are one-sided.
   - Report results with and without 2024-12-03.
   - Weight or exclude KST 02–07 for Coinone execution (zero-volume share 15–28% from 2023).
5. **Snapshot ids.** Record the ids in the table above in the ledger with the first campaign that uses each series and segment.
6. **Collector check.** Run a raw-level duplicate-timestamp count (normalize hides it), and add the missing Binance 2026-11 partition.

## Post-mortem draft for the researcher (`LessonBook().postmortem(..., category="data")`)
- title: "Coinone 1m stores synthesised no-trade bars without a filled flag"
- what_happened: "The Coinone 1m store is 100% complete because no-trade minutes, including maintenance windows of up to 7.8 h, are stored as flat bars with volume=0. regularize() marks them filled=False, so engine and signal-study entries could fill at stale synthetic prices. This affects 7.6% of Dev bars and 32 maintenance windows."
- root_cause: "The collector or source regularises Coinone bars before writing, and the quality pipeline uses only absent rows to detect gaps."
- prevention: "Treat volume==0 as filled for Coinone (and any venue) in the loaders; add a data-steward check that flags series at 100.000% coverage with zero-volume flat bars; the collector writes an explicit filled column."
