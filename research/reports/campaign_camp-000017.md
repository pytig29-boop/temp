# Campaign camp-000017 — Coinone BTC-KRW 1h: atr_channel_breakout(span 24, mult 3) taken only when 24h realised vol is in the upper half / tercile of its trailing range (vol_regime(24, 0.5 / 0.67)), with ATR exits (SL 2/3 ATR x TP 1.5/3 ATR, max hold 480 min = information horizon), earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite (incl. regime share <= 70%).

Date (UTC) 2026-11-10T01:24:37+00:00 · hypothesis hyp-000017 (registered 2026-11-10T01:24:34+00:00) · campaign registered 2026-11-10T01:24:34+00:00 · family `vol_breakout` · data snapshot `snap-93b066dd27ee41f0a0ec` · code `ddbb30370f4872a9b29678664c2d392aa758d707+dirty`

**Verdict: no candidate passed.**

> Agent-safe report: Dev detail + Val (G6) / lockbox **outcome codes only**. The full human report is `research/reports/human/campaign_camp-000017.md` (human-only; not readable in research mode).

## Hypothesis (pre-registered)

- Text: Coinone BTC-KRW 1h: atr_channel_breakout(span 24, mult 3) taken only when 24h realised vol is in the upper half / tercile of its trailing range (vol_regime(24, 0.5 / 0.67)), with ATR exits (SL 2/3 ATR x TP 1.5/3 ATR, max hold 480 min = information horizon), earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite (incl. regime share <= 70%).
- Rationale: Mechanism M3 (multi-hour time-series momentum; in high vol, breakouts trigger stop/short-liquidation cascades and trend-following flow). Counterparty: stops and counter-trend sellers at breakout levels, late chasers we sell to at exit; persists because liquidation flow is forced and Coinone is too thin for large arbitrageurs. Kills it: bear regimes for a long-only spot book, Upbit-level costs (grade B), false breakouts in high vol. Evidence (all Dev; lockbox none, Val none): screen camp-000015 (fact-000015) passed both configs: vol 0.5 n=850 p=3e-4 net +33.3 bp @480 (Upbit +19.3), vol 0.67 n=678 p=6e-4 net +39.0 bp (Upbit +25.0), info horizon 480 peaked; exit suggestions TP 1.38-1.40 ATR, SL 1.80-1.86 ATR. Motivation from camp-000013 Dev robustness only (up-trend 30d days 74% of PnL; high-vol tercile stronger); the G6 outcome of camp-000013 is NOT used. Exits are held identical to camp-000013 (SL 2/3 x TP 1.5/3 ATR, hold 480) so that the only change vs camp-000013 is the vol filter (isolates the filter; the set brackets the screen suggestions). Logged side calculation camp-000016 (9 configs, Dev): excess at 480 by trailing-30d trend tercile: unfiltered down 33.9 bp z 2.83 / sideways 27.3 z 2.41 / up 27.5 z 2.44; vol0.5 25.3 z 1.71 / 41.2 z 3.04 / 31.8 z 2.24; vol0.67 13.3 z 0.79 / 40.1 z 2.63 / 44.4 z 2.66. The grid was fixed before this side calculation and not changed by it. Pre-stated expectation / predicted failure modes: robustness regime share (up-trend) and G4 recent-12-month Sharpe (2025 weak in every momentum config); the vol filter thins down-regime events, so REGIME_DEPENDENT remains the most likely failure; G5 margin is thin (required Sharpe 1.29 vs camp-000013 Sharpe 1.46). [derived-from drv-000011 ← fact-000015; family vol_breakout]
- Success criteria: At least one spec passes G0-G6 and the Dev robustness suite (latency, outliers, leave-year-out, cost x1.5, regime share <= 70%, alpha, cross-venue). Refuted for this filter if the best Dev Sharpe_w is not above camp-000013 representative (1.02) or the regime check still fails.

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 2,541,600 1m rows (filled 7.56%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- Entry `atr_channel_breakout`: {'span': [24], 'mult': [3.0]}
- Filter set 0: `vol_regime` {'window': [24], 'min_pct': [0.5, 0.67]}
- Decision timeframe **1h**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [2.0, 3.0], 'tp_atr': [1.5, 3.0], 'max_hold_min': [480]} → 4 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress`
- Specs: **8** (template budget 8); runs recorded: 16; runtime 2.56s

## Signal screen linkage

- Gate: every signal config passed camp-000015 (every (entry params × filter params × timeframe × exchange) config passed a recorded screen); 2 configs matched:
  - atr_channel_breakout{'span': 24, 'mult': 3.0} & vol_regime{'window': 24, 'min_pct': 0.5} ← screen `camp-000015` run `run-000149` (p_signal 0.0003, events 850, info horizon 480)
  - atr_channel_breakout{'span': 24, 'mult': 3.0} & vol_regime{'window': 24, 'min_pct': 0.67} ← screen `camp-000015` run `run-000150` (p_signal 0.0006, events 678, info horizon 480)

## Power check (at registration)

- 계획 격자 8 스펙, 계열 `vol_breakout` 기존 시도 56, 압축비 0.188 (history median of 6 campaigns) → 실질 시도 ≈ 12.0
- Dev 1765일, V=0.000288 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 1.29** (시도 1회였다면 0.75)
- 목표 연 Sharpe 2 로 감당 가능한 실질 시도 ≈ 9766 (격자 ≈ 52028 스펙)
- Required annual Sharpe stored: **1.29**; target 2; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.span | entry.mult | vol_regime.window | vol_regime.min_pct | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | recent_n_trades | expectancy | max_dd | rolling_frac_positive | exposure_days |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c3_vol_breakout_atrch_1h_hivol_atr_3247d3023d | vol_regime | 24 | 3 | 24 | 0.670 | 2 | 3 | 480 | 1.145 | 1.587 | 1.035 | 326 | 60 | 0.003 | 0.092 | 0.811 | 0.146 |
| c3_vol_breakout_atrch_1h_hivol_atr_3ec830b2b1 | vol_regime | 24 | 3 | 24 | 0.500 | 2 | 3 | 480 | 1.138 | 1.493 | 0.829 | 411 | 75 | 0.002 | 0.122 | 0.849 | 0.189 |
| c3_vol_breakout_atrch_1h_hivol_atr_89b549922a | vol_regime | 24 | 3 | 24 | 0.500 | 3 | 3 | 480 | 1.109 | 1.398 | 0.966 | 403 | 74 | 0.003 | 0.118 | 0.830 | 0.194 |
| c3_vol_breakout_atrch_1h_hivol_atr_47fcab1afd | vol_regime | 24 | 3 | 24 | 0.670 | 3 | 3 | 480 | 1.050 | 1.465 | 0.862 | 321 | 60 | 0.003 | 0.097 | 0.792 | 0.149 |
| c3_vol_breakout_atrch_1h_hivol_atr_ce3aa50031 | vol_regime | 24 | 3 | 24 | 0.500 | 3 | 1.500 | 480 | 0.621 | 0.861 | 0.491 | 494 | 95 | 0.001 | 0.116 | 0.736 | 0.194 |
| c3_vol_breakout_atrch_1h_hivol_atr_aa9cf13cb9 | vol_regime | 24 | 3 | 24 | 0.670 | 3 | 1.500 | 480 | 0.600 | 1.050 | 0.413 | 386 | 74 | 0.002 | 0.106 | 0.679 | 0.150 |
| c3_vol_breakout_atrch_1h_hivol_atr_93492a9226 | vol_regime | 24 | 3 | 24 | 0.500 | 2 | 1.500 | 480 | 0.386 | 0.787 | -0.008 | 503 | 96 | 0.001 | 0.139 | 0.660 | 0.189 |
| c3_vol_breakout_atrch_1h_hivol_atr_0494db4897 | vol_regime | 24 | 3 | 24 | 0.670 | 2 | 1.500 | 480 | 0.271 | 0.924 | -0.154 | 392 | 75 | 0.001 | 0.125 | 0.660 | 0.146 |

BTC buy & hold over the same Dev period (information only): total return 4.121, Sharpe 0.915, MDD 0.741.

Distribution of weighted Sharpe over all 8 specs: median 0.836, share > 0 100.0%, max 1.145.

## Funnel

- All specs (metric-only gates): 8 specs → G1 8, weighted Sharpe > 0 8, G4 6, all three 6
- Top 1 candidates: G0 1, G1 1, G2 1, G3 1, G4 1, G5 1 → Dev survivors (all G0–G5) 1 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=8, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 1.9
- Ledger trials: family 64, global 121 → DSR trials N = max(K, N_family·K/N) = 8; V = 0.000285 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.073

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 8 specs → 1 clusters (largest sizes [8]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| 3247d3023d70d1f7 | 1 | 8 | 1.145 |

- Val budget status: val_id=val1, cap=20, used=2, remaining=18

## Candidates — per-gate results

### #1 `c3_vol_breakout_atrch_1h_hivol_atr_3247d3023d` — failed G6 (ledger only)

```yaml
id: c3_vol_breakout_atrch_1h_hivol_atr_3247d3023d
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
filters:
- block: vol_regime
  params:
    window: 24
    min_pct: 0.67
version: 1
timeframe: 1h
```
Weighted Sharpe 1.145, Sharpe 1.587, trades 326, expectancy 0.00322, MDD 0.092

- **G0 PASS** — validate=ok, timeframe=1h, exit_mode=atr, n_filters=1, n_params=7, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[], causality_cuts=3, causality_violations=[]
- **G1 PASS** — n_trades=326, recent_n_trades=60
- **G2 PASS** — sharpe_w=1.1445, p_value=0.0230, percentile=0.9780, n_null=1000, calibration=trade_count, p_signal_rate=0.0832, p=0.0282, tf_min=60, atr_bars=14, n_trades_median=327, candidate_n_trades=326, sharpe_w_median=0.1395, sharpe_w_p95=0.9932, cost=coinone_conservative
- **G3 PASS** — selected=1.1445, neighbor_median=1.0504, required=0.5723, n_neighbors=3
- **G4 PASS** — rolling_frac_positive=0.8113, rolling_n_windows=53, recent_sharpe=1.0350
  - exposure-aware variant (shadow, OFF — information only): applicable=✓, rolling_n_active=50, rolling_n_neutral=3, rolling_frac_positive_active=0.8000, exposure_days=0.1462, via=base, passed=✓
- **G5 PASS** — dsr=0.9988, pbo=0.0729, sr=0.0830, sr0=0.0247, n_trials=8, var_trial_sr=0.0003
- **G6 (Val) FAIL** — outcome code only (Val detail is human-only: research/reports/human/)
- G3 neighbors: vol_regime.min_pct=0.5→1.14, sl_atr=3.0→1.05, tp_atr=1.5→0.27

Buy & hold reference (Dev, primary cost; information only — not a gate):

| | strategy | BTC buy & hold |
|---|---|---|
| Total return | 1.751 | 4.121 |
| Sharpe | 1.587 | 0.915 |
| Max drawdown | 0.092 | 0.741 |

- time in market (exposure_time) 0.043; days in market (exposure_days) 0.146; exposure-matched B&H return 0.101; excess vs matched B&H 1.650; excess vs B&H -2.370; up-day capture 0.072; down-day capture 0.011; up-regime return (trailing 30d BTC > 0) 1.076; down-regime return (trailing 30d BTC ≤ 0) 0.309; up-regime capture 0.330; down-regime capture -1.286; downside deviation (ann.) 0.057; rolling 6m windows neutral / active 3 / 50 (neutral = exposure_days < 0.10)
- Worst B&H drawdown episodes: #1 2021-11-09→2022-12-28 B&H −0.741 → strategy 0.115; #2 2021-04-14→2021-07-20 B&H −0.563 → strategy 0.040; #3 2024-03-14→2024-09-07 B&H −0.290 → strategy -0.071

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.410 | 2.510 | 0.030 | 51 |
| 2022 | 365 | 0.141 | 0.972 | 0.092 | 63 |
| 2023 | 365 | 0.394 | 2.280 | 0.069 | 92 |
| 2024 | 366 | 0.191 | 1.235 | 0.085 | 69 |
| 2025 | 304 | 0.030 | 0.481 | 0.037 | 51 |

Ablation (removable iff delta(SR_w full - SR_w dropped) < 1 paired-bootstrap s.e.):

| filter | dropped_sharpe_w | delta | se | removable | source |
|---|---|---|---|---|---|
| vol_regime | 1.019 | 0.126 | 0.348 | ✓ | ablation campaign camp-000018 |

Simplified spec proposed as a NEW spec (logic hash `72bbc9dae527d230`), to be tested in a follow-up campaign — the candidate above is unchanged.

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G6': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).
