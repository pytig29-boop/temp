# Limit-order fill model — candle trade-through rule vs queue simulation (live WS data)

Generated 2026-11-10 04:25 UTC by `python -m btc_quant.research.fill_model`. Forward segment only (Forward start 2026-11-09 00:00:00+00:00); lockbox / Val not read. Not a ledger trial (execution measurement; no strategy hypothesis tested).

**Data status: PROVISIONAL** — 7 UTC hours observed (7 distinct KST hours, 2 day(s)); final needs ≥ 72 h, ≥ 24 KST hours, ≥ 7 days (cost-calibration thresholds). Every number below is preliminary and comes from one short regime.

**Recommendation (class ①, proposal only):** `LimitFillModel(through_ticks=1, through_bp=0, fill_prob=1, tick_from_data=True)` — basis `1tick`, timed precision lower bound 98.9 %; current default `LIMIT_FILL_DEFAULT` through_ticks=1, through_bp=0, fill_prob=1 → **equal**. No params-registry entry exists for the fill model, so nothing is auto-applied.

**Key findings (preliminary):**

- Touch rule is optimistic: only 74.3 % [70.5, 77.7] (n = 3071) of touch-rule fills had really filled by the end of that minute (queue ahead of us).
- `1tick` trade-through (engine default): precision 99.4 % [98.9, 99.9] (n = 2654), recall 96.9 % [95.7, 98.0] (n = 2730) of real (conservative-queue) fills.
- Adverse selection (TIF 15): real maker fills mark out -0.90 bp at 1 min and -0.43 bp at 5 min vs unconditional drift -0.01 bp; the candle rule's fills as the backtest sees them -1.35 / -0.54 bp (more adverse = conservative if lower).
- Real fills the `1tick` rule leaves out mark out 8.08 [3.74, 10.93] (24) bp at 5 min (positive = omitting them is conservative: they are touch-and-bounce fills, the maker's best ones).

## Data

- Exchange coinone; window 2026-11-09 22:49:00+00:00 .. 2026-11-10 04:23:00+00:00 (335 live minutes, 291 with trades); 79,276 trades, 15,551 book snapshots (top 15 levels, ≤ 1/s); collector gaps 6.
- Tick inferred from trade prices: **10,000 KRW** (≈ 0.98 bp at the median price 101,770,000).
- Side sanity: taker-sell prints at/below the prevailing best bid 87.1 %, taker-buy at/above the best ask 87.9 % (book snapshot ≤ 1 s old; values < 100 % are mostly the 1 s snapshot lag).
- Hypothetical orders: 276 placement minutes × 2 sides × 5 offsets (0, 2, 5, 10, 20 bp from the minute open, snapped to the tick) × TIF 1, 5, 15 min; latency 1 s after the first trade of the minute; order size 300,000 KRW. Mean-reversion subset: 20 buy placements at `bollinger_z_revert(window=20, z=2.0) on 1m bars` signal minutes.
- Queue position at placement: cross 269, deep 427, gap 239, inside 16, level 1809 (cross = marketable at placement, excluded from the precision statistics; deep/none = level not visible yet — queue set when it first appears).
- CIs: 95 % block bootstrap over 30-min blocks of placement time (orders overlap; iid CIs would be too narrow).

## Fill probability by side, offset and TIF (%)

| side | offset bp | TIF | n | touch | 1 tick | 2 ticks | 1 bp | 1.5 bp | 2 bp | 3 bp | 5 bp | queue (cons.) | queue (opt.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| buy | 0 | 1 | 125 | 100.0 | 71.2 | 52.8 | 52.8 | 52.8 | 36.0 | 19.2 | 7.2 | 69.6 | 72.0 |
| buy | 0 | 5 | 125 | 100.0 | 91.2 | 84.0 | 84.0 | 84.0 | 76.8 | 67.2 | 46.4 | 90.4 | 91.2 |
| buy | 0 | 15 | 125 | 100.0 | 96.0 | 93.6 | 93.6 | 93.6 | 87.2 | 83.2 | 72.8 | 95.2 | 96.0 |
| buy | 2 | 1 | 276 | 43.5 | 27.2 | 18.5 | 18.5 | 18.5 | 13.8 | 9.4 | 3.6 | 30.1 | 32.2 |
| buy | 2 | 5 | 276 | 76.4 | 69.6 | 59.4 | 59.4 | 59.4 | 50.7 | 44.6 | 33.0 | 71.0 | 72.5 |
| buy | 2 | 15 | 276 | 88.0 | 84.8 | 79.0 | 79.0 | 79.0 | 74.6 | 69.9 | 63.8 | 85.1 | 86.2 |
| buy | 5 | 1 | 276 | 13.8 | 9.4 | 6.5 | 6.5 | 6.5 | 3.6 | 1.8 | 1.1 | 11.2 | 12.0 |
| buy | 5 | 5 | 276 | 50.7 | 44.6 | 39.5 | 39.5 | 39.5 | 33.0 | 27.9 | 18.5 | 46.7 | 49.3 |
| buy | 5 | 15 | 276 | 74.6 | 69.9 | 66.7 | 66.7 | 66.7 | 63.8 | 60.9 | 51.4 | 71.4 | 72.8 |
| buy | 10 | 1 | 276 | 1.4 | 1.1 | 1.1 | 1.1 | 1.1 | 0.7 | 0.7 | 0.0 | 1.1 | 1.1 |
| buy | 10 | 5 | 276 | 21.0 | 18.5 | 16.7 | 16.7 | 16.7 | 14.9 | 12.7 | 10.1 | 20.3 | 20.7 |
| buy | 10 | 15 | 276 | 55.1 | 51.4 | 45.3 | 45.3 | 45.3 | 41.3 | 38.8 | 34.4 | 52.5 | 53.3 |
| buy | 20 | 1 | 276 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| buy | 20 | 5 | 276 | 4.3 | 3.3 | 1.8 | 1.8 | 1.8 | 1.1 | 0.7 | 0.0 | 3.6 | 3.6 |
| buy | 20 | 15 | 276 | 22.5 | 20.7 | 18.5 | 18.5 | 18.5 | 17.0 | 13.8 | 10.5 | 21.7 | 22.1 |
| sell | 0 | 1 | 159 | 100.0 | 64.8 | 42.8 | 42.8 | 42.8 | 32.7 | 24.5 | 11.9 | 68.6 | 73.0 |
| sell | 0 | 5 | 159 | 100.0 | 83.6 | 66.0 | 66.0 | 66.0 | 59.7 | 56.0 | 37.7 | 86.8 | 89.9 |
| sell | 0 | 15 | 159 | 100.0 | 88.1 | 76.1 | 76.1 | 76.1 | 73.0 | 70.4 | 54.1 | 90.6 | 91.2 |
| sell | 2 | 1 | 275 | 38.9 | 28.7 | 17.8 | 17.8 | 17.8 | 12.0 | 6.5 | 4.7 | 29.8 | 31.6 |
| sell | 2 | 5 | 275 | 65.5 | 58.9 | 46.2 | 46.2 | 46.2 | 37.8 | 32.0 | 22.9 | 59.6 | 61.1 |
| sell | 2 | 15 | 275 | 75.3 | 70.2 | 59.3 | 59.3 | 59.3 | 53.5 | 48.7 | 42.9 | 70.9 | 72.0 |
| sell | 5 | 1 | 276 | 12.3 | 6.9 | 5.8 | 5.8 | 5.8 | 5.1 | 2.9 | 1.1 | 7.6 | 8.7 |
| sell | 5 | 5 | 276 | 38.0 | 32.2 | 27.5 | 27.5 | 27.5 | 23.2 | 18.8 | 13.0 | 33.7 | 34.8 |
| sell | 5 | 15 | 276 | 53.6 | 48.9 | 46.0 | 46.0 | 46.0 | 43.1 | 39.9 | 33.3 | 50.0 | 51.1 |
| sell | 10 | 1 | 276 | 1.8 | 1.1 | 0.4 | 0.4 | 0.4 | 0.0 | 0.0 | 0.0 | 1.1 | 1.1 |
| sell | 10 | 5 | 276 | 15.9 | 13.0 | 9.8 | 9.8 | 9.8 | 8.7 | 7.6 | 4.3 | 14.9 | 15.9 |
| sell | 10 | 15 | 276 | 35.5 | 33.3 | 30.1 | 30.1 | 30.1 | 29.0 | 25.0 | 20.7 | 34.1 | 35.1 |
| sell | 20 | 1 | 276 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| sell | 20 | 5 | 276 | 3.3 | 3.3 | 2.5 | 2.5 | 2.5 | 1.8 | 1.8 | 0.7 | 3.3 | 3.3 |
| sell | 20 | 15 | 276 | 13.0 | 12.0 | 10.9 | 10.9 | 10.9 | 9.8 | 9.1 | 7.2 | 12.7 | 13.0 |

## Is the candle rule conservative? (all sides, offsets, TIFs pooled; non-crossing orders)

Precision = P(conservative-queue fill no later than the end of the candle-rule fill minute | candle fill) — should be ≈ 100 % for a safe backtest rule. Untimed = queue fill anywhere in the TIF. Recall = P(candle fill | queue fill): the share of real fills the rule books (the rest are left out).

| rule | candle fills | precision timed % | precision untimed % | recall % | fill rate % (candle / queue) |
|---|---|---|---|---|---|
| touch | 3071 | 74.3 [70.5, 77.7] (3071) | 88.9 [86.8, 90.4] (3071) | 100.0 [100.0, 100.0] (2730) | 41.1 / 36.5 |
| 1tick | 2654 | 99.4 [98.9, 99.9] (2654) | 99.7 [99.4, 99.9] (2654) | 96.9 [95.7, 98.0] (2730) | 35.5 / 36.5 |
| 2tick | 2262 | 100.0 [100.0, 100.0] (2262) | 100.0 [100.0, 100.0] (2262) | 82.9 [80.7, 85.0] (2730) | 30.3 / 36.5 |
| 1bp | 2262 | 100.0 [100.0, 100.0] (2262) | 100.0 [100.0, 100.0] (2262) | 82.9 [80.7, 85.0] (2730) | 30.3 / 36.5 |
| 1.5bp | 2262 | 100.0 [100.0, 100.0] (2262) | 100.0 [100.0, 100.0] (2262) | 82.9 [80.7, 85.0] (2730) | 30.3 / 36.5 |
| 2bp | 1998 | 100.0 [100.0, 100.0] (1998) | 100.0 [100.0, 100.0] (1998) | 73.2 [69.8, 76.5] (2730) | 26.7 / 36.5 |
| 3bp | 1758 | 100.0 [100.0, 100.0] (1758) | 100.0 [100.0, 100.0] (1758) | 64.4 [60.5, 68.2] (2730) | 23.5 / 36.5 |
| 5bp | 1364 | 100.0 [100.0, 100.0] (1364) | 100.0 [100.0, 100.0] (1364) | 50.0 [45.0, 54.1] (2730) | 18.3 / 36.5 |

By TIF (engine default rule `1tick`): TIF 1: precision 98.7 [97.6, 99.7] (397), recall 93.6 [90.5, 96.2] (419); TIF 5: precision 99.5 [98.9, 99.9] (918), recall 96.5 [95.1, 97.7] (949); TIF 15: precision 99.6 [99.3, 99.9] (1339), recall 98.2 [97.2, 99.1] (1362).

How the conservative queue fills happened: through 76.7 %, queue 23.3 % (through = a print beyond our price; queue = displayed size ahead of us traded at our price).

## Adverse selection — markouts after fills (bp, + = good for the maker; TIF 15)

| subset | 1 min | 5 min | 15 min |
|---|---|---|---|
| queue fills (from real fill time) | -0.90 [-1.29, -0.49] (1361) | -0.43 [-1.77, 0.62] (1348) | -0.23 [-2.73, 1.93] (1272) |
| candle `1tick` fills (from end of fill minute — backtest view) | -1.35 [-2.09, -0.71] (1337) | -0.54 [-1.88, 0.62] (1320) | -0.41 [-2.75, 1.52] (1254) |
| candle `1bp` fills (from end of fill minute — backtest view) | -2.45 [-3.28, -1.76] (1219) | -1.75 [-3.16, -0.52] (1205) | -1.57 [-4.39, 0.84] (1143) |
| candle `2bp` fills (from end of fill minute — backtest view) | -3.35 [-4.32, -2.51] (1140) | -2.63 [-4.22, -1.26] (1129) | -2.30 [-5.67, 0.50] (1074) |
| real fills the `1tick` rule leaves out (from real fill time) | 4.23 [2.31, 5.93] (23) | 8.08 [3.74, 10.93] (24) | 12.10 [5.53, 16.77] (23) |
| real fills the `1bp` rule leaves out (from real fill time) | 3.39 [2.91, 4.00] (142) | 9.56 [7.55, 11.97] (141) | 14.04 [11.19, 17.24] (128) |
| all placements: mid drift from τ (no fill) | -0.01 [-0.07, 0.04] (2473) | -0.01 [-0.18, 0.18] (2418) | 0.02 [-0.17, 0.18] (2329) |

Reading: a maker fill is adverse when its markout is below the unconditional drift. If the fills the candle rule leaves out have *negative* markouts, omitting them flatters the backtest (it skips losing fills); if positive, omitting them is conservative.

## Mean-reversion signal times (buy limits only)

| offset bp | TIF | n | touch % | 1 tick % | queue (cons.) % | precision `1tick` % | queue-fill markout 5 min bp |
|---|---|---|---|---|---|---|---|
| 0 | 1 | 13 | 100.0 | 92.3 | 84.6 | 91.7 [66.7, 100.0] (12) | -2.10 [-6.40, 3.88] (11) |
| 0 | 5 | 13 | 100.0 | 100.0 | 100.0 | 92.3 [70.0, 100.0] (13) | -3.37 [-7.79, 2.37] (13) |
| 0 | 15 | 13 | 100.0 | 100.0 | 100.0 | 92.3 [70.0, 100.0] (13) | -3.37 [-7.79, 2.37] (13) |
| 2 | 1 | 20 | 60.0 | 30.0 | 35.0 | 100.0 [100.0, 100.0] (6) | -4.43 [-11.33, 0.89] (7) |
| 2 | 5 | 20 | 90.0 | 90.0 | 90.0 | 100.0 [100.0, 100.0] (18) | -2.57 [-8.80, 2.59] (18) |
| 2 | 15 | 20 | 95.0 | 95.0 | 95.0 | 100.0 [100.0, 100.0] (19) | -2.49 [-8.34, 2.38] (19) |
| 5 | 1 | 20 | 10.0 | 5.0 | 5.0 | 100.0 (1) | -11.35 (1) |
| 5 | 5 | 20 | 65.0 | 50.0 | 55.0 | 100.0 [100.0, 100.0] (10) | -4.71 [-10.45, 1.59] (11) |
| 5 | 15 | 20 | 90.0 | 80.0 | 80.0 | 100.0 [100.0, 100.0] (16) | -3.27 [-8.50, 2.07] (16) |
| 10 | 1 | 20 | 0.0 | 0.0 | 0.0 | – (0) | – (0) |
| 10 | 5 | 20 | 15.0 | 15.0 | 15.0 | 100.0 [100.0, 100.0] (3) | -6.44 [-15.80, 12.29] (3) |
| 10 | 15 | 20 | 60.0 | 55.0 | 55.0 | 100.0 [100.0, 100.0] (11) | -2.47 [-9.58, 7.40] (11) |
| 20 | 1 | 20 | 0.0 | 0.0 | 0.0 | – (0) | – (0) |
| 20 | 5 | 20 | 0.0 | 0.0 | 0.0 | – (0) | – (0) |
| 20 | 15 | 20 | 20.0 | 20.0 | 20.0 | 100.0 (4) | -4.57 (4) |

## Recommendation detail

| rule | through depth | timed precision % [95 % CI] (n) |
|---|---|---|
| 1tick | max(1 tick, 0 bp) | 99.4 [98.9, 99.9] (2654) |
| 2tick | max(2 tick, 0 bp) | 100.0 [100.0, 100.0] (2262) |
| 1bp | max(1 tick, 1 bp) | 100.0 [100.0, 100.0] (2262) |
| 1.5bp | max(1 tick, 1.5 bp) | 100.0 [100.0, 100.0] (2262) |
| 2bp | max(1 tick, 2 bp) | 100.0 [100.0, 100.0] (1998) |
| 3bp | max(1 tick, 3 bp) | 100.0 [100.0, 100.0] (1758) |
| 5bp | max(1 tick, 5 bp) | 100.0 [100.0, 100.0] (1364) |

Rule: the least strict margin whose lower bound is ≥ 95 % → fill_prob 1; otherwise the strictest with fill_prob = its lower bound (floored to 0.05). The engine reads the tick from the 1m data per UTC day (`signals.infer_ticks`; Coinone BTC 1,000 → 10,000 KRW on 2024-09-29/30), so `through_ticks` is exact in every period; a bp-only margin (tick_from_data=False) must stay ≥ 1 tick (≥ 1.25 bp for Coinone BTC since 2024-09-30, min price 80.6 M KRW; XRP/ETH ticks are larger in bp) or it admits touch fills.

## Paper-path replay (ReferenceEngine: candle rule vs QueueFillOracle)

Toy 1m maker strategy over the live window: buy limit 5 bp below the minute open, TIF 5 min, TP limit +10 bp, SL −30 bp (market), hold ≤ 30 min, zero cost (fill logic only). `queue` = the same engine with `fill_oracle = QueueFillOracle` — the hook the paper trader uses.

| entries | rule | trades | mean net bp | win % | TP exits | same entry minutes |
|---|---|---|---|---|---|---|
| grid_5bp | candle | 11 | -5.08 | 54.5 | 5 | 9 |
| grid_5bp | queue | 12 | -3.84 | 50.0 | 6 | 9 |
| mr_signal_5bp | candle | 3 | -2.79 | 66.7 | 2 | 3 |
| mr_signal_5bp | queue | 4 | -0.13 | 75.0 | 2 | 3 |

## Limitations

- A few hours of one regime; hour-of-day, volatility and weekend effects are not sampled. CIs are block bootstrap over 30-min blocks — with few blocks they are themselves unstable.
- Book snapshots ≤ 1/s and top-15 levels: queue ahead is read from the last snapshot before τ (orders that joined in that ≤ 1 s are not counted ahead of us — slightly optimistic); levels deeper than 15 are unknown until visible. Hidden / iceberg liquidity and self-trade rules are not modelled.
- Trades are stamped with exchange time, books with receive time (median offset ≈ 0.1 s).
- Coinone volume includes bot-like micro prints (pitfall P-D1); they consume queue as any trade does.
- The candle comparison uses the prepared bars the backtest sees (wick cleaning can only remove extreme lows/highs → fewer candle fills, conservative).
- Our own order would add to displayed liquidity and could change others' behaviour; ignored.

## Re-run

```
uv run python -m btc_quant.research.fill_model --report research/reports/fill_model_<date>.md
```
