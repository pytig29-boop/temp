# Campaign camp-000034 — Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 4h decisions: donchian_breakout(48) with half-size entries (entry_fraction 0.5) and ATR exits (SL 2 ATR, TP 1.5/3 ATR, max hold 1440 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G6 (rs-001) and the robustness suite.

Date (UTC) 2026-11-12T01:59:40+00:00 · hypothesis hyp-000033 (registered 2026-11-12T01:59:34+00:00) · campaign registered 2026-11-12T01:59:34+00:00 · family `vol_breakout` · data snapshot `snap-93b066dd27ee41f0a0ec|XRP-KRW:snap-b636892dd83e17554616|ETH-KRW:snap-b46925c3bb3e2bfd5239` · code `8fcea0044ef19e3aa2dfad8d1fed87ff1ea08a06`

**Verdict: 1 candidate(s) passed G0–G6.**

> ⚠ HUMAN-ONLY report: contains Val (G6) detail. Do not paste Val numbers into agent prompts, lessons or hypotheses. Agent-safe version: `research/reports/campaign_camp-000034.md`.

## Hypothesis (pre-registered)

- Text: Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 4h decisions: donchian_breakout(48) with half-size entries (entry_fraction 0.5) and ATR exits (SL 2 ATR, TP 1.5/3 ATR, max hold 1440 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G6 (rs-001) and the robustness suite.
- Rationale: Autonomous program R1 track B. Mechanism M3 at multi-day horizons (time-series momentum; stop/short-liquidation cascades; trend-following flows). Counterparty: counter-trend limit sellers and liquidated shorts at 4-8 day highs; late chasers at exit. Kills: long-only bear regime 2022, regime concentration, per-asset spreads (D17), BTC sleeve re-uses a family with VAL_FAIL history (skeptic must weigh). Exits fixed from the screen before the campaign: SL = |MAE| p75 suggestion 1.85-1.91 ATR rounded to 2.0; TP = MFE-median suggestion 1.34-1.43 ATR rounded to 1.5 and 2x; max hold = information horizon 1440 min. Evidence (Dev only; lockbox none, Val none): screen camp-000033 pooled donchian 24 n 1088 p 0.0054 excess 51 bp net 75 bp @1440; donchian 48 n 807 p 0.0008 excess 72 bp net 96 bp @1440. Plausible Sharpe z/sqrt(4.83 y) ~1.4. Grid shrunk to the screen's strongest entry (lookback 48) before running: with lookback 24 included the strict power check required Sharpe 1.41 > plausible 1.40 (family vol_breakout already has 100 trials). Target Sharpe = screen z at the information horizon / sqrt(Dev years) = 3.46/sqrt(4.83) = 1.57 (same convention as earlier campaigns); the 1.40 first written was a rough pair-level figure.
- Success criteria: G0-G6 under ruleset rs-001 (MTM, size scaling, combined Val evidence), robustness suite, and per-asset results reported.

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 7,624,800 1m rows (filled 13.29%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- **Universe ['BTC-KRW', 'XRP-KRW', 'ETH-KRW']** (PLAN D15): the same spec on every asset, pooled as ONE strategy — equal fixed sleeves (1/N), independent position per asset, portfolio daily return = mean of sleeve returns; gates use the portfolio series and pooled trades (G1 = sum over assets); 1 ledger trial per spec. Per-asset rows below are information only.
  - BTC-KRW: 2,541,600 1m rows (filled 7.56%)
  - XRP-KRW: 2,541,600 1m rows (filled 13.21%)
  - ETH-KRW: 2,541,600 1m rows (filled 19.10%)
- Entry `donchian_breakout`: {'lookback': [48]}
- Filter set 0: (no filter)
- Decision timeframe **4h**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [2.0], 'tp_atr': [1.5, 3.0], 'max_hold_min': [1440]} → 2 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress` — per-asset models where defined (PLAN D17): BTC-KRW: coinone_conservative, upbit_conservative; XRP-KRW: coinone_conservative@XRP-KRW, upbit_conservative@XRP-KRW; ETH-KRW: coinone_conservative@ETH-KRW, upbit_conservative@ETH-KRW
- Daily returns for gates (PLAN D18): **mark-to-market** — open inventory marked at each UTC day close (liquidation value incl. costs); G2–G6, Monte Carlo MDD95 (with intra-position MTM troughs), robustness and §7 limits use it
- Specs: **2** (template budget 2); runs recorded: 4; runtime 5.27s

## Signal screen linkage

- Gate: every signal config passed camp-000033 (every (entry params × filter params × timeframe × exchange × universe) config passed a recorded screen); 1 configs matched:
  - donchian_breakout{'lookback': 48} ← screen `camp-000033` run `run-000272` (p_signal 0.0008, events 807, info horizon 1440)

## Power check (at registration)

- 계획 격자 2 스펙, 계열 `vol_breakout` 기존 시도 100, 압축비 0.5 (history median of 12 campaigns; floored at 1/n_grid = 0.5 (single-cluster small grid, pm-000007)) → 실질 시도 ≈ 51.0
- Dev 1765일, V=0.000291 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 1.49** (시도 1회였다면 0.75)
- 목표 연 Sharpe 1.57 로 감당 가능한 실질 시도 ≈ 97 (격자 ≈ 93 스펙)
- Required annual Sharpe stored: **1.49**; target 1.57; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.lookback | sizing.entry_fraction | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | n_positions | recent_n_trades | expectancy | max_dd | rolling_frac_positive | exposure_days | worst_day | cvar5_daily | max_inventory |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| r1b_vol_breakout_donch_4h_u3_half_0dce65e919 | - | 48 | 0.500 | 2 | 3 | 1440 | 1.827 | 1.497 | 2.669 | 554 | 554 | 132 | 0.007 | 0.092 | 0.962 | 0.335 | -0.046 | -0.009 | 0.500 |
| r1b_vol_breakout_donch_4h_u3_half_2ae53a4052 | - | 48 | 0.500 | 2 | 1.500 | 1440 | 1.408 | 1.185 | 1.854 | 610 | 610 | 147 | 0.004 | 0.077 | 0.849 | 0.318 | -0.020 | -0.008 | 0.500 |

Per-asset breakdown of the top specs (Dev, primary cost; information only — gates use the pooled portfolio):

| spec_id | asset.BTC-KRW.n_trades | asset.BTC-KRW.sharpe_w | asset.BTC-KRW.total_return | asset.XRP-KRW.n_trades | asset.XRP-KRW.sharpe_w | asset.XRP-KRW.total_return | asset.ETH-KRW.n_trades | asset.ETH-KRW.sharpe_w | asset.ETH-KRW.total_return |
|---|---|---|---|---|---|---|---|---|---|
| r1b_vol_breakout_donch_4h_u3_half_0dce65e919 | 198 | 1.739 | 0.985 | 150 | 1.340 | 1.073 | 206 | 0.604 | 0.310 |
| r1b_vol_breakout_donch_4h_u3_half_2ae53a4052 | 224 | 1.373 | 0.504 | 166 | 0.895 | 0.460 | 220 | 0.637 | 0.277 |

Buy & hold over the same Dev period (equal-weight 3-asset buy & hold (BTC-KRW, XRP-KRW, ETH-KRW; same venue 1m close, daily-rebalanced sleeves like the strategy, no cost); information only): total return 11.453, Sharpe 1.148, MDD 0.730.

Distribution of weighted Sharpe over all 2 specs: median 1.617, share > 0 100.0%, max 1.827.

## Funnel

- All specs (metric-only gates): 2 specs → G1 2, weighted Sharpe > 0 2, G4 2, all three 2
- Top 1 candidates: G0 1, G1 1, G2 1, G3 1, G4 1, G5 1 → Dev survivors (all G0–G5) 1 → G6 validated 1

## Selection-bias inputs (G5)

- Campaign specs N=2, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 1.4
- Ledger trials: family 102, global 188 → DSR trials N = max(K, N_family·K/N) = 51; V = 0.000133 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.228

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 2 specs → 1 clusters (largest sizes [2]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| 0dce65e919fc1171 | 1 | 2 | 1.827 |

- Val budget status: val_id=val1, cap=20, used=3, remaining=17, passed=1, pending=0

## Candidates — per-gate results

### #1 `r1b_vol_breakout_donch_4h_u3_half_0dce65e919` — validated (grade A)

```yaml
id: r1b_vol_breakout_donch_4h_u3_half_0dce65e919
entry:
  block: donchian_breakout
  params:
    lookback: 48
exit:
  max_hold_min: 1440
  signal_exit: null
  stop_loss_atr: 2.0
  take_profit_atr: 3.0
filters: []
version: 1
timeframe: 4h
universe:
- BTC-KRW
- XRP-KRW
- ETH-KRW
sizing:
  entry_fraction: 0.5
```
Weighted Sharpe 1.827, Sharpe 1.497, trades 554, expectancy 0.00654, MDD 0.092

- **G0 PASS** — validate=ok, timeframe=4h, exit_mode=atr, n_filters=0, n_params=4, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=9, causality_violations=[]
- **G1 PASS** — n_trades=554, recent_n_trades=132
- **G2 PASS** — sharpe_w=1.8267, p_value=0.0020, percentile=0.9990, n_null=1000, calibration=trade_count, p_signal_rate={'BTC-KRW': 0.04014425358261365, 'XRP-KRW': 0.02534649705714828, 'ETH-KRW': 0.039266020155923176}, p={'BTC-KRW': 0.02188880947695758, 'XRP-KRW': 0.016435169977777653, 'ETH-KRW': 0.024381406939834042}, tf_min=240, atr_bars=14, n_trades_median=555, candidate_n_trades=554, sharpe_w_median=0.0675, sharpe_w_p95=0.9939, cost=coinone_conservative
- **G3 PASS** — selected=1.8267, neighbor_median=1.4082, required=0.4567, n_neighbors=1
- **G4 PASS** — rolling_frac_positive=0.9623, rolling_n_windows=53, recent_sharpe=2.6690
  - exposure-aware variant (shadow, OFF — information only): applicable=✓, rolling_n_active=53, rolling_n_neutral=0, rolling_frac_positive_active=0.9623, exposure_days=0.3348, via=base, passed=✓
- **G5 PASS** — dsr=0.9896, pbo=0.2278, sr=0.0784, sr0=0.0264, n_trials=51, var_trial_sr=0.0001
- **G6 PASS** — rule=b, sharpe=0.9060, max_dd=0.0179, stress_sharpe=0.7063, stress_mdd=0.0184, mc_mdd95=0.0418, n_trades=38, total_return=0.0192, cost=coinone_conservative, stress_cost=coinone_stress, reference_engine_match=✓, val_k=3, val_p_value=0.2634, val_holm_passed=✗, ok_sharpe=✓, ok_mdd=✓, ok_stress_sharpe=✓, ok_mc_mdd=✓, path=sharpe, stress_mode=off, drawdown_mode=size, position_scale=1, ok_position_scale=✓, val_evidence=combined, val_evidence_passed=✓
  - exposure-aware variant (shadow, OFF — information only): applicable=✓, status=flat_pass, exposure_days=0.2044, total_return=0.0192, stress_total_return=0.0145, max_dd=0.0179, bh_total_return=-0.3907, bh_max_dd=0.4947, n_trades=38, dev_exposure_days=0.3348, passed=✓
- G3 neighbors: tp_atr=1.5→1.41

Buy & hold reference (Dev, primary cost; information only — not a gate):

| | strategy | EW 3-asset buy & hold |
|---|---|---|
| Total return | 0.799 | 11.453 |
| Sharpe | 1.497 | 1.148 |
| Max drawdown | 0.092 | 0.730 |

- time in market (exposure_time) 0.040; days in market (exposure_days) 0.335; exposure-matched B&H return 0.148; excess vs matched B&H 0.651; excess vs B&H -10.655; up-day capture 0.057; down-day capture 0.033; up-regime return (trailing 30d BTC > 0) 0.535; down-regime return (trailing 30d BTC ≤ 0) 0.173; up-regime capture 0.143; down-regime capture -0.771; downside deviation (ann.) 0.046; rolling 6m windows neutral / active 0 / 53 (neutral = exposure_days < 0.10)
- Worst B&H drawdown episodes: #1 2021-11-09→2022-06-18 B&H −0.730 → strategy 0.030; #2 2021-05-09→2021-07-20 B&H −0.572 → strategy -0.037; #3 2025-01-18→2025-04-08 B&H −0.425 → strategy -0.021

Per-asset (Dev, primary cost; information only — gates use the pooled portfolio):

| asset | n_trades | recent_n_trades | sharpe_w | sharpe | total_return | max_dd | expectancy | win_rate |
|---|---|---|---|---|---|---|---|---|
| BTC-KRW | 198 | 48 | 1.739 | 1.811 | 0.985 | 0.048 | 0.007 | 0.586 |
| XRP-KRW | 150 | 40 | 1.340 | 0.896 | 1.073 | 0.225 | 0.011 | 0.507 |
| ETH-KRW | 206 | 44 | 0.604 | 0.567 | 0.310 | 0.097 | 0.003 | 0.529 |

Per-asset **Val** (HUMAN-ONLY, information only):

| asset | n_trades | sharpe | total_return | max_dd | expectancy |
|---|---|---|---|---|---|
| BTC-KRW | 13 | 0.010 | -0.000 | 0.024 | 0.000 |
| XRP-KRW | 9 | 2.935 | 0.126 | 0.019 | 0.027 |
| ETH-KRW | 16 | -2.113 | -0.061 | 0.061 | -0.008 |

Buy & hold reference (**Val** — HUMAN-ONLY; information only — not a gate):

| | strategy | EW 3-asset buy & hold |
|---|---|---|
| Total return | 0.019 | -0.391 |
| Sharpe | 0.906 | -1.479 |
| Max drawdown | 0.018 | 0.495 |

- time in market (exposure_time) 0.027; days in market (exposure_days) 0.204; exposure-matched B&H return -0.011; excess vs matched B&H 0.030; excess vs B&H 0.410; up-day capture 0.027; down-day capture 0.012; downside deviation (ann.) 0.020

Tail risk (Dev, realized daily returns; information only — not a gate):

- worst day -0.046; worst week -0.044; CVaR 5% (daily) -0.009; skew (daily) 2.021; max inventory (fraction of sleeve) 0.500; longest time at max inventory (min) 1440; 2022 bear: total return 0.067; 2022 bear: Sharpe 1.006; 2022 bear: max DD 0.039; return in worst B&H drawdown 0.030; positions (ladders) / tranche rows 554 / 554

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.149 | 1.162 | 0.092 | 148 |
| 2022 | 365 | 0.067 | 1.006 | 0.039 | 82 |
| 2023 | 365 | 0.111 | 1.983 | 0.025 | 94 |
| 2024 | 366 | 0.219 | 2.508 | 0.030 | 133 |
| 2025 | 304 | 0.083 | 1.425 | 0.034 | 97 |

Ablation: no filters (nothing to remove).

Cost grade **A** — under `upbit_conservative`: G2 PASS, G4 PASS, G6 PASS

- **G6 PASS** — rule=b, sharpe=0.5431, max_dd=0.0193, stress_sharpe=0.0763, stress_mdd=0.0210, mc_mdd95=0.0468, n_trades=38, total_return=0.0110, cost=upbit_conservative, stress_cost=upbit_stress, reference_engine_match=✓, ok_sharpe=✓, ok_mdd=✓, ok_stress_sharpe=✓, ok_mc_mdd=✓, path=sharpe, stress_mode=off, drawdown_mode=size, position_scale=1, ok_position_scale=✓
  - exposure-aware variant (shadow, OFF — information only): applicable=✓, status=flat_pass, exposure_days=0.2044, total_return=0.0110, stress_total_return=0.0011, max_dd=0.0193, bh_total_return=-0.3907, bh_max_dd=0.4947, n_trades=38, dev_exposure_days=0.3348, passed=✓ (grade cost)

Risk limits (§7, from Val): daily -0.0300, weekly -0.0600, drawdown -0.1200, max trades/day 5

Robustness suite (Dev, coinone_conservative): **robust**

| check | result | key numbers |
|---|---|---|
| latency | PASS | base_sharpe=1.497; min_retention=0.845 |
| outliers | PASS | base_sharpe=1.497; n_trades=554; top_1pct→SR 1.13; top_5→SR 1.19 |
| leave_year_out | PASS | min_sharpe=1.246; worst_year_removed=2024 |
| cost | PASS | ×1.0→SR 1.50; ×1.5→SR 1.42; ×2.0→SR 1.34 |
| regime | PASS | trend: max share 0.52; vol: max share 0.42 |
| alpha | PASS | alpha_t=2.436; beta=0.053; exposure=0.040 |
| cross_venue | PASS | other_venue=upbit; sharpe_target=1.497; sharpe_other=1.767; ratio=1.180 |
| tail | ok (informational) | mtm_worst_day=-0.046; mtm_worst_week=-0.044; mtm_cvar5_daily=-0.009; mtm_max_dd=0.092; max_inventory=0.500; max_inventory_minutes=1440; bear_total_return=0.067 |

Registry: {'strategy_id': 'r1b_vol_breakout_donch_4h_u3_half_0dce65e919', 'version': 1, 'state': 'validated', 'risk_yaml': '/home/user/temp/strategies/validated/r1b_vol_breakout_donch_4h_u3_half_0dce65e919_v1.risk.yaml'}

## Conclusion

1 candidate(s) passed G0–G6 and the robustness suite: `r1b_vol_breakout_donch_4h_u3_half_0dce65e919` (grade A). Registered as `validated` (risk limits in `<id>_v<ver>.risk.yaml`). Next: skeptic review (candidate-review), then a lockbox (G7) request that a human must approve. Lockbox was **not** accessed.
