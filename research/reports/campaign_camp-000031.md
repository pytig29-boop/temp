# Campaign camp-000031 — Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 1h: roc_threshold(12,100) with half-size entries (entry_fraction 0.5) and ATR exits (SL 2 ATR x TP 1/2 ATR, max hold 480/960 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev and the robustness suite.

Date (UTC) 2026-11-10T07:09:41+00:00 · hypothesis hyp-000030 (registered 2026-11-10T07:09:36+00:00) · campaign registered 2026-11-10T07:09:36+00:00 · family `vol_breakout` · data snapshot `snap-93b066dd27ee41f0a0ec|XRP-KRW:snap-b636892dd83e17554616|ETH-KRW:snap-b46925c3bb3e2bfd5239` · code `e6cc86bb960b9e9900074963077957ff1b4df0bf+dirty`

**Verdict: no candidate passed.**

> Agent-safe report: Dev detail + Val (G6) / lockbox **outcome codes only**. The full human report is `research/reports/human/campaign_camp-000031.md` (human-only; not readable in research mode).

## Hypothesis (pre-registered)

- Text: Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 1h: roc_threshold(12,100) with half-size entries (entry_fraction 0.5) and ATR exits (SL 2 ATR x TP 1/2 ATR, max hold 480/960 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev and the robustness suite.
- Rationale: Mechanism M3 (1h time-series momentum; stop cascades, short liquidations, late trend followers). Counterparty: counter-trend limit sellers, stopped-out shorts, late chasers at exit. Kills: bear regimes (2022), per-asset cost (D17), drift confound (int-000009/int-000011), regime concentration. Structural change vs the paused BTC-only line: the fixed 3-asset universe (D15) - same parameters, ~3x positions, pooled equal-sleeve evaluation. Caveat stated before the run: the BTC sleeve re-uses a rule family whose BTC-only versions carry taxonomy VAL_FAIL (fact-000013/fact-000016); a skeptic must weigh that the BTC sleeve is not new evidence. Partial sizing entry_fraction 0.5 (user request, D16): size does not change the edge gates (G2/G5) and halves drawdowns. Exits fixed by the pre-screen plan (scratchpad PLAN_prescreen.txt): sl_atr = screen MAE-p75 suggestion rounded to 0.5, tp_atr = {MFE-median suggestion rounded to 0.5, 2x}, max hold {info horizon 480, 960}. MTM gate returns (D18). Evidence (Dev only; lockbox none, Val none): screen camp-000027 (drv-000019): pooled n 17392 (BTC 5033, XRP 6209, ETH 6150), p 0.0027, excess 11.8 bp z 3.12 @480 (peaked), net +11.4 bp (Upbit -1.3); MFE median 1.10 ATR, |MAE| p75 1.93 ATR (1h ATR 0.95 %). Plausible Sharpe z/sqrt(4.83 y) = 1.42; required ~1.28; target 1.40. Highest event rate of the cycle (user preference for more trades). Predicted failures: G2 (drift in the null), Upbit grade (B at best), regime share, PBO. [derived-from drv-000023 ← fact-000024; family vol_breakout]
- Success criteria: At least one spec passes G0-G5, ablation, G6 (outcome code) and the Dev robustness suite. Refuted as a tradeable edge if no spec passes G2 or the best Dev Sharpe_w (MTM) < 1.0.

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 7,624,800 1m rows (filled 13.29%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- **Universe ['BTC-KRW', 'XRP-KRW', 'ETH-KRW']** (PLAN D15): the same spec on every asset, pooled as ONE strategy — equal fixed sleeves (1/N), independent position per asset, portfolio daily return = mean of sleeve returns; gates use the portfolio series and pooled trades (G1 = sum over assets); 1 ledger trial per spec. Per-asset rows below are information only.
  - BTC-KRW: 2,541,600 1m rows (filled 7.56%)
  - XRP-KRW: 2,541,600 1m rows (filled 13.21%)
  - ETH-KRW: 2,541,600 1m rows (filled 19.10%)
- Entry `roc_threshold`: {'lookback': [12], 'thr_bp': [100]}
- Filter set 0: (no filter)
- Decision timeframe **1h**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [2.0], 'tp_atr': [1.0, 2.0], 'max_hold_min': [480, 960]} → 4 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress` — per-asset models where defined (PLAN D17): BTC-KRW: coinone_conservative, upbit_conservative; XRP-KRW: coinone_conservative@XRP-KRW, upbit_conservative@XRP-KRW; ETH-KRW: coinone_conservative@ETH-KRW, upbit_conservative@ETH-KRW
- Daily returns for gates (PLAN D18): **mark-to-market** — open inventory marked at each UTC day close (liquidation value incl. costs); G2–G6, Monte Carlo MDD95 (with intra-position MTM troughs), robustness and §7 limits use it
- Specs: **4** (template budget 4); runs recorded: 8; runtime 4.78s

## Signal screen linkage

- Gate: every signal config passed camp-000027 (every (entry params × filter params × timeframe × exchange × universe) config passed a recorded screen); 1 configs matched:
  - roc_threshold{'lookback': 12, 'thr_bp': 100} ← screen `camp-000027` run `run-000215` (p_signal 0.0027, events 17392, info horizon 480)

## Power check (at registration)

- 계획 격자 4 스펙, 계열 `vol_breakout` 기존 시도 92, 압축비 0.125 (history median of 11 campaigns) → 실질 시도 ≈ 12.0
- Dev 1765일, V=0.000285 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 1.29** (시도 1회였다면 0.75)
- 목표 연 Sharpe 1.4 로 감당 가능한 실질 시도 ≈ 26 (격자 ≈ 117 스펙)
- Required annual Sharpe stored: **1.29**; target 1.40; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.lookback | entry.thr_bp | sizing.entry_fraction | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | n_positions | recent_n_trades | expectancy | max_dd | rolling_frac_positive | exposure_days | worst_day | cvar5_daily | max_inventory |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c4_vol_breakout_roc_1h_u3_half_91a6706e89 | - | 12 | 100 | 0.500 | 2 | 2 | 960 | -0.483 | 0.156 | -0.168 | 5702 | 5702 | 1221 | 0.000 | 0.400 | 0.321 | 0.934 | -0.044 | -0.019 | 0.500 |
| c4_vol_breakout_roc_1h_u3_half_d617b37afb | - | 12 | 100 | 0.500 | 2 | 2 | 480 | -0.550 | -0.253 | 0.034 | 7069 | 7069 | 1505 | -0.000 | 0.536 | 0.245 | 0.927 | -0.037 | -0.018 | 0.500 |
| c4_vol_breakout_roc_1h_u3_half_cc051a201f | - | 12 | 100 | 0.500 | 2 | 1 | 960 | -1.569 | -0.987 | -1.418 | 7746 | 7746 | 1645 | -0.001 | 0.629 | 0.113 | 0.932 | -0.043 | -0.018 | 0.500 |
| c4_vol_breakout_roc_1h_u3_half_61d3c824cb | - | 12 | 100 | 0.500 | 2 | 1 | 480 | -1.734 | -1.540 | -1.292 | 8548 | 8548 | 1815 | -0.001 | 0.699 | 0.000 | 0.926 | -0.038 | -0.017 | 0.500 |

Per-asset breakdown of the top specs (Dev, primary cost; information only — gates use the pooled portfolio):

| spec_id | asset.BTC-KRW.n_trades | asset.BTC-KRW.sharpe_w | asset.BTC-KRW.total_return | asset.XRP-KRW.n_trades | asset.XRP-KRW.sharpe_w | asset.XRP-KRW.total_return | asset.ETH-KRW.n_trades | asset.ETH-KRW.sharpe_w | asset.ETH-KRW.total_return |
|---|---|---|---|---|---|---|---|---|---|
| c4_vol_breakout_roc_1h_u3_half_91a6706e89 | 1690 | -0.296 | 0.163 | 2054 | -0.460 | -0.385 | 1958 | -0.291 | 0.400 |
| c4_vol_breakout_roc_1h_u3_half_d617b37afb | 2078 | -0.739 | -0.273 | 2539 | -0.241 | -0.517 | 2452 | -0.442 | 0.045 |
| c4_vol_breakout_roc_1h_u3_half_cc051a201f | 2294 | -0.566 | 0.024 | 2771 | -1.316 | -0.801 | 2681 | -1.376 | -0.569 |
| c4_vol_breakout_roc_1h_u3_half_61d3c824cb | 2536 | -1.044 | -0.273 | 3047 | -1.192 | -0.854 | 2965 | -1.571 | -0.671 |

Buy & hold over the same Dev period (equal-weight 3-asset buy & hold (BTC-KRW, XRP-KRW, ETH-KRW; same venue 1m close, daily-rebalanced sleeves like the strategy, no cost); information only): total return 11.453, Sharpe 1.148, MDD 0.730.

Distribution of weighted Sharpe over all 4 specs: median -1.059, share > 0 0.0%, max -0.483.

## Funnel

- All specs (metric-only gates): 4 specs → G1 4, weighted Sharpe > 0 0, G4 0, all three 0
- Top 1 candidates: G0 1, G1 1, G2 0, G3 0, G4 0, G5 0 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=4, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 1.4
- Ledger trials: family 96, global 178 → DSR trials N = max(K, N_family·K/N) = 24; V = 0.001563 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.001

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 4 specs → 1 clusters (largest sizes [4]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| 91a6706e8914e9a1 | 1 | 4 | -0.483 |

## Candidates — per-gate results

### #1 `c4_vol_breakout_roc_1h_u3_half_91a6706e89` — failed G2 (ledger only)

```yaml
id: c4_vol_breakout_roc_1h_u3_half_91a6706e89
entry:
  block: roc_threshold
  params:
    lookback: 12
    thr_bp: 100
exit:
  max_hold_min: 960
  signal_exit: null
  stop_loss_atr: 2.0
  take_profit_atr: 2.0
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
Weighted Sharpe -0.483, Sharpe 0.156, trades 5702, expectancy 0.00013, MDD 0.400

- **G0 PASS** — validate=ok, timeframe=1h, exit_mode=atr, n_filters=0, n_params=5, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=9, causality_violations=[]
- **G1 PASS** — n_trades=5702, recent_n_trades=1221
- **G2 FAIL** — sharpe_w=-0.4828, p_value=0.1588, percentile=0.8420, n_null=1000, calibration=trade_count, p_signal_rate={'BTC-KRW': 0.2127442852066801, 'XRP-KRW': 0.27383857288384544, 'ETH-KRW': 0.27449632614363595}, p={'BTC-KRW': 0.06324925441098803, 'XRP-KRW': 0.09681655591830687, 'ETH-KRW': 0.08899442333736278}, tf_min=60, atr_bars=14, n_trades_median=5664, candidate_n_trades=5702, sharpe_w_median=-0.9056, sharpe_w_p95=-0.2248, cost=coinone_conservative
- **G3 FAIL** — selected=-0.4828, neighbor_median=-1.0594, required=-0.2414, n_neighbors=2
- **G4 FAIL** — rolling_frac_positive=0.3208, rolling_n_windows=53, recent_sharpe=-0.1682
  - exposure-aware variant (shadow, OFF — information only): applicable=✓, rolling_n_active=53, rolling_n_neutral=0, rolling_frac_positive_active=0.3208, exposure_days=0.9337, via=–, passed=✗
- **G5 FAIL** — dsr=0.0015, pbo=0.0008, sr=0.0082, sr0=0.0783, n_trials=24, var_trial_sr=0.0016
- G3 neighbors: tp_atr=1.0→-1.57, max_hold_min=480→-0.55

Buy & hold reference (Dev, primary cost; information only — not a gate):

| | strategy | EW 3-asset buy & hold |
|---|---|---|
| Total return | 0.060 | 11.453 |
| Sharpe | 0.156 | 1.148 |
| Max drawdown | 0.400 | 0.730 |

- time in market (exposure_time) 0.191; days in market (exposure_days) 0.934; exposure-matched B&H return 0.867; excess vs matched B&H -0.807; excess vs B&H -11.393; up-day capture 0.180; down-day capture 0.209; up-regime return (trailing 30d BTC > 0) 0.134; down-regime return (trailing 30d BTC ≤ 0) -0.177; up-regime capture 0.053; down-regime capture 0.791; downside deviation (ann.) 0.107; rolling 6m windows neutral / active 0 / 53 (neutral = exposure_days < 0.10)
- Worst B&H drawdown episodes: #1 2021-11-09→2022-06-18 B&H −0.730 → strategy -0.123; #2 2021-05-09→2021-07-20 B&H −0.572 → strategy -0.038; #3 2025-01-18→2025-04-08 B&H −0.425 → strategy -0.086

Per-asset (Dev, primary cost; information only — gates use the pooled portfolio):

| asset | n_trades | recent_n_trades | sharpe_w | sharpe | total_return | max_dd | expectancy | win_rate |
|---|---|---|---|---|---|---|---|---|
| BTC-KRW | 1690 | 308 | -0.296 | 0.278 | 0.163 | 0.188 | 0.000 | 0.508 |
| XRP-KRW | 2054 | 461 | -0.460 | -0.178 | -0.385 | 0.757 | -0.000 | 0.488 |
| ETH-KRW | 1958 | 452 | -0.291 | 0.429 | 0.400 | 0.452 | 0.000 | 0.511 |

Tail risk (Dev, realized daily returns; information only — not a gate):

- worst day -0.044; worst week -0.084; CVaR 5% (daily) -0.019; skew (daily) 1.432; max inventory (fraction of sleeve) 0.500; longest time at max inventory (min) 960; 2022 bear: total return -0.073; 2022 bear: Sharpe -0.398; 2022 bear: max DD 0.175; return in worst B&H drawdown -0.123; positions (ladders) / tranche rows 5702 / 5702

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.514 | 1.634 | 0.134 | 1443 |
| 2022 | 365 | -0.073 | -0.398 | 0.175 | 1139 |
| 2023 | 365 | -0.107 | -1.263 | 0.157 | 983 |
| 2024 | 366 | -0.110 | -0.798 | 0.175 | 1169 |
| 2025 | 304 | -0.049 | -0.326 | 0.111 | 968 |

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G2': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).

## Researcher notes (cycle 4)

Mechanism/counterparty, evidence exposure, screen linkage, power, B&H reading, fill ratios, trades/day, tail-check reading, lessons (facts, interpretations int-000014..016, pm-000007) and budget are in the cycle report `research/reports/cycle4_20261110.md`. Robustness: not run (no Dev survivor). Exploratory trials: none. Val/lockbox: not reached / not accessed.
