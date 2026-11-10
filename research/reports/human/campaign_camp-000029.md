# Campaign camp-000029 — Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 5m: after bollinger_z_revert(96,2.5) a maker scale-in ladder (first tranche market, 3-4 equal tranches with limit rungs every 0.5/1.0 ATR below, maker take-profit 2.5 ATR per tranche or on average cost, aggregate stop 1.5 ATR below the last rung, max hold 120 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev, beat the ladder-matched random-entry null and pass the binding tail check.

Date (UTC) 2026-11-10T07:08:21+00:00 · hypothesis hyp-000028 (registered 2026-11-10T07:08:11+00:00) · campaign registered 2026-11-10T07:08:11+00:00 · family `liq_meanrev` · data snapshot `snap-93b066dd27ee41f0a0ec|XRP-KRW:snap-b636892dd83e17554616|ETH-KRW:snap-b46925c3bb3e2bfd5239` · code `e6cc86bb960b9e9900074963077957ff1b4df0bf+dirty`

**Verdict: no candidate passed.**

> ⚠ HUMAN-ONLY report: contains Val (G6) detail. Do not paste Val numbers into agent prompts, lessons or hypotheses. Agent-safe version: `research/reports/campaign_camp-000029.md`.

## Hypothesis (pre-registered)

- Text: Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 5m: after bollinger_z_revert(96,2.5) a maker scale-in ladder (first tranche market, 3-4 equal tranches with limit rungs every 0.5/1.0 ATR below, maker take-profit 2.5 ATR per tranche or on average cost, aggregate stop 1.5 ATR below the last rung, max hold 120 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev, beat the ladder-matched random-entry null and pass the binding tail check.
- Rationale: Mechanism M4 liquidity provision (mechanisms.md): forced/panic selling pushes thin Coinone books (esp. XRP/ETH) below fair value and part of it reverts; a resting buyer that adds on further dips is paid for immediacy. Counterparty: liquidated longs / panic sellers hitting thin bids. Kills: falling knives (2022 bear), information-driven drops, adverse selection of maker rungs (fill_model_20261110: -2.5..-4.7 bp at 5 min at dip signal times, n=20, BTC Forward; lockbox none, Val none), per-asset spread (D17), Upbit cost (grade B). Design (user request, D16: 'buy a part on a dip, buy more on a further dip, sell on small rises'), fixed in scratchpad PLAN_prescreen.txt BEFORE the screens ran: first tranche = market at the screened decision; equal tranches max_exposure/N (N in {3,4}), rungs every step_atr in {0.5,1.0} x decision-bar ATR14 below the first fill as maker limit orders (conservative 1-tick trade-through fill rule), maker take-profit limits; exit mode gridded {per_tranche (sell each tranche on a small rise), average (sell all at average cost + TP)}; mandatory aggregate stop sl_atr 1.5 ATR below the LAST rung (max loss fixed at ladder start); max_exposure 1.0 per sleeve. A limit first entry was dropped because entry 2 + exits 3 + ladder 2 + exit-mode grid 1 + offset 1 = 9 > MAX_PARAMS 8 (the user-requested per_tranche-vs-average comparison was kept). TP = screen MFE-median ATR suggestion rounded to 0.25, max hold = information horizon. Gates use MTM daily returns (D18); the tail check is binding for ladders (D19). Evidence (Dev only; lockbox none, Val none): screen camp-000025 (drv-000017): pooled n 8942 (BTC 2999, XRP 3025, ETH 2918), p 0.004, excess 6.3 bp z 3.00 @120 (info horizon 120, peaked), net of taker round trip only +0.7 bp (Upbit -12.0); MFE median 2.48 ATR, |MAE| p75 4.71 ATR (5m ATR median 0.234 %). Plausible Sharpe: z/sqrt(4.83 y) = 1.37 on excess - a ceiling that needs maker execution to recover most of the taker cost; with taker cost it is ~0.15. Power: required 1.20 at N_dsr ~7 (target 1.35 = this ceiling). Predicted failure modes (stated before the run): G2 vs the ladder-matched null (the ladder structure, not the entry, may carry the PnL), net cost (XRP/ETH spreads), tail check 2022 / CVaR, G4 recent 12 months. [derived-from drv-000020 ← fact-000022; family liq_meanrev]
- Success criteria: At least one spec passes G0-G5, ablation, G6 (outcome code) and the Dev robustness suite incl. the binding tail check. Refuted as a tradeable edge if no spec passes G2 vs the ladder-matched null or the best Dev Sharpe_w (MTM) < 1.0. Report per exit mode, per tranche count, per asset, fill rates, trades/day and 2022 results.

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 7,624,800 1m rows (filled 13.29%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- **Universe ['BTC-KRW', 'XRP-KRW', 'ETH-KRW']** (PLAN D15): the same spec on every asset, pooled as ONE strategy — equal fixed sleeves (1/N), independent position per asset, portfolio daily return = mean of sleeve returns; gates use the portfolio series and pooled trades (G1 = sum over assets); 1 ledger trial per spec. Per-asset rows below are information only.
  - BTC-KRW: 2,541,600 1m rows (filled 7.56%)
  - XRP-KRW: 2,541,600 1m rows (filled 13.21%)
  - ETH-KRW: 2,541,600 1m rows (filled 19.10%)
- Entry `bollinger_z_revert`: {'window': [96], 'z': [2.5]}
- Filter set 0: (no filter)
- Decision timeframe **5m**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [1.5], 'tp_atr': [2.5], 'max_hold_min': [120]} → 1 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress` — per-asset models where defined (PLAN D17): BTC-KRW: coinone_conservative, upbit_conservative; XRP-KRW: coinone_conservative@XRP-KRW, upbit_conservative@XRP-KRW; ETH-KRW: coinone_conservative@ETH-KRW, upbit_conservative@ETH-KRW
- Daily returns for gates (PLAN D18): **mark-to-market** — open inventory marked at each UTC day close (liquidation value incl. costs); G2–G6, Monte Carlo MDD95 (with intra-position MTM troughs), robustness and §7 limits use it
- Specs: **8** (template budget 8); runs recorded: 16; runtime 10.04s

## Signal screen linkage

- Gate: every signal config passed camp-000025 (every (entry params × filter params × timeframe × exchange × universe) config passed a recorded screen); 1 configs matched:
  - bollinger_z_revert{'window': 96, 'z': 2.5} ← screen `camp-000025` run `run-000209` (p_signal 0.0040, events 8942, info horizon 120)

## Power check (at registration)

- 계획 격자 8 스펙, 계열 `liq_meanrev` 기존 시도 58, 압축비 0.125 (history median of 9 campaigns) → 실질 시도 ≈ 8.2
- Dev 1765일, V=0.00028 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 1.22** (시도 1회였다면 0.75)
- 목표 연 Sharpe 1.35 로 감당 가능한 실질 시도 ≈ 19 (격자 ≈ 95 스펙)
- Required annual Sharpe stored: **1.22**; target 1.35; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.window | entry.z | ladder.tranches | ladder.step_atr | ladder.exit | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | n_positions | recent_n_trades | expectancy | max_dd | rolling_frac_positive | exposure_days | worst_day | cvar5_daily | max_inventory |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c4_liq_meanrev_ladder_5m_u3_1fd10db577 | - | 96 | 2.500 | 4 | 1 | per_tranche | 1.500 | 2.500 | 120 | -0.656 | -0.553 | 0.281 | 23118 | 9159 | 4733 | -0.000 | 0.450 | 0.302 | 0.934 | -0.060 | -0.017 | 1 |
| c4_liq_meanrev_ladder_5m_u3_5bf4f07ffe | - | 96 | 2.500 | 4 | 1 | average | 1.500 | 2.500 | 120 | -0.666 | -0.629 | 0.385 | 23360 | 9310 | 4780 | -0.000 | 0.498 | 0.302 | 0.934 | -0.059 | -0.018 | 1 |
| c4_liq_meanrev_ladder_5m_u3_92ae410b18 | - | 96 | 2.500 | 3 | 1 | per_tranche | 1.500 | 2.500 | 120 | -1.386 | -1.132 | -0.282 | 21380 | 9933 | 4351 | -0.000 | 0.641 | 0.226 | 0.934 | -0.083 | -0.019 | 1 |
| c4_liq_meanrev_ladder_5m_u3_af00c3aa70 | - | 96 | 2.500 | 3 | 1 | average | 1.500 | 2.500 | 120 | -1.438 | -1.272 | -0.233 | 21581 | 10058 | 4391 | -0.000 | 0.673 | 0.226 | 0.934 | -0.085 | -0.020 | 1 |
| c4_liq_meanrev_ladder_5m_u3_a8926cab58 | - | 96 | 2.500 | 4 | 0.500 | per_tranche | 1.500 | 2.500 | 120 | -1.568 | -1.462 | -0.797 | 31587 | 10493 | 6490 | -0.000 | 0.707 | 0.113 | 0.935 | -0.106 | -0.020 | 1 |
| c4_liq_meanrev_ladder_5m_u3_769b62f419 | - | 96 | 2.500 | 4 | 0.500 | average | 1.500 | 2.500 | 120 | -1.743 | -1.555 | -1.004 | 31907 | 10642 | 6556 | -0.000 | 0.731 | 0.113 | 0.934 | -0.118 | -0.021 | 1 |
| c4_liq_meanrev_ladder_5m_u3_8217f18da4 | - | 96 | 2.500 | 3 | 0.500 | per_tranche | 1.500 | 2.500 | 120 | -1.774 | -1.814 | -0.888 | 27404 | 11239 | 5702 | -0.000 | 0.777 | 0.094 | 0.934 | -0.095 | -0.023 | 1 |
| c4_liq_meanrev_ladder_5m_u3_39eadf6d83 | - | 96 | 2.500 | 3 | 0.500 | average | 1.500 | 2.500 | 120 | -1.880 | -1.931 | -1.047 | 27647 | 11353 | 5740 | -0.000 | 0.798 | 0.094 | 0.934 | -0.104 | -0.023 | 1 |

Per-asset breakdown of the top specs (Dev, primary cost; information only — gates use the pooled portfolio):

| spec_id | asset.BTC-KRW.n_trades | asset.BTC-KRW.sharpe_w | asset.BTC-KRW.total_return | asset.XRP-KRW.n_trades | asset.XRP-KRW.sharpe_w | asset.XRP-KRW.total_return | asset.ETH-KRW.n_trades | asset.ETH-KRW.sharpe_w | asset.ETH-KRW.total_return |
|---|---|---|---|---|---|---|---|---|---|
| c4_liq_meanrev_ladder_5m_u3_1fd10db577 | 7766 | 0.576 | 0.431 | 7785 | -0.403 | -0.512 | 7567 | -1.214 | -0.510 |
| c4_liq_meanrev_ladder_5m_u3_5bf4f07ffe | 7858 | 0.718 | 0.490 | 7875 | -0.261 | -0.514 | 7627 | -1.532 | -0.604 |
| c4_liq_meanrev_ladder_5m_u3_92ae410b18 | 7201 | -0.188 | -0.086 | 7173 | -0.403 | -0.501 | 7006 | -2.205 | -0.760 |
| c4_liq_meanrev_ladder_5m_u3_af00c3aa70 | 7292 | -0.086 | -0.073 | 7240 | -0.528 | -0.606 | 7049 | -2.277 | -0.788 |
| c4_liq_meanrev_ladder_5m_u3_a8926cab58 | 10676 | -0.156 | -0.166 | 10636 | -0.825 | -0.674 | 10275 | -2.250 | -0.819 |
| c4_liq_meanrev_ladder_5m_u3_769b62f419 | 10799 | -0.254 | -0.266 | 10757 | -1.098 | -0.729 | 10351 | -2.272 | -0.825 |
| c4_liq_meanrev_ladder_5m_u3_8217f18da4 | 9327 | -0.743 | -0.290 | 9199 | -1.044 | -0.790 | 8878 | -2.155 | -0.889 |
| c4_liq_meanrev_ladder_5m_u3_39eadf6d83 | 9410 | -0.748 | -0.317 | 9292 | -1.280 | -0.845 | 8945 | -2.093 | -0.895 |

Buy & hold over the same Dev period (equal-weight 3-asset buy & hold (BTC-KRW, XRP-KRW, ETH-KRW; same venue 1m close, daily-rebalanced sleeves like the strategy, no cost); information only): total return 11.453, Sharpe 1.148, MDD 0.730.

Distribution of weighted Sharpe over all 8 specs: median -1.503, share > 0 0.0%, max -0.656.

## Funnel

- All specs (metric-only gates): 8 specs → G1 8, weighted Sharpe > 0 0, G4 0, all three 0
- Top 1 candidates: G0 1, G1 1, G2 0, G3 0, G4 0, G5 0 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=8, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 1.9
- Ledger trials: family 66, global 170 → DSR trials N = max(K, N_family·K/N) = 8.2; V = 0.000701 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.000

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 8 specs → 1 clusters (largest sizes [8]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| 1fd10db57790e80f | 1 | 8 | -0.656 |

## Candidates — per-gate results

### #1 `c4_liq_meanrev_ladder_5m_u3_1fd10db577` — failed G2 (ledger only)

```yaml
id: c4_liq_meanrev_ladder_5m_u3_1fd10db577
entry:
  block: bollinger_z_revert
  params:
    window: 96
    z: 2.5
exit:
  max_hold_min: 120
  signal_exit: null
  stop_loss_atr: 1.5
  take_profit_atr: 2.5
filters: []
version: 1
universe:
- BTC-KRW
- XRP-KRW
- ETH-KRW
execution:
  take_profit: limit
ladder:
  tranches: 4
  step_atr: 1.0
  order: limit
  exit: per_tranche
```
Weighted Sharpe -0.656, Sharpe -0.553, trades 23118, expectancy -0.00015, MDD 0.450

- **G0 PASS** — validate=ok, timeframe=5m, exit_mode=atr, n_filters=0, n_params=7, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=9, causality_violations=[]
- **G1 PASS** — n_trades=9159, recent_n_trades=1894
- **G2 FAIL** — sharpe_w=-0.6559, p_value=0.0010, percentile=1, n_null=1000, calibration=trade_count, p_signal_rate={'BTC-KRW': 0.035432850977405436, 'XRP-KRW': 0.041435258771683775, 'ETH-KRW': 0.04700402686591972}, p={'BTC-KRW': 0.00905218234562421, 'XRP-KRW': 0.011296367523876775, 'ETH-KRW': 0.013974380795682697}, tf_min=5, atr_bars=14, n_trades_median=9167, candidate_n_trades=23118, sharpe_w_median=-2.8999, sharpe_w_p95=-1.9946, cost=coinone_conservative
- **G3 FAIL** — selected=-0.6559, neighbor_median=-1.3861, required=-0.3279, n_neighbors=3
- **G4 FAIL** — rolling_frac_positive=0.3019, rolling_n_windows=53, recent_sharpe=0.2814
  - exposure-aware variant (shadow, OFF — information only): applicable=✓, rolling_n_active=53, rolling_n_neutral=0, rolling_frac_positive_active=0.3019, exposure_days=0.9337, via=–, passed=✗
- **G5 FAIL** — dsr=0.0017, pbo=0.0000, sr=-0.0289, sr0=0.0391, n_trials=8.2500, var_trial_sr=0.0007
- G3 neighbors: ladder.tranches=3→-1.39, ladder.step_atr=0.5→-1.57, ladder.exit=average→-0.67

Buy & hold reference (Dev, primary cost; information only — not a gate):

| | strategy | EW 3-asset buy & hold |
|---|---|---|
| Total return | -0.281 | 11.453 |
| Sharpe | -0.553 | 1.148 |
| Max drawdown | 0.450 | 0.730 |

- time in market (exposure_time) 0.036; days in market (exposure_days) 0.934; exposure-matched B&H return 0.130; excess vs matched B&H -0.411; excess vs B&H -11.734; up-day capture 0.041; down-day capture 0.067; up-regime return (trailing 30d BTC > 0) 0.015; down-regime return (trailing 30d BTC ≤ 0) -0.337; up-regime capture 0.010; down-regime capture 1.897; downside deviation (ann.) 0.091; rolling 6m windows neutral / active 0 / 53 (neutral = exposure_days < 0.10)
- Worst B&H drawdown episodes: #1 2021-11-09→2022-06-18 B&H −0.730 → strategy -0.274; #2 2021-05-09→2021-07-20 B&H −0.572 → strategy -0.069; #3 2025-01-18→2025-04-08 B&H −0.425 → strategy -0.074

Per-asset (Dev, primary cost; information only — gates use the pooled portfolio):

| asset | n_trades | recent_n_trades | sharpe_w | sharpe | total_return | max_dd | expectancy | win_rate |
|---|---|---|---|---|---|---|---|---|
| BTC-KRW | 7766 | 1521 | 0.576 | 0.742 | 0.431 | 0.248 | 0.000 | 0.536 |
| XRP-KRW | 7785 | 1691 | -0.403 | -0.726 | -0.512 | 0.603 | -0.000 | 0.515 |
| ETH-KRW | 7567 | 1521 | -1.214 | -0.813 | -0.510 | 0.637 | -0.000 | 0.506 |

Tail risk (Dev, realized daily returns; information only — not a gate):

- worst day -0.060; worst week -0.052; CVaR 5% (daily) -0.017; skew (daily) -1.800; max inventory (fraction of sleeve) 1; longest time at max inventory (min) 58; 2022 bear: total return -0.262; 2022 bear: Sharpe -2.907; 2022 bear: max DD 0.267; return in worst B&H drawdown -0.274; positions (ladders) / tranche rows 9159 / 23118

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.165 | 0.981 | 0.135 | 5018 |
| 2022 | 365 | -0.262 | -2.907 | 0.267 | 5035 |
| 2023 | 365 | -0.096 | -1.774 | 0.123 | 4167 |
| 2024 | 366 | -0.001 | 0.038 | 0.135 | 4987 |
| 2025 | 304 | -0.073 | -0.957 | 0.126 | 3911 |

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G2': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).
