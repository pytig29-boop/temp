# Campaign camp-000008 — Coinone 5m bollinger_z_revert(96, 2.5) with/without trend_regime_1h(24), held 120-240 min with wide ATR exits (SL 6, TP 6 ATR) that approximate the event study's time exit, passes G0-G5 on Dev.

Date (UTC) 2026-11-09T22:49:54+00:00 · hypothesis hyp-000008 (registered 2026-11-09T22:49:51+00:00) · campaign registered 2026-11-09T22:49:51+00:00 · family `liq_meanrev` · data snapshot `snap-93b066dd27ee41f0a0ec` · code `c61ec03e407146b87ec4858a529fbae9cc056441`

**Verdict: no candidate passed.**

> ⚠ HUMAN-ONLY report: contains Val (G6) detail. Do not paste Val numbers into agent prompts, lessons or hypotheses. Agent-safe version: `research/reports/campaign_camp-000008.md`.

## Hypothesis (pre-registered)

- Text: Coinone 5m bollinger_z_revert(96, 2.5) with/without trend_regime_1h(24), held 120-240 min with wide ATR exits (SL 6, TP 6 ATR) that approximate the event study's time exit, passes G0-G5 on Dev.
- Rationale: int-000003: camp-000005 TP 3 / SL 5 ATR may truncate the 120-240 min reversion drift measured by screen camp-000004; this tests a time-dominated exit on the strongest screened configs (z 2.5). New campaign in the same family (2 signal configs x 2 exit variants = 4 specs). [derived-from drv-000002 ← fact-000005; family liq_meanrev]
- Success criteria: At least one spec passes G0-G6 and the Dev robustness suite; refuted if Dev Sharpe < 1 and G4 rolling fraction < 0.6.

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 2,541,600 1m rows (filled 7.56%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- Entry `bollinger_z_revert`: {'window': [96], 'z': [2.5]}
- Filter set 0: (no filter)
- Filter set 1: `trend_regime_1h` {'span': [24]}
- Decision timeframe **5m**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [6.0], 'tp_atr': [6.0], 'max_hold_min': [120, 240]} → 2 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress`
- Specs: **4** (template budget 4); runs recorded: 8; runtime 3.58s

## Signal screen linkage

- Gate: every signal config passed camp-000004 (every (entry params × filter params × timeframe × exchange) config passed a recorded screen); 2 configs matched:
  - bollinger_z_revert{'window': 96, 'z': 2.5} ← screen `camp-000004` run `run-000026` (p_signal 0.0011, events 2999, info horizon 240)
  - bollinger_z_revert{'window': 96, 'z': 2.5} & trend_regime_1h{'span': 24} ← screen `camp-000004` run `run-000027` (p_signal 0.0000, events 678, info horizon 120)

## Power check (at registration)

- 계획 격자 4 스펙, 계열 `liq_meanrev` 기존 시도 26, 압축비 0.125 (history median of 3 campaigns) → 실질 시도 ≈ 3.8
- Dev 1765일, V=0.000851 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 1.31** (시도 1회였다면 0.75)
- 목표 연 Sharpe 2 로 감당 가능한 실질 시도 ≈ 46 (격자 ≈ 340 스펙)
- Required annual Sharpe stored: **1.31**; target 2; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.window | entry.z | trend_regime_1h.span | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | recent_n_trades | expectancy | max_dd | rolling_frac_positive |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c2_liq_meanrev_wide_atr_v1_728b01519b | - | 96 | 2.500 | – | 6 | 6 | 120 | 0.594 | 0.531 | 1.422 | 2652 | 523 | 0.000 | 0.371 | 0.453 |
| c2_liq_meanrev_wide_atr_v1_7cf92d03f9 | trend_regime_1h | 96 | 2.500 | 24 | 6 | 6 | 120 | 0.571 | 1.055 | 0.928 | 653 | 129 | 0.001 | 0.128 | 0.642 |
| c2_liq_meanrev_wide_atr_v1_5b0260933b | trend_regime_1h | 96 | 2.500 | 24 | 6 | 6 | 240 | 0.562 | 0.713 | 1.599 | 646 | 128 | 0.001 | 0.177 | 0.642 |
| c2_liq_meanrev_wide_atr_v1_90b80c3c1c | - | 96 | 2.500 | – | 6 | 6 | 240 | 0.554 | 0.361 | 1.415 | 2507 | 491 | 0.000 | 0.471 | 0.566 |

Distribution of weighted Sharpe over all 4 specs: median 0.567, share > 0 100.0%, max 0.594.

## Funnel

- All specs (metric-only gates): 4 specs → G1 4, weighted Sharpe > 0 4, G4 2, all three 2
- Top 2 candidates: G0 2, G1 2, G2 2, G3 2, G4 1, G5 0 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=4, correlation clusters K=2 (threshold 0.5), eigen-entropy trials 2.4
- Ledger trials: family 30, global 63 → DSR trials N = max(K, N_family·K/N) = 15; V = 0.000280 (cluster representatives)
- PBO (CSCV, 16 splits) = 0.322

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 4 specs → 2 clusters (largest sizes [2, 2]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| 728b01519b0c4dea | 2 | 2 | 0.594 |
| 7cf92d03f9b8d576 | 1 | 2 | 0.571 |

## Candidates — per-gate results

### #1 `c2_liq_meanrev_wide_atr_v1_728b01519b` — failed G4 (ledger only)

```yaml
id: c2_liq_meanrev_wide_atr_v1_728b01519b
entry:
  block: bollinger_z_revert
  params:
    window: 96
    z: 2.5
exit:
  max_hold_min: 120
  signal_exit: null
  stop_loss_atr: 6.0
  take_profit_atr: 6.0
filters: []
version: 1
```
Weighted Sharpe 0.594, Sharpe 0.531, trades 2652, expectancy 0.00023, MDD 0.371

- **G0 PASS** — validate=ok, timeframe=5m, exit_mode=atr, n_filters=0, n_params=5, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=3, causality_violations=[]
- **G1 PASS** — n_trades=2652, recent_n_trades=523
- **G2 PASS** — sharpe_w=0.5945, p_value=0.0010, percentile=1, n_null=1000, calibration=trade_count, p_signal_rate=0.0354, p=0.0081, tf_min=5, atr_bars=14, n_trades_median=2722, candidate_n_trades=2652, sharpe_w_median=-1.3450, sharpe_w_p95=-0.5137, cost=coinone_conservative
- **G3 PASS** — selected=0.5945, neighbor_median=0.5541, required=0.2972, n_neighbors=1
- **G4 FAIL** — rolling_frac_positive=0.4528, rolling_n_windows=53, recent_sharpe=1.4216
- **G5 FAIL** — dsr=0.4685, pbo=0.3217, sr=0.0278, sr0=0.0296, n_trials=15, var_trial_sr=0.0003
- G3 neighbors: max_hold_min=240→0.55

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.704 | 1.850 | 0.188 | 569 |
| 2022 | 365 | -0.196 | -0.853 | 0.228 | 591 |
| 2023 | 365 | -0.101 | -0.682 | 0.198 | 509 |
| 2024 | 366 | 0.256 | 0.972 | 0.171 | 555 |
| 2025 | 304 | 0.036 | 0.330 | 0.107 | 428 |

### #2 `c2_liq_meanrev_wide_atr_v1_7cf92d03f9` — failed G5 (ledger only)

```yaml
id: c2_liq_meanrev_wide_atr_v1_7cf92d03f9
entry:
  block: bollinger_z_revert
  params:
    window: 96
    z: 2.5
exit:
  max_hold_min: 120
  signal_exit: null
  stop_loss_atr: 6.0
  take_profit_atr: 6.0
filters:
- block: trend_regime_1h
  params:
    span: 24
version: 1
```
Weighted Sharpe 0.571, Sharpe 1.055, trades 653, expectancy 0.00086, MDD 0.128

- **G0 PASS** — validate=ok, timeframe=5m, exit_mode=atr, n_filters=1, n_params=6, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=3, causality_violations=[]
- **G1 PASS** — n_trades=653, recent_n_trades=129
- **G2 PASS** — sharpe_w=0.5713, p_value=0.0070, percentile=0.9940, n_null=1000, calibration=trade_count, p_signal_rate=0.0103, p=0.0035, tf_min=5, atr_bars=14, n_trades_median=649, candidate_n_trades=653, sharpe_w_median=-0.7295, sharpe_w_p95=0.1360, cost=coinone_conservative
- **G3 PASS** — selected=0.5713, neighbor_median=0.5618, required=0.2857, n_neighbors=1
- **G4 PASS** — rolling_frac_positive=0.6415, rolling_n_windows=53, recent_sharpe=0.9277
- **G5 FAIL** — dsr=0.8715, pbo=0.3217, sr=0.0552, sr0=0.0296, n_trials=15, var_trial_sr=0.0003
- G3 neighbors: max_hold_min=240→0.56

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.505 | 2.602 | 0.090 | 143 |
| 2022 | 365 | 0.027 | 0.292 | 0.100 | 138 |
| 2023 | 365 | 0.048 | 0.620 | 0.081 | 120 |
| 2024 | 366 | 0.054 | 0.568 | 0.070 | 148 |
| 2025 | 304 | 0.000 | 0.032 | 0.052 | 104 |

## Conclusion

**No candidate passed.** First failing gate among the top 2: {'G4': 1, 'G5': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).
