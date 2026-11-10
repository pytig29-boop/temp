# Data quality — 1m OHLCV, XRP-KRW / ETH-KRW (Coinone + Upbit), Dev + Val (2026-11-10 UTC)
Status: **WARN** — usable for D15 pooled research with the same pipeline as BTC (zero-volume flat bars → `filled`,
Coinone wick k = 0.01, gap warm-up), but **costs are not**: the current cost models are BTC-calibrated and
understate XRP/ETH spreads (Issue 1). Read before running any 3-asset campaign.

Context: PLAN D15 (user decision 2026-11-10) — fixed universe BTC-KRW, XRP-KRW, ETH-KRW on Coinone, Upbit as
reference/aux venue per asset. Companion to `data_audit_20261109.md` (BTC).
Scope: read only via `DataAccess.read("dev"|"val", …)`. Dev = 2021-01-01..2025-10-31, Val = 2025-11-01..2026-04-30
(UTC, end-exclusive). **Lockbox (2026-05-01..2026-11-08) not read**; Val rows are aggregate quality only
(`Val: 노출 (집계 품질 지표만)`, `잠금: 없음`). Script: one-off, run from the session scratchpad (not committed).

## Download (2026-11-10)
Existing collectors (`btc-quant fetch-candles`, `coinone.fetch_candles` / `upbit.fetch_candles`), same storage
layout `data/raw/exchange=<ex>/symbol=<SYM>/timeframe=1m/year=YYYY/month=MM/part.parquet`, range
2021-01-01 00:00 → 2026-11-10 03:37 UTC (stored through the Forward start like BTC; nothing beyond Dev/Val was read).

| exchange | symbol | segment | coverage % | missing min | max gap | gaps ≥30m (min) | bad OHLC / ≤0 / NaN | zero-vol % | flat % | zero-vol flat runs ≥30m (min, max) | wick>3% (>1%) | \|r\|>5% / >2% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| coinone | XRP-KRW | dev | 100.000 (pre-filled) | 0 | 0 | 0 | 0 / 0 / 0 | 13.21 | 35.21 | 34 (7,478, 470) | 43 (518) | 34 / 452 |
| coinone | XRP-KRW | val | 100.000 (pre-filled) | 0 | 0 | 0 | 0 / 0 / 0 | 12.88 | 46.26 | 2 (679, 370) | 1 (11) | 2 / 8 |
| upbit | XRP-KRW | dev | 99.667 | 8,474 | 414 | 40 (7,431) | 0 / 0 / 0 | 0 | 2.04 | – | 20 (534) | 45 / 579 |
| upbit | XRP-KRW | val | 99.758 | 632 | 334 | 2 (630) | 0 / 0 / 0 | 0 | 0.08 | – | 0 (1) | 0 / 6 |
| coinone | ETH-KRW | dev | 100.000 (pre-filled) | 0 | 0 | 0 | 0 / 0 / 0 | **19.10** | **46.40** | 52 (8,224, 470) | 9 (204) | 4 / 113 |
| coinone | ETH-KRW | val | 100.000 (pre-filled) | 0 | 0 | 0 | 0 / 0 / 0 | **23.65** | **56.25** | 2 (679, 369) | 1 (22) | 0 / 3 |
| upbit | ETH-KRW | dev | 98.934 | 27,086 | 414 | 41 (7,462) | 0 / 0 / 0 | 0 | 6.00 | – | 6 (150) | 12 / 171 |
| upbit | ETH-KRW | val | 99.743 | 669 | 334 | 2 (630) | 0 / 0 / 0 | 0 | 1.47 | – | 0 (7) | 0 / 1 |

For comparison (BTC, 2026-11-09 audit): Coinone Dev zero-vol 7.56 %, flat 17.9 %; Upbit Dev coverage 99.677 %.
All series: 0 OHLC violations, 0 non-positive prices, 0 NaN rows, UTC minute-aligned; every Coinone zero-volume bar
is exactly flat (0 zero-volume non-flat bars) → `regularize(zero_volume_as_filled=True)` marks them `filled`, exactly
as for BTC. `|r|` = contiguous 1m close-to-close log return.

## Issues (ordered by impact on research)

1. **Cost models understate XRP/ETH execution costs (BTC-calibrated).** Coinone tick sizes and live spreads differ a
   lot by asset:

   | asset | Coinone tick (Dev, by year: KRW → bp of median price) | live Coinone spread 2026-11-10 (180 s sample) | Upbit tick |
   |---|---|---|---|
   | BTC | 1,000 → 10,000 (2024-09-29) | **1.0 bp** (median 0.99) | – |
   | XRP | 0.1 KRW 2021–24 (0.9–1.8 bp); **1 KRW from 2025 (2.7 bp at 3,658; 6.7 bp at today's ≈1,484)** | **6.7 bp** (= 1 tick, every sample) | 1 KRW 2021–23 (**9–18 bp**), 0.1 KRW 2024, 1 KRW 2025 |
   | ETH | 1,000 KRW (2.3–4.4 bp) | **3.5 bp** median (3.9 mean, max 7.0) | 500 → 1,000 KRW (1.6–2.5 bp) |

   `coinone_conservative` = spread 2 bp + slip 1 bp per side (≈ 4 bp round trip net of spread/slip at 0 % fee).
   A market round trip on Coinone XRP today pays ≥ 6.7 bp of spread alone; ETH ≈ 3.5 bp. Pooled 3-asset results under
   the BTC cost model are therefore **optimistic for the XRP and ETH sleeves**. Code now looks up per-asset models
   `<name>@<SYM>` (e.g. `coinone_conservative@XRP-KRW`) for non-BTC sleeves in campaigns, the pipeline (incl. G2 nulls,
   G6, robustness), forward and paper (`research.campaign.sleeve_cost_name`); **none exist yet** — adding them is a
   cost-registry (class ①) decision. Recommended before the first 3-asset campaign: XRP ≥ 1 tick spread (≈ 7 bp) and
   ETH ≈ 4 bp spread, slip ≥ BTC's. Book depth at the touch is also thin for XRP (≈ 0.12–0.15 M XRP ≈ 180–220 M KRW;
   fine for the 300k KRW paper notional).
2. **Coinone ETH (and to a lesser extent XRP) is much thinner than Coinone BTC.** Zero-volume (= `filled`) share of
   Dev minutes:

   | year | XRP zero-vol % | XRP flat % | XRP median quote vol / traded min (M KRW) | ETH zero-vol % | ETH flat % | ETH median (M KRW) | Coinone/Upbit KRW volume XRP / ETH |
   |---|---|---|---|---|---|---|---|
   | 2021 | 5.8 | 22.5 | 16.5 | 3.0 | 12.1 | 21.3 | 9.7 % / 14.9 % |
   | 2022 | 15.0 | 40.4 | 6.3 | 18.7 | 47.7 | 7.1 | 9.3 % / 14.6 % |
   | 2023 | 17.6 | 41.2 | 3.4 | **33.0** | **69.5** | **1.7** | 7.6 % / 17.7 % |
   | 2024 | **20.3** | 44.0 | 3.1 | 20.3 | 48.7 | 2.7 | 3.1 % / 7.6 % |
   | 2025 (Jan–Oct) | 6.2 | 26.4 | 5.5 | 20.9 | 55.4 | 2.0 | 3.2 % / 3.3 % |
   | Val (aggregate) | 12.9 | 46.3 | – | 23.7 | 56.3 | – | – |

   Worst months: ETH 2023-08 (54 % zero-vol), 2023-09 (48 %), 2023-10 (44 %), 2023-07 (39 %); XRP 2024-10 (41 %),
   2024-09 (35 %), 2024-06 (30 %). Intraday (2023+): zero-vol share peaks at KST 03–05 (XRP 29–30 %, ETH 39–40 %) vs
   8–13 % (XRP) / 15–23 % (ETH) at KST 09–23. Consequences: (a) `bars.complete` (B ≤ 5 m) is false much more often on
   ETH → fewer eligible decisions, entries biased to active periods (the same caveat as BTC, stronger); (b) the
   gap-warm-up rule fires after many more ≥ 30 min filled runs on ETH (52 vs 32 for BTC); (c) per-asset trade counts
   in pooled campaigns will be uneven — per-asset rows are reported in every campaign report for this reason.
3. **Coinone wicks: the venue k = 0.01 also applies to XRP/ETH, but there it clips more real moves.** Wick outliers
   checked against the other venue's same-minute range (venue-specific = outside it by > 1.5 %):

   | series | k = 3 % flagged / venue-specific | k = 2 % | k = 1 % |
   |---|---|---|---|
   | Coinone XRP | 43 / 41 | 111 / 96 | 518 / 192 (37 %) |
   | Coinone ETH | 9 / 9 | 28 / 26 | 204 / 81 (40 %) |
   | Upbit XRP | 20 / 16 | 76 / 48 | 534 / 170 |
   | Upbit ETH | 6 / 6 | 21 / 17 | 150 / 57 |
   | (BTC Coinone, 2026-11-09) | 13 / 13 | – | 106 / 103 (97 %) |

   For BTC almost every 1 % Coinone wick was a bad print; for XRP/ETH ~60 % of 1 % wicks are real moves (the other
   venue traded there too). **No parameter change was made**: wick parameters are per venue (`quality.wick_params`),
   the class ① rule allows only stricter changes automatically, and no stricter change is warranted — k = 0.03 on
   Upbit already leaves only 16 (XRP) / 6 (ETH) venue-specific prints in 4.8 years. A **looser** per-symbol Coinone
   k (e.g. 0.02 for XRP/ETH: catches 96/111 and 26/28 venue-specific) would need human approval and per-(venue,
   symbol) plumbing in `quality.wick_params` + `config/params.yaml`; it is an open item, not a blocker (clipping is
   applied identically in research and paper).
   Examples (Coinone vs Upbit, same minute): XRP 2021-02-01 13:44 low 423 vs 457, 2021-02-01 13:34 low 501 vs 530;
   ETH 2024-03-05 20:38 high 5.10 M vs 4.86 M, 2024-07-05 14:12 low 4.063 M vs 4.227 M.
4. **XRP has many genuine extreme minutes.** Dev |r| > 5 %: 34 (Coinone) / 45 (Upbit), > 2 %: 452 / 579 (BTC Upbit:
   6 / 56). They cluster in 2021 (2021-01-30..02-01 pump, 2021-04) and 2024-11/12. Real data — do not clean; they
   will dominate tail statistics and outlier-removal robustness checks for XRP sleeves.
5. **Maintenance windows are exchange-wide.** Upbit ≥ 30 min gaps: XRP 40 (7,431 min), ETH 41 (7,462 min) — the same
   windows as BTC (40/41 and 41/41 overlap; KST 02:01 quarter-/year-end pattern). Coinone zero-volume flat runs
   ≥ 30 min: XRP 34 (32 = all BTC maintenance windows + 2 thin-liquidity runs), ETH 52 (all 32 BTC windows + 20
   liquidity-driven runs, mostly 2023-08/09 night hours). Coinone kept trading in 72.9 % (XRP) / 44.7 % (ETH) of the
   minutes Upbit was missing.
6. **Upbit ETH has 27,086 missing Dev minutes (98.93 % coverage)** — beyond the 7,462 maintenance minutes these are
   no-trade minutes in thin 2023 periods (Upbit creates no candle without trades); they become `filled` after
   `regularize`. Not a collector problem.
7. **Cross-venue structure is like BTC's.** Coinone − Upbit log close gap (both traded): XRP median −4.8 bp,
   p1 −71.7, p99 +28.5, |gap| > 20 bp in 19 % of minutes; ETH median −4.4 bp, p1 −69.2, p99 +16.1, > 20 bp 12 %.
   Negative skew (Coinone discount in fast markets), 2021 widest (XRP |gap| > 20 bp 46 %), 2025 tight (≈ 1 %).
   1m return correlation at lag 0: XRP 0.53–0.82, ETH 0.64–0.83 (rising to 2025); Upbit(t−1)→Coinone(t) 0.05–0.20
   (XRP 2024: 0.198), Coinone(t−1)→Upbit(t) ≤ 0.08 → Upbit leads, as for BTC. Coinone/Upbit 1m volatility ratio
   XRP 0.66–0.95, ETH 0.82–1.03 (Coinone smoother/slower in older years).
8. **2024-12-03 martial-law dislocation** also appears in ETH (Coinone low 4.30 M vs Upbit 4.50 M at 14:33, high 4.64 M
   vs 4.18 M at 14:05) — already a `*`-venue window in `quality.AUDIT_EXCLUSION_WINDOWS` (optional mask), so it
   covers XRP/ETH too.

## Recommendations / decisions needed
- **Human (cost registry):** add per-asset cost models `coinone_{base,conservative,stress}@XRP-KRW` and `@ETH-KRW`
  (and Upbit grade models) before running 3-asset campaigns; until then pooled results flatter XRP/ETH.
- Optional, human: per-symbol Coinone wick k (0.02) for XRP/ETH (looser → class ① human approval).
- Report per-asset rows (now automatic) and per-year results for every 3-asset candidate; treat Coinone ETH 2023
  and XRP 2024-H2 like BTC 2023 (thin-liquidity years).
- Collector: the running live collector subscribes to BTC only; restart it with
  `--symbols BTC-KRW,XRP-KRW,ETH-KRW` to start the XRP/ETH trade/orderbook record (Forward data, fill-model and
  spread calibration).
