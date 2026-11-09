# Campaign camp-000013 — Coinone BTC-KRW 1h: atr_channel_breakout(span 24, mult 3) with ATR exits (SL 2/3 ATR x TP 1.5/3 ATR, max hold 480 min = information horizon) earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite.

Date (UTC) 2026-11-09T23:32:11+00:00 · hypothesis hyp-000013 (registered 2026-11-09T23:32:10+00:00) · campaign registered 2026-11-09T23:32:10+00:00 · family `vol_breakout` · data snapshot `snap-93b066dd27ee41f0a0ec` · code `d84669e94a1eb443b4c733f2b32c044a4dfb4f92+dirty`

**Verdict: no candidate passed.**

> ⚠ HUMAN-ONLY report: contains Val (G6) detail. Do not paste Val numbers into agent prompts, lessons or hypotheses. Agent-safe version: `research/reports/campaign_camp-000013.md`.

## Hypothesis (pre-registered)

- Text: Coinone BTC-KRW 1h: atr_channel_breakout(span 24, mult 3) with ATR exits (SL 2/3 ATR x TP 1.5/3 ATR, max hold 480 min = information horizon) earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite.
- Rationale: Mechanism M3 at multi-hour horizons (time-series momentum: slow information diffusion, trend-following flows, short-liquidation/stop cascades). Counterparty: counter-trend limit sellers and stops at breakout levels, late chasers we sell to at exit. Kills it: bear regimes for a long-only spot book (2022 excess ~0 or negative in the per-year side calculation for several configs), false breakouts, regime beta (robustness alpha check). Screen camp-000009 (Dev only, lockbox none, Val none) passed this entry; exits are ATR multiples centred on the screen's MFE/MAE suggestion and max hold at the information horizon. Pre-stated expectation: Dev Sharpe >= 1.5 with leave-year-out and alpha checks passing; per-year side calculation showed 2022/2025 weakness, so REGIME_DEPENDENT is the predicted failure mode. Screen config: n=1165, info horizon 480 (peaked), net +29.0 bp at 480 (Upbit grade cost +14.9 bp); suggestion TP 1.41 ATR, SL 2.03 ATR. [derived-from drv-000008 ← fact-000009; family vol_breakout]
- Success criteria: At least one spec passes G0-G6 and the Dev robustness suite (latency, outliers, leave-year-out, cost x1.5, regime, alpha, cross-venue). Refuted for this timeframe if the best Dev Sharpe_w < 1 or leave-year-out/regime/alpha fail.

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 2,541,600 1m rows (filled 7.56%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- Entry `atr_channel_breakout`: {'span': [24], 'mult': [3.0]}
- Filter set 0: (no filter)
- Decision timeframe **1h**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [2.0, 3.0], 'tp_atr': [1.5, 3.0], 'max_hold_min': [480]} → 4 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress`
- Specs: **4** (template budget 4); runs recorded: 8; runtime 1.75s

## Signal screen linkage

- Gate: every signal config passed camp-000009 (every (entry params × filter params × timeframe × exchange) config passed a recorded screen); 1 configs matched:
  - atr_channel_breakout{'span': 24, 'mult': 3.0} ← screen `camp-000009` run `run-000103` (p_signal 0.0000, events 1165, info horizon 480)

## Power check (at registration)

- 계획 격자 4 스펙, 계열 `vol_breakout` 기존 시도 33, 압축비 0.188 (history median of 4 campaigns) → 실질 시도 ≈ 6.9
- Dev 1765일, V=0.000566 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 1.38** (시도 1회였다면 0.75)
- 목표 연 Sharpe 2 로 감당 가능한 실질 시도 ≈ 191 (격자 ≈ 986 스펙)
- Required annual Sharpe stored: **1.38**; target 2; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.span | entry.mult | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | recent_n_trades | expectancy | max_dd | rolling_frac_positive |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c2b_vol_breakout_atrch_1h_atr_72bbc9dae5 | - | 24 | 3 | 2 | 3 | 480 | 1.019 | 1.457 | 0.783 | 610 | 114 | 0.002 | 0.129 | 0.792 |
| c2b_vol_breakout_atrch_1h_atr_ff6f052751 | - | 24 | 3 | 3 | 3 | 480 | 0.997 | 1.487 | 0.827 | 598 | 113 | 0.002 | 0.129 | 0.811 |
| c2b_vol_breakout_atrch_1h_atr_f7ba8f0c09 | - | 24 | 3 | 3 | 1.500 | 480 | 0.455 | 0.996 | 0.584 | 717 | 139 | 0.001 | 0.186 | 0.642 |
| c2b_vol_breakout_atrch_1h_atr_7b0be2b923 | - | 24 | 3 | 2 | 1.500 | 480 | 0.244 | 0.833 | 0.196 | 727 | 140 | 0.001 | 0.190 | 0.604 |

Distribution of weighted Sharpe over all 4 specs: median 0.726, share > 0 100.0%, max 1.019.

## Funnel

- All specs (metric-only gates): 4 specs → G1 4, weighted Sharpe > 0 4, G4 4, all three 4
- Top 1 candidates: G0 1, G1 1, G2 1, G3 1, G4 1, G5 1 → Dev survivors (all G0–G5) 1 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=4, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 1.7
- Ledger trials: family 37, global 94 → DSR trials N = max(K, N_family·K/N) = 9.2; V = 0.000296 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.058

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 4 specs → 1 clusters (largest sizes [4]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| 72bbc9dae527d230 | 1 | 4 | 1.019 |

- Val budget status: val_id=val1, cap=20, used=1, remaining=19, passed=0, pending=0

## Candidates — per-gate results

### #1 `c2b_vol_breakout_atrch_1h_atr_72bbc9dae5` — failed G6 (ledger only)

```yaml
id: c2b_vol_breakout_atrch_1h_atr_72bbc9dae5
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
```
Weighted Sharpe 1.019, Sharpe 1.457, trades 610, expectancy 0.00185, MDD 0.129

- **G0 PASS** — validate=ok, timeframe=1h, exit_mode=atr, n_filters=0, n_params=5, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=3, causality_violations=[]
- **G1 PASS** — n_trades=610, recent_n_trades=114
- **G2 PASS** — sharpe_w=1.0185, p_value=0.0110, percentile=0.9900, n_null=1000, calibration=trade_count, p_signal_rate=0.0456, p=0.0168, tf_min=60, atr_bars=14, n_trades_median=623, candidate_n_trades=610, sharpe_w_median=-0.1897, sharpe_w_p95=0.7044, cost=coinone_conservative
- **G3 PASS** — selected=1.0185, neighbor_median=0.6207, required=0.5093, n_neighbors=2
- **G4 PASS** — rolling_frac_positive=0.7925, rolling_n_windows=53, recent_sharpe=0.7830
- **G5 PASS** — dsr=0.9912, pbo=0.0583, sr=0.0763, sr0=0.0264, n_trials=9.2500, var_trial_sr=0.0003
- **G6 FAIL** — rule=b, sharpe=-0.1919, max_dd=0.0882, stress_sharpe=-0.5710, stress_mdd=0.0991, mc_mdd95=0.1481, n_trades=48, total_return=-0.0124, cost=coinone_conservative, stress_cost=coinone_stress, reference_engine_match=✓, val_k=1, val_p_value=0.5452, val_holm_passed=✗, ok_sharpe=✗, ok_mdd=✓, ok_stress_sharpe=✗, ok_mc_mdd=✓
- G3 neighbors: sl_atr=3.0→1.00, tp_atr=1.5→0.24

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.480 | 2.246 | 0.113 | 126 |
| 2022 | 365 | 0.070 | 0.500 | 0.129 | 98 |
| 2023 | 365 | 0.373 | 2.026 | 0.100 | 152 |
| 2024 | 366 | 0.373 | 1.850 | 0.088 | 140 |
| 2025 | 304 | -0.022 | -0.250 | 0.062 | 94 |

Ablation: no filters (nothing to remove).

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G6': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).
