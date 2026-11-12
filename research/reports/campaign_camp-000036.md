# Campaign camp-000036 — Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 4h decisions: donchian_breakout(48) entries taken only while Coinone BTC-KRW closes above its 90-bar (15-day) 4h EMA (experimental btc_trend_filter, aux coinone_btc), half-size entries (entry_fraction 0.5), ATR exits (SL 2 ATR, TP 1.5/3 ATR, max hold 1440 min) earn positive per-asset coinone_conservative net MTM returns that pass G0-G6 (rs-003) and the robustness suite.

Date (UTC) 2026-11-12T03:20:49+00:00 · hypothesis hyp-000035 (registered 2026-11-12T03:20:40+00:00) · campaign registered 2026-11-12T03:20:40+00:00 · family `xasset_btc_lead` · data snapshot `snap-93b066dd27ee41f0a0ec|coinone_btc:snap-93b066dd27ee41f0a0ec|XRP-KRW:snap-b636892dd83e17554616|coinone_btc/XRP-KRW:snap-93b066dd27ee41f0a0ec|ETH-KRW:snap-b46925c3bb3e2bfd5239|coinone_btc/ETH-KRW:snap-93b066dd27ee41f0a0ec` · code `eeb463d8908e4ad78bbeea7efaac4013224f0eaa`

**Verdict: no candidate passed.**

> Agent-safe report: Dev detail + Val (G6) / lockbox **outcome codes only**. The full human report is `research/reports/human/campaign_camp-000036.md` (human-only; not readable in research mode).

## Hypothesis (pre-registered)

- Text: Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 4h decisions: donchian_breakout(48) entries taken only while Coinone BTC-KRW closes above its 90-bar (15-day) 4h EMA (experimental btc_trend_filter, aux coinone_btc), half-size entries (entry_fraction 0.5), ATR exits (SL 2 ATR, TP 1.5/3 ATR, max hold 1440 min) earn positive per-asset coinone_conservative net MTM returns that pass G0-G6 (rs-003) and the robustness suite.
- Rationale: Autonomous program R2 track C. Mechanism: alt breakouts persist when the market leader (BTC) trends up (common crypto factor) and fail otherwise. Counterparty: late chasers buying alt breakouts against a falling BTC; counter-trend sellers at multi-day highs. Kills: BTC sleeve nearly identical to camp-000034 candidate (correlation; final pooling by cluster), XRP still not significant alone, regime concentration, 2022 bear. Evidence (Dev only; lockbox none, Val none): screen camp-000035 span 90 n 742 p 0.0015 z 3.30 excess 62 bp net 104 bp @1440 (span 42: n 757 p 0.0058). Exits = screen suggestion (TP ~1.5 ATR, SL ~1.8 ATR -> 2.0) and 2x TP, hold = information horizon 1440. Target Sharpe = screen z / sqrt(Dev years) = 3.30/sqrt(4.83) = 1.50, fixed before the power check.
- Success criteria: G0-G6 under ruleset rs-003, robustness suite, per-asset results reported (XRP/ETH vs camp-000034).

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 7,624,800 1m rows (filled 13.29%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- **Universe ['BTC-KRW', 'XRP-KRW', 'ETH-KRW']** (PLAN D15): the same spec on every asset, pooled as ONE strategy — equal fixed sleeves (1/N), independent position per asset, portfolio daily return = mean of sleeve returns; gates use the portfolio series and pooled trades (G1 = sum over assets); 1 ledger trial per spec. Per-asset rows below are information only.
  - BTC-KRW: 2,541,600 1m rows (filled 7.56%), aux rows {'coinone_btc': 2541600}
  - XRP-KRW: 2,541,600 1m rows (filled 13.21%), aux rows {'coinone_btc': 2541600}
  - ETH-KRW: 2,541,600 1m rows (filled 19.10%), aux rows {'coinone_btc': 2541600}
- Entry `donchian_breakout`: {'lookback': [48]}
- Filter set 0: `btc_trend_filter` {'span': [90], 'band_bp': [0.0]}
- Decision timeframe **4h**; aux data ['coinone_btc'] ({'coinone_btc': 2541600} 1m rows, same construction as paper: prepare_m1 → to_tf, block-side reindex without ffill)
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [2.0], 'tp_atr': [1.5, 3.0], 'max_hold_min': [1440]} → 2 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress` — per-asset models where defined (PLAN D17): BTC-KRW: coinone_conservative, upbit_conservative; XRP-KRW: coinone_conservative@XRP-KRW, upbit_conservative@XRP-KRW; ETH-KRW: coinone_conservative@ETH-KRW, upbit_conservative@ETH-KRW
- Daily returns for gates (PLAN D18): **mark-to-market** — open inventory marked at each UTC day close (liquidation value incl. costs); G2–G6, Monte Carlo MDD95 (with intra-position MTM troughs), robustness and §7 limits use it
- Specs: **2** (template budget 2); runs recorded: 4; runtime 9.76s

## Signal screen linkage

- Gate: every signal config passed camp-000035 (every (entry params × filter params × timeframe × exchange × universe) config passed a recorded screen); 1 configs matched:
  - donchian_breakout{'lookback': 48} & btc_trend_filter{'span': 90, 'band_bp': 0.0} ← screen `camp-000035` run `run-000284` (p_signal 0.0015, events 742, info horizon 1440)

## Power check (at registration)

- 계획 격자 2 스펙, 계열 `xasset_btc_lead` 기존 시도 2, 압축비 0.5 (history median of 13 campaigns; floored at 1/n_grid = 0.5 (single-cluster small grid, pm-000007)) → 실질 시도 ≈ 2.0
- Dev 1765일, V=0.000285 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 0.92** (시도 1회였다면 0.75)
- 목표 연 Sharpe 1.5 로 감당 가능한 실질 시도 ≈ 57 (격자 ≈ 112 스펙)
- Required annual Sharpe stored: **0.92**; target 1.50; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.lookback | btc_trend_filter.span | btc_trend_filter.band_bp | sizing.entry_fraction | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | n_positions | recent_n_trades | expectancy | max_dd | rolling_frac_positive | exposure_days | worst_day | cvar5_daily | max_inventory |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| r2c_xasset_btc_lead_donch_4h_u3_half_d27e04b72e | btc_trend_filter | 48 | 90 | 0.000 | 0.500 | 2 | 3 | 1440 | 1.881 | 1.569 | 2.621 | 500 | 500 | 123 | 0.007 | 0.080 | 0.943 | 0.296 | -0.046 | -0.008 | 0.500 |
| r2c_xasset_btc_lead_donch_4h_u3_half_9552669a4d | btc_trend_filter | 48 | 90 | 0.000 | 0.500 | 2 | 1.500 | 1440 | 1.437 | 1.352 | 1.750 | 555 | 555 | 139 | 0.004 | 0.065 | 0.887 | 0.282 | -0.020 | -0.007 | 0.500 |

Per-asset breakdown of the top specs (Dev, primary cost; information only — gates use the pooled portfolio):

| spec_id | asset.BTC-KRW.n_trades | asset.BTC-KRW.sharpe_w | asset.BTC-KRW.total_return | asset.XRP-KRW.n_trades | asset.XRP-KRW.sharpe_w | asset.XRP-KRW.total_return | asset.ETH-KRW.n_trades | asset.ETH-KRW.sharpe_w | asset.ETH-KRW.total_return |
|---|---|---|---|---|---|---|---|---|---|
| r2c_xasset_btc_lead_donch_4h_u3_half_d27e04b72e | 197 | 1.750 | 1.003 | 110 | 1.266 | 0.592 | 193 | 0.799 | 0.514 |
| r2c_xasset_btc_lead_donch_4h_u3_half_9552669a4d | 223 | 1.386 | 0.518 | 126 | 0.747 | 0.325 | 206 | 0.865 | 0.474 |

Buy & hold over the same Dev period (equal-weight 3-asset buy & hold (BTC-KRW, XRP-KRW, ETH-KRW; same venue 1m close, daily-rebalanced sleeves like the strategy, no cost); information only): total return 11.453, Sharpe 1.148, MDD 0.730.

Distribution of weighted Sharpe over all 2 specs: median 1.659, share > 0 100.0%, max 1.881.

## Funnel

- All specs (metric-only gates): 2 specs → G1 2, weighted Sharpe > 0 2, G4 2, all three 2
- Top 1 candidates: G0 1, G1 1, G2 1, G3 1, G4 1, G5 0 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=2, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 1.4
- Ledger trials: family 4, global 192 → DSR trials N = max(K, N_family·K/N) = 7; V = 0.000065 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.378

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 2 specs → 1 clusters (largest sizes [2]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| d27e04b72e24a4d1 | 1 | 2 | 1.881 |

> ⚠ **Experimental blocks used**: btc_trend_filter — human block review required before lockbox (D11).

## Candidates — per-gate results

### #1 `r2c_xasset_btc_lead_donch_4h_u3_half_d27e04b72e` — failed G5 (ledger only)

> ⚠ uses experimental block(s) btc_trend_filter: **human block review required before lockbox** (D11).

```yaml
id: r2c_xasset_btc_lead_donch_4h_u3_half_d27e04b72e
entry:
  block: donchian_breakout
  params:
    lookback: 48
exit:
  max_hold_min: 1440
  signal_exit: null
  stop_loss_atr: 2.0
  take_profit_atr: 3.0
filters:
- block: btc_trend_filter
  params:
    span: 90
    band_bp: 0.0
version: 1
timeframe: 4h
universe:
- BTC-KRW
- XRP-KRW
- ETH-KRW
sizing:
  entry_fraction: 0.5
```
Weighted Sharpe 1.881, Sharpe 1.569, trades 500, expectancy 0.00667, MDD 0.080

- **G0 PASS** — validate=ok, timeframe=4h, exit_mode=atr, n_filters=1, n_params=6, not_live_ready=[], requires_aux=[coinone_btc], disallowed_aux=[], uses_experimental=[btc_trend_filter], causality_cuts=9, causality_violations=[]
- **G1 PASS** — n_trades=500, recent_n_trades=123
- **G2 PASS** — sharpe_w=1.8814, p_value=0.0020, percentile=0.9990, n_null=1000, calibration=trade_count, p_signal_rate={'BTC-KRW': 0.07522281639928699, 'XRP-KRW': 0.037446504992867335, 'ETH-KRW': 0.06877456234369418}, p={'BTC-KRW': 0.04472775423628773, 'XRP-KRW': 0.023251617380120514, 'ETH-KRW': 0.04459478640055723}, tf_min=240, atr_bars=14, n_trades_median=494, candidate_n_trades=500, sharpe_w_median=0.2719, sharpe_w_p95=1.2259, cost=coinone_conservative
- **G3 PASS** — selected=1.8814, neighbor_median=1.4374, required=0.4703, n_neighbors=1
- **G4 PASS** — rolling_frac_positive=0.9434, rolling_n_windows=53, recent_sharpe=2.6211
  - exposure-aware variant (shadow, OFF — information only): applicable=✓, rolling_n_active=53, rolling_n_neutral=0, rolling_frac_positive_active=0.9434, exposure_days=0.2958, via=base, passed=✓
- **G5 FAIL** — dsr=0.9987, pbo=0.3782, sr=0.0821, sr0=0.0112, n_trials=7, var_trial_sr=0.0001
- G3 neighbors: tp_atr=1.5→1.44

Buy & hold reference (Dev, primary cost; information only — not a gate):

| | strategy | EW 3-asset buy & hold |
|---|---|---|
| Total return | 0.722 | 11.453 |
| Sharpe | 1.569 | 1.148 |
| Max drawdown | 0.080 | 0.730 |

- time in market (exposure_time) 0.037; days in market (exposure_days) 0.296; exposure-matched B&H return 0.133; excess vs matched B&H 0.588; excess vs B&H -10.732; up-day capture 0.051; down-day capture 0.029; up-regime return (trailing 30d BTC > 0) 0.460; down-regime return (trailing 30d BTC ≤ 0) 0.190; up-regime capture 0.126; down-regime capture -0.837; downside deviation (ann.) 0.043; rolling 6m windows neutral / active 0 / 53 (neutral = exposure_days < 0.10)
- Worst B&H drawdown episodes: #1 2021-11-09→2022-06-18 B&H −0.730 → strategy 0.048; #2 2021-05-09→2021-07-20 B&H −0.572 → strategy -0.020; #3 2025-01-18→2025-04-08 B&H −0.425 → strategy -0.032

Per-asset (Dev, primary cost; information only — gates use the pooled portfolio):

| asset | n_trades | recent_n_trades | sharpe_w | sharpe | total_return | max_dd | expectancy | win_rate |
|---|---|---|---|---|---|---|---|---|
| BTC-KRW | 197 | 48 | 1.750 | 1.839 | 1.003 | 0.048 | 0.007 | 0.589 |
| XRP-KRW | 110 | 32 | 1.266 | 0.716 | 0.592 | 0.173 | 0.009 | 0.509 |
| ETH-KRW | 193 | 43 | 0.799 | 0.895 | 0.514 | 0.090 | 0.005 | 0.549 |

Tail risk (Dev, realized daily returns; information only — not a gate):

- worst day -0.046; worst week -0.044; CVaR 5% (daily) -0.008; skew (daily) 0.827; max inventory (fraction of sleeve) 0.500; longest time at max inventory (min) 1440; 2022 bear: total return 0.094; 2022 bear: Sharpe 1.653; 2022 bear: max DD 0.030; return in worst B&H drawdown 0.048; positions (ladders) / tranche rows 500 / 500

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.084 | 0.841 | 0.080 | 135 |
| 2022 | 365 | 0.094 | 1.653 | 0.030 | 65 |
| 2023 | 365 | 0.108 | 1.953 | 0.033 | 87 |
| 2024 | 366 | 0.221 | 2.597 | 0.025 | 125 |
| 2025 | 304 | 0.072 | 1.314 | 0.034 | 88 |

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G5': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).
