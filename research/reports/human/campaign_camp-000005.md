# Campaign camp-000005 — Coinone BTC-KRW 5m: bollinger_z_revert(window 96, z 2.5/3.0), with or without trend_regime_1h(24), held 120-240 min with ATR-multiple exits, earns positive coinone_conservative net returns that survive G0-G6 and the robustness suite.

Date (UTC) 2026-11-09T16:18:04+00:00 · hypothesis hyp-000005 (registered 2026-11-09T16:18:01+00:00) · campaign registered 2026-11-09T16:18:01+00:00 · family `liq_meanrev` · data snapshot `snap-93b066dd27ee41f0a0ec` · code `e2d2ff63ce9ab272162441125b6d784cb3e30a73+dirty`

**Verdict: no candidate passed.**

> ⚠ HUMAN-ONLY report: contains Val (G6) detail. Do not paste Val numbers into agent prompts, lessons or hypotheses. Agent-safe version: `research/reports/campaign_camp-000005.md`.

## Hypothesis (pre-registered)

- Text: Coinone BTC-KRW 5m: bollinger_z_revert(window 96, z 2.5/3.0), with or without trend_regime_1h(24), held 120-240 min with ATR-multiple exits, earns positive coinone_conservative net returns that survive G0-G6 and the robustness suite.
- Rationale: M4 mean reversion after forced/panic selling (counterparty: liquidated levered longs, panic-selling retail). Entry/filters fixed to the 4 window-96 configs that all passed screen camp-000004 (BH q=0.10, net>0): info horizons 30-240 min (unfiltered z2.5 rising to 240; trend-filtered z2.5 peaked at 120). Exits from screen ATR suggestions (TP ~2-3.4 ATR = MFE median, SL ~2.7-6 ATR = |MAE| p75): SL {3,5} ATR x TP {3} ATR x max hold {120,240} min = 4 exit variants (low turnover, <1 trade/day). Window 24 configs not used (screen neighbours at different lookback; keep grid small). Expected annual Sharpe estimate from screen net mean x frequency: ~1.2 (unfiltered) to ~2.5 (trend-filtered). Upbit-cost grade expected to fail (screen upbit_conservative net < 0) -> at best grade B. No lockbox/Val-exposed evidence.
- Success criteria: At least one spec passes G0-G6 and the Dev robustness suite (outliers / leave-year-out / latency / cost x1.5 / regime / alpha).

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 2,541,600 1m rows (filled 7.56%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- Entry `bollinger_z_revert`: {'window': [96], 'z': [2.5, 3.0]}
- Filter set 0: (no filter)
- Filter set 1: `trend_regime_1h` {'span': [24]}
- Decision timeframe **5m**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [3.0, 5.0], 'tp_atr': [3.0], 'max_hold_min': [120, 240]} → 4 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress`
- Specs: **16** (template budget 16); runs recorded: 32; runtime 2.93s

## Signal screen linkage

- Gate: every signal config passed camp-000004 (every (entry params × filter params × timeframe × exchange) config passed a recorded screen); 4 configs matched:
  - bollinger_z_revert{'window': 96, 'z': 2.5} ← screen `camp-000004` run `run-000026` (p_signal 0.0011, events 2999, info horizon 240)
  - bollinger_z_revert{'window': 96, 'z': 2.5} & trend_regime_1h{'span': 24} ← screen `camp-000004` run `run-000027` (p_signal 0.0000, events 678, info horizon 120)
  - bollinger_z_revert{'window': 96, 'z': 3.0} ← screen `camp-000004` run `run-000028` (p_signal 0.0012, events 1657, info horizon 60)
  - bollinger_z_revert{'window': 96, 'z': 3.0} & trend_regime_1h{'span': 24} ← screen `camp-000004` run `run-000029` (p_signal 0.0001, events 396, info horizon 30)

## Power check (at registration)

- 계획 격자 16 스펙, 계열 `liq_meanrev` 기존 시도 10, 압축비 0.05 (default 0.05 (history 0 < 3)) → 실질 시도 ≈ 1.3
- Dev 1765일, V=0.000567 (1/n_days (null minimum)), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 0.76** (시도 1회였다면 0.75)
- 목표 연 Sharpe 2 로 감당 가능한 실질 시도 ≈ 190 (격자 ≈ 3784 스펙)
- Required annual Sharpe stored: **0.76**; target 2; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.window | entry.z | trend_regime_1h.span | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | recent_n_trades | expectancy | max_dd | rolling_frac_positive |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c1_liq_meanrev_atr_v1_ff60d1a7c3 | trend_regime_1h | 96 | 3 | 24 | 5 | 3 | 120 | 0.886 | 0.837 | 1.735 | 410 | 77 | 0.001 | 0.095 | 0.547 |
| c1_liq_meanrev_atr_v1_d9c74bf65e | trend_regime_1h | 96 | 3 | 24 | 5 | 3 | 240 | 0.863 | 1.003 | 2.052 | 410 | 77 | 0.001 | 0.092 | 0.679 |
| c1_liq_meanrev_atr_v1_324240ca4c | trend_regime_1h | 96 | 3 | 24 | 3 | 3 | 120 | 0.708 | 0.648 | 1.942 | 449 | 83 | 0.000 | 0.114 | 0.547 |
| c1_liq_meanrev_atr_v1_7f0af526d7 | trend_regime_1h | 96 | 2.500 | 24 | 5 | 3 | 120 | 0.607 | 1.036 | 0.991 | 699 | 140 | 0.001 | 0.115 | 0.604 |
| c1_liq_meanrev_atr_v1_ce1d964f1c | trend_regime_1h | 96 | 2.500 | 24 | 5 | 3 | 240 | 0.604 | 1.054 | 1.409 | 696 | 140 | 0.001 | 0.123 | 0.585 |
| c1_liq_meanrev_atr_v1_f287a61c90 | trend_regime_1h | 96 | 3 | 24 | 3 | 3 | 240 | 0.560 | 0.769 | 1.930 | 449 | 83 | 0.001 | 0.114 | 0.585 |
| c1_liq_meanrev_atr_v1_0b89e1f404 | - | 96 | 3 | – | 5 | 3 | 120 | 0.485 | 0.632 | 1.620 | 1728 | 325 | 0.000 | 0.369 | 0.434 |
| c1_liq_meanrev_atr_v1_21b44ca03b | - | 96 | 3 | – | 5 | 3 | 240 | 0.354 | 0.345 | 1.525 | 1715 | 323 | 0.000 | 0.478 | 0.528 |
| c1_liq_meanrev_atr_v1_6c77541a1c | trend_regime_1h | 96 | 2.500 | 24 | 3 | 3 | 240 | 0.277 | 0.824 | 0.911 | 764 | 152 | 0.000 | 0.140 | 0.509 |
| c1_liq_meanrev_atr_v1_c0c13ad860 | trend_regime_1h | 96 | 2.500 | 24 | 3 | 3 | 120 | 0.177 | 0.733 | 0.671 | 766 | 152 | 0.000 | 0.157 | 0.472 |

Distribution of weighted Sharpe over all 16 specs: median 0.316, share > 0 75.0%, max 0.886.

## Funnel

- All specs (metric-only gates): 16 specs → G1 16, weighted Sharpe > 0 12, G4 2, all three 2
- Top 2 candidates: G0 2, G1 2, G2 2, G3 1, G4 0, G5 0 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=16, correlation clusters K=2 (threshold 0.5), eigen-entropy trials 4.3
- Ledger trials: family 26, global 47 → DSR trials N = max(K, N_family·K/N) = 3.2; V = 0.000851 (cluster representatives)
- PBO (CSCV, 16 splits) = 0.448

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 16 specs → 2 clusters (largest sizes [8, 8]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| ff60d1a7c3c5c055 | 1 | 8 | 0.886 |
| 0b89e1f4047261c3 | 2 | 8 | 0.485 |

## Candidates — per-gate results

### #1 `c1_liq_meanrev_atr_v1_ff60d1a7c3` — failed G4 (ledger only)

```yaml
id: c1_liq_meanrev_atr_v1_ff60d1a7c3
entry:
  block: bollinger_z_revert
  params:
    window: 96
    z: 3.0
exit:
  max_hold_min: 120
  signal_exit: null
  stop_loss_atr: 5.0
  take_profit_atr: 3.0
filters:
- block: trend_regime_1h
  params:
    span: 24
version: 1
```
Weighted Sharpe 0.886, Sharpe 0.837, trades 410, expectancy 0.00071, MDD 0.095

- **G0 PASS** — validate=ok, timeframe=5m, exit_mode=atr, n_filters=1, n_params=6, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=3, causality_violations=[]
- **G1 PASS** — n_trades=410, recent_n_trades=77
- **G2 PASS** — sharpe_w=0.8857, p_value=0.0010, percentile=1, n_null=1000, calibration=trade_count, p_signal_rate=0.0051, p=0.0021, tf_min=5, atr_bars=14, n_trades_median=409, candidate_n_trades=410, sharpe_w_median=-0.8904, sharpe_w_p95=0.0099, cost=coinone_conservative
- **G3 PASS** — selected=0.8857, neighbor_median=0.7081, required=0.4428, n_neighbors=3
- **G4 FAIL** — rolling_frac_positive=0.5472, rolling_n_windows=53, recent_sharpe=1.7352
- **G5 FAIL** — dsr=0.7645, pbo=0.4485, sr=0.0438, sr0=0.0266, n_trials=3.2500, var_trial_sr=0.0009
- G3 neighbors: entry.z=2.5→0.61, sl_atr=3.0→0.71, max_hold_min=240→0.86

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.161 | 1.727 | 0.094 | 81 |
| 2022 | 365 | 0.002 | 0.063 | 0.068 | 86 |
| 2023 | 365 | 0.038 | 0.683 | 0.056 | 84 |
| 2024 | 366 | 0.016 | 0.252 | 0.053 | 99 |
| 2025 | 304 | 0.076 | 1.731 | 0.028 | 60 |

### #2 `c1_liq_meanrev_atr_v1_0b89e1f404` — failed G3 (ledger only)

```yaml
id: c1_liq_meanrev_atr_v1_0b89e1f404
entry:
  block: bollinger_z_revert
  params:
    window: 96
    z: 3.0
exit:
  max_hold_min: 120
  signal_exit: null
  stop_loss_atr: 5.0
  take_profit_atr: 3.0
filters: []
version: 1
```
Weighted Sharpe 0.485, Sharpe 0.632, trades 1728, expectancy 0.00029, MDD 0.369

- **G0 PASS** — validate=ok, timeframe=5m, exit_mode=atr, n_filters=0, n_params=5, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=3, causality_violations=[]
- **G1 PASS** — n_trades=1728, recent_n_trades=325
- **G2 PASS** — sharpe_w=0.4854, p_value=0.0010, percentile=1, n_null=1000, calibration=trade_count, p_signal_rate=0.0158, p=0.0047, tf_min=5, atr_bars=14, n_trades_median=1689, candidate_n_trades=1728, sharpe_w_median=-1.4948, sharpe_w_p95=-0.6462, cost=coinone_conservative
- **G3 FAIL** — selected=0.4854, neighbor_median=0.1648, required=0.2427, n_neighbors=3
- **G4 FAIL** — rolling_frac_positive=0.4340, rolling_n_windows=53, recent_sharpe=1.6204
- **G5 FAIL** — dsr=0.6104, pbo=0.4485, sr=0.0331, sr0=0.0266, n_trials=3.2500, var_trial_sr=0.0009
- G3 neighbors: entry.z=2.5→0.13, sl_atr=3.0→0.16, max_hold_min=240→0.35

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.549 | 2.221 | 0.195 | 354 |
| 2022 | 365 | -0.066 | -0.360 | 0.153 | 391 |
| 2023 | 365 | -0.078 | -0.674 | 0.172 | 353 |
| 2024 | 366 | 0.080 | 0.508 | 0.170 | 374 |
| 2025 | 304 | 0.063 | 0.741 | 0.055 | 256 |

## Conclusion

**No candidate passed.** First failing gate among the top 2: {'G4': 1, 'G3': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).
