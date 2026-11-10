# Campaign camp-000030 — Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 1h: atr_channel_breakout(24,3) with half-size entries (entry_fraction 0.5) and ATR exits (SL 2 ATR x TP 1.5/3 ATR, max hold 480/960 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev and the robustness suite.

Date (UTC) 2026-11-10T07:09:22+00:00 · hypothesis hyp-000029 (registered 2026-11-10T07:09:17+00:00) · campaign registered 2026-11-10T07:09:17+00:00 · family `vol_breakout` · data snapshot `snap-93b066dd27ee41f0a0ec|XRP-KRW:snap-b636892dd83e17554616|ETH-KRW:snap-b46925c3bb3e2bfd5239` · code `e6cc86bb960b9e9900074963077957ff1b4df0bf+dirty`

**Verdict: no candidate passed.**

> Agent-safe report: Dev detail + Val (G6) / lockbox **outcome codes only**. The full human report is `research/reports/human/campaign_camp-000030.md` (human-only; not readable in research mode).

## Hypothesis (pre-registered)

- Text: Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 1h: atr_channel_breakout(24,3) with half-size entries (entry_fraction 0.5) and ATR exits (SL 2 ATR x TP 1.5/3 ATR, max hold 480/960 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev and the robustness suite.
- Rationale: Mechanism M3 (1h time-series momentum; stop cascades, short liquidations, late trend followers). Counterparty: counter-trend limit sellers, stopped-out shorts, late chasers at exit. Kills: bear regimes (2022), per-asset cost (D17), drift confound (int-000009/int-000011), regime concentration. Structural change vs the paused BTC-only line: the fixed 3-asset universe (D15) - same parameters, ~3x positions, pooled equal-sleeve evaluation. Caveat stated before the run: the BTC sleeve re-uses a rule family whose BTC-only versions carry taxonomy VAL_FAIL (fact-000013/fact-000016); a skeptic must weigh that the BTC sleeve is not new evidence. Partial sizing entry_fraction 0.5 (user request, D16): size does not change the edge gates (G2/G5) and halves drawdowns. Exits fixed by the pre-screen plan (scratchpad PLAN_prescreen.txt): sl_atr = screen MAE-p75 suggestion rounded to 0.5, tp_atr = {MFE-median suggestion rounded to 0.5, 2x}, max hold {info horizon 480, 960}. MTM gate returns (D18). Evidence (Dev only; lockbox none, Val none): screen camp-000027 (drv-000019): pooled n 3225 (BTC 1165, XRP 862, ETH 1198), p 0.0034, excess 21.8 bp z 3.06 @480 (peaked), net +23.5 bp (Upbit +10.7); MFE median 1.37 ATR, |MAE| p75 2.16 ATR (1h ATR median 0.86 %). Plausible Sharpe z/sqrt(4.83 y) = 1.39; required ~1.28 (N_dsr ~12); target 1.35. Predicted failures: G2 (drift in the null), regime share, G4 recent, PBO. [derived-from drv-000022 ← fact-000024; family vol_breakout]
- Success criteria: At least one spec passes G0-G5, ablation, G6 (outcome code) and the Dev robustness suite. Refuted as a tradeable edge if no spec passes G2 or the best Dev Sharpe_w (MTM) < 1.0.

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 7,624,800 1m rows (filled 13.29%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- **Universe ['BTC-KRW', 'XRP-KRW', 'ETH-KRW']** (PLAN D15): the same spec on every asset, pooled as ONE strategy — equal fixed sleeves (1/N), independent position per asset, portfolio daily return = mean of sleeve returns; gates use the portfolio series and pooled trades (G1 = sum over assets); 1 ledger trial per spec. Per-asset rows below are information only.
  - BTC-KRW: 2,541,600 1m rows (filled 7.56%)
  - XRP-KRW: 2,541,600 1m rows (filled 13.21%)
  - ETH-KRW: 2,541,600 1m rows (filled 19.10%)
- Entry `atr_channel_breakout`: {'span': [24], 'mult': [3.0]}
- Filter set 0: (no filter)
- Decision timeframe **1h**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [2.0], 'tp_atr': [1.5, 3.0], 'max_hold_min': [480, 960]} → 4 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress` — per-asset models where defined (PLAN D17): BTC-KRW: coinone_conservative, upbit_conservative; XRP-KRW: coinone_conservative@XRP-KRW, upbit_conservative@XRP-KRW; ETH-KRW: coinone_conservative@ETH-KRW, upbit_conservative@ETH-KRW
- Daily returns for gates (PLAN D18): **mark-to-market** — open inventory marked at each UTC day close (liquidation value incl. costs); G2–G6, Monte Carlo MDD95 (with intra-position MTM troughs), robustness and §7 limits use it
- Specs: **4** (template budget 4); runs recorded: 8; runtime 4.79s

## Signal screen linkage

- Gate: every signal config passed camp-000027 (every (entry params × filter params × timeframe × exchange × universe) config passed a recorded screen); 1 configs matched:
  - atr_channel_breakout{'span': 24, 'mult': 3.0} ← screen `camp-000027` run `run-000214` (p_signal 0.0034, events 3225, info horizon 480)

## Power check (at registration)

- 계획 격자 4 스펙, 계열 `vol_breakout` 기존 시도 88, 압축비 0.125 (history median of 10 campaigns) → 실질 시도 ≈ 11.5
- Dev 1765일, V=0.000283 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 1.28** (시도 1회였다면 0.75)
- 목표 연 Sharpe 1.35 로 감당 가능한 실질 시도 ≈ 19 (격자 ≈ 62 스펙)
- Required annual Sharpe stored: **1.28**; target 1.35; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.span | entry.mult | sizing.entry_fraction | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | n_positions | recent_n_trades | expectancy | max_dd | rolling_frac_positive | exposure_days | worst_day | cvar5_daily | max_inventory |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c4_vol_breakout_atrch_1h_u3_half_4fc37395fd | - | 24 | 3 | 0.500 | 2 | 3 | 480 | 0.983 | 0.779 | 1.570 | 1773 | 1773 | 365 | 0.001 | 0.088 | 0.698 | 0.490 | -0.028 | -0.008 | 0.500 |
| c4_vol_breakout_atrch_1h_u3_half_c91a160641 | - | 24 | 3 | 0.500 | 2 | 3 | 960 | 0.979 | 0.983 | 1.248 | 1618 | 1618 | 341 | 0.002 | 0.096 | 0.717 | 0.507 | -0.025 | -0.009 | 0.500 |
| c4_vol_breakout_atrch_1h_u3_half_98e9e3cdc0 | - | 24 | 3 | 0.500 | 2 | 1.500 | 960 | -0.450 | 0.092 | -0.281 | 1970 | 1970 | 427 | 0.000 | 0.199 | 0.377 | 0.499 | -0.031 | -0.009 | 0.500 |
| c4_vol_breakout_atrch_1h_u3_half_303ea40cb9 | - | 24 | 3 | 0.500 | 2 | 1.500 | 480 | -0.627 | -0.103 | -0.113 | 2064 | 2064 | 444 | -0.000 | 0.219 | 0.377 | 0.487 | -0.025 | -0.008 | 0.500 |

Per-asset breakdown of the top specs (Dev, primary cost; information only — gates use the pooled portfolio):

| spec_id | asset.BTC-KRW.n_trades | asset.BTC-KRW.sharpe_w | asset.BTC-KRW.total_return | asset.XRP-KRW.n_trades | asset.XRP-KRW.sharpe_w | asset.XRP-KRW.total_return | asset.ETH-KRW.n_trades | asset.ETH-KRW.sharpe_w | asset.ETH-KRW.total_return |
|---|---|---|---|---|---|---|---|---|---|
| c4_vol_breakout_atrch_1h_u3_half_4fc37395fd | 610 | 0.946 | 0.733 | 536 | 0.203 | -0.043 | 627 | 1.135 | 0.376 |
| c4_vol_breakout_atrch_1h_u3_half_c91a160641 | 546 | 1.138 | 0.997 | 509 | 0.263 | -0.083 | 563 | 0.934 | 0.767 |
| c4_vol_breakout_atrch_1h_u3_half_98e9e3cdc0 | 686 | 0.443 | 0.424 | 593 | -1.127 | -0.503 | 691 | 0.380 | 0.402 |
| c4_vol_breakout_atrch_1h_u3_half_303ea40cb9 | 727 | 0.238 | 0.322 | 606 | -1.151 | -0.451 | 731 | 0.119 | 0.140 |

Buy & hold over the same Dev period (equal-weight 3-asset buy & hold (BTC-KRW, XRP-KRW, ETH-KRW; same venue 1m close, daily-rebalanced sleeves like the strategy, no cost); information only): total return 11.453, Sharpe 1.148, MDD 0.730.

Distribution of weighted Sharpe over all 4 specs: median 0.264, share > 0 50.0%, max 0.983.

## Funnel

- All specs (metric-only gates): 4 specs → G1 4, weighted Sharpe > 0 2, G4 2, all three 2
- Top 1 candidates: G0 1, G1 1, G2 1, G3 0, G4 1, G5 0 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=4, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 1.7
- Ledger trials: family 92, global 174 → DSR trials N = max(K, N_family·K/N) = 23; V = 0.000754 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.001

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 4 specs → 1 clusters (largest sizes [4]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| 4fc37395fd5a8a50 | 1 | 4 | 0.983 |

## Candidates — per-gate results

### #1 `c4_vol_breakout_atrch_1h_u3_half_4fc37395fd` — failed G3 (ledger only)

```yaml
id: c4_vol_breakout_atrch_1h_u3_half_4fc37395fd
entry:
  block: atr_channel_breakout
  params:
    span: 24
    mult: 3.0
exit:
  max_hold_min: 480
  signal_exit: null
  stop_loss_atr: 2.0
  take_profit_atr: 3.0
filters: []
version: 1
timeframe: 1h
universe:
- BTC-KRW
- XRP-KRW
- ETH-KRW
sizing:
  entry_fraction: 0.5
```
Weighted Sharpe 0.983, Sharpe 0.779, trades 1773, expectancy 0.00106, MDD 0.088

- **G0 PASS** — validate=ok, timeframe=1h, exit_mode=atr, n_filters=0, n_params=5, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=9, causality_violations=[]
- **G1 PASS** — n_trades=1773, recent_n_trades=365
- **G2 PASS** — sharpe_w=0.9827, p_value=0.0030, percentile=0.9980, n_null=1000, calibration=trade_count, p_signal_rate={'BTC-KRW': 0.04557621698448419, 'XRP-KRW': 0.031745279666437656, 'ETH-KRW': 0.04683574306707751}, p={'BTC-KRW': 0.016827037218924534, 'XRP-KRW': 0.01519969197763721, 'ETH-KRW': 0.01885712784979691}, tf_min=60, atr_bars=14, n_trades_median=1787, candidate_n_trades=1773, sharpe_w_median=-0.5844, sharpe_w_p95=0.3904, cost=coinone_conservative
- **G3 FAIL** — selected=0.9827, neighbor_median=0.1761, required=0.4914, n_neighbors=2
- **G4 PASS** — rolling_frac_positive=0.6981, rolling_n_windows=53, recent_sharpe=1.5698
  - exposure-aware variant (shadow, OFF — information only): applicable=✓, rolling_n_active=53, rolling_n_neutral=0, rolling_frac_positive_active=0.6981, exposure_days=0.4901, via=base, passed=✓
- **G5 FAIL** — dsr=0.2763, pbo=0.0014, sr=0.0408, sr0=0.0539, n_trials=23, var_trial_sr=0.0008
- G3 neighbors: tp_atr=1.5→-0.63, max_hold_min=960→0.98

Buy & hold reference (Dev, primary cost; information only — not a gate):

| | strategy | EW 3-asset buy & hold |
|---|---|---|
| Total return | 0.352 | 11.453 |
| Sharpe | 0.779 | 1.148 |
| Max drawdown | 0.088 | 0.730 |

- time in market (exposure_time) 0.038; days in market (exposure_days) 0.490; exposure-matched B&H return 0.137; excess vs matched B&H 0.215; excess vs B&H -11.102; up-day capture 0.039; down-day capture 0.028; up-regime return (trailing 30d BTC > 0) 0.304; down-regime return (trailing 30d BTC ≤ 0) -0.023; up-regime capture 0.090; down-regime capture 0.089; downside deviation (ann.) 0.043; rolling 6m windows neutral / active 0 / 53 (neutral = exposure_days < 0.10)
- Worst B&H drawdown episodes: #1 2021-11-09→2022-06-18 B&H −0.730 → strategy 0.023; #2 2021-05-09→2021-07-20 B&H −0.572 → strategy -0.020; #3 2025-01-18→2025-04-08 B&H −0.425 → strategy -0.004

Per-asset (Dev, primary cost; information only — gates use the pooled portfolio):

| asset | n_trades | recent_n_trades | sharpe_w | sharpe | total_return | max_dd | expectancy | win_rate |
|---|---|---|---|---|---|---|---|---|
| BTC-KRW | 610 | 114 | 0.946 | 1.387 | 0.733 | 0.065 | 0.002 | 0.505 |
| XRP-KRW | 536 | 124 | 0.203 | 0.042 | -0.043 | 0.386 | 0.000 | 0.431 |
| ETH-KRW | 627 | 127 | 1.135 | 0.630 | 0.376 | 0.131 | 0.001 | 0.469 |

Tail risk (Dev, realized daily returns; information only — not a gate):

- worst day -0.028; worst week -0.036; CVaR 5% (daily) -0.008; skew (daily) 3.978; max inventory (fraction of sleeve) 0.500; longest time at max inventory (min) 480; 2022 bear: total return -0.011; 2022 bear: Sharpe -0.167; 2022 bear: max DD 0.064; return in worst B&H drawdown 0.023; positions (ladders) / tranche rows 1773 / 1773

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.136 | 1.032 | 0.063 | 392 |
| 2022 | 365 | -0.011 | -0.167 | 0.064 | 301 |
| 2023 | 365 | 0.019 | 0.360 | 0.058 | 403 |
| 2024 | 366 | 0.086 | 1.104 | 0.054 | 379 |
| 2025 | 304 | 0.087 | 1.387 | 0.027 | 298 |

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G3': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).

## Researcher notes (cycle 4)

Mechanism/counterparty, evidence exposure, screen linkage, power, B&H reading, fill ratios, trades/day, tail-check reading, lessons (facts, interpretations int-000014..016, pm-000007) and budget are in the cycle report `research/reports/cycle4_20261110.md`. Robustness: not run (no Dev survivor). Exploratory trials: none. Val/lockbox: not reached / not accessed.
