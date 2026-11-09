# Execution study — Upbit→Coinone 1m cross-venue signal (live WS data)

Generated 2026-11-09 23:22 UTC by `python -m btc_quant.research.execution_study`. Forward segment only (Forward start 2026-11-09 00:00:00+00:00); lockbox / Val not read. Not a ledger trial (measurement of execution, no new hypothesis test).

**Verdict: INSUFFICIENT_DATA** — only 0 measurable live events (< 30); preliminary entry shortfall vs the backtest open – bp against the model's 2.0 bp, executable 5-min net – bp; unconditional live minutes 0.45 [0.27, 0.62] bp (n = 22). Re-run on more hours.

> **No eligible minutes:** every live decision minute in the window was removed by the template's filter blocks (`session_kst all_but_quiet` excludes KST 03–08 = 18:00–24:00 UTC). The pre-registered signal can only fire on data collected 00:00–18:00 UTC; the diagnostics section measures the same quantities without the filter.

## Data

- Window: since 2026-11-09 22:48:00+00:00 until 2026-11-09 23:23:00+00:00; events evaluated in 2026-11-09 22:49:00+00:00 .. 2026-11-09 23:23:00+00:00; eligible decision minutes (session filter etc.) 0 (= 0.0 h); before the filter blocks (session_kst) 22 minutes.
- Template `research/campaign_templates/screens/c1_xvenue_lag_1m.yaml` (8 configs), declustering 30 min, cost `coinone_conservative` v-current: spread 2 bp + slip 1 bp/side → entry-side charge 2.0 bp, round trip 4.0 bp.
- Latencies L = 0.5, 1, 2 s (primary 1 s); notional 300,000 KRW; bars via MinuteBarBuilder (grace 2 s), REST warm-up on.

| venue | trades (used / late / dup) | live minutes (real bars) | REST seed bars | book snapshots (median / p95 interval s) | recv−exchange clock median s | collector gaps |
|---|---|---|---|---|---|---|
| coinone | 7096 (6932 / 0 / 0) | 26 (26) | 300 | 1417 (1.09 / 1.48) | 0.113 | 0 |
| upbit | 3516 (3495 / 0 / 0) | 26 (26) | 300 | 1487 (1.10 / 1.30) | 0.181 | 0 |

## Signal events (per config, live)

| config | eligible min | raw triggers | events (declustered) |
|---|---|---|---|
| `upbit_lead_impulse(k=1,thr_bp=10) & session_kst(session=all_but_quiet)` | 0 | 0 | 0 |
| `upbit_lead_impulse(k=1,thr_bp=20) & session_kst(session=all_but_quiet)` | 0 | 0 | 0 |
| `upbit_lead_impulse(k=3,thr_bp=10) & session_kst(session=all_but_quiet)` | 0 | 0 | 0 |
| `upbit_lead_impulse(k=3,thr_bp=20) & session_kst(session=all_but_quiet)` | 0 | 0 | 0 |
| `venue_gap_revert(window=60,z=2.0) & session_kst(session=all_but_quiet)` | 0 | 0 | 0 |
| `venue_gap_revert(window=60,z=3.0) & session_kst(session=all_but_quiet)` | 0 | 0 | 0 |
| `venue_gap_revert(window=240,z=2.0) & session_kst(session=all_but_quiet)` | 0 | 0 | 0 |
| `venue_gap_revert(window=240,z=3.0) & session_kst(session=all_but_quiet)` | 0 | 0 | 0 |

Pooled (union over configs, one row per decision minute): **0 events**, 0 measurable (book present at t+1 s and minute-t open printed); lead family 0, gap family 0. Control: 0 matched minutes (0 measurable).

## Entry: backtest open (a) vs executable (b)

Cost model entry-side charge = 2.0 bp (half spread 1 + slip 1). Slippage (b − a) above that is shortfall the backtest does not charge.

| metric | events: mean [95% CI] (n) | p10 / p50 / p90 | control: mean [95% CI] (n) | events − control [95% CI] |
|---|---|---|---|---|
| first Coinone print in minute t, s after t | – (0) | – | – (0) | – |
| Coinone mid(t) vs last print (bar close), bp | – (0) | – | – (0) | – |
| backtest open (a) vs Coinone mid(t), bp | – (0) | – | – (0) | – |
| Coinone spread at t, bp | – (0) | – | – (0) | – |
| best ask @t+0.5s − a, bp | – (0) | – | – (0) | – |
| ask-walk VWAP 300k @t+0.5s − a, bp | – (0) | – | – (0) | – |
| best ask @t+1s − a, bp | – (0) | – | – (0) | – |
| ask-walk VWAP 300k @t+1s − a, bp | – (0) | – | – (0) | – |
| best ask @t+2s − a, bp | – (0) | – | – (0) | – |
| ask-walk VWAP 300k @t+2s − a, bp | – (0) | – | – (0) | – |
| half spread @t+1s, bp | – (0) | – | – (0) | – |
| walk VWAP − best ask @t+1s, bp | – (0) | – | – (0) | – |
| age of the snapshot used @t+1s, s | – (0) | – | – (0) | – |

## Catch-up (Coinone book after the signal)

| metric | events: mean [95% CI] (n) | p10 / p50 / p90 | control: mean [95% CI] (n) | events − control [95% CI] |
|---|---|---|---|---|
| book deficit D0 (Coinone cheap vs Upbit, vs [t−20,t−5] min median), bp | – (0) | – | – (0) | – |
| share with D0 < 1 bp (book already caught up at t) | – (0) | – | – (0) | – |
| catch-up 50 % time, s (uncensored) | – (0) | – | – (0) | – |
| catch-up 90 % time, s | – (0) | – | – (0) | – |
| share not 50 % caught up by 300 s | – (0) | – | – (0) | – |
| deficit share closed by t+0.5s | – (0) | – | – (0) | – |
| deficit share closed by t+1s | – (0) | – | – (0) | – |
| deficit share closed by t+2s | – (0) | – | – (0) | – |
| Upbit mid return t−60s→t, bp | – (0) | – | – (0) | – |
| Coinone mid return t−60s→t, bp | – (0) | – | – (0) | – |

Mean mid paths (bp vs the mid at t; s relative to the decision time t):

| s | Coinone mid (events) | Upbit mid (events) | deficit closure (events, median) | Coinone mid (control) |
|---|---|---|---|---|
| -60 | – | – | – | – |
| -30 | – | – | – | – |
| -10 | – | – | – | – |
| -5 | – | – | – | – |
| 0 | – | – | – | – |
| 1 | – | – | – | – |
| 2 | – | – | – | – |
| 5 | – | – | – | – |
| 10 | – | – | – | – |
| 20 | – | – | – | – |
| 30 | – | – | – | – |
| 60 | – | – | – | – |
| 120 | – | – | – | – |
| 180 | – | – | – | – |
| 300 | – | – | – | – |

## Forward returns

| metric | events: mean [95% CI] (n) | p10 / p50 / p90 | control: mean [95% CI] (n) | events − control [95% CI] |
|---|---|---|---|---|
| h=1m backtest gross (open→close), bp | – (0) | – | – (0) | – |
| h=1m backtest net of cost v2, bp | – (0) | – | – (0) | – |
| h=1m executable entry (walk @t+1s) + model exit, bp | – (0) | – | – (0) | – |
| h=1m executable both sides (ask walk → bid walk), bp | – (0) | – | – (0) | – |
| h=1m Coinone mid→mid (no cost), bp | – (0) | – | – (0) | – |
| h=3m backtest gross (open→close), bp | – (0) | – | – (0) | – |
| h=3m backtest net of cost v2, bp | – (0) | – | – (0) | – |
| h=3m executable entry (walk @t+1s) + model exit, bp | – (0) | – | – (0) | – |
| h=3m executable both sides (ask walk → bid walk), bp | – (0) | – | – (0) | – |
| h=3m Coinone mid→mid (no cost), bp | – (0) | – | – (0) | – |
| h=5m backtest gross (open→close), bp | – (0) | – | – (0) | – |
| h=5m backtest net of cost v2, bp | – (0) | – | – (0) | – |
| h=5m executable entry (walk @t+1s) + model exit, bp | – (0) | – | – (0) | – |
| h=5m executable both sides (ask walk → bid walk), bp | – (0) | – | – (0) | – |
| h=5m Coinone mid→mid (no cost), bp | – (0) | – | – (0) | – |

## Diagnostics outside the pre-registered selection

Not part of the verdict. (i) **baseline** = every eligible live decision minute with no entry and no filter block (the generic gap between the backtest's Coinone 1m open and an executable buy); (ii) **unfiltered events** = the template's 8 entry configs with the `session_kst` filter dropped (so KST 03–08 minutes count), pooled and declustered the same way. These describe microstructure, not the pre-registered signal's edge.

Unfiltered: 0 per-config events (0 raw triggers) → 0 pooled, 0 measurable. Baseline: 22 minutes, 22 measurable.

| metric | unfiltered events: mean [95% CI] (n) | p10 / p50 / p90 | baseline: mean [95% CI] (n) | unfiltered events − baseline [95% CI] |
|---|---|---|---|---|
| first Coinone print in minute t, s after t | – (0) | – | 3.71 [1.70, 6.37] (22) | – |
| Coinone mid(t) vs last print (bar close), bp | – (0) | – | 0.07 [-0.16, 0.27] (22) | – |
| backtest open (a) vs Coinone mid(t), bp | – (0) | – | 0.11 [-0.09, 0.31] (22) | – |
| Coinone spread at t, bp | – (0) | – | 1.02 [0.98, 1.11] (22) | – |
| ask-walk VWAP @t+0.5s − a, bp | – (0) | – | 0.40 [0.22, 0.58] (22) | – |
| ask-walk VWAP @t+1s − a, bp | – (0) | – | 0.45 [0.27, 0.62] (22) | – |
| ask-walk VWAP @t+2s − a, bp | – (0) | – | 0.36 [0.13, 0.58] (22) | – |
| best ask @t+1s − a, bp | – (0) | – | 0.45 [0.27, 0.62] (22) | – |
| walk VWAP − best ask @t+1s, bp | – (0) | – | 0.00 [-0.00, 0.00] (22) | – |
| book deficit D0, bp | – (0) | – | -2.66 [-5.10, -0.24] (10) | – |
| catch-up 50 % time, s | – (0) | – | 37.00 [9.00, 81.00] (3) | – |
| deficit share closed by t+1s | – (0) | – | 0.04 [0.00, 0.11] (3) | – |
| h=5m backtest net, bp | – (0) | – | -6.80 [-10.53, -3.55] (22) | – |
| h=5m executable both sides net, bp | – (0) | – | -3.62 [-7.38, -0.37] (22) | – |
| h=5m Coinone mid→mid, bp | – (0) | – | -2.58 [-6.32, 0.67] (22) | – |

## Verdict and what more data is needed

- Status **INSUFFICIENT_DATA** with n = 0 measurable pooled events (verdict needs ≥ 30).
- Entry shortfall vs backtest open at L = 1 s: – bp (control – bp). The model already charges 2.0 bp, so the uncharged extra is – bp vs the Dev excess of 1.3–2.9 bp.
- Fully executable net at 5 min: – bp vs backtest-style net – bp on the same events.
- Unconditional live minutes (baseline): entry shortfall 0.45 [0.27, 0.62] bp (n = 22).
- Event rate 1.00 pooled events per eligible hour (Dev prior from fact-000001, live rate unknown); dispersion from baseline (sd shortfall 0.50 bp, sd net 8.66 bp). For a ±0.5 bp CI on the entry shortfall: n ≈ 4 (≈ 4 eligible hours). To detect a +2 bp executable net edge (80 % power): n ≈ 148 (≈ 148 eligible hours).

## Caveats

- Book snapshots are sampled ≤ 1/s by the collector (receive clock); sub-second latencies (0.5 s) are therefore resolved only to the last snapshot received, typically 0–1 s old (see snapshot age row). The paper trader additionally waits the 2 s bar-close grace, so its real latency is ≈ 2 s + L.
- Top-15 depth only; a walk that runs out of visible depth is reported as missing (never happened if the n matches).
- Events within a config are ≥ the declustering gap apart, but pooled events from different configs can be close in time; bootstrap CIs are iid and therefore somewhat optimistic.
- Short live window: one regime / time of day; Dev excess numbers come from 2021–2025.


## Re-run

```
uv run python -m btc_quant.research.execution_study --since 2026-11-09T22:48   # --until, --latencies, --notional, --no-rest-warmup, --events-csv
```
