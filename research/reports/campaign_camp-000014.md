# Campaign camp-000014 — Coinone BTC-KRW 1h: donchian_breakout(lookback 48/144 h) with ATR exits (SL 2.5/3.5 ATR x TP 2.5/5 ATR, max hold 1440 min = information horizon) earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite.

Date (UTC) 2026-11-09T23:32:42+00:00 · hypothesis hyp-000014 (registered 2026-11-09T23:32:39+00:00) · campaign registered 2026-11-09T23:32:39+00:00 · family `vol_breakout` · data snapshot `snap-93b066dd27ee41f0a0ec` · code `9db1f9fffc3963e77f36b158e2845d55910f6aec`

**Verdict: no candidate passed.**

> Agent-safe report: Dev detail + Val (G6) / lockbox **outcome codes only**. The full human report is `research/reports/human/campaign_camp-000014.md` (human-only; not readable in research mode).

## Hypothesis (pre-registered)

- Text: Coinone BTC-KRW 1h: donchian_breakout(lookback 48/144 h) with ATR exits (SL 2.5/3.5 ATR x TP 2.5/5 ATR, max hold 1440 min = information horizon) earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite.
- Rationale: Mechanism M3 at multi-hour horizons (time-series momentum: slow information diffusion, trend-following flows, short-liquidation/stop cascades). Counterparty: counter-trend limit sellers and stops at breakout levels, late chasers we sell to at exit. Kills it: bear regimes for a long-only spot book (2022 excess ~0 or negative in the per-year side calculation for several configs), false breakouts, regime beta (robustness alpha check). Screen camp-000009 (Dev only, lockbox none, Val none) passed this entry; exits are ATR multiples centred on the screen's MFE/MAE suggestion and max hold at the information horizon. Pre-stated expectation: Dev Sharpe >= 1.5 with leave-year-out and alpha checks passing; per-year side calculation showed 2022/2025 weakness, so REGIME_DEPENDENT is the predicted failure mode. Screen configs: lookback 144 n=583 net +80.7 bp at 1440 (Upbit +66.6), lookback 48 n=952 net +48.0 (Upbit +33.9); info horizon 1440 (rising); suggestion TP 2.4-2.7 ATR, SL 3.5-3.7 ATR. lookback 24 (also passed) is left out to keep the grid small; 48 is the neighbour for G3. [derived-from drv-000009 ← fact-000009; family vol_breakout]
- Success criteria: At least one spec passes G0-G6 and the Dev robustness suite (latency, outliers, leave-year-out, cost x1.5, regime, alpha, cross-venue). Refuted for this timeframe if the best Dev Sharpe_w < 1 or leave-year-out/regime/alpha fail.

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 2,541,600 1m rows (filled 7.56%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- Entry `donchian_breakout`: {'lookback': [48, 144]}
- Filter set 0: (no filter)
- Decision timeframe **1h**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [2.5, 3.5], 'tp_atr': [2.5, 5.0], 'max_hold_min': [1440]} → 4 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress`
- Specs: **8** (template budget 8); runs recorded: 16; runtime 2.17s

## Signal screen linkage

- Gate: every signal config passed camp-000009 (every (entry params × filter params × timeframe × exchange) config passed a recorded screen); 2 configs matched:
  - donchian_breakout{'lookback': 48} ← screen `camp-000009` run `run-000098` (p_signal 0.0102, events 952, info horizon 1440)
  - donchian_breakout{'lookback': 144} ← screen `camp-000009` run `run-000100` (p_signal 0.0002, events 583, info horizon 1440)

## Power check (at registration)

- 계획 격자 8 스펙, 계열 `vol_breakout` 기존 시도 37, 압축비 0.25 (history median of 5 campaigns) → 실질 시도 ≈ 11.2
- Dev 1765일, V=0.000296 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 1.29** (시도 1회였다면 0.75)
- 목표 연 Sharpe 2 로 감당 가능한 실질 시도 ≈ 7829 (격자 ≈ 31278 스펙)
- Required annual Sharpe stored: **1.29**; target 2; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.lookback | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | recent_n_trades | expectancy | max_dd | rolling_frac_positive |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c2b_vol_breakout_donch_1h_atr_ca225bbff7 | - | 144 | 3.500 | 2.500 | 1440 | 0.988 | 0.994 | 1.064 | 339 | 74 | 0.002 | 0.119 | 0.811 |
| c2b_vol_breakout_donch_1h_atr_f037adae67 | - | 144 | 2.500 | 5 | 1440 | 0.988 | 1.288 | 0.549 | 297 | 68 | 0.004 | 0.151 | 0.774 |
| c2b_vol_breakout_donch_1h_atr_8e3a062644 | - | 144 | 3.500 | 5 | 1440 | 0.885 | 1.179 | 0.367 | 291 | 65 | 0.004 | 0.148 | 0.868 |
| c2b_vol_breakout_donch_1h_atr_1b5725c94e | - | 144 | 2.500 | 2.500 | 1440 | 0.833 | 0.800 | 0.865 | 350 | 76 | 0.002 | 0.132 | 0.755 |
| c2b_vol_breakout_donch_1h_atr_cfe9e27df8 | - | 48 | 3.500 | 5 | 1440 | 0.752 | 1.082 | -0.068 | 492 | 114 | 0.002 | 0.223 | 0.623 |
| c2b_vol_breakout_donch_1h_atr_dfe30e1473 | - | 48 | 2.500 | 5 | 1440 | 0.465 | 1.069 | -0.549 | 509 | 121 | 0.002 | 0.221 | 0.717 |
| c2b_vol_breakout_donch_1h_atr_40de6ba664 | - | 48 | 3.500 | 2.500 | 1440 | 0.151 | 0.612 | -0.204 | 561 | 133 | 0.001 | 0.285 | 0.453 |
| c2b_vol_breakout_donch_1h_atr_686f6238c4 | - | 48 | 2.500 | 2.500 | 1440 | -0.365 | 0.459 | -0.982 | 582 | 139 | 0.001 | 0.291 | 0.453 |

Distribution of weighted Sharpe over all 8 specs: median 0.792, share > 0 87.5%, max 0.988.

## Funnel

- All specs (metric-only gates): 8 specs → G1 6, weighted Sharpe > 0 7, G4 4, all three 2
- Top 1 candidates: G0 1, G1 1, G2 1, G3 1, G4 1, G5 0 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=8, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 3.0
- Ledger trials: family 45, global 102 → DSR trials N = max(K, N_family·K/N) = 5.6; V = 0.000226 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.525

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 8 specs → 1 clusters (largest sizes [8]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| ca225bbff7d5df87 | 1 | 8 | 0.988 |

## Candidates — per-gate results

### #1 `c2b_vol_breakout_donch_1h_atr_ca225bbff7` — failed G5 (ledger only)

```yaml
id: c2b_vol_breakout_donch_1h_atr_ca225bbff7
entry:
  block: donchian_breakout
  params:
    lookback: 144
exit:
  max_hold_min: 1440
  signal_exit: null
  stop_loss_atr: 3.5
  take_profit_atr: 2.5
filters: []
version: 1
timeframe: 1h
```
Weighted Sharpe 0.988, Sharpe 0.994, trades 339, expectancy 0.00219, MDD 0.119

- **G0 PASS** — validate=ok, timeframe=1h, exit_mode=atr, n_filters=0, n_params=4, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=3, causality_violations=[]
- **G1 PASS** — n_trades=339, recent_n_trades=74
- **G2 PASS** — sharpe_w=0.9878, p_value=0.0190, percentile=0.9820, n_null=1000, calibration=trade_count, p_signal_rate=0.0188, p=0.0090, tf_min=60, atr_bars=14, n_trades_median=325, candidate_n_trades=339, sharpe_w_median=0.0271, sharpe_w_p95=0.8131, cost=coinone_conservative
- **G3 PASS** — selected=0.9878, neighbor_median=0.8325, required=0.4939, n_neighbors=3
- **G4 PASS** — rolling_frac_positive=0.8113, rolling_n_windows=53, recent_sharpe=1.0643
- **G5 FAIL** — dsr=0.9218, pbo=0.5250, sr=0.0520, sr0=0.0190, n_trials=5.6250, var_trial_sr=0.0002
- G3 neighbors: entry.lookback=48→0.15, sl_atr=2.5→0.83, tp_atr=5.0→0.88

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.223 | 1.097 | 0.119 | 84 |
| 2022 | 365 | -0.047 | -0.378 | 0.114 | 43 |
| 2023 | 365 | 0.321 | 2.011 | 0.064 | 65 |
| 2024 | 366 | 0.297 | 1.560 | 0.070 | 93 |
| 2025 | 304 | -0.004 | 0.017 | 0.111 | 54 |

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G5': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).
