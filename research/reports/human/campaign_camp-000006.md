# Campaign camp-000006 — Coinone BTC-KRW 5m: donchian_breakout(lookback 288 = 1 day high), with or without vol_regime(24, 0.67), with ATR-multiple exits and 30-120 min max hold, earns positive coinone_conservative net returns that survive G0-G6 and the robustness suite.

Date (UTC) 2026-11-09T16:18:20+00:00 · hypothesis hyp-000006 (registered 2026-11-09T16:18:18+00:00) · campaign registered 2026-11-09T16:18:18+00:00 · family `vol_breakout` · data snapshot `snap-93b066dd27ee41f0a0ec` · code `e2d2ff63ce9ab272162441125b6d784cb3e30a73+dirty`

**Verdict: no candidate passed.**

> ⚠ HUMAN-ONLY report: contains Val (G6) detail. Do not paste Val numbers into agent prompts, lessons or hypotheses. Agent-safe version: `research/reports/campaign_camp-000006.md`.

## Hypothesis (pre-registered)

- Text: Coinone BTC-KRW 5m: donchian_breakout(lookback 288 = 1 day high), with or without vol_regime(24, 0.67), with ATR-multiple exits and 30-120 min max hold, earns positive coinone_conservative net returns that survive G0-G6 and the robustness suite.
- Rationale: M3 volatility breakout continuation (counterparty: stops / liquidated shorts above the 1-day high, late chasers). Entry/filters fixed to the 2 configs that passed screen camp-000003 (all shorter lookbacks failed on net cost). Info horizon 15 min (peaked) but the vol-filtered excess keeps growing to 240 min, so hold {30,120} (low-turnover variant included per pipeline calibration). Exits centred on screen ATR suggestions (TP ~1.0-1.1 ATR at 15 min scaling to ~3 ATR at 120 min; SL ~1.6-1.7 ATR): SL {2.0} ATR x TP {1.5, 3.0} ATR x hold {30,120} = 4 exit variants. Expected annual Sharpe estimate ~1.0-1.5 (net 2-3.5 bp/trade, ~250-450 trades/yr) - below the ~2 the pipeline reliably detects at <=1 trade/day; run anyway because the grid is tiny (8 specs) and the result is informative. vol_regime filter motivated by K-P0-4 (lockbox+Val exposed), value = default block-grid point, not tuned.
- Success criteria: At least one spec passes G0-G6 and the Dev robustness suite.

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 2,541,600 1m rows (filled 7.56%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- Entry `donchian_breakout`: {'lookback': [288]}
- Filter set 0: (no filter)
- Filter set 1: `vol_regime` {'window': [24], 'min_pct': [0.67]}
- Decision timeframe **5m**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [2.0], 'tp_atr': [1.5, 3.0], 'max_hold_min': [30, 120]} → 4 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress`
- Specs: **8** (template budget 8); runs recorded: 16; runtime 2.49s

## Signal screen linkage

- Gate: every signal config passed camp-000003 (every (entry params × filter params × timeframe × exchange) config passed a recorded screen); 2 configs matched:
  - donchian_breakout{'lookback': 288} ← screen `camp-000003` run `run-000017` (p_signal 0.0000, events 2177, info horizon 15)
  - donchian_breakout{'lookback': 288} & vol_regime{'window': 24, 'min_pct': 0.67} ← screen `camp-000003` run `run-000018` (p_signal 0.0003, events 1222, info horizon 15)

## Power check (at registration)

- 계획 격자 8 스펙, 계열 `vol_breakout` 기존 시도 9, 압축비 0.05 (default 0.05 (history 1 < 3)) → 실질 시도 ≈ 1.0
- Dev 1765일, V=0.000851 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 0.75** (시도 1회였다면 0.75)
- 목표 연 Sharpe 1.5 로 감당 가능한 실질 시도 ≈ 7 (격자 ≈ 121 스펙)
- Required annual Sharpe stored: **0.75**; target 1.50; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.lookback | vol_regime.window | vol_regime.min_pct | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | recent_n_trades | expectancy | max_dd | rolling_frac_positive |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c1_vol_breakout_atr_v1_dd008ca9c6 | vol_regime | 288 | 24 | 0.670 | 2 | 3 | 120 | 0.041 | 0.161 | 0.140 | 1348 | 268 | 0.000 | 0.209 | 0.491 |
| c1_vol_breakout_atr_v1_c0c8f4ce58 | vol_regime | 288 | 24 | 0.670 | 2 | 3 | 30 | -0.215 | -0.053 | -0.204 | 1552 | 317 | -0.000 | 0.255 | 0.377 |
| c1_vol_breakout_atr_v1_274bcbb459 | vol_regime | 288 | 24 | 0.670 | 2 | 1.500 | 120 | -1.512 | -1.154 | -1.377 | 1605 | 323 | -0.000 | 0.439 | 0.038 |
| c1_vol_breakout_atr_v1_f1ec597200 | - | 288 | – | – | 2 | 3 | 30 | -1.526 | -0.866 | -2.158 | 2841 | 635 | -0.000 | 0.470 | 0.245 |
| c1_vol_breakout_atr_v1_a2b58043af | - | 288 | – | – | 2 | 3 | 120 | -1.552 | -0.749 | -1.928 | 2459 | 548 | -0.000 | 0.534 | 0.264 |
| c1_vol_breakout_atr_v1_45dabef44f | vol_regime | 288 | 24 | 0.670 | 2 | 1.500 | 30 | -1.564 | -1.153 | -1.489 | 1714 | 349 | -0.000 | 0.429 | 0.057 |
| c1_vol_breakout_atr_v1_9cfa3373d0 | - | 288 | – | – | 2 | 1.500 | 30 | -3.474 | -2.413 | -4.167 | 3122 | 702 | -0.000 | 0.681 | 0.000 |
| c1_vol_breakout_atr_v1_8493bfaa65 | - | 288 | – | – | 2 | 1.500 | 120 | -3.601 | -2.432 | -4.410 | 2910 | 651 | -0.000 | 0.726 | 0.000 |

Distribution of weighted Sharpe over all 8 specs: median -1.539, share > 0 12.5%, max 0.041.

## Funnel

- All specs (metric-only gates): 8 specs → G1 8, weighted Sharpe > 0 1, G4 0, all three 0
- Top 1 candidates: G0 1, G1 1, G2 1, G3 0, G4 0, G5 0 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=8, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 2.8
- Ledger trials: family 17, global 55 → DSR trials N = max(K, N_family·K/N) = 2.1; V = 0.002492 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.000

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 8 specs → 1 clusters (largest sizes [8]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| dd008ca9c652b784 | 1 | 8 | 0.041 |

## Candidates — per-gate results

### #1 `c1_vol_breakout_atr_v1_dd008ca9c6` — failed G3 (ledger only)

```yaml
id: c1_vol_breakout_atr_v1_dd008ca9c6
entry:
  block: donchian_breakout
  params:
    lookback: 288
exit:
  max_hold_min: 120
  signal_exit: null
  stop_loss_atr: 2.0
  take_profit_atr: 3.0
filters:
- block: vol_regime
  params:
    window: 24
    min_pct: 0.67
version: 1
```
Weighted Sharpe 0.041, Sharpe 0.161, trades 1348, expectancy 0.00006, MDD 0.209

- **G0 PASS** — validate=ok, timeframe=5m, exit_mode=atr, n_filters=1, n_params=6, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=3, causality_violations=[]
- **G1 PASS** — n_trades=1348, recent_n_trades=268
- **G2 PASS** — sharpe_w=0.0405, p_value=0.0060, percentile=0.9950, n_null=1000, calibration=trade_count, p_signal_rate=0.0211, p=0.0108, tf_min=5, atr_bars=14, n_trades_median=1371, candidate_n_trades=1348, sharpe_w_median=-1.2964, sharpe_w_p95=-0.4502, cost=coinone_conservative
- **G3 FAIL** — selected=0.0405, neighbor_median=-0.8637, required=0.0203, n_neighbors=2
- **G4 FAIL** — rolling_frac_positive=0.4906, rolling_n_windows=53, recent_sharpe=0.1395
- **G5 FAIL** — dsr=0.1943, pbo=0.0000, sr=0.0084, sr0=0.0287, n_trials=2.1250, var_trial_sr=0.0025
- G3 neighbors: tp_atr=1.5→-1.51, max_hold_min=30→-0.22

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.092 | 0.702 | 0.104 | 275 |
| 2022 | 365 | -0.140 | -1.324 | 0.185 | 222 |
| 2023 | 365 | 0.147 | 1.332 | 0.075 | 316 |
| 2024 | 366 | -0.033 | -0.307 | 0.091 | 316 |
| 2025 | 304 | 0.016 | 0.237 | 0.057 | 219 |

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G3': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).
