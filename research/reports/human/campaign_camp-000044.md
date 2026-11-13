# Campaign camp-000044 — 코인원 BTC-KRW, 1시간 결정: 매일 21:00 UTC(06:00 KST)에 전액 매수하고 ATR 청산(손절 0.89 ATR, 익절 0.56/1.12 ATR, 최대 보유 120분)하는 규칙이 coinone_conservative 순 MTM 수익으로 G0~G6(현행 규칙 묶음)과 견고성 검사를 통과한다.

Date (UTC) 2026-11-13T08:50:18+00:00 · hypothesis hyp-000043 (registered 2026-11-13T08:50:15+00:00) · campaign registered 2026-11-13T08:50:15+00:00 · family `session_premium` · data snapshot `snap-93b066dd27ee41f0a0ec` · code `6d111b38360245f611331aad182280cc9c3ab911`

**Verdict: no candidate passed.**

> ⚠ HUMAN-ONLY report: contains Val (G6) detail. Do not paste Val numbers into agent prompts, lessons or hypotheses. Agent-safe version: `research/reports/campaign_camp-000044.md`.

## Hypothesis (pre-registered)

- Text: 코인원 BTC-KRW, 1시간 결정: 매일 21:00 UTC(06:00 KST)에 전액 매수하고 ATR 청산(손절 0.89 ATR, 익절 0.56/1.12 ATR, 최대 보유 120분)하는 규칙이 coinone_conservative 순 MTM 수익으로 G0~G6(현행 규칙 묶음)과 견고성 검사를 통과한다.
- Rationale: 메커니즘은 주요 주식 시장이 모두 닫힌 시간대에 위험을 드는 대가이고, 반대편은 장 마감에 맞춰 위험을 줄이는 참여자다. 하루 몫이 수 bp 라 수수료가 있으면 먹을 수 없어서 남아 있다. 새벽 스프레드, 2021년 이후 시간대 이동, 원화 시장 미전이, 약세장이 이를 죽일 수 있다. 근거: Quantpedia 2023-09-13·2024-11-12(외부 문헌, 잠금·Val 노출 없음, 비용 미반영·다른 거래소)와 선별 camp-000040(개발 구간만; 사건 1630, p 6.4e-05, 120분 초과 8.1 bp, z 3.83, coinone_conservative 순 +4.7 bp, upbit_conservative 순 -9.3 bp). 문헌의 시각 탐색 24건을 계열 시도로 계상했다. 청산은 실행 전 선별 제안값으로 고정: 손절 = |MAE| 75% 분위 0.89 ATR, 익절 = MFE 중앙 0.56 ATR 와 그 2배 1.12 ATR, 최대 보유 = 정보 지평 120분. 목표 Sharpe = 선별 z / √(개발 연수) = 3.83/√4.83 = 1.74. 미리 아는 약점: (1) 문헌 표본과 개발 구간이 2021~2023 에서 겹쳐 독립 재현이 아니다; (2) 미국 서머타임으로 뉴욕 장 마감은 20시/21시 UTC 로 움직이는데 규칙은 UTC 고정이다; (3) 한국 새벽 코인원 호가가 얇아 1분봉 시가에 실제로 살 수 있었는지 확인이 필요하다; (4) 하루 몫이 비용과 같은 자릿수라 비용 ×1.5 견고성이 관건이다; (5) 0% 수수료 이벤트가 끝나면 사라지는 B등급일 가능성이 있다. 데이터 점검(단계 1.4)의 조건은 해당하지 않아 stress 비용 성공 기준은 추가하지 않았다.
- Success criteria: G0-G6 under the current ruleset (MTM, size scaling, combined Val evidence) and the robustness suite; grade reported (A/B).

## Grid & budget

- Data: coinone BTC-KRW 1m, segment `dev` [2021-01-01T00:00:00+00:00, 2025-11-01T00:00:00+00:00), 2,541,600 1m rows (filled 7.56%) — read via DataAccess, `prepare_m1` (venue clean_wicks k=0.01 → regularize → gap warm-up)
- Entry `clock_entry_utc`: {'hour': [21], 'minute': [0]}
- Filter set 0: (no filter)
- Decision timeframe **1h**; aux data none
- Exits (atr mode, ATR window 14 bars): {'sl_atr': [0.89], 'tp_atr': [0.56, 1.12], 'max_hold_min': [120]} → 2 exit variants
- Costs: primary `coinone_conservative`, grade `upbit_conservative`, stress `coinone_stress` / `upbit_stress`
- Daily returns for gates (PLAN D18): **mark-to-market** — open inventory marked at each UTC day close (liquidation value incl. costs); G2–G6, Monte Carlo MDD95 (with intra-position MTM troughs), robustness and §7 limits use it
- Specs: **2** (template budget 2); runs recorded: 4; runtime 2.46s

## Signal screen linkage

- Gate: every signal config passed camp-000040 (every (entry params × filter params × timeframe × exchange × universe) config passed a recorded screen); 1 configs matched:
  - clock_entry_utc{'hour': 21, 'minute': 0} ← screen `camp-000040` run `run-000316` (p_signal 0.0001, events 1630, info horizon 120)

## Power check (at registration)

- 계획 격자 2 스펙, 계열 `session_premium` 기존 시도 26, 압축비 0.5 (history median of 14 campaigns; floored at 1/n_grid = 0.5 (single-cluster small grid, pm-000007)) → 실질 시도 ≈ 14.0
- Dev 1765일, V=0.000283 (history median), 왜도 0, 첨도 3, DSR ≥ 0.95
- **G5 통과 최소 연 Sharpe ≈ 1.31** (시도 1회였다면 0.75)
- 목표 연 Sharpe 1.74 로 감당 가능한 실질 시도 ≈ 554 (격자 ≈ 1083 스펙)
- Required annual Sharpe stored: **1.31**; target 1.74; OK (strict)

## Top specs (primary cost, ranked by weighted Sharpe)

| spec_id | filters | entry.hour | entry.minute | sl_atr | tp_atr | max_hold_min | sharpe_w | sharpe | recent_sharpe | n_trades | recent_n_trades | expectancy | max_dd | rolling_frac_positive | exposure_days |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| r4e_session_2123utc_1h_320ca54e51 | - | 21 | 0 | 0.890 | 1.120 | 120 | 0.522 | 0.951 | -0.457 | 1630 | 315 | 0.000 | 0.222 | 0.491 | 0.924 |
| r4e_session_2123utc_1h_257e53da1a | - | 21 | 0 | 0.890 | 0.560 | 120 | -0.144 | 0.399 | -0.940 | 1630 | 315 | 0.000 | 0.309 | 0.415 | 0.924 |

Buy & hold over the same Dev period (BTC buy & hold (same venue 1m close, no cost); information only): total return 4.121, Sharpe 0.915, MDD 0.741.

Distribution of weighted Sharpe over all 2 specs: median 0.189, share > 0 50.0%, max 0.522.

## Funnel

- All specs (metric-only gates): 2 specs → G1 2, weighted Sharpe > 0 1, G4 0, all three 0
- Top 1 candidates: G0 1, G1 1, G2 1, G3 0, G4 0, G5 0 → Dev survivors (all G0–G5) 0 → G6 validated 0

## Selection-bias inputs (G5)

- Campaign specs N=2, correlation clusters K=1 (threshold 0.5), eigen-entropy trials 1.3
- Ledger trials: family 28, global 225 → DSR trials N = max(K, N_family·K/N) = 23.5; V = 0.000416 (all campaign columns (single cluster fallback))
- PBO (CSCV, 16 splits) = 0.002

## Candidate selection (correlation clusters)

- Method `cluster_best` (threshold 0.5): 2 specs → 1 clusters (largest sizes [2]); best spec per cluster, top K by weighted Sharpe go to the gates (only those can use Val).
| logic_hash | cluster | cluster_size | sharpe_w |
|---|---|---|---|
| 320ca54e51b22860 | 1 | 2 | 0.522 |

> ⚠ **Experimental blocks used**: clock_entry_utc — human block review required before lockbox (D11).

## Candidates — per-gate results

### #1 `r4e_session_2123utc_1h_320ca54e51` — failed G3 (ledger only)

> ⚠ uses experimental block(s) clock_entry_utc: **human block review required before lockbox** (D11).

```yaml
id: r4e_session_2123utc_1h_320ca54e51
entry:
  block: clock_entry_utc
  params:
    hour: 21
    minute: 0
exit:
  max_hold_min: 120
  signal_exit: null
  stop_loss_atr: 0.89
  take_profit_atr: 1.12
filters: []
version: 1
timeframe: 1h
```
Weighted Sharpe 0.522, Sharpe 0.951, trades 1630, expectancy 0.00034, MDD 0.222

- **G0 PASS** — validate=ok, timeframe=1h, exit_mode=atr, n_filters=0, n_params=5, not_live_ready=[], requires_aux=[], disallowed_aux=[], uses_experimental=[clock_entry_utc], causality_cuts=3, causality_violations=[]
- **G1 PASS** — n_trades=1630, recent_n_trades=315
- **G2 PASS** — sharpe_w=0.5218, p_value=0.0010, percentile=1, n_null=1000, calibration=trade_count, p_signal_rate=0.0414, p=0.0414, tf_min=60, atr_bars=14, n_trades_median=1589, candidate_n_trades=1630, sharpe_w_median=-1.4231, sharpe_w_p95=-0.5342, cost=coinone_conservative
- **G3 FAIL** — selected=0.5218, neighbor_median=-0.1442, required=0.1305, n_neighbors=1
- **G4 FAIL** — rolling_frac_positive=0.4906, rolling_n_windows=53, recent_sharpe=-0.4566
  - exposure-aware variant (shadow, OFF — information only): applicable=✓, rolling_n_active=53, rolling_n_neutral=0, rolling_frac_positive_active=0.4906, exposure_days=0.9235, via=–, passed=✗
- **G5 FAIL** — dsr=0.6583, pbo=0.0020, sr=0.0498, sr0=0.0402, n_trials=23.5000, var_trial_sr=0.0004
- G3 neighbors: tp_atr=0.56→-0.14

Buy & hold reference (Dev, primary cost; information only — not a gate):

| | strategy | BTC buy & hold |
|---|---|---|
| Total return | 0.674 | 4.121 |
| Sharpe | 0.951 | 0.915 |
| Max drawdown | 0.222 | 0.741 |

- time in market (exposure_time) 0.059; days in market (exposure_days) 0.924; exposure-matched B&H return 0.141; excess vs matched B&H 0.533; excess vs B&H -3.447; up-day capture 0.054; down-day capture 0.026; up-regime return (trailing 30d BTC > 0) 0.253; down-regime return (trailing 30d BTC ≤ 0) 0.200; up-regime capture 0.104; down-regime capture -0.897; downside deviation (ann.) 0.076; rolling 6m windows neutral / active 0 / 53 (neutral = exposure_days < 0.10)
- Worst B&H drawdown episodes: #1 2021-11-09→2022-12-28 B&H −0.741 → strategy -0.102; #2 2021-04-14→2021-07-20 B&H −0.563 → strategy 0.096; #3 2024-03-14→2024-09-07 B&H −0.290 → strategy 0.166

Tail risk (Dev, realized daily returns; information only — not a gate):

- worst day -0.025; worst week -0.046; CVaR 5% (daily) -0.013; skew (daily) 0.766; max inventory (fraction of sleeve) 1; longest time at max inventory (min) 120; 2022 bear: total return -0.127; 2022 bear: Sharpe -1.081; 2022 bear: max DD 0.140; return in worst B&H drawdown -0.102; positions (ladders) / tranche rows 1630 / 1630

Yearly (Dev, primary cost):

| year | n_days | total_return | sharpe | max_dd | n_trades |
|---|---|---|---|---|---|
| 2021 | 365 | 0.723 | 3.143 | 0.066 | 358 |
| 2022 | 365 | -0.127 | -1.081 | 0.140 | 348 |
| 2023 | 365 | -0.041 | -0.522 | 0.105 | 314 |
| 2024 | 366 | 0.126 | 1.148 | 0.114 | 354 |
| 2025 | 304 | 0.031 | 0.497 | 0.046 | 256 |

## Conclusion

**No candidate passed.** First failing gate among the top 1: {'G3': 1}. Gates are not relaxed (PLAN §10); failures stay in the ledger. A new hypothesis must be a new campaign (trials accumulate).
