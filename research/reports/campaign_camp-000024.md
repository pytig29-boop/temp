# Campaign camp-000024 — Coinone BTC-KRW 1h: a >= 100 bp rise over 3 h or 12 h (roc_threshold) on abnormally high decision-bar volume (volume_z(48) >= 1), with ATR exits (SL 3 ATR x TP 2 / 4 ATR, max hold 1440 min = information horizon or 2880 min low-turnover variant), earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite (incl. regime share <= 70%).

Date (UTC) 2026-11-10T01:37:09+00:00 · hypothesis hyp-000023 (registered 2026-11-10T01:37:07+00:00) · campaign registered 2026-11-10T01:37:07+00:00 · family `vol_breakout` · data snapshot `snap-93b066dd27ee41f0a0ec` · code `c987c88e946e6592698a04e8472d28c52aa6b5cc+dirty`

**Verdict: no candidate passed.**

> Agent-safe report: Dev detail + Val (G6) / lockbox **outcome codes only**. The full human report is `research/reports/human/campaign_camp-000024.md` (human-only; not readable in research mode).

## Hypothesis (pre-registered)

- Text: Coinone BTC-KRW 1h: a >= 100 bp rise over 3 h or 12 h (roc_threshold) on abnormally high decision-bar volume (volume_z(48) >= 1), with ATR exits (SL 3 ATR x TP 2 / 4 ATR, max hold 1440 min = information horizon or 2880 min low-turnover variant), earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite (incl. regime share <= 70%).
- Rationale: Mechanism: volume-return dynamics (M3 variant): impulses on abnormal volume carry informed / forced (liquidation, stop) / trend-following flow that continues; counterparty: counter-trend limit sellers and liquidity providers absorbing the flow, late chasers at exit. Kills: bot-contaminated Coinone volume (P-D1), long-only bear regimes, Upbit cost (grade B), generic momentum confound. Evidence (Dev only; lockbox none, Val none): screen camp-000020 (drv-000012): roc(3,100)&vz(48,1) n 981, p 0.058, excess 28.8 bp z 2.07 @1440, net +43.0 bp (Upbit +29.0); roc(12,100)&vz(48,1) n 1419, p 0.075, excess 24.3 bp z 1.96 @1440, net +37.3 bp (Upbit +23.3); both beat their unfiltered siblings (8.3 / 16.9 bp at 1440), so the pre-registered volume contrast is met. Info horizon 1440 (rising). Exit suggestions TP 2.19-2.20 ATR, SL 3.22-3.33 ATR -> SL fixed at 3 ATR, TP bracketing at 2 / 4 ATR, hold 1440 (info horizon) and 2880 (rising profile; low-turnover variant per pipeline_calibration.md). The min_z 2.0 configs (also passing, n 371 / 440) are excluded a priori on sample size: with 24 h holds and one position, G1 (>= 300 Dev trades, recent-12m minimum) would bind. Pre-stated expectations: plausible Sharpe from the screen is ~1.1-1.4 on excess (1.9-2.2 on net incl. drift, overlap ignored), i.e. near the DSR requirement and below the ~2 needed for Val; predicted failure modes: G2 (weak z ~2), regime share (long-only up-trend tilt), G4 recent 12 months, PBO. No side calculations were run. [derived-from drv-000016 ← fact-000017; family vol_breakout]
- Success criteria: At least one spec passes G0-G6 and the Dev robustness suite. Refuted as a tradeable edge if no spec passes G2 on Dev or the best Dev Sharpe_w < 1.0.

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 2,541,600 1m rows (filled 7.56%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- Entry `roc_threshold`: {'lookback': [3, 12], 'thr_bp': [100]}
- Filter set 0: `volume_z` {'window': [48], 'min_z': [1.0]}
- Decision timeframe **1h**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [3.0], 'tp_atr': [2.0, 4.0], 'max_hold_min': [1440, 2880]} → 4 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress`
- Specs: **8** (template budget 8); runs recorded: 16; runtime 2.45s

## Signal screen linkage

- Gate: every signal config passed camp-000020 (every (entry params × filter params × timeframe × exchange) config passed a recorded screen); 2 configs matched:
  - roc_threshold{'lookback': 3, 'thr_bp': 100} & volume_z{'window': 48, 'min_z': 1.0} ← screen `camp-000020` run `run-000181` (p_signal 0.0575, events 981, info horizon 1440)
  - roc_threshold{'lookback': 12, 'thr_bp': 100} & volume_z{'window': 48, 'min_z': 1.0} ← screen `camp-000020` run `run-000184` (p_signal 0.0749, events 1419, info horizon 1440)

## Power check (at registration)

- 계획 격자 8 스펙, 계열 `vol_breakout` 기존 시도 75, 압축비 0.125 (history median of 7 campaigns) → 실질 시도 ≈ 10.4
- Dev 1765일, V=0.000285 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 1.26** (시도 1회였다면 0.75)
- 목표 연 Sharpe 2 로 감당 가능한 실질 시도 ≈ 10460 (격자 ≈ 83604 스펙)
- Required annual Sharpe stored: **1.26**; target 2; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.lookback | entry.thr_bp | volume_z.window | volume_z.min_z | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | recent_n_trades | expectancy | max_dd | rolling_frac_positive | exposure_days |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c3c_vol_breakout_roc_volz_1h_atr_36b866681f | volume_z | 3 | 100 | 48 | 1 | 3 | 4 | 1440 | 0.906 | 1.204 | 0.693 | 614 | 104 | 0.003 | 0.183 | 0.868 | 0.483 |
| c3c_vol_breakout_roc_volz_1h_atr_8f43208681 | volume_z | 12 | 100 | 48 | 1 | 3 | 4 | 1440 | 0.857 | 1.168 | 0.840 | 676 | 128 | 0.002 | 0.265 | 0.811 | 0.507 |
| c3c_vol_breakout_roc_volz_1h_atr_5de32b960c | volume_z | 3 | 100 | 48 | 1 | 3 | 4 | 2880 | 0.760 | 0.806 | 0.525 | 567 | 98 | 0.002 | 0.324 | 0.811 | 0.544 |
| c3c_vol_breakout_roc_volz_1h_atr_5c7c567fb5 | volume_z | 12 | 100 | 48 | 1 | 3 | 2 | 1440 | 0.631 | 0.872 | 0.634 | 838 | 164 | 0.001 | 0.322 | 0.585 | 0.495 |
| c3c_vol_breakout_roc_volz_1h_atr_40d615aecc | volume_z | 12 | 100 | 48 | 1 | 3 | 2 | 2880 | 0.607 | 0.755 | 0.767 | 805 | 159 | 0.001 | 0.362 | 0.604 | 0.529 |
| c3c_vol_breakout_roc_volz_1h_atr_c89ea216a7 | volume_z | 12 | 100 | 48 | 1 | 3 | 4 | 2880 | 0.556 | 0.818 | 0.399 | 618 | 119 | 0.002 | 0.342 | 0.698 | 0.571 |
| c3c_vol_breakout_roc_volz_1h_atr_49ff23508a | volume_z | 3 | 100 | 48 | 1 | 3 | 2 | 1440 | 0.554 | 0.857 | -0.097 | 727 | 120 | 0.001 | 0.280 | 0.679 | 0.457 |
| c3c_vol_breakout_roc_volz_1h_atr_d80e2b6f34 | volume_z | 3 | 100 | 48 | 1 | 3 | 2 | 2880 | 0.267 | 0.383 | -0.628 | 702 | 119 | 0.001 | 0.435 | 0.585 | 0.496 |

BTC buy & hold over the same Dev period (information only): total return 4.121, Sharpe 0.915, MDD 0.741.

Distribution of weighted Sharpe over all 8 specs: median 0.619, share > 0 100.0%, max 0.906.

## Funnel

- All specs (metric-only gates): 8 specs → G1 8, weighted Sharpe > 0 8, G4 5, all three 5
- Top 1 candidates: G0 1, G1 1, G2 0, G3 1, G4 1, G5 1 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=8, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 3.1
- Ledger trials: family 83, global 144 → DSR trials N = max(K, N_family·K/N) = 10.4; V = 0.000178 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.201

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 8 specs → 1 clusters (largest sizes [8]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| 36b866681f776535 | 1 | 8 | 0.906 |

## Candidates — per-gate results

### #1 `c3c_vol_breakout_roc_volz_1h_atr_36b866681f` — failed G2 (ledger only)

```yaml
id: c3c_vol_breakout_roc_volz_1h_atr_36b866681f
entry:
  block: roc_threshold
  params:
    lookback: 3
    thr_bp: 100
exit:
  max_hold_min: 1440
  signal_exit: null
  stop_loss_atr: 3.0
  take_profit_atr: 4.0
filters:
- block: volume_z
  params:
    window: 48
    min_z: 1.0
version: 1
timeframe: 1h
```
Weighted Sharpe 0.906, Sharpe 1.204, trades 614, expectancy 0.00254, MDD 0.183

- **G0 PASS** — validate=ok, timeframe=1h, exit_mode=atr, n_filters=1, n_params=7, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=3, causality_violations=[]
- **G1 PASS** — n_trades=614, recent_n_trades=104
- **G2 FAIL** — sharpe_w=0.9063, p_value=0.1119, percentile=0.8890, n_null=1000, calibration=trade_count, p_signal_rate=0.1853, p=0.1201, tf_min=60, atr_bars=14, n_trades_median=619, candidate_n_trades=614, sharpe_w_median=0.3862, sharpe_w_p95=1.0912, cost=coinone_conservative
- **G3 PASS** — selected=0.9063, neighbor_median=0.7597, required=0.4532, n_neighbors=3
- **G4 PASS** — rolling_frac_positive=0.8679, rolling_n_windows=53, recent_sharpe=0.6930
  - exposure-aware variant (shadow, OFF — information only): applicable=✓, rolling_n_active=53, rolling_n_neutral=0, rolling_frac_positive_active=0.8679, exposure_days=0.4827, via=base, passed=✓
- **G5 PASS** — dsr=0.9643, pbo=0.2015, sr=0.0630, sr0=0.0213, n_trials=10.3750, var_trial_sr=0.0002
- G3 neighbors: entry.lookback=12→0.86, tp_atr=2.0→0.55, max_hold_min=2880→0.76

Buy & hold reference (Dev, primary cost; information only — not a gate):

| | strategy | BTC buy & hold |
|---|---|---|
| Total return | 3.016 | 4.121 |
| Sharpe | 1.204 | 0.915 |
| Max drawdown | 0.183 | 0.741 |

- time in market (exposure_time) 0.236; days in market (exposure_days) 0.483; exposure-matched B&H return 0.647; excess vs matched B&H 2.369; excess vs B&H -1.104; up-day capture 0.227; down-day capture 0.154; up-regime return (trailing 30d BTC > 0) 1.583; down-regime return (trailing 30d BTC ≤ 0) 0.580; up-regime capture 0.447; down-regime capture -2.446; downside deviation (ann.) 0.161; rolling 6m windows neutral / active 0 / 53 (neutral = exposure_days < 0.10)
- Worst B&H drawdown episodes: #1 2021-11-09→2022-12-28 B&H −0.741 → strategy 0.067; #2 2021-04-14→2021-07-20 B&H −0.563 → strategy 0.148; #3 2024-03-14→2024-09-07 B&H −0.290 → strategy -0.109

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.664 | 1.576 | 0.183 | 158 |
| 2022 | 365 | 0.176 | 0.712 | 0.171 | 122 |
| 2023 | 365 | 0.335 | 1.555 | 0.130 | 114 |
| 2024 | 366 | 0.464 | 1.606 | 0.144 | 142 |
| 2025 | 304 | 0.050 | 0.403 | 0.129 | 78 |

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G2': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).
