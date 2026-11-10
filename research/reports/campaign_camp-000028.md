# Campaign camp-000028 — Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 15m: after the equal-weight reversion composite composite_z(reversion) >= 2.0 a maker scale-in ladder (first tranche market, 3-4 equal tranches with limit rungs every 0.5/1.0 ATR below, maker take-profit 1.25 ATR per tranche or on average cost, aggregate stop 1.5 ATR below the last rung, max hold 60 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev, beat the ladder-matched random-entry null and pass the binding tail check.

Date (UTC) 2026-11-10T07:07:37+00:00 · hypothesis hyp-000027 (registered 2026-11-10T07:07:28+00:00) · campaign registered 2026-11-10T07:07:28+00:00 · family `liq_meanrev` · data snapshot `snap-93b066dd27ee41f0a0ec|XRP-KRW:snap-b636892dd83e17554616|ETH-KRW:snap-b46925c3bb3e2bfd5239` · code `e6cc86bb960b9e9900074963077957ff1b4df0bf+dirty`

**Verdict: no candidate passed.**

> Agent-safe report: Dev detail + Val (G6) / lockbox **outcome codes only**. The full human report is `research/reports/human/campaign_camp-000028.md` (human-only; not readable in research mode).

## Hypothesis (pre-registered)

- Text: Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 15m: after the equal-weight reversion composite composite_z(reversion) >= 2.0 a maker scale-in ladder (first tranche market, 3-4 equal tranches with limit rungs every 0.5/1.0 ATR below, maker take-profit 1.25 ATR per tranche or on average cost, aggregate stop 1.5 ATR below the last rung, max hold 60 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev, beat the ladder-matched random-entry null and pass the binding tail check.
- Rationale: Mechanism M4 liquidity provision (mechanisms.md): forced/panic selling pushes thin Coinone books (esp. XRP/ETH) below fair value and part of it reverts; a resting buyer that adds on further dips is paid for immediacy. Counterparty: liquidated longs / panic sellers hitting thin bids. Kills: falling knives (2022 bear), information-driven drops, adverse selection of maker rungs (fill_model_20261110: -2.5..-4.7 bp at 5 min at dip signal times, n=20, BTC Forward; lockbox none, Val none), per-asset spread (D17), Upbit cost (grade B). Design (user request, D16: 'buy a part on a dip, buy more on a further dip, sell on small rises'), fixed in scratchpad PLAN_prescreen.txt BEFORE the screens ran: first tranche = market at the screened decision; equal tranches max_exposure/N (N in {3,4}), rungs every step_atr in {0.5,1.0} x decision-bar ATR14 below the first fill as maker limit orders (conservative 1-tick trade-through fill rule), maker take-profit limits; exit mode gridded {per_tranche (sell each tranche on a small rise), average (sell all at average cost + TP)}; mandatory aggregate stop sl_atr 1.5 ATR below the LAST rung (max loss fixed at ladder start); max_exposure 1.0 per sleeve. A limit first entry was dropped because entry 2 + exits 3 + ladder 2 + exit-mode grid 1 + offset 1 = 9 > MAX_PARAMS 8 (the user-requested per_tranche-vs-average comparison was kept). TP = screen MFE-median ATR suggestion rounded to 0.25, max hold = information horizon. Gates use MTM daily returns (D18); the tail check is binding for ladders (D19). Composite (cycle-4 B at 15m): equal-weight -r_6, -boll_48, +log1p vol, no fitted weights; experimental block (human review needed before any lockbox request). Evidence (Dev only; lockbox none, Val none): screen camp-000026 (drv-000018): composite_z(reversion,2.0) pooled n 3883 (BTC 1396, XRP 1186, ETH 1301), p 0.0019, excess 9.3 bp z 3.22 @60 (info horizon 60, peaked), net +2.9 bp (Upbit -9.9); pre-registered contrast met (excess 9.3 > bollinger(24,2.5) sibling 6.2 bp, which failed on net cost; the difference is within ~1 SE). Per-asset p: BTC 0.075, XRP 0.0006, ETH 0.17 (report only). MFE median 1.16 ATR, |MAE| p75 2.15 ATR (15m ATR median 0.48 %). Plausible Sharpe z/sqrt(4.83 y) = 1.47 on excess (ceiling); power required 1.20, target 1.45. Predicted failure modes: G2 vs ladder null, concentration in XRP, tail 2022, the 60 min hold leaves deep rungs little time (per_tranche and average may differ little). [derived-from drv-000021 ← fact-000023; family liq_meanrev]
- Success criteria: At least one spec passes G0-G5, ablation, G6 (outcome code) and the Dev robustness suite incl. the binding tail check. Refuted as a tradeable edge if no spec passes G2 vs the ladder-matched null or the best Dev Sharpe_w (MTM) < 1.0. Report per exit mode, per tranche count, per asset, fill rates, trades/day and 2022 results.

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 7,624,800 1m rows (filled 13.29%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- **Universe ['BTC-KRW', 'XRP-KRW', 'ETH-KRW']** (PLAN D15): the same spec on every asset, pooled as ONE strategy — equal fixed sleeves (1/N), independent position per asset, portfolio daily return = mean of sleeve returns; gates use the portfolio series and pooled trades (G1 = sum over assets); 1 ledger trial per spec. Per-asset rows below are information only.
  - BTC-KRW: 2,541,600 1m rows (filled 7.56%)
  - XRP-KRW: 2,541,600 1m rows (filled 13.21%)
  - ETH-KRW: 2,541,600 1m rows (filled 19.10%)
- Entry `composite_z`: {'preset': ['reversion'], 'threshold': [2.0]}
- Filter set 0: (no filter)
- Decision timeframe **15m**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [1.5], 'tp_atr': [1.25], 'max_hold_min': [60]} → 1 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress` — per-asset models where defined (PLAN D17): BTC-KRW: coinone_conservative, upbit_conservative; XRP-KRW: coinone_conservative@XRP-KRW, upbit_conservative@XRP-KRW; ETH-KRW: coinone_conservative@ETH-KRW, upbit_conservative@ETH-KRW
- Daily returns for gates (PLAN D18): **mark-to-market** — open inventory marked at each UTC day close (liquidation value incl. costs); G2–G6, Monte Carlo MDD95 (with intra-position MTM troughs), robustness and §7 limits use it
- Specs: **8** (template budget 8); runs recorded: 16; runtime 9.13s

## Signal screen linkage

- Gate: every signal config passed camp-000026 (every (entry params × filter params × timeframe × exchange × universe) config passed a recorded screen); 1 configs matched:
  - composite_z{'preset': 'reversion', 'threshold': 2.0} ← screen `camp-000026` run `run-000213` (p_signal 0.0019, events 3883, info horizon 60)

## Power check (at registration)

- 계획 격자 8 스펙, 계열 `liq_meanrev` 기존 시도 50, 압축비 0.125 (history median of 8 campaigns) → 실질 시도 ≈ 7.2
- Dev 1765일, V=0.000283 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 1.20** (시도 1회였다면 0.75)
- 목표 연 Sharpe 1.45 로 감당 가능한 실질 시도 ≈ 39 (격자 ≈ 264 스펙)
- Required annual Sharpe stored: **1.20**; target 1.45; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.preset | entry.threshold | ladder.tranches | ladder.step_atr | ladder.exit | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | n_positions | recent_n_trades | expectancy | max_dd | rolling_frac_positive | exposure_days | worst_day | cvar5_daily | max_inventory |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c4_liq_meanrev_ladder_15m_u3_7207679868 | - | reversion | 2 | 4 | 0.500 | average | 1.500 | 1.250 | 60 | -0.127 | -0.146 | 0.264 | 9910 | 4011 | 2178 | -0.000 | 0.361 | 0.321 | 0.593 | -0.061 | -0.014 | 1 |
| c4_liq_meanrev_ladder_15m_u3_de73b441e4 | - | reversion | 2 | 3 | 1 | average | 1.500 | 1.250 | 60 | -0.329 | -0.104 | -0.089 | 6777 | 3942 | 1475 | -0.000 | 0.275 | 0.283 | 0.594 | -0.039 | -0.012 | 1 |
| c4_liq_meanrev_ladder_15m_u3_02dabffc52 | - | reversion | 2 | 4 | 1 | average | 1.500 | 1.250 | 60 | -0.407 | 0.275 | -0.048 | 7185 | 3883 | 1563 | 0.000 | 0.246 | 0.302 | 0.594 | -0.033 | -0.011 | 1 |
| c4_liq_meanrev_ladder_15m_u3_239e68d2b9 | - | reversion | 2 | 4 | 0.500 | per_tranche | 1.500 | 1.250 | 60 | -0.458 | -0.354 | -0.274 | 9819 | 3957 | 2134 | -0.000 | 0.368 | 0.245 | 0.593 | -0.061 | -0.013 | 1 |
| c4_liq_meanrev_ladder_15m_u3_297838ca1b | - | reversion | 2 | 3 | 0.500 | average | 1.500 | 1.250 | 60 | -0.512 | -0.262 | -0.300 | 8685 | 4088 | 1898 | -0.000 | 0.403 | 0.226 | 0.593 | -0.052 | -0.014 | 1 |
| c4_liq_meanrev_ladder_15m_u3_e44b5363a5 | - | reversion | 2 | 3 | 1 | per_tranche | 1.500 | 1.250 | 60 | -0.514 | -0.290 | -0.283 | 6707 | 3899 | 1454 | -0.000 | 0.263 | 0.302 | 0.593 | -0.035 | -0.012 | 1 |
| c4_liq_meanrev_ladder_15m_u3_6a70a4bcca | - | reversion | 2 | 4 | 1 | per_tranche | 1.500 | 1.250 | 60 | -0.705 | -0.069 | -0.350 | 7099 | 3824 | 1535 | -0.000 | 0.272 | 0.226 | 0.593 | -0.035 | -0.011 | 1 |
| c4_liq_meanrev_ladder_15m_u3_380addba03 | - | reversion | 2 | 3 | 0.500 | per_tranche | 1.500 | 1.250 | 60 | -0.723 | -0.450 | -0.587 | 8597 | 4043 | 1870 | -0.000 | 0.403 | 0.208 | 0.593 | -0.052 | -0.014 | 1 |

Per-asset breakdown of the top specs (Dev, primary cost; information only — gates use the pooled portfolio):

| spec_id | asset.BTC-KRW.n_trades | asset.BTC-KRW.sharpe_w | asset.BTC-KRW.total_return | asset.XRP-KRW.n_trades | asset.XRP-KRW.sharpe_w | asset.XRP-KRW.total_return | asset.ETH-KRW.n_trades | asset.ETH-KRW.sharpe_w | asset.ETH-KRW.total_return |
|---|---|---|---|---|---|---|---|---|---|
| c4_liq_meanrev_ladder_15m_u3_7207679868 | 3681 | -0.204 | -0.053 | 2936 | 0.543 | 0.190 | 3293 | -0.841 | -0.353 |
| c4_liq_meanrev_ladder_15m_u3_de73b441e4 | 2481 | 0.213 | 0.072 | 2031 | 0.056 | 0.089 | 2265 | -0.907 | -0.310 |
| c4_liq_meanrev_ladder_15m_u3_02dabffc52 | 2617 | 0.297 | 0.168 | 2160 | 0.189 | 0.311 | 2408 | -1.058 | -0.189 |
| c4_liq_meanrev_ladder_15m_u3_239e68d2b9 | 3637 | -0.362 | -0.088 | 2898 | 0.226 | 0.009 | 3284 | -1.073 | -0.381 |
| c4_liq_meanrev_ladder_15m_u3_297838ca1b | 3211 | -0.569 | -0.117 | 2587 | 0.428 | 0.214 | 2887 | -1.253 | -0.429 |
| c4_liq_meanrev_ladder_15m_u3_e44b5363a5 | 2447 | -0.025 | -0.031 | 2006 | 0.073 | 0.061 | 2254 | -1.102 | -0.350 |
| c4_liq_meanrev_ladder_15m_u3_6a70a4bcca | 2580 | 0.045 | 0.040 | 2130 | -0.027 | 0.109 | 2389 | -1.204 | -0.252 |
| c4_liq_meanrev_ladder_15m_u3_380addba03 | 3171 | -0.607 | -0.132 | 2554 | 0.140 | 0.015 | 2872 | -1.324 | -0.442 |

Buy & hold over the same Dev period (equal-weight 3-asset buy & hold (BTC-KRW, XRP-KRW, ETH-KRW; same venue 1m close, daily-rebalanced sleeves like the strategy, no cost); information only): total return 11.453, Sharpe 1.148, MDD 0.730.

Distribution of weighted Sharpe over all 8 specs: median -0.485, share > 0 0.0%, max -0.127.

## Funnel

- All specs (metric-only gates): 8 specs → G1 8, weighted Sharpe > 0 0, G4 0, all three 0
- Top 1 candidates: G0 1, G1 1, G2 0, G3 0, G4 0, G5 0 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=8, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 1.9
- Ledger trials: family 58, global 162 → DSR trials N = max(K, N_family·K/N) = 7.2; V = 0.000136 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.488

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 8 specs → 1 clusters (largest sizes [8]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| 7207679868297f0f | 1 | 8 | -0.127 |

> ⚠ **Experimental blocks used**: composite_z — human block review required before lockbox (D11).

## Candidates — per-gate results

### #1 `c4_liq_meanrev_ladder_15m_u3_7207679868` — failed G2 (ledger only)

> ⚠ uses experimental block(s) composite_z: **human block review required before lockbox** (D11).

```yaml
id: c4_liq_meanrev_ladder_15m_u3_7207679868
entry:
  block: composite_z
  params:
    preset: reversion
    threshold: 2.0
exit:
  max_hold_min: 60
  signal_exit: null
  stop_loss_atr: 1.5
  take_profit_atr: 1.25
filters: []
version: 1
timeframe: 15m
universe:
- BTC-KRW
- XRP-KRW
- ETH-KRW
execution:
  take_profit: limit
ladder:
  tranches: 4
  step_atr: 0.5
  order: limit
  exit: average
```
Weighted Sharpe -0.127, Sharpe -0.146, trades 9910, expectancy -0.00008, MDD 0.361

- **G0 PASS** — validate=ok, timeframe=15m, exit_mode=atr, n_filters=0, n_params=7, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[composite_z], causality_cuts=9, causality_violations=[]
- **G1 PASS** — n_trades=4011, recent_n_trades=872
- **G2 FAIL** — sharpe_w=-0.1266, p_value=0.0010, percentile=1, n_null=1000, calibration=trade_count, p_signal_rate={'BTC-KRW': 0.0170445466328143, 'XRP-KRW': 0.014150943396226415, 'ETH-KRW': 0.01563989178757688}, p={'BTC-KRW': 0.009293604951428789, 'XRP-KRW': 0.008414201285396612, 'ETH-KRW': 0.009711260898193137}, tf_min=15, atr_bars=14, n_trades_median=4021, candidate_n_trades=9910, sharpe_w_median=-2.2930, sharpe_w_p95=-1.4511, cost=coinone_conservative
- **G3 FAIL** — selected=-0.1266, neighbor_median=-0.4575, required=-0.0633, n_neighbors=3
- **G4 FAIL** — rolling_frac_positive=0.3208, rolling_n_windows=53, recent_sharpe=0.2642
  - exposure-aware variant (shadow, OFF — information only): applicable=✓, rolling_n_active=53, rolling_n_neutral=0, rolling_frac_positive_active=0.3208, exposure_days=0.5932, via=–, passed=✗
- **G5 FAIL** — dsr=0.1530, pbo=0.4880, sr=-0.0076, sr0=0.0164, n_trials=7.2500, var_trial_sr=0.0001
- G3 neighbors: ladder.tranches=3→-0.51, ladder.step_atr=1.0→-0.41, ladder.exit=per_tranche→-0.46

Buy & hold reference (Dev, primary cost; information only — not a gate):

| | strategy | EW 3-asset buy & hold |
|---|---|---|
| Total return | -0.083 | 11.453 |
| Sharpe | -0.146 | 1.148 |
| Max drawdown | 0.361 | 0.730 |

- time in market (exposure_time) 0.010; days in market (exposure_days) 0.593; exposure-matched B&H return 0.035; excess vs matched B&H -0.119; excess vs B&H -11.537; up-day capture 0.008; down-day capture 0.014; up-regime return (trailing 30d BTC > 0) -0.120; down-regime return (trailing 30d BTC ≤ 0) -0.026; up-regime capture -0.037; down-regime capture 0.101; downside deviation (ann.) 0.078; rolling 6m windows neutral / active 0 / 53 (neutral = exposure_days < 0.10)
- Worst B&H drawdown episodes: #1 2021-11-09→2022-06-18 B&H −0.730 → strategy -0.068; #2 2021-05-09→2021-07-20 B&H −0.572 → strategy -0.018; #3 2025-01-18→2025-04-08 B&H −0.425 → strategy 0.013

Per-asset (Dev, primary cost; information only — gates use the pooled portfolio):

| asset | n_trades | recent_n_trades | sharpe_w | sharpe | total_return | max_dd | expectancy | win_rate |
|---|---|---|---|---|---|---|---|---|
| BTC-KRW | 3681 | 805 | -0.204 | -0.065 | -0.053 | 0.302 | -0.000 | 0.566 |
| XRP-KRW | 2936 | 698 | 0.543 | 0.311 | 0.190 | 0.426 | 0.000 | 0.581 |
| ETH-KRW | 3293 | 675 | -0.841 | -0.671 | -0.353 | 0.459 | -0.000 | 0.546 |

Tail risk (Dev, realized daily returns; information only — not a gate):

- worst day -0.061; worst week -0.054; CVaR 5% (daily) -0.014; skew (daily) -3.573; max inventory (fraction of sleeve) 1; longest time at max inventory (min) 49; 2022 bear: total return -0.132; 2022 bear: Sharpe -1.767; 2022 bear: max DD 0.133; return in worst B&H drawdown -0.068; positions (ladders) / tranche rows 4011 / 9910

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.186 | 1.389 | 0.116 | 2080 |
| 2022 | 365 | -0.132 | -1.767 | 0.133 | 2086 |
| 2023 | 365 | -0.078 | -1.570 | 0.111 | 1799 |
| 2024 | 366 | -0.080 | -0.790 | 0.136 | 2145 |
| 2025 | 304 | 0.050 | 0.704 | 0.079 | 1800 |

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G2': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).

## Researcher notes (cycle 4)

Mechanism/counterparty, evidence exposure, screen linkage, power, B&H reading, fill ratios, trades/day, tail-check reading, lessons (facts, interpretations int-000014..016, pm-000007) and budget are in the cycle report `research/reports/cycle4_20261110.md`. Robustness: not run (no Dev survivor). Exploratory trials: none. Val/lockbox: not reached / not accessed.
