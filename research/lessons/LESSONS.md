# 오답노트 (LESSONS) — 자동 생성

> `btc_quant.research.lessons.render_markdown()` 생성물. **직접 편집 금지** (원본: `research/lessons/*.jsonl`).
> 사실(Facts)은 파이프라인 결과에서 자동 추출되어 불변이다. 해석(Interpretations)은 AI 작성이며
> 후속 캠페인으로 verified/refuted 되기 전까지 **unverified** 이다. Val/잠금은 결과 코드만.
> 생성: 2026-11-13T08:46:22+00:00 · 사실 35 · 해석 19 · 사후분석 7 · 파생 23

## 실패 유형 (자동 분류)

| 코드 | 의미 |
|---|---|
| `NO_SIGNAL` | 진입 신호에 정보가 없음 — 신호 선별 실패 또는 G2(양의 가중 Sharpe·무작위 진입 귀무 상위 5%) 실패 |
| `COST_EATEN` | 비용 전 수익은 양(+)인데 비용 후 음(−) — 비용 바닥을 못 넘음 (비용 +50% 견고성 실패, 업비트 비용 G2/G4 실패 포함) |
| `LOW_SAMPLE` | G1 표본 부족 (거래 수 / 최근 12개월 거래 수) |
| `PARAM_SPIKE` | G3 고원 실패 — 선택 조합만 튀고 인접 조합은 약함 |
| `UNSTABLE` | G4 안정성 실패 — 롤링 6개월 양수 비율 또는 최근 12개월 Sharpe |
| `REGIME_DEPENDENT` | 특정 국면·연도·시간대·거래소에서만 작동 (견고성: 연도 제외, 국면별, 거래소 재현) |
| `OUTLIER_DEPENDENT` | 소수 거래·소수 일자에 의존 (견고성: 상위 거래 제거) |
| `LATENCY_SENSITIVE` | 1봉 지연에 무너짐 (견고성: 진입 지연) |
| `SELECTION_BIAS` | G5 선택편향 — DSR < 0.95 또는 PBO ≥ 0.3 |
| `VAL_FAIL` | Dev 는 통과했지만 G6(Val) 실패 — 결과 코드만 기록 |
| `IMPL_OR_DATA_ISSUE` | 구현·데이터 문제 — G0 실패(검증·인과성·live_ready), 엔진 불일치, 파이프라인 오류 |
| `UNCLASSIFIED` | 규칙으로 분류되지 않은 결과 |

## 계열 요약

| family | 캠페인 | 누적 시도 | 결과 | 주 실패 유형 |
|---|---|---|---|---|
| `liq_meanrev` | 12 | 68 | passed 4, failed 8 | `NO_SIGNAL`×4, `COST_EATEN`×2, `PARAM_SPIKE`×1, `UNSTABLE`×1 |
| `managed_hold` | 1 | 4 | failed 1 | `NO_SIGNAL`×1 |
| `session_premium` | 1 | 25 | passed 1 |  |
| `vol_breakout` | 16 | 103 | passed 7, failed 8, validated 1 | `PARAM_SPIKE`×2, `VAL_FAIL`×2, `NO_SIGNAL`×2, `SELECTION_BIAS`×1, `COST_EATEN`×1 |
| `xasset_btc_lead` | 2 | 4 | passed 1, failed 1 | `SELECTION_BIAS`×1 |
| `xvenue_upbit_lead` | 3 | 16 | failed 2, passed 1 | `COST_EATEN`×1, `LOW_SAMPLE`×1 |

## 교훈 (사실 → 해석)

### fact-000001 · signal_screen `camp-000001` · family `xvenue_upbit_lead`

**사실 (자동, 불변)**

- 가설 hyp-000001: On Coinone BTC-KRW at 1m decisions, after Upbit moves up and Coinone has not followed (upbit_lead_impulse) or Coinone trades at an unusually large discount to Upbit vs its own causal rolling basis (venue_gap_revert), Coinone forward returns over 5-30 min exceed a matched (KST hour x volatility) null, net of coinone_conservative cost.
- 결과: **failed** · 주 실패 유형: `COST_EATEN` · 분포: `COST_EATEN`×8
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `upbit_lead_impulse(k=1,thr_bp=10) & session_kst(session=all_but_quiet)` events=24941 p=0.000 best_h=5 → `COST_EATEN`
  - `upbit_lead_impulse(k=1,thr_bp=20) & session_kst(session=all_but_quiet)` events=5618 p=0.000 best_h=5 → `COST_EATEN`
  - `upbit_lead_impulse(k=3,thr_bp=10) & session_kst(session=all_but_quiet)` events=32450 p=0.000 best_h=5 → `COST_EATEN`
  - `upbit_lead_impulse(k=3,thr_bp=20) & session_kst(session=all_but_quiet)` events=10152 p=0.000 best_h=5 → `COST_EATEN`
  - `venue_gap_revert(window=60,z=2.0) & session_kst(session=all_but_quiet)` events=23097 p=0.000 best_h=5 → `COST_EATEN`
  - `venue_gap_revert(window=60,z=3.0) & session_kst(session=all_but_quiet)` events=5656 p=0.000 best_h=5 → `COST_EATEN`
  - `venue_gap_revert(window=240,z=2.0) & session_kst(session=all_but_quiet)` events=17624 p=0.000 best_h=5 → `COST_EATEN`
  - `venue_gap_revert(window=240,z=3.0) & session_kst(session=all_but_quiet)` events=3499 p=0.000 best_h=5 → `COST_EATEN`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['upbit_lead_impulse', 'venue_gap_revert'] / None · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000001 (신뢰도 medium): The Upbit->Coinone 1m lead is real but too small and too short for taker-taker execution: matched-null excess is only 1.3-2.9 bp at 5 min (z 4-11) with the information horizon at 1 min, i.e. below the 4 bp coinone_conservative round trip. Part of the 1-min excess may also be a stale-last-trade artefact on thin Coinone bars rather than an executable price. Only per-event moves larger than the cost floor (bigger Upbit impulses) or cheaper execution could convert it.
  - 근거: fact-000001 configs: excess_bp 1.26-2.87 at best primary horizon 5, net_bp[coinone_conservative] -1.25..-2.86, info_h 1 for all 8 configs; research/reports/data_audit_20261109.md (lockbox none, Val none): Coinone 1m flat-bar share 18-33%, Upbit(t-1)->Coinone(t) corr 0.06-0.12
  - 검증 방법: 1m screen (same family) of upbit_lead_impulse thr_bp 40 (block-grid value) k {1,3} with session_kst all_but_quiet: refuted if net mean at 5-15 min is still <= 0 with >= 300 events; plus forward orderbook check that the entry open price was actually executable.

### fact-000002 · signal_screen `camp-000002` · family `xvenue_upbit_lead`

**사실 (자동, 불변)**

- 가설 hyp-000002: On Coinone BTC-KRW at 5m decisions, after Upbit rises by thr_bp over k 5m bars while Coinone lags by at least half of it (upbit_lead_impulse), Coinone forward returns over 30-120 min exceed a matched (KST hour x volatility) null, net of coinone_conservative cost.
- 결과: **passed** · 주 실패 유형: `None` · 분포: `COST_EATEN`×2, `NO_SIGNAL`×1
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `upbit_lead_impulse(k=1,thr_bp=20) & session_kst(session=all_but_quiet)` events=3905 p=0.004 best_h=30 → `COST_EATEN`
  - `upbit_lead_impulse(k=1,thr_bp=40) & session_kst(session=all_but_quiet)` events=488 p=0.012 best_h=30 → PASS
  - `upbit_lead_impulse(k=2,thr_bp=20) & session_kst(session=all_but_quiet)` events=4945 p=0.058 best_h=30 → `COST_EATEN`
  - `upbit_lead_impulse(k=2,thr_bp=40) & session_kst(session=all_but_quiet)` events=718 p=0.399 best_h=30 → `NO_SIGNAL`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['upbit_lead_impulse'] / None · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000003 · signal_screen `camp-000003` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000003: On Coinone BTC-KRW at 5m decisions, upside channel breakouts (donchian_breakout, atr_channel_breakout) are followed by 30-120 min returns above a matched (KST hour x volatility) null, and more so when short-term realised volatility is in the upper part of its trailing 7-day range (vol_regime) or in the KST night session.
- 결과: **passed** · 주 실패 유형: `None` · 분포: `COST_EATEN`×6, `NO_SIGNAL`×1
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `donchian_breakout(lookback=48)` events=6430 p=0.012 best_h=30 → `COST_EATEN`
  - `donchian_breakout(lookback=48) & vol_regime(window=24,min_pct=0.67)` events=2493 p=0.032 best_h=30 → `COST_EATEN`
  - `donchian_breakout(lookback=96)` events=4173 p=0.011 best_h=30 → `COST_EATEN`
  - `donchian_breakout(lookback=96) & vol_regime(window=24,min_pct=0.67)` events=1902 p=0.028 best_h=30 → `COST_EATEN`
  - `donchian_breakout(lookback=288)` events=2177 p=0.000 best_h=30 → PASS
  - `donchian_breakout(lookback=288) & vol_regime(window=24,min_pct=0.67)` events=1222 p=0.000 best_h=30 → PASS
  - `atr_channel_breakout(span=24,mult=3)` events=5790 p=0.045 best_h=30 → `COST_EATEN`
  - `atr_channel_breakout(span=24,mult=3) & vol_regime(window=24,min_pct=0.67)` events=2553 p=0.038 best_h=30 → `COST_EATEN`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['atr_channel_breakout', 'donchian_breakout'] / None · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000004 · signal_screen `camp-000004` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000004: On Coinone BTC-KRW at 5m decisions, sharp downside dislocations (close far below its rolling mean: bollinger_z_revert; RSI crossing back up from oversold: rsi_oversold_bounce) are followed by 30-120 min returns above a matched (KST hour x volatility) null, at least when the 1h trend is up (trend_regime_1h).
- 결과: **passed** · 주 실패 유형: `None` · 분포: `NO_SIGNAL`×1, `COST_EATEN`×1
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `bollinger_z_revert(window=24,z=2.5)` events=4647 p=0.004 best_h=120 → PASS
  - `bollinger_z_revert(window=24,z=2.5) & trend_regime_1h(span=24)` events=2118 p=0.015 best_h=120 → PASS
  - `bollinger_z_revert(window=24,z=3.0)` events=1826 p=0.461 best_h=120 → `NO_SIGNAL`
  - `bollinger_z_revert(window=24,z=3.0) & trend_regime_1h(span=24)` events=809 p=0.032 best_h=30 → PASS
  - `bollinger_z_revert(window=96,z=2.5)` events=2999 p=0.001 best_h=60 → PASS
  - `bollinger_z_revert(window=96,z=2.5) & trend_regime_1h(span=24)` events=678 p=0.000 best_h=120 → PASS
  - `bollinger_z_revert(window=96,z=3.0)` events=1657 p=0.001 best_h=60 → PASS
  - `bollinger_z_revert(window=96,z=3.0) & trend_regime_1h(span=24)` events=396 p=0.000 best_h=30 → PASS
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['bollinger_z_revert', 'rsi_oversold_bounce'] / None · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000005 · campaign `camp-000005` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000005: Coinone BTC-KRW 5m: bollinger_z_revert(window 96, z 2.5/3.0), with or without trend_regime_1h(24), held 120-240 min with ATR-multiple exits, earns positive coinone_conservative net returns that survive G0-G6 and the robustness suite.
- 결과: **failed** · 주 실패 유형: `PARAM_SPIKE` · 분포: `UNSTABLE`×1, `PARAM_SPIKE`×1
- 깔때기(전체 16 스펙): G1 16 · SR_w>0 12 · G4 2 | 상위 5: G0 2 · G1 2 · G2 2 · G3 1 · G4 0 · G5 0 · Dev 생존 0
- 선택편향 입력: n_campaign_specs=16, k_eff_clusters=2, n_family_trials=26, n_global_trials=47, n_trials_dsr=3.250, pbo=0.448, cluster_threshold=0.500
- Val(G6): 평가 0 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c1_liq_meanrev_atr_v1_ff60d1a7c3` SR_w=0.886 trades=410 first_failed=G4 → `UNSTABLE` ['UNSTABLE', 'SELECTION_BIAS'] · val=not_run
  - #2 `c1_liq_meanrev_atr_v1_0b89e1f404` SR_w=0.485 trades=1728 first_failed=G3 → `PARAM_SPIKE` ['PARAM_SPIKE', 'UNSTABLE', 'SELECTION_BIAS'] · val=not_run
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 bollinger_z_revert / [[], ['trend_regime_1h']] · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-08 passes

**해석 (AI, 검증 상태 표시)**

- [REFUTED] int-000003 (신뢰도 low): Mean reversion after 96-bar Bollinger lows carries information (both cluster representatives beat the trade-count-matched null, G2 p 0.001) but the converted strategy is weak and time-concentrated: Dev Sharpe 0.5-0.9, 6-month rolling positive fraction 0.43-0.55, recent-12m Sharpe 1.6-1.7, heavy tails (kurtosis 20-43). The ATR exits (TP 3 / SL 5 ATR) likely truncate the 120-240 min drift the event study measured, and the cluster picked z 3.0 rather than the screen's strongest config (z 2.5 + trend, net 12.6 bp at 120 min).
  - 근거: fact-000005 candidates: sharpe_w 0.886 / 0.485, G4 rolling_frac_positive 0.547 / 0.434, G3 neighbor_median 0.708 / 0.165, G5 dsr 0.764 / 0.61, pbo 0.448; fact-000004 (screen camp-000004): bollinger_z_revert(96,2.5)&trend_regime_1h(24) net 12.6 bp at 120 min, n=678; unfiltered (96,2.5) info horizon rising to 240
  - 검증 방법: Derived campaign in family liq_meanrev: entry fixed to the screened window-96 configs, time-dominated exits (SL 6 ATR, TP 6 ATR, hold 120/240); refuted if Dev Sharpe stays < 1 and G4 rolling fraction < 0.6.
  - refuted by camp-000008 (2026-11-09T22:51:33+00:00): camp-000008 tested the exit-truncation claim directly: same entries (bollinger 96/2.5 with and without trend_regime_1h 24), SL 6 / TP 6 ATR, hold 120/240. Trend-filtered: sharpe_w 0.56-0.57, about the same as the best camp-000005 z2.5+trend spec under SL5/TP3 (0.61). Plain Sharpe was 1.055 vs 1.054, so widening the exits did not help. Unfiltered: sharpe_w rose from -0.32..0.13 to 0.55-0.59 but stayed below 1, with rolling fraction 0.45-0.57 (below 0.6). The pre-stated refutation rule (Sharpe < 1 AND rolling < 0.6) is fully met for the unfiltered specs. The trend specs have Sharpe_w < 1 but rolling 0.64. Exits are not what binds; the strategy stays below the DSR bar (best DSR 0.87, n_trials 15). The first half of int-000003 (information real but weak and time-concentrated) is still consistent with the data (G2 p ≤ 0.007).

### fact-000006 · campaign `camp-000006` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000006: Coinone BTC-KRW 5m: donchian_breakout(lookback 288 = 1 day high), with or without vol_regime(24, 0.67), with ATR-multiple exits and 30-120 min max hold, earns positive coinone_conservative net returns that survive G0-G6 and the robustness suite.
- 결과: **failed** · 주 실패 유형: `PARAM_SPIKE` · 분포: `PARAM_SPIKE`×1
- 깔때기(전체 8 스펙): G1 8 · SR_w>0 1 · G4 0 | 상위 5: G0 1 · G1 1 · G2 1 · G3 0 · G4 0 · G5 0 · Dev 생존 0
- 선택편향 입력: n_campaign_specs=8, k_eff_clusters=1, n_family_trials=17, n_global_trials=55, n_trials_dsr=2.125, pbo=0.000, cluster_threshold=0.500
- Val(G6): 평가 0 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c1_vol_breakout_atr_v1_dd008ca9c6` SR_w=0.041 trades=1348 first_failed=G3 → `PARAM_SPIKE` ['PARAM_SPIKE', 'UNSTABLE', 'SELECTION_BIAS'] · val=not_run
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 donchian_breakout / [[], ['vol_regime']] · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-08 passes

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000004 (신뢰도 medium): Breakout of the 1-day high on Coinone 5m has statistically detectable but economically thin information (screen net 2-3.5 bp, info horizon 15 min); as a strategy it is essentially break-even (Dev Sharpe_w 0.04, neighbour exit variants negative at -0.86), so the edge is eaten by cost plus exit slippage - the taxonomy says PARAM_SPIKE but the substance is closer to COST_EATEN.
  - 근거: fact-000006 candidates[0]: sharpe_w 0.041, G3 neighbor_median -0.864, G5 dsr 0.194; fact-000003 (screen camp-000003): shorter lookbacks 48/96 all failed on net cost; donchian 288 net 1.95 bp, with vol_regime 3.52 bp at 30 min
  - 검증 방법: Screen (family vol_breakout) of donchian_breakout(288) with vol_regime min_pct 0.8 (block-grid value): refuted as a 'high-vol concentration' story if net mean at 60-120 min is not >= 2x the 0.67 filter's.

### fact-000007 · campaign `camp-000007` · family `xvenue_upbit_lead`

**사실 (자동, 불변)**

- 가설 hyp-000007: Coinone BTC-KRW 5m: upbit_lead_impulse(k 1, thr 40bp) during non-quiet KST hours with ATR-multiple exits and 30-120 min max hold earns positive coinone_conservative net returns that survive G0-G6 and the robustness suite.
- 결과: **failed** · 주 실패 유형: `LOW_SAMPLE` · 분포: `LOW_SAMPLE`×1
- 깔때기(전체 4 스펙): G1 0 · SR_w>0 4 · G4 0 | 상위 5: G0 1 · G1 0 · G2 1 · G3 1 · G4 0 · G5 0 · Dev 생존 0
- 선택편향 입력: n_campaign_specs=4, k_eff_clusters=1, n_family_trials=16, n_global_trials=59, n_trials_dsr=4.000, pbo=0.811, cluster_threshold=0.500
- Val(G6): 평가 0 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c1_xvenue_lead_5m_atr_v1_ea21283709` SR_w=0.585 trades=572 first_failed=G1 → `LOW_SAMPLE` ['LOW_SAMPLE', 'UNSTABLE', 'SELECTION_BIAS'] · val=not_run
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 upbit_lead_impulse / [['session_kst']] · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-08 passes

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000002 (신뢰도 medium): The 5m Upbit-impulse (40 bp) signal on Coinone is a fading, mostly early-Dev phenomenon: the strategy had 572 Dev trades but only 33 in the last 12 months (G1 recent-sample fail) and 6-month rolling positive fraction 0.47, consistent with the Coinone-Upbit gap tails shrinking from 2021 to 2025. The mechanism (Coinone lag) may persist but large lags are now too rare to trade.
  - 근거: fact-000007 candidates[0].gates_dev.G1 recent_n_trades 33, G4 rolling_frac_positive 0.472, G2 p 0.002 (signal beats trade-count-matched null); research/reports/data_audit_20261109.md (lockbox none, Val none): share of minutes with |Coinone-Upbit gap|>20bp 29% (2021) -> 0.4% (2025)
  - 검증 방법: Event study of the same config split by year (side calculation, no ledger) followed, if 2024-25 still shows positive net excess, by a 5m screen with thr_bp 20 restricted by a new period filter; refuted if 2024-25 net excess is >= 2021-22 net excess.

### fact-000008 · campaign `camp-000008` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000008: Coinone 5m bollinger_z_revert(96, 2.5) with/without trend_regime_1h(24), held 120-240 min with wide ATR exits (SL 6, TP 6 ATR) that approximate the event study's time exit, passes G0-G5 on Dev.
- 결과: **failed** · 주 실패 유형: `UNSTABLE` · 분포: `UNSTABLE`×1, `SELECTION_BIAS`×1
- 파생: drv-000002 (계열 점검: ok)
- 깔때기(전체 4 스펙): G1 4 · SR_w>0 4 · G4 2 | 상위 5: G0 2 · G1 2 · G2 2 · G3 2 · G4 1 · G5 0 · Dev 생존 0
- 선택편향 입력: n_campaign_specs=4, k_eff_clusters=2, n_family_trials=30, n_global_trials=63, n_trials_dsr=15.000, pbo=0.322, cluster_threshold=0.500
- Val(G6): 평가 0 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c2_liq_meanrev_wide_atr_v1_728b01519b` SR_w=0.594 trades=2652 first_failed=G4 → `UNSTABLE` ['UNSTABLE', 'SELECTION_BIAS'] · val=not_run
  - #2 `c2_liq_meanrev_wide_atr_v1_7cf92d03f9` SR_w=0.571 trades=653 first_failed=G5 → `SELECTION_BIAS` ['SELECTION_BIAS'] · val=not_run
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 bollinger_z_revert / [[], ['trend_regime_1h']] · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-08 passes

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000005 (신뢰도 medium): The trend-filtered 96-bar Bollinger reversion (z 2.5 + trend_regime_1h 24) does not look like steady liquidity-provision income. It behaves like bull-regime dip buying. Its Dev PnL is concentrated in 2021 and in up-trend 30d regimes, sideways regimes lose money, and the alpha vs BTC is not significant. Because the edge lives in a few regime-specific periods, Dev Sharpe stays near 1 (weighted about 0.57) whatever the exits, and DSR stays below 0.95.
  - 근거: fact-000008 candidates: trend spec 7cf92d03f9 sharpe 1.055 / sharpe_w 0.571, G4 rolling 0.64, G5 dsr 0.872 (n_trials 15), skew 2.41, kurtosis 44.5; unfiltered 728b01519b sharpe_w 0.594, rolling 0.45; ad-hoc Dev robustness_for_spec(c2_liq_meanrev_wide_atr_v1_7cf92d03f9), no ledger trial: regime FAIL, trend 'up' days carry 0.78 of PnL (Sharpe 2.44), 'sideways' days Sharpe -0.62; alpha FAIL (alpha_t 1.93, beta_t 5.3); leave-year-out: 2021 Sharpe 2.60, rest 0.40, 2025 0.03; top 1% of trades = 0.47 of PnL; latency / cost x1.5 / cross-venue (Upbit ratio 0.93) passed; camp-000005 runs (same entries, SL 3-5 / TP 3 ATR): z2.5+trend sharpe_w 0.18-0.61, so exits change little
  - 검증 방법: Side calculation (signal_study.study_signal, no ledger) of bollinger_z_revert(96,2.5)&trend_regime_1h(24), split by calendar year on Dev. Refuted if 2022-2025 each show positive excess at 120 min with z > 2. Alternatively, a pre-registered screen in family liq_meanrev that pairs the entry with a vol/trend-strength filter, testing whether the edge is conditional on regime rather than on the dislocation.

### fact-000009 · signal_screen `camp-000009` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000009: On Coinone BTC-KRW at 1h decisions, upside breakouts of multi-day highs (donchian_breakout 24/48/144 h), ATR-channel breakouts and persistent positive EMA slope (ema_slope span 48) are followed by 4-24 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost; optionally only in high short-term vol (vol_regime 0.67) or above the 1-week EMA (trend_regime_1h 168).
- 결과: **passed** · 주 실패 유형: `None` · 분포: `NO_SIGNAL`×2, `LOW_SAMPLE`×1
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `donchian_breakout(lookback=24)` events=1361 p=0.005 best_h=1440 → PASS
  - `donchian_breakout(lookback=24) & vol_regime(window=24,min_pct=0.67)` events=518 p=0.138 best_h=1440 → `NO_SIGNAL`
  - `donchian_breakout(lookback=48)` events=952 p=0.010 best_h=1440 → PASS
  - `donchian_breakout(lookback=48) & vol_regime(window=24,min_pct=0.67)` events=398 p=0.023 best_h=1440 → PASS
  - `donchian_breakout(lookback=144)` events=583 p=0.000 best_h=1440 → PASS
  - `donchian_breakout(lookback=144) & vol_regime(window=24,min_pct=0.67)` events=285 p=0.003 best_h=1440 → `LOW_SAMPLE`
  - `atr_channel_breakout(span=24,mult=2.0)` events=2666 p=0.008 best_h=480 → PASS
  - `atr_channel_breakout(span=24,mult=3.0)` events=1165 p=0.000 best_h=480 → PASS
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['atr_channel_breakout', 'donchian_breakout', 'ema_slope'] / None · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000010 · signal_screen `camp-000010` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000010: On Coinone BTC-KRW at 4h decisions, breakouts of 4-8 day highs (donchian_breakout 24/48 bars), ATR-channel breakouts (24, 3) and positive EMA slope (ema_slope 24, 5 bp/bar) are followed by 8-48 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost.
- 결과: **passed** · 주 실패 유형: `None` · 분포: –
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `donchian_breakout(lookback=24)` events=401 p=0.003 best_h=1440 → PASS
  - `donchian_breakout(lookback=48)` events=303 p=0.001 best_h=1440 → PASS
  - `atr_channel_breakout(span=24,mult=3.0)` events=360 p=0.001 best_h=480 → PASS
  - `ema_slope(span=24,min_slope_bp=5.0)` events=2171 p=0.012 best_h=480 → PASS
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['atr_channel_breakout', 'donchian_breakout', 'ema_slope'] / None · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000011 · signal_screen `camp-000011` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000011: On Coinone BTC-KRW at 15m decisions, closes far below the rolling mean (bollinger_z_revert window 24/96, z 2.5) are followed by 1-4 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost, with and without a 1h trend filter (trend_regime_1h 24) or a 1-week bull-regime filter (trend_regime_1h 168), and the excess is not concentrated in one Dev year.
- 결과: **passed** · 주 실패 유형: `None` · 분포: `NO_SIGNAL`×4
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `bollinger_z_revert(window=24,z=2.5)` events=2058 p=0.009 best_h=240 → PASS
  - `bollinger_z_revert(window=24,z=2.5) & trend_regime_1h(span=24)` events=495 p=0.043 best_h=120 → PASS
  - `bollinger_z_revert(window=24,z=2.5) & trend_regime_1h(span=168)` events=879 p=0.146 best_h=120 → `NO_SIGNAL`
  - `bollinger_z_revert(window=96,z=2.5)` events=1965 p=0.084 best_h=60 → `NO_SIGNAL`
  - `bollinger_z_revert(window=96,z=2.5) & trend_regime_1h(span=24)` events=44 p=1.000 best_h=120 → `NO_SIGNAL`
  - `bollinger_z_revert(window=96,z=2.5) & trend_regime_1h(span=168)` events=501 p=0.666 best_h=60 → `NO_SIGNAL`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['bollinger_z_revert'] / None · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000008 (신뢰도 medium): The M4 reversion signal does not lengthen with the decision interval: at 15m decisions the information horizon is 30 min and the net edge at the pre-registered primary horizons is 5-6 bp (Upbit cost negative); at 1h decisions (fact-000012) nothing passes. A 1-week bull-regime filter (trend_regime_1h 168) did not add information within its own matched pool. By the pre-registered per-year rule the 15m excess is not concentrated in one year (positive 5/5 years, max-year share 0.50), and for the original 5m config of int-000005 the 2022-2025 excess is positive every year (9-14 bp) but with z 1.2-1.9 < 2, so int-000005's own refutation criterion is not met either: the reversion is a small, sub-hour, year-persistent effect whose 2021 size (2-3x other years) drives strategy Sharpe.
  - 근거: fact-000011: bollinger(24,2.5) n=2058 p_signal 0.009 info_h 30, net 4.87 bp at 240 (Upbit -9.1); +trend_regime_1h(24) n=495 net 6.37 bp; +trend_regime_1h(168) p 0.146 not significant; window 96 not significant; fact-000012: 1h bollinger/rsi 0 of 5 passed (NO_SIGNAL); per-year side calculation (Dev, no ledger, n_null 300): 15m boll24 excess at 240 min 19.1/4.8/2.3/2.5/14.2 bp (2021..2025); 5m boll96+trend24 excess at 120 min 31.9/11.1/9.2/11.2/13.6 bp, z 3.0/1.2/1.4/1.7/1.9; power_check liq_meanrev 4 specs: required Sharpe 1.42; plausible Sharpe from screen net mean x frequency ~1.1
  - 검증 방법: int-000005's per-year criterion with more data: when Dev is extended, refuted if 2022-2025 each reach z > 2 at 120 min; supported if the extended years show excess <= 0. No further 15m/1h liq_meanrev strategy campaign until a cost or data change (plausible Sharpe below required).

### fact-000012 · signal_screen `camp-000012` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000012: On Coinone BTC-KRW at 1h decisions, closes far below the rolling mean (bollinger_z_revert window 24/96 h, z 2.5) or RSI(14) crossing back above 25 are followed by 4-24 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost, with and without a 1-week bull-regime filter (trend_regime_1h 168), and the excess is not concentrated in one Dev year.
- 결과: **failed** · 주 실패 유형: `NO_SIGNAL` · 분포: `NO_SIGNAL`×5
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `bollinger_z_revert(window=24,z=2.5)` events=691 p=0.787 best_h=240 → `NO_SIGNAL`
  - `bollinger_z_revert(window=24,z=2.5) & trend_regime_1h(span=168)` events=141 p=1.000 best_h=240 → `NO_SIGNAL`
  - `bollinger_z_revert(window=96,z=2.5)` events=835 p=1.000 best_h=240 → `NO_SIGNAL`
  - `bollinger_z_revert(window=96,z=2.5) & trend_regime_1h(span=168)` events=1 p=1.000 best_h=240 → `NO_SIGNAL`
  - `rsi_oversold_bounce(period=14,level=25)` events=284 p=1.000 best_h=240 → `NO_SIGNAL`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['bollinger_z_revert', 'rsi_oversold_bounce'] / None · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000013 · campaign `camp-000013` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000013: Coinone BTC-KRW 1h: atr_channel_breakout(span 24, mult 3) with ATR exits (SL 2/3 ATR x TP 1.5/3 ATR, max hold 480 min = information horizon) earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite.
- 결과: **failed** · 주 실패 유형: `VAL_FAIL` · 분포: –
- 파생: drv-000008 (계열 점검: ok)
- 깔때기(전체 4 스펙): G1 4 · SR_w>0 4 · G4 4 | 상위 5: G0 1 · G1 1 · G2 1 · G3 1 · G4 1 · G5 1 · Dev 생존 1
- 선택편향 입력: n_campaign_specs=4, k_eff_clusters=1, n_family_trials=37, n_global_trials=94, n_trials_dsr=9.250, pbo=0.058, cluster_threshold=0.500
- Val(G6): 평가 1 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c2b_vol_breakout_atrch_1h_atr_72bbc9dae5` SR_w=1.019 trades=610 first_failed=None → `VAL_FAIL` ['VAL_FAIL'] · val=fail
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 atr_channel_breakout / [[]] · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-08 passes

**해석 (AI, 검증 상태 표시)**

- [REFUTED] int-000006 (신뢰도 medium): 1h ATR-channel breakout (span 24, mult 3) is the first signal in this ledger whose information survives conversion to a strategy on Dev (G0-G5 pass) and is not cost-eaten at Coinone costs, but it is long-only time-series momentum whose Dev PnL is concentrated in up-trend 30d regimes and in 2021/2023/2024; 2022 is weak and 2025 negative. It behaves like trend participation with real but regime-conditional timing information, not regime-independent alpha. Under Upbit grade cost it is negative (grade B at best).
  - 근거: fact-000013 candidates[0]: sharpe 1.457, sharpe_w 1.019, gross_sharpe_w 1.365, 610 trades, G2 p 0.011, G3 neighbour median 0.62, G4 rolling 0.79, G5 dsr 0.991 pbo 0.058; G6 outcome fail (code only); camp-000013 results.parquet yearly Sharpe (Dev, coinone_conservative): 2021 2.25, 2022 0.50, 2023 2.03, 2024 1.85, 2025 -0.25; upbit_conservative sharpe_w -0.26; ad-hoc Dev robustness_for_spec(c2b_vol_breakout_atrch_1h_atr_72bbc9dae5), no ledger trial: regime FAIL (up-trend days 0.74 of PnL, Sharpe 2.74; sideways 0.94, down 0.84); alpha pass (alpha_t 2.78, beta 0.10); leave-year-out min 1.22; top 1% trades 0.27 of PnL; latency 1 bar retention 0.95; cost x2 Sharpe 1.16; cross-venue Upbit ratio 0.94; per-year side calculation of screen config (camp-000009, Dev, no ledger): excess at 480 min positive in all 5 years, z>2 only 2023/2024
  - 검증 방법: Pre-registered 1h screen in family vol_breakout of atr_channel_breakout(24,3) with vol_regime(24, 0.67) vs none, plus a per-year/regime table: refuted (i.e. the edge is regime-independent) if excess at 480 min is > 0 with z > 2 in down- and sideways-30d-regime subsets separately on Dev. No Dev parameter may be chosen from the G6 outcome.
  - refuted by camp-000015 (2026-11-10T01:26:23+00:00): Partial refutation by its own pre-registered criterion. The test named in int-000006 (1h screen of atr_channel_breakout(24,3) with vol_regime(24,0.67) vs none plus a per-regime table) was run: screen camp-000015 (fact-000015) and the logged per-regime side calculation camp-000016 (Dev). For the unfiltered config that int-000006 describes, the excess at 480 min is > 0 with z > 2 in down (33.9 bp, z 2.83) and sideways (27.3 bp, z 2.41) 30d regimes separately, so the claim that the timing information is regime-conditional is refuted. What still holds: strategy PnL stays concentrated in up-trend days (camp-000017 survivor 0.67), 2022/2025 are weak, Upbit cost is near zero or negative (grade B at best). For the vol_regime 0.67 config the criterion is not met (down z 0.79). See int-000009 for the drift explanation (unverified).

### fact-000014 · campaign `camp-000014` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000014: Coinone BTC-KRW 1h: donchian_breakout(lookback 48/144 h) with ATR exits (SL 2.5/3.5 ATR x TP 2.5/5 ATR, max hold 1440 min = information horizon) earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite.
- 결과: **failed** · 주 실패 유형: `SELECTION_BIAS` · 분포: `SELECTION_BIAS`×1
- 파생: drv-000009 (계열 점검: ok)
- 깔때기(전체 8 스펙): G1 6 · SR_w>0 7 · G4 4 | 상위 5: G0 1 · G1 1 · G2 1 · G3 1 · G4 1 · G5 0 · Dev 생존 0
- 선택편향 입력: n_campaign_specs=8, k_eff_clusters=1, n_family_trials=45, n_global_trials=102, n_trials_dsr=5.625, pbo=0.525, cluster_threshold=0.500
- Val(G6): 평가 0 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c2b_vol_breakout_donch_1h_atr_ca225bbff7` SR_w=0.988 trades=339 first_failed=G5 → `SELECTION_BIAS` ['SELECTION_BIAS'] · val=not_run
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 donchian_breakout / [[]] · 재검토 기한 2027-05-08
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-08 passes

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000007 (신뢰도 low): At 1h decisions the donchian breakout information (screen camp-000009: 24/48/144 all passed, info horizon 1440 rising) is concentrated in the longest lookback; lookback 48 converts poorly (sharpe_w -0.37..0.75) so the campaign is a narrow cluster with PBO 0.53. Both lookbacks lose in 2022 and 2025, i.e. the multi-day-high breakout is mostly bull-year trend participation; it is the same mechanism as fact-000013 but with fewer trades (~300) so G5 has less power.
  - 근거: fact-000014 candidates[0] (lookback 144, SL 3.5 / TP 2.5 ATR, hold 1440): sharpe_w 0.988, 339 trades, G5 dsr 0.922, pbo 0.525; camp-000014 results.parquet: lookback 48 sharpe_w -0.37/0.15/0.47/0.75 vs lookback 144 0.83-0.99; yearly 2022 -0.38, 2025 0.02 for the representative; upbit_conservative sharpe_w 0.02-0.40 (lookback 144); per-year side calculation of screen camp-000009 configs (Dev, no ledger): donchian 48 excess negative in 2022 and 2025, donchian 144 2025 excess 3.5 bp
  - 검증 방법: Same-family campaign that holds lookback 144 and adds a regime filter would be a new trial; refuted if lookback 144 without filter shows 2022 and 2025 Dev Sharpe > 0.5 under any of the 4 exit variants (it does not in camp-000014, so the claim stands until new data).

### fact-000015 · signal_screen `camp-000015` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000015: On Coinone BTC-KRW at 1h decisions, atr_channel_breakout(24,3) carries more information per event when 24h realised vol is in the upper half / tercile of its trailing 7-day range (vol_regime 0.5 / 0.67), and that information is present in down- and sideways-30d regimes, not only up-trends.
- 결과: **passed** · 주 실패 유형: `None` · 분포: –
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `atr_channel_breakout(span=24,mult=3.0) & vol_regime(window=24,min_pct=0.5)` events=850 p=0.000 best_h=480 → PASS
  - `atr_channel_breakout(span=24,mult=3.0) & vol_regime(window=24,min_pct=0.67)` events=678 p=0.001 best_h=480 → PASS
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['atr_channel_breakout'] / None · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000009 (신뢰도 medium): The event-level timing information of 1h atr_channel_breakout(24,3) at 480 min is not confined to up-trend 30d regimes: against a null matched within each regime subset, the unfiltered signal has similar excess in down, sideways and up terciles. The up-trend concentration of strategy PnL (camp-000013 0.74, camp-000017 0.67) therefore comes mostly from unadjusted drift (a long-only book keeps the regime return during the hold) and from more events in up regimes, not from timing information that only exists in bull markets. The high-vol filter does not add down-regime information; at vol_regime 0.67 the down-regime excess is weak.
  - 근거: camp-000016 (log_exploration, 9 configs, Dev): excess at 480 min by trailing-30d BTC return tercile, null matched within regime: unfiltered down 33.9 bp z 2.83 (n 281) / sideways 27.3 z 2.41 (n 379) / up 27.5 z 2.44 (n 488); vol_regime 0.5: 25.3 z 1.71 / 41.2 z 3.04 / 31.8 z 2.24; vol_regime 0.67: 13.3 z 0.79 / 40.1 z 2.63 / 44.4 z 2.66; raw mean unfiltered down 29.4 vs up 40.7 bp; fact-000015: both vol-filtered configs passed (n 850 / 678, p 3e-4 / 6e-4, info horizon 480 peaked); camp-000019 (log_exploration, 1 spec): Dev robustness of camp-000017 survivor: up-trend PnL share 0.67, Sharpe down 0.98 / sideways 0.84 / up 2.64; research/knowledge/mechanisms.md#M3 (motivating evidence K-P0-4 is lockbox-exposed; not used for grid)
  - 검증 방법: Same family: per-regime strategy PnL minus exposure-matched BTC B&H for the unfiltered 1h atr_channel(24,3) spec (camp-000013 grid) on Dev; refuted if the down- and sideways-regime excess vs matched B&H is <= 0 while the up-regime excess is > 0. Re-run the per-regime event study on any Dev extension; refuted if the down-regime excess z < 1 there.

### fact-000016 · campaign `camp-000017` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000017: Coinone BTC-KRW 1h: atr_channel_breakout(span 24, mult 3) taken only when 24h realised vol is in the upper half / tercile of its trailing range (vol_regime(24, 0.5 / 0.67)), with ATR exits (SL 2/3 ATR x TP 1.5/3 ATR, max hold 480 min = information horizon), earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite (incl. regime share <= 70%).
- 결과: **failed** · 주 실패 유형: `VAL_FAIL` · 분포: –
- 파생: drv-000011 (계열 점검: ok)
- 깔때기(전체 8 스펙): G1 8 · SR_w>0 8 · G4 6 | 상위 5: G0 1 · G1 1 · G2 1 · G3 1 · G4 1 · G5 1 · Dev 생존 1
- 선택편향 입력: n_campaign_specs=8, k_eff_clusters=1, n_family_trials=64, n_global_trials=121, n_trials_dsr=8.000, pbo=0.073, cluster_threshold=0.500
- Val(G6): 평가 1 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c3_vol_breakout_atrch_1h_hivol_atr_3247d3023d` SR_w=1.145 trades=326 first_failed=None → `VAL_FAIL` ['VAL_FAIL'] · val=fail
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 atr_channel_breakout / [['vol_regime']] · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-09 passes

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000010 (신뢰도 medium): Adding vol_regime(24, 0.5/0.67) to 1h atr_channel_breakout(24,3) with unchanged ATR exits mainly thins trades (610 -> 326-411) and gives a small, statistically insignificant improvement on Dev: weighted Sharpe 1.02 -> 1.14, 2025 Sharpe -0.25 -> 0.48, Upbit-cost Sharpe_w -0.26 -> 0.37, up-trend PnL share 0.74 -> 0.67. The ablation says the filter is removable (delta 0.13, bootstrap s.e. 0.35). The filtered and unfiltered representatives both failed G6 (outcome code only), so the vol filter is not a new mechanism and does not change the verdict on this signal. The Dev robustness pass (regime share 0.67 vs limit 0.70) is marginal.
  - 근거: fact-000016 candidates[0] (vol 0.67, SL 2 / TP 3 ATR, hold 480): sharpe 1.587, sharpe_w 1.145, gross_sharpe_w 1.353, 326 trades (60 recent), G2 p 0.023, G3 neighbour median 1.05, G4 rolling 0.81 recent 1.04, G5 dsr 0.999 pbo 0.073; G6 outcome fail (code only); camp-000017 ablation_dev: dropping vol_regime -> logic 72bbc9dae527d230 (= camp-000013 representative) sharpe_w 1.019, delta 0.126, se 0.348, removable (ablation campaign camp-000018); camp-000017 results.parquet (Dev, coinone_conservative): yearly Sharpe 2021..2025 2.51/0.97/2.28/1.24/0.48; all 8 specs sharpe_w > 0 (0.27..1.14), TP 3 specs 1.05..1.14, TP 1.5 specs 0.27..0.62; upbit_conservative sharpe_w 0.18..0.37 for TP 3, negative for TP 1.5; camp-000019 (log_exploration): robustness_for_spec all 7 checks pass; regime up share 0.670; alpha_t 3.21 beta 0.08; top 1% trades 0.19 of PnL; leave-year-out min 1.34; cross-venue ratio 0.81; camp-000013 / fact-000013 (Dev): unfiltered representative sharpe_w 1.019, 610 trades, yearly 2.25/0.50/2.03/1.85/-0.25, up share 0.74
  - 검증 방법: No further Dev campaign on this signal is informative: Dev is exhausted for it (two Dev survivors, two G6 fails). Refuted if forward/paper observation (after a human decision) or a Dev extension shows the filtered spec Sharpe below the unfiltered one by more than the bootstrap s.e.

### fact-000017 · signal_screen `camp-000020` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000019: On Coinone BTC-KRW at 1h decisions, a rise of >= 100 bp over the last 3 h or 12 h (roc_threshold) is followed by 4-24 h returns above a matched (KST hour x vol) null net of coinone_conservative cost, and more so when the decision bar's volume is abnormally high vs its trailing 2-day distribution (volume_z(48) >= 1 / >= 2).
- 결과: **passed** · 주 실패 유형: `None` · 분포: `NO_SIGNAL`×1
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `roc_threshold(lookback=3,thr_bp=100)` events=2545 p=0.744 best_h=1440 → `NO_SIGNAL`
  - `roc_threshold(lookback=3,thr_bp=100) & volume_z(window=48,min_z=1.0)` events=981 p=0.058 best_h=1440 → PASS
  - `roc_threshold(lookback=3,thr_bp=100) & volume_z(window=48,min_z=2.0)` events=371 p=0.077 best_h=1440 → PASS
  - `roc_threshold(lookback=12,thr_bp=100)` events=5033 p=0.030 best_h=480 → PASS
  - `roc_threshold(lookback=12,thr_bp=100) & volume_z(window=48,min_z=1.0)` events=1419 p=0.075 best_h=1440 → PASS
  - `roc_threshold(lookback=12,thr_bp=100) & volume_z(window=48,min_z=2.0)` events=440 p=0.046 best_h=1440 → PASS
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['roc_threshold'] / None · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000018 · signal_screen `camp-000021` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000020: On Coinone BTC-KRW at 1h decisions at the KST morning (decision hour 09/10 KST, session_kst asia_open), an overnight rise of >= 100 bp over the last 12 h / 24 h is followed by 4-24 h returns above a matched null drawn from the same morning hours, net of coinone_conservative cost, and this excess is larger than for the same entry at the US open (placebo, us_open) and than the unfiltered roc(12,100) config of screen c3c_vol_breakout_roc_volz_1h.
- 결과: **failed** · 주 실패 유형: `NO_SIGNAL` · 분포: `NO_SIGNAL`×3
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `roc_threshold(lookback=12,thr_bp=100) & session_kst(session=asia_open)` events=493 p=0.111 best_h=240 → `NO_SIGNAL`
  - `roc_threshold(lookback=12,thr_bp=100) & session_kst(session=us_open)` events=742 p=0.970 best_h=1440 → `NO_SIGNAL`
  - `roc_threshold(lookback=24,thr_bp=100) & session_kst(session=asia_open)` events=579 p=0.137 best_h=1440 → `NO_SIGNAL`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['roc_threshold'] / None · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000012 (신뢰도 medium): The KST-morning attention-lag mechanism (Korean retail reacting at 09-10 KST to overnight up-moves) shows no information beyond generic hourly momentum on Coinone Dev: asia_open roc(12,100) excess 10.5 bp z 1.79 @240 and roc(24,100) 19.1 bp z 1.69 @1440 are no larger than the unfiltered roc(12,100) (8.5 bp z 2.32 @480; 16.9 bp @1440), and the us_open placebo is 6.8 bp z 0.46. The salient Dev feature of morning hours is instead a negative unconditional 4 h drift in the matched null (about -14 bp at 240 min), so the raw 4 h mean after an overnight rise is itself negative (-3.8 bp). Long-only continuation trades at the KST open are not supported.
  - 근거: fact-000018 (screen camp-000021): asia_open roc12 n 493 p_signal 0.111; roc24 n 579 p 0.137; us_open n 742 p 0.97; research/campaigns/camp-000021/signal_screen.md roc(12,100)&asia_open table: null mean -14.3 bp @240, raw mean -3.8 bp; fact-000017 (screen camp-000020): roc(12,100) unfiltered excess 8.5 bp z 2.32 @480; research/knowledge/phase0_findings.md#K-P0-3 (lockbox exposed, Val exposed) - motivation only
  - 검증 방법: Same 3-config screen on Upbit 1h (the venue where Korean retail is the marginal trader): refuted if the asia_open config's excess exceeds both the us_open placebo and the unfiltered roc(12,100) excess by >= 2 HAC SE with z >= 3.

### fact-000019 · signal_screen `camp-000022` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000021: On Coinone BTC-KRW at 15m decisions, closes >= 2.5 sd below the 24-bar (6 h) mean on abnormally high volume (volume_z(96) >= 1 / >= 2 vs the trailing day) - the price+volume proxy for liquidation cascades in mechanisms.md M4 - are followed by 30-120 min returns above a matched (KST hour x vol) null, net of coinone_conservative cost, with larger excess than the unfiltered bollinger_z_revert(24, 2.5) of screen camp-000011.
- 결과: **failed** · 주 실패 유형: `NO_SIGNAL` · 분포: `NO_SIGNAL`×2
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `bollinger_z_revert(window=24,z=2.5) & volume_z(window=96,min_z=1.0)` events=1474 p=0.107 best_h=30 → `NO_SIGNAL`
  - `bollinger_z_revert(window=24,z=2.5) & volume_z(window=96,min_z=2.0)` events=889 p=0.972 best_h=120 → `NO_SIGNAL`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['bollinger_z_revert'] / None · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000013 (신뢰도 medium): Abnormal Coinone volume does not isolate forced-selling (cascade) dislocations: adding volume_z to bollinger_z_revert(24,2.5) lowers the reversion excess at 15m (vz>=1: 3.5 bp z 1.81 @30 min; vz>=2: 1.8 bp z 0.46, vs unfiltered 4.4 bp z 2.97 in camp-000011) and leaves the 1h version without information (<= 2.9 bp, z <= 0.41, camp-000023). Either high-volume drops on Coinone are more often information-driven (no reversion) or Coinone volume is too bot-contaminated (P-D1) to proxy liquidations; the price+volume M4 proxy in mechanisms.md is not supported on Coinone volume.
  - 근거: fact-000019 (screen camp-000022): vz(96,1) n 1474 p_signal 0.107 excess 3.5 bp z 1.81 @30; vz(96,2) n 889 p 0.97; fact-000020 (screen camp-000023, 1h): vz(48,1) n 541 p 1.0; vz(48,2) n 314 p 1.0; fact-000011 (screen camp-000011): unfiltered bollinger(24,2.5) 15m excess 4.4 bp z 2.97 @30 min; research/knowledge/pitfalls.md#P-D1 (lockbox none, Val none)
  - 검증 방법: Experimental block upbit_volume_z (Upbit quote-volume z-score, live-ready) as a filter on bollinger_z_revert(24,2.5) at 15m (family liq_meanrev): refuted if the Upbit-volume-filtered excess at 30-60 min exceeds the unfiltered 4.4 bp by >= 2 HAC SE.

### fact-000020 · signal_screen `camp-000023` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000022: On Coinone BTC-KRW at 1h decisions, closes >= 2.5 sd below the 24 h mean on abnormally high volume (volume_z(48) >= 1 / >= 2 vs the trailing 2 days) are followed by 4-24 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost, unlike the price-only 1h reversion that was NO_SIGNAL (camp-000012).
- 결과: **failed** · 주 실패 유형: `NO_SIGNAL` · 분포: `NO_SIGNAL`×2
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `bollinger_z_revert(window=24,z=2.5) & volume_z(window=48,min_z=1.0)` events=541 p=1.000 best_h=240 → `NO_SIGNAL`
  - `bollinger_z_revert(window=24,z=2.5) & volume_z(window=48,min_z=2.0)` events=314 p=1.000 best_h=240 → `NO_SIGNAL`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['bollinger_z_revert'] / None · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000021 · campaign `camp-000024` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000023: Coinone BTC-KRW 1h: a >= 100 bp rise over 3 h or 12 h (roc_threshold) on abnormally high decision-bar volume (volume_z(48) >= 1), with ATR exits (SL 3 ATR x TP 2 / 4 ATR, max hold 1440 min = information horizon or 2880 min low-turnover variant), earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite (incl. regime share <= 70%).
- 결과: **failed** · 주 실패 유형: `NO_SIGNAL` · 분포: `NO_SIGNAL`×1
- 파생: drv-000016 (계열 점검: ok)
- 깔때기(전체 8 스펙): G1 8 · SR_w>0 8 · G4 5 | 상위 5: G0 1 · G1 1 · G2 0 · G3 1 · G4 1 · G5 1 · Dev 생존 0
- 선택편향 입력: n_campaign_specs=8, k_eff_clusters=1, n_family_trials=83, n_global_trials=144, n_trials_dsr=10.375, pbo=0.201, cluster_threshold=0.500
- Val(G6): 평가 0 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c3c_vol_breakout_roc_volz_1h_atr_36b866681f` SR_w=0.906 trades=614 first_failed=G2 → `NO_SIGNAL` ['NO_SIGNAL'] · val=not_run
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 roc_threshold / [['volume_z']] · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-09 passes

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000011 (신뢰도 low): The event-level volume-confirmation effect on Coinone 1h (screen camp-000020: volume_z(48)>=1 configs excess 24-29 bp at 1440 min vs 8-17 bp unfiltered, but z only ~2) does not convert into a strategy that beats random entries in the same high-volume hours: the best spec (roc(3,100)&vz(48,1), SL3/TP4 ATR, 1440 min) has Sharpe_w 0.91 at the 89th percentile of the trade-count-matched null whose median is already 0.39. Most of the return is the drift of being long in high-volume hours (exposure 48% of days) plus generic 1h momentum, not volume-specific timing. Bot-contaminated Coinone volume (P-D1) may also blur the filter.
  - 근거: fact-000021 candidates[0].gates_dev.G2 p_value 0.112, sharpe_w 0.906, null median 0.386, null p95 1.091; fact-000017 (screen camp-000020): roc(3,100)&vz(48,1) excess 28.8 bp z 2.07 @1440; roc(12,100)&vz(48,1) 24.3 bp z 1.96; unfiltered roc(3,100) 8.3 bp z 0.68, roc(12,100) 16.9 bp @1440; research/reports/campaign_camp-000024.md B&H reference: exposure_days 0.483, up-regime capture 0.45, down-regime capture -2.45 (Dev); research/knowledge/pitfalls.md#P-D1 (lockbox none, Val none)
  - 검증 방법: Signal screen (family vol_breakout) of roc_threshold(3,100) x {none, volume_z(48,1.0)} on Upbit 1h data (uncontaminated volume): refuted if the volume_z config shows excess z >= 3 at 1440 min AND exceeds the unfiltered excess by >= 2 HAC SE; otherwise the volume-confirmation line stays closed.

### fact-000022 · signal_screen `camp-000025` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000024: On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled) at 5m decisions, sharp downside dislocations - close >= 2.5 sd below the 96-bar mean (bollinger_z_revert(96,2.5)) or an equal-weight reversion composite score >= 2.0 (composite_z reversion: -r_6, -boll_48, +log1p vol) - are followed by 30-120 min returns above a per-asset matched (KST hour x vol) null, net of per-asset coinone_conservative cost (D17).
- 결과: **passed** · 주 실패 유형: `None` · 분포: `COST_EATEN`×1
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `bollinger_z_revert(window=96,z=2.5)` events=8942 p=0.004 best_h=120 → PASS
  - `composite_z(preset=reversion,threshold=2.0)` events=7080 p=0.011 best_h=60 → `COST_EATEN`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['bollinger_z_revert', 'composite_z'] / None · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000023 · signal_screen `camp-000026` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000025: On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled) at 15m decisions, sharp downside dislocations - close >= 2.5 sd below the 24-bar (6 h) mean (bollinger_z_revert(24,2.5)) or an equal-weight reversion composite score >= 1.5 / >= 2.0 (composite_z reversion) - are followed by 1-4 h returns above a per-asset matched (KST hour x vol) null, net of per-asset coinone_conservative cost (D17).
- 결과: **passed** · 주 실패 유형: `None` · 분포: `COST_EATEN`×2
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `bollinger_z_revert(window=24,z=2.5)` events=6278 p=0.002 best_h=60 → `COST_EATEN`
  - `composite_z(preset=reversion,threshold=1.5)` events=8467 p=0.003 best_h=60 → `COST_EATEN`
  - `composite_z(preset=reversion,threshold=2.0)` events=3883 p=0.002 best_h=60 → PASS
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['bollinger_z_revert', 'composite_z'] / None · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000024 · signal_screen `camp-000027` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000026: On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled) at 1h decisions, upside momentum entries - atr_channel_breakout(24,3), roc_threshold(12,100) and the equal-weight composite scores composite_z momentum >= 1.0 / >= 1.5 and volume_momentum >= 1.5 - are followed by 4-24 h returns above a per-asset matched (KST hour x vol) null, net of per-asset coinone_conservative cost (D17).
- 결과: **passed** · 주 실패 유형: `None` · 분포: `NO_SIGNAL`×1
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `atr_channel_breakout(span=24,mult=3.0)` events=3225 p=0.003 best_h=480 → PASS
  - `roc_threshold(lookback=12,thr_bp=100)` events=17392 p=0.003 best_h=480 → PASS
  - `composite_z(preset=momentum,threshold=1.0)` events=9569 p=0.009 best_h=480 → PASS
  - `composite_z(preset=momentum,threshold=1.5)` events=3933 p=0.048 best_h=480 → PASS
  - `composite_z(preset=volume_momentum,threshold=1.5)` events=2859 p=0.325 best_h=480 → `NO_SIGNAL`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['atr_channel_breakout', 'composite_z', 'roc_threshold'] / None · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000025 · campaign `camp-000028` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000027: Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 15m: after the equal-weight reversion composite composite_z(reversion) >= 2.0 a maker scale-in ladder (first tranche market, 3-4 equal tranches with limit rungs every 0.5/1.0 ATR below, maker take-profit 1.25 ATR per tranche or on average cost, aggregate stop 1.5 ATR below the last rung, max hold 60 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev, beat the ladder-matched random-entry null and pass the binding tail check.
- 결과: **failed** · 주 실패 유형: `COST_EATEN` · 분포: `COST_EATEN`×1
- 파생: drv-000021 (계열 점검: ok)
- 깔때기(전체 8 스펙): G1 8 · SR_w>0 0 · G4 0 | 상위 5: G0 1 · G1 1 · G2 0 · G3 0 · G4 0 · G5 0 · Dev 생존 0
- 선택편향 입력: n_campaign_specs=8, k_eff_clusters=1, n_family_trials=58, n_global_trials=162, n_trials_dsr=7.250, pbo=0.488, cluster_threshold=0.500
- Val(G6): 평가 0 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c4_liq_meanrev_ladder_15m_u3_7207679868` SR_w=-0.127 trades=9910 first_failed=G2 → `COST_EATEN` ['COST_EATEN', 'UNSTABLE', 'SELECTION_BIAS'] · val=not_run
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 composite_z / [[]] · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-09 passes

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000014 (신뢰도 medium): The dip-buy trigger carries real timing information but the maker scale-in ladder built on it is cost-eaten on the 3-coin Coinone universe. At 15m (composite_z reversion 2.0) and 5m (bollinger 96/2.5), every ladder spec beats the ladder-matched random-entry null by a wide margin (G2 percentile 1.0; null median Sharpe_w -2.29 at 15m, -2.90 at 5m). But gross Sharpe_w is at most 0.61 (15m) / 0.88 (5m), and net Sharpe_w at per-asset coinone_conservative is negative for all 16 specs (best -0.13). Maker rungs and maker take-profits do not close the gap: the first tranche, stops and time exits still pay taker cost, and filled rungs are falling-knife inventory. The losses sit in 2022-2023 and in the ETH sleeve. Without a trigger, the same ladder structure (the random null) loses heavily, so 'buy a part on a dip, more on a further dip, sell on small rises' is negative expectancy at these costs; only the dip trigger keeps it near zero.
  - 근거: fact-000025 candidates[0]: sharpe_w -0.127, G2 null sharpe_w_median -2.293 p95 -1.451, gross neighbor median 0.376; fact-000026 candidates[0]: sharpe_w -0.656, G2 null median -2.900 p95 -1.995, gross neighbor median 0.629; camp-000028/029 results (Dev): gross_sharpe_w 0.15-0.61 / -0.03-0.88, Upbit-cost sharpe_w -2.2..-6.7; per-asset ETH sharpe_w -0.84..-2.28; research/reports/fill_model_20261110.md (Forward, lockbox none, Val none): maker markout at dip signals -2.5..-4.7 bp at 5 min, n=20
  - 검증 방법: When liq_meanrev is released (cost change such as a Coinone maker rebate or lower XRP/ETH spread, or a new Dev period): pre-register the same trigger with a single entry at the first-tranche size (entry_fraction 1/N) and the same exits next to the ladder. The ladder-adds-value claim is refuted again if the ladder's gross Sharpe_w does not exceed the single entry's by >= 0.3. The cost-eaten claim is refuted if any ladder spec reaches net Sharpe_w > 0 with G2 passing.

### fact-000026 · campaign `camp-000029` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000028: Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 5m: after bollinger_z_revert(96,2.5) a maker scale-in ladder (first tranche market, 3-4 equal tranches with limit rungs every 0.5/1.0 ATR below, maker take-profit 2.5 ATR per tranche or on average cost, aggregate stop 1.5 ATR below the last rung, max hold 120 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev, beat the ladder-matched random-entry null and pass the binding tail check.
- 결과: **failed** · 주 실패 유형: `COST_EATEN` · 분포: `COST_EATEN`×1
- 파생: drv-000020 (계열 점검: ok)
- 깔때기(전체 8 스펙): G1 8 · SR_w>0 0 · G4 0 | 상위 5: G0 1 · G1 1 · G2 0 · G3 0 · G4 0 · G5 0 · Dev 생존 0
- 선택편향 입력: n_campaign_specs=8, k_eff_clusters=1, n_family_trials=66, n_global_trials=170, n_trials_dsr=8.250, pbo=0.000, cluster_threshold=0.500
- Val(G6): 평가 0 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c4_liq_meanrev_ladder_5m_u3_1fd10db577` SR_w=-0.656 trades=23118 first_failed=G2 → `COST_EATEN` ['COST_EATEN', 'UNSTABLE', 'SELECTION_BIAS'] · val=not_run
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 bollinger_z_revert / [[]] · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-09 passes

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000015 (신뢰도 medium): Tighter ladders are worse. At 5m, rungs every 0.5 ATR fill 67-72 % of the time against 50-58 % at 1.0 ATR, and those specs have net Sharpe_w -1.57..-1.88, 2022 returns -43..-51 % and worst MTM day -9.5..-11.8 %. The 1.0 ATR specs have Sharpe_w -0.66..-1.44 and 2022 returns -26..-41 %. Every 5m ladder would also fail the binding D19 tail check on 2022 (< -20 %), and the 0.5-ATR 4-tranche specs on worst day (> 10 %). Inventory accumulates in trending declines faster than the reversion pays it back, so more and closer rungs mean more falling-knife exposure, not more liquidity premium.
  - 근거: camp-000029 results (Dev, coinone_conservative): rung fill ratio (n_trades-n_positions)/(n_positions*(N-1)) 0.50-0.72; bear_total_return -0.26..-0.51; worst_day -0.059..-0.118; camp-000028 results (Dev): 15m 2022 returns -0.07..-0.17, worst_day -0.033..-0.061; config/gates.yaml robustness tail thresholds (D19)
  - 검증 방법: If liq_meanrev is released: a 2-value step grid {1.0, 1.5} ATR with the same trigger. Refuted if the wider step does not raise net Sharpe_w and lower the 2022 loss relative to 1.0 ATR.

### fact-000027 · campaign `camp-000030` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000029: Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 1h: atr_channel_breakout(24,3) with half-size entries (entry_fraction 0.5) and ATR exits (SL 2 ATR x TP 1.5/3 ATR, max hold 480/960 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev and the robustness suite.
- 결과: **failed** · 주 실패 유형: `PARAM_SPIKE` · 분포: `PARAM_SPIKE`×1
- 파생: drv-000022 (계열 점검: ok)
- 깔때기(전체 4 스펙): G1 4 · SR_w>0 2 · G4 2 | 상위 5: G0 1 · G1 1 · G2 1 · G3 0 · G4 1 · G5 0 · Dev 생존 0
- 선택편향 입력: n_campaign_specs=4, k_eff_clusters=1, n_family_trials=92, n_global_trials=174, n_trials_dsr=23.000, pbo=0.001, cluster_threshold=0.500
- Val(G6): 평가 0 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c4_vol_breakout_atrch_1h_u3_half_4fc37395fd` SR_w=0.983 trades=1773 first_failed=G3 → `PARAM_SPIKE` ['PARAM_SPIKE', 'SELECTION_BIAS'] · val=not_run
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 atr_channel_breakout / [[]] · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-09 passes

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000016 (신뢰도 low): Moving 1h atr_channel_breakout(24,3) to the pooled 3-coin universe at half size passes G2 for the first time against a trade-count-matched null (Sharpe_w 0.98, null median -0.58, p 0.003; 1.0 position/day; MTM MDD 8.8 %; 2022 -1 %). But the result rests on the TP 3 ATR exit: the TP 1.5 neighbours are negative (-0.45/-0.63, G3 fail). DSR is 0.28 at N_dsr 23. The Upbit-grade Sharpe_w is about 0 (-0.08/+0.12), and the XRP sleeve adds nothing (Sharpe_w 0.20). This is weak, exit-sensitive momentum with a right-skewed payoff (daily skew 3.98), not a validated edge. The BTC sleeve is the same rule whose BTC-only versions carry VAL_FAIL.
  - 근거: fact-000027 candidates[0]: sharpe_w 0.983, G2 p 0.003 percentile 0.998, G3 neighbor_median 0.176, G5 dsr 0.276 n_trials 23 pbo 0.001; camp-000030 results (Dev): upbit_conservative sharpe_w -0.084 / 0.124 for TP 3 ATR specs; per-asset sharpe_w BTC 0.95, XRP 0.20, ETH 1.14; fact-000013, fact-000016 (taxonomy VAL_FAIL, outcome codes only)
  - 검증 방법: No further grid in this line. Falsify on unseen data only: when Dev is extended (lockbox rolled into Dev) or via a human-approved Forward watchlist entry, evaluate the exact spec c4_vol_breakout_atrch_1h_u3_half_4fc37395fd unchanged. Refuted if the new-period Sharpe_w (MTM, coinone_conservative) <= 0 over >= 300 positions.

### fact-000028 · campaign `camp-000031` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000030: Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 1h: roc_threshold(12,100) with half-size entries (entry_fraction 0.5) and ATR exits (SL 2 ATR x TP 1/2 ATR, max hold 480/960 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev and the robustness suite.
- 결과: **failed** · 주 실패 유형: `COST_EATEN` · 분포: `COST_EATEN`×1
- 파생: drv-000023 (계열 점검: ok)
- 깔때기(전체 4 스펙): G1 4 · SR_w>0 0 · G4 0 | 상위 5: G0 1 · G1 1 · G2 0 · G3 0 · G4 0 · G5 0 · Dev 생존 0
- 선택편향 입력: n_campaign_specs=4, k_eff_clusters=1, n_family_trials=96, n_global_trials=178, n_trials_dsr=24.000, pbo=0.001, cluster_threshold=0.500
- Val(G6): 평가 0 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `c4_vol_breakout_roc_1h_u3_half_91a6706e89` SR_w=-0.483 trades=5702 first_failed=G2 → `COST_EATEN` ['COST_EATEN', 'UNSTABLE', 'SELECTION_BIAS'] · val=not_run
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 roc_threshold / [[]] · 재검토 기한 2027-05-09
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-09 passes

### fact-000029 · signal_screen `camp-000032` · family `managed_hold`

**사실 (자동, 불변)**

- 가설 hyp-000031: On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled) at 4h decisions, the long-horizon trend state trend_ema_4h(span, band 0) - close above EMA(close, span) for span 42/90/180/360 4h bars (7/15/30/60 days) - is followed by 2-7 day returns above a per-asset matched (KST hour x vol) null, net of per-asset coinone_conservative round-trip cost (D17).
- 결과: **failed** · 주 실패 유형: `NO_SIGNAL` · 분포: `NO_SIGNAL`×4
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `trend_ema_4h(span=42,band_bp=0.0)` events=3027 p=0.350 best_h=4320 → `NO_SIGNAL`
  - `trend_ema_4h(span=90,band_bp=0.0)` events=2840 p=0.203 best_h=2880 → `NO_SIGNAL`
  - `trend_ema_4h(span=180,band_bp=0.0)` events=2795 p=0.306 best_h=2880 → `NO_SIGNAL`
  - `trend_ema_4h(span=360,band_bp=0.0)` events=2853 p=1.000 best_h=2880 → `NO_SIGNAL`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['trend_ema_4h'] / None · 재검토 기한 2027-05-11
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000030 · signal_screen `camp-000033` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000032: On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, per-asset matched null) at 4h decisions, the same pre-registered entries as the BTC-only 4h screen camp-000013-era c2b (donchian_breakout 24/48, atr_channel_breakout(24,3), ema_slope(24,5bp)) are followed by 8-48 h returns above a matched (KST hour x vol) null, net of per-asset coinone_conservative cost (D17).
- 결과: **passed** · 주 실패 유형: `None` · 분포: –
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `donchian_breakout(lookback=24)` events=1088 p=0.005 best_h=1440 → PASS
  - `donchian_breakout(lookback=48)` events=807 p=0.001 best_h=1440 → PASS
  - `atr_channel_breakout(span=24,mult=3.0)` events=944 p=0.003 best_h=480 → PASS
  - `ema_slope(span=24,min_slope_bp=5.0)` events=6523 p=0.011 best_h=480 → PASS
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['atr_channel_breakout', 'donchian_breakout', 'ema_slope'] / None · 재검토 기한 2027-05-11
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000031 · campaign `camp-000034` · family `vol_breakout`

**사실 (자동, 불변)**

- 가설 hyp-000033: [redacted: Val/lockbox detail]
- 결과: **validated** · 주 실패 유형: `None` · 분포: –
- 깔때기(전체 2 스펙): G1 2 · SR_w>0 2 · G4 2 | 상위 5: G0 1 · G1 1 · G2 1 · G3 1 · G4 1 · G5 1 · Dev 생존 1
- 선택편향 입력: n_campaign_specs=2, k_eff_clusters=1, n_family_trials=102, n_global_trials=188, n_trials_dsr=51.000, pbo=0.228, cluster_threshold=0.500
- Val(G6): 평가 1 · 통과 1 (결과 코드만) · 잠금: not_accessed
  - #1 `r1b_vol_breakout_donch_4h_u3_half_0dce65e919` SR_w=1.827 trades=554 first_failed=None → `None` [] · val=pass
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 donchian_breakout / [[]] · 재검토 기한 2027-05-11
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-11 passes

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000017 (신뢰도 medium): The pooled 4h donchian(48) edge is carried mostly by the BTC sleeve (Dev sleeve Sharpe 1.82; Dev XRP+ETH-only sub-portfolio Sharpe 1.06 < required 1.49), and BTC 4h donchian(48) had already been screened on the same Dev data (camp-000010). XRP/ETH add diversification more than independent evidence; XRP's Dev median trade is -5 bp with 64% of its Dev PnL in 5 trades.
  - 근거: skeptic review of camp-000034 (Dev only): per-sleeve Dev Sharpe BTC 1.82 / XRP 0.87 / ETH 0.68; Dev PnL shares 0.375/0.429/0.196; XRP top-5 trades 64% of Dev PnL; camp-000010 BTC 4h donchian 48 screen n 303 p 0.0005
  - 검증 방법: Forward/paper after the design date: pooled Sharpe > 0 and XRP+ETH sub-portfolio Sharpe > 0.

### fact-000032 · signal_screen `camp-000035` · family `xasset_btc_lead`

**사실 (자동, 불변)**

- 가설 hyp-000034: On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (pooled, per-asset matched null) at 4h decisions, donchian_breakout(48) entries taken only while Coinone BTC-KRW is in a 4h up-trend (close > EMA 42 or 90 bars = 7 or 15 days; experimental btc_trend_filter, aux coinone_btc) are followed by 8-48 h returns above the matched null, net of per-asset coinone_conservative cost.
- 결과: **passed** · 주 실패 유형: `None` · 분포: –
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `donchian_breakout(lookback=48) & btc_trend_filter(span=42,band_bp=0.0)` events=757 p=0.006 best_h=1440 → PASS
  - `donchian_breakout(lookback=48) & btc_trend_filter(span=90,band_bp=0.0)` events=742 p=0.001 best_h=1440 → PASS
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['donchian_breakout'] / None · 재검토 기한 2027-05-11
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

### fact-000033 · campaign `camp-000036` · family `xasset_btc_lead`

**사실 (자동, 불변)**

- 가설 hyp-000035: [redacted: Val/lockbox detail]
- 결과: **failed** · 주 실패 유형: `SELECTION_BIAS` · 분포: `SELECTION_BIAS`×1
- 깔때기(전체 2 스펙): G1 2 · SR_w>0 2 · G4 2 | 상위 5: G0 1 · G1 1 · G2 1 · G3 1 · G4 1 · G5 0 · Dev 생존 0
- 선택편향 입력: n_campaign_specs=2, k_eff_clusters=1, n_family_trials=4, n_global_trials=192, n_trials_dsr=7.000, pbo=0.378, cluster_threshold=0.500
- Val(G6): 평가 0 · 통과 0 (결과 코드만) · 잠금: not_accessed
  - #1 `r2c_xasset_btc_lead_donch_4h_u3_half_d27e04b72e` SR_w=1.881 trades=500 first_failed=G5 → `SELECTION_BIAS` ['SELECTION_BIAS'] · val=not_run
- 범위: coinone BTC-KRW 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 donchian_breakout / [['btc_trend_filter']] · 재검토 기한 2027-05-11
- 재검토 조건: Dev data period or segment boundaries change (new data, lockbox rolled into Dev/Val); venue or cost model changes (e.g. Coinone 0% fee promotion ends, Upbit fee changes); timeframe / decision interval changes (5m ↔ 1m); a block used here is re-implemented (new block version) or an engine bug is fixed; review date 2027-05-11 passes

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000018 (신뢰도 0.6): BTC-trend filter on alt breakouts did not rescue the alt sleeves: Dev XRP Sharpe 0.72, ETH 0.89 with the filter (BTC sleeve 1.84 unchanged). The 2-spec exit grid failed G5 on PBO 0.378 (DSR 0.999), i.e. TP 1.5 vs 3 ATR rank flips across CSCV splits; the pooled edge is still carried by the BTC sleeve, as in camp-000034.
  - 근거: camp-000036; camp-000035; camp-000034
  - 검증 방법: Do not re-run this hypothesis with a different exit grid (result shopping). Track C closed; next diversification must be a different mechanism (track D 4h dip-buy in up-trend).

### fact-000034 · signal_screen `camp-000037` · family `liq_meanrev`

**사실 (자동, 불변)**

- 가설 hyp-000036: On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, per-asset matched null) at 4h decisions, multi-day oversold dips (close >= 2 sd below the 24-bar = 4-day mean, bollinger_z_revert(24,2.0), or RSI(14) <= 30 bounce) taken only while the asset is above its own 90-bar 4h EMA (experimental trend_ema_filter span 90 = 15 days; trend_regime_1h is invalid on 4h bars) are followed by 8-48 h returns above the matched null, net of per-asset coinone_conservative cost.
- 결과: **failed** · 주 실패 유형: `NO_SIGNAL` · 분포: `NO_SIGNAL`×2
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `bollinger_z_revert(window=24,z=2.0) & trend_ema_filter(span=90,band_bp=0.0)` events=150 p=1.000 best_h=1440 → `NO_SIGNAL`
  - `rsi_oversold_bounce(period=14,level=30) & trend_ema_filter(span=90,band_bp=0.0)` events=17 p=1.000 best_h=480 → `NO_SIGNAL`
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['bollinger_z_revert', 'rsi_oversold_bounce'] / None · 재검토 기한 2027-05-11
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

**해석 (AI, 검증 상태 표시)**

- [UNVERIFIED] int-000019 (신뢰도 0.7): At 4h decisions, multi-day oversold dips almost never occur while the asset stays above its 15-day EMA (bollinger 2 sd: 150 pooled events, excess -1 bp; RSI<=30: 17 events). Dip-buying inside a trend is not a separate 4h signal on Coinone BTC/XRP/ETH; the trend-following edge is captured by breakouts.
  - 근거: camp-000037; camp-000012; camp-000011

### fact-000035 · signal_screen `camp-000040` · family `session_premium`

**사실 (자동, 불변)**

- 가설 hyp-000039: 코인원 BTC-KRW 에서 매일 21:00 UTC(06:00 KST)에 사서 120분 보유한 수익이, 직전 변동성으로 짝지은 다른 시각의 120분 보유(귀무)보다 높고 coinone_conservative 비용을 뺀 평균이 0 보다 크다.
- 결과: **passed** · 주 실패 유형: `None` · 분포: –
- 깔때기(전체 None 스펙): G1 None · SR_w>0 None · G4 None | 상위 None:  · Dev 생존 None
  - `clock_entry_utc(hour=21,minute=0)` events=1630 p=0.000 best_h=120 → PASS
- 범위: coinone None 1m · dev 2021-01-01T00:00:00+00:00~2025-11-01T00:00:00+00:00 · 블록 ['clock_entry_utc'] / None · 재검토 기한 2027-05-12
- 재검토 조건: Dev data period changes; venue/cost model changes; decision interval changes (bar_minutes)

## 파생 가설 (계열 상속)

| id | 부모 | family | override | 가설 |
|---|---|---|---|---|
| drv-000001 | fact-000001 | `xvenue_upbit_lead` | – | On Coinone 1m, only large Upbit impulses (upbit_lead_impulse thr_bp 40, k 1/3, non-quiet KST hours) produce a Coinone catch-up larger than the 4 bp conservative round trip within 5-30 min. |
| drv-000002 | fact-000005 | `liq_meanrev` | – | Coinone 5m bollinger_z_revert(96, 2.5) with/without trend_regime_1h(24), held 120-240 min with wide ATR exits (SL 6, TP 6 ATR) that approximate the event study's time exit, passes G0-G5 on Dev. |
| drv-000003 | fact-000006 | `vol_breakout` | – | Coinone 5m donchian_breakout(288) carries materially more net information when 5m realised vol is in the top 20% of its trailing 7-day range (vol_regime min_pct 0.8). |
| drv-000004 | fact-000006 | `vol_breakout` | – | On Coinone BTC-KRW at 1h decisions, upside breakouts of multi-day highs (donchian_breakout 24/48/144 h), ATR-channel breakouts and persistent positive EMA slope (ema_slope span 48) are followed by 4-24 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost; optionally only in high short-term vol (vol_regime 0.67) or above the 1-week EMA (trend_regime_1h 168). |
| drv-000005 | fact-000006 | `vol_breakout` | – | On Coinone BTC-KRW at 4h decisions, breakouts of 4-8 day highs (donchian_breakout 24/48 bars), ATR-channel breakouts (24, 3) and positive EMA slope (ema_slope 24, 5 bp/bar) are followed by 8-48 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost. |
| drv-000006 | fact-000008 | `liq_meanrev` | – | On Coinone BTC-KRW at 15m decisions, closes far below the rolling mean (bollinger_z_revert window 24/96, z 2.5) are followed by 1-4 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost, with and without a 1h trend filter (trend_regime_1h 24) or a 1-week bull-regime filter (trend_regime_1h 168), and the excess is not concentrated in one Dev year. |
| drv-000007 | fact-000008 | `liq_meanrev` | – | On Coinone BTC-KRW at 1h decisions, closes far below the rolling mean (bollinger_z_revert window 24/96 h, z 2.5) or RSI(14) crossing back above 25 are followed by 4-24 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost, with and without a 1-week bull-regime filter (trend_regime_1h 168), and the excess is not concentrated in one Dev year. |
| drv-000008 | fact-000009 | `vol_breakout` | – | Coinone BTC-KRW 1h: atr_channel_breakout(span 24, mult 3) with ATR exits (SL 2/3 ATR x TP 1.5/3 ATR, max hold 480 min = information horizon) earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite. |
| drv-000009 | fact-000009 | `vol_breakout` | – | Coinone BTC-KRW 1h: donchian_breakout(lookback 48/144 h) with ATR exits (SL 2.5/3.5 ATR x TP 2.5/5 ATR, max hold 1440 min = information horizon) earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite. |
| drv-000010 | fact-000013 | `vol_breakout` | – | On Coinone BTC-KRW at 1h decisions, atr_channel_breakout(24,3) carries more information per event when 24h realised vol is in the upper half / tercile of its trailing 7-day range (vol_regime 0.5 / 0.67), and that information is present in down- and sideways-30d regimes, not only up-trends. |
| drv-000011 | fact-000015 | `vol_breakout` | – | Coinone BTC-KRW 1h: atr_channel_breakout(span 24, mult 3) taken only when 24h realised vol is in the upper half / tercile of its trailing range (vol_regime(24, 0.5 / 0.67)), with ATR exits (SL 2/3 ATR x TP 1.5/3 ATR, max hold 480 min = information horizon), earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite (incl. regime share <= 70%). |
| drv-000012 | fact-000009 | `vol_breakout` | – | On Coinone BTC-KRW at 1h decisions, a rise of >= 100 bp over the last 3 h or 12 h (roc_threshold) is followed by 4-24 h returns above a matched (KST hour x vol) null net of coinone_conservative cost, and more so when the decision bar's volume is abnormally high vs its trailing 2-day distribution (volume_z(48) >= 1 / >= 2). |
| drv-000013 | fact-000009 | `vol_breakout` | – | On Coinone BTC-KRW at 1h decisions at the KST morning (decision hour 09/10 KST, session_kst asia_open), an overnight rise of >= 100 bp over the last 12 h / 24 h is followed by 4-24 h returns above a matched null drawn from the same morning hours, net of coinone_conservative cost, and this excess is larger than for the same entry at the US open (placebo, us_open) and than the unfiltered roc(12,100) config of screen c3c_vol_breakout_roc_volz_1h. |
| drv-000014 | fact-000011 | `liq_meanrev` | – | On Coinone BTC-KRW at 15m decisions, closes >= 2.5 sd below the 24-bar (6 h) mean on abnormally high volume (volume_z(96) >= 1 / >= 2 vs the trailing day) - the price+volume proxy for liquidation cascades in mechanisms.md M4 - are followed by 30-120 min returns above a matched (KST hour x vol) null, net of coinone_conservative cost, with larger excess than the unfiltered bollinger_z_revert(24, 2.5) of screen camp-000011. |
| drv-000015 | fact-000012 | `liq_meanrev` | – | On Coinone BTC-KRW at 1h decisions, closes >= 2.5 sd below the 24 h mean on abnormally high volume (volume_z(48) >= 1 / >= 2 vs the trailing 2 days) are followed by 4-24 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost, unlike the price-only 1h reversion that was NO_SIGNAL (camp-000012). |
| drv-000016 | fact-000017 | `vol_breakout` | – | Coinone BTC-KRW 1h: a >= 100 bp rise over 3 h or 12 h (roc_threshold) on abnormally high decision-bar volume (volume_z(48) >= 1), with ATR exits (SL 3 ATR x TP 2 / 4 ATR, max hold 1440 min = information horizon or 2880 min low-turnover variant), earns positive coinone_conservative net returns that pass G0-G5 on Dev and the robustness suite (incl. regime share <= 70%). |
| drv-000017 | fact-000004 | `liq_meanrev` | – | On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled) at 5m decisions, sharp downside dislocations - close >= 2.5 sd below the 96-bar mean (bollinger_z_revert(96,2.5)) or an equal-weight reversion composite score >= 2.0 (composite_z reversion: -r_6, -boll_48, +log1p vol) - are followed by 30-120 min returns above a per-asset matched (KST hour x vol) null, net of per-asset coinone_conservative cost (D17). |
| drv-000018 | fact-000011 | `liq_meanrev` | – | On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled) at 15m decisions, sharp downside dislocations - close >= 2.5 sd below the 24-bar (6 h) mean (bollinger_z_revert(24,2.5)) or an equal-weight reversion composite score >= 1.5 / >= 2.0 (composite_z reversion) - are followed by 1-4 h returns above a per-asset matched (KST hour x vol) null, net of per-asset coinone_conservative cost (D17). |
| drv-000019 | fact-000009 | `vol_breakout` | – | On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled) at 1h decisions, upside momentum entries - atr_channel_breakout(24,3), roc_threshold(12,100) and the equal-weight composite scores composite_z momentum >= 1.0 / >= 1.5 and volume_momentum >= 1.5 - are followed by 4-24 h returns above a per-asset matched (KST hour x vol) null, net of per-asset coinone_conservative cost (D17). |
| drv-000020 | fact-000022 | `liq_meanrev` | – | Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 5m: after bollinger_z_revert(96,2.5) a maker scale-in ladder (first tranche market, 3-4 equal tranches with limit rungs every 0.5/1.0 ATR below, maker take-profit 2.5 ATR per tranche or on average cost, aggregate stop 1.5 ATR below the last rung, max hold 120 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev, beat the ladder-matched random-entry null and pass the binding tail check. |
| drv-000021 | fact-000023 | `liq_meanrev` | – | Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 15m: after the equal-weight reversion composite composite_z(reversion) >= 2.0 a maker scale-in ladder (first tranche market, 3-4 equal tranches with limit rungs every 0.5/1.0 ATR below, maker take-profit 1.25 ATR per tranche or on average cost, aggregate stop 1.5 ATR below the last rung, max hold 60 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev, beat the ladder-matched random-entry null and pass the binding tail check. |
| drv-000022 | fact-000024 | `vol_breakout` | – | Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 1h: atr_channel_breakout(24,3) with half-size entries (entry_fraction 0.5) and ATR exits (SL 2 ATR x TP 1.5/3 ATR, max hold 480/960 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev and the robustness suite. |
| drv-000023 | fact-000024 | `vol_breakout` | – | Coinone 3-asset universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, equal sleeves), 1h: roc_threshold(12,100) with half-size entries (entry_fraction 0.5) and ATR exits (SL 2 ATR x TP 1/2 ATR, max hold 480/960 min) earns positive per-asset coinone_conservative net MTM returns that pass G0-G5 on Dev and the robustness suite. |

## 과정 사후분석 (버그·데이터·절차)

### pm-000001 · [procedure] Phase 0 분석이 분할 확정 전에 잠금 구간 기술통계를 봄 (2026-11-09, mitigated)

- **무슨 일**: Phase 0 비용 바닥 분석이 업비트·빗썸·바이낸스 2025-11-01~2026-11-08 1분봉을 사용했다. 이 기간은 지금의 Val 전체와 잠금 구간 전체에 해당한다. 전략 수익률은 보지 않았지만 시간대별 변동폭, 변동성 국면, 바이낸스 선행 상관 같은 기술통계를 보았고, session_kst·vol_regime·binance_* 블록 가설이 이 관찰에서 나왔다.
- **근본 원인**: 분할·잠금 정책과 DataAccess 계층이 생기기 전에 분석을 먼저 했다. 분석 CLI 가 데이터 저장소를 직접 읽어 분할을 강제하지 않았다.
- **영향**: 이 블록 계열을 쓰는 후보의 잠금 평가가 약간 낙관 편향될 수 있다. Val 도 같은 관찰에 노출됨.
- **발견 경위**: 자체 점검 후 PLAN §11 에 공개 (2026-11-09)
- **재발 방지**:
  - 모든 분석 CLI 를 DataAccess 경유로 변경(기본 Dev~Val, 잠금은 LockboxKey 필요) — 커밋 f8c80ae
  - 해당 블록 계열 후보 리포트에 노출 사실을 표기, 최종 판정은 노출 없는 Forward(페이퍼)
  - research/knowledge/ 의 모든 항목에 '잠금/Val 노출 여부'를 표시
  - 새 탐색 분석은 Dev 구간에서만 (signal_study 도 Dev 전용)
- 참고: docs/PLAN.md §11, research/reports/SUMMARY_20261109_cost_floor.md

### pm-000002 · [bug] G2 귀무의 거래 수 편향 (신호 비율 보정) (2026-11-09, mitigated)

- **무슨 일**: G2 귀무(random_entry, 같은 필터·청산)의 진입 확률을 후보의 '신호 비율'로 맞추자, 연속 True 인 상태형·군집형 진입은 쿨다운 때문에 실제 거래가 적고 귀무는 훨씬 자주 거래해 비용을 더 냈다. 그 결과 무작위 보행 합성 시장에서도 후보가 '덜 거래한다'는 이유만으로 귀무를 이겼다(p 매우 작음).
- **근본 원인**: 귀무가 후보와 같은 '기회 집합·빈도'를 가정했지만 거래 수·노출이 달랐다. 비용이 큰 시장에서 거래 수 차이가 곧 성과 차이가 된다.
- **영향**: 보정 전 G2 는 위양성을 냈다(합성 무작위 보행에서 확인). 실데이터 캠페인 판정 이전에 발견.
- **발견 경위**: 합성 무작위 보행 시장 파이프라인 테스트
- **재발 방지**:
  - 기본 보정을 '거래 수 맞춤'으로 변경, 두 p 와 귀무 거래 수 분포를 모두 보고 (research/nulls.py)
  - 관문 전체를 합성 시장 다수로 검정력·오탐률 측정 (PLAN §10b-8)
  - 귀무 비교 시 거래 수·노출 시간이 맞는지 리포트에서 확인 (skeptic 체크리스트)
- 참고: src/btc_quant/research/nulls.py, tests/test_research_pipeline.py

### pm-000003 · [bug] .gitignore 의 data/ 패턴이 src/btc_quant/data 를 첫 커밋에서 제외 (2026-11-09, mitigated)

- **무슨 일**: 데이터 디렉터리를 무시하려던 비고정 패턴 `data/` 가 모든 깊이의 data 디렉터리와 일치해 패키지 코드 src/btc_quant/data 가 첫 커밋에서 빠졌다.
- **근본 원인**: 루트에 고정되지 않은 .gitignore 패턴; 커밋 후 깨끗한 체크아웃에서 import 검사가 없었다.
- **영향**: 깨끗한 클론에서 데이터 계층 import 실패 가능 — 재현성 위협. 수정 전 커밋에 한함.
- **발견 경위**: 커밋 내용 점검
- **재발 방지**:
  - 루트 고정 패턴 `/data/*` + `!/data/.gitkeep` 사용
  - 커밋 후 `git ls-files src | wc -l` 또는 깨끗한 클론에서 테스트 실행으로 누락 확인
- 참고: .gitignore

### pm-000004 · [design] DSR 검정력 문제: 청산 격자 ×80 이 통과를 거의 불가능하게 만듦 (2026-11-09, mitigated)

- **무슨 일**: 캠페인마다 청산 3요소 격자(최대 약 80 조합)를 곱해 시도 수가 폭증했다. 개발 구간 4.8년 기준 DSR 통과에 필요한 연 Sharpe 는 실질 시도 10회 1.5~2.3, 200회 2.1~3.5, 1,000회 2.4~4.0 — 탐색할수록 아무것도 통과하지 못하는 구조였다.
- **근본 원인**: 청산을 데이터로 고르는 차원으로 취급했고, 캠페인 등록 전에 검정력(통과 가능한 최소 Sharpe)을 계산하지 않았다.
- **영향**: v3 캠페인 설계 전반. v4 연구 체계 개편의 직접 원인.
- **발견 경위**: 2026-11-09 심층 검토 (PLAN §10b)
- **재발 방지**:
  - 신호 선별 단계(이벤트 스터디)를 전략 격자 전에 (btc_quant.research.signal_study)
  - 원칙적 청산: ATR 배수·신호 지속 시간 기반, 청산 변형 ≤ 6
  - 캠페인 등록 전 검정력 계산기 (btc_quant.research.power) 로 필요 Sharpe 확인
  - 계열별 주간 시도 예산 (LessonBook.trial_budget)
- 참고: docs/PLAN.md §10b

### pm-000005 · [data] Coinone 1m stores synthesised no-trade bars without a filled flag (2026-11-09, mitigated)

- **무슨 일**: Coinone 1m store is 100% complete: minutes with no trades (incl. maintenance windows up to 7.8h) are flat bars with volume=0. regularize() marked them filled=False, so engine and signal-study entries could fill at stale synthetic prices. 7.6% of Dev bars, 32 maintenance windows; the earlier real-data 1m smoke screen may be affected.
- **근본 원인**: The source/collector regularises Coinone bars before writing; quality pipeline detected gaps only from missing rows.
- **영향**: Possible spurious entries at stale prices in Coinone 1m research before the fix; no official ledger results affected (ledger empty).
- **발견 경위**: data-steward audit research/reports/data_audit_20261109.md
- **재발 방지**:
  - regularize(zero_volume_as_filled=True) by default: zero-volume flat bars are filled (fixed 2026-11-09, test added)
  - data-steward flags any series at 100.000% coverage with zero-volume flat bars
  - re-run any Coinone 1m screen made before the fix
  - follow-ups: Coinone-specific wick cleaning (k≈0.01 with Upbit confirmation); no Coinone 1m volume/range/ATR features; exclude KST 02-07 and 2023-07..10 for Coinone execution studies
- 참고: research/reports/data_audit_20261109.md

### pm-000006 · [procedure] Derivation rationale overstated the ordering of grid choice and side calculation (2026-11-10, mitigated)

- **무슨 일**: drv-000011 / hyp-000017 rationale says the camp-000017 grid was fixed before the per-regime side calculation camp-000016. The filters (both passing screen configs) were fixed by the cycle plan before it, but the exit set (copied from camp-000013: SL 2/3 x TP 1.5/3 ATR, hold 480) was written into the template after the side calculation had run.
- **근본 원인**: The exit choice was decided in reasoning before the side calculation but only written down afterwards; the rationale sentence described intent, not the recorded order. The side calculation concerns entry/filter information by regime and does not bear on exits, and no grid element was changed after seeing it, but the ledger does not prove that.
- **영향**: Low: the exit set is identical to the prior pre-registered campaign and both screen-passing filter configs were included; the 9 + 1 exploration trials are logged and counted in N_family.
- **발견 경위**: researcher self-review while writing cycle3a report
- **재발 방지**:
  - Write the full strategy template (incl. exits) to disk before running any side calculation whose result could inform it
  - State order of events in rationales only as recorded (file timestamps / ledger ids)
- 참고: drv-000011, hyp-000017, camp-000016, camp-000017

### pm-000007 · [design] Power check understates N_dsr for small single-cluster grids (4-spec campaigns) (2026-11-10, mitigated)

- **무슨 일**: For camp-000030/031 (4 specs each, 1 correlation cluster) power_check estimated N_dsr 11.5 (required Sharpe ~1.28) using the history compression 0.125, but the pipeline used N_dsr = N_family*K/N = 92*1/4 = 23 (and 24), raising the DSR bar. Smaller grids get a larger K/N and so a larger N_dsr, which is the opposite of what the power tool reports.
- **근본 원인**: power.estimate_effective_trials applies a fixed historical compression ratio; G5 uses the realised K/N of the campaign, which is 1/n_specs when every spec falls into one cluster.
- **영향**: Low: neither campaign was near the DSR bar (DSR 0.28 / 0.002); the reported required Sharpe was optimistic by about 0.1-0.15.
- **발견 경위**: researcher while reading the G5 inputs of camp-000030
- **재발 방지**:
  - Researchers: for grids with < 8 specs assume K/N = 1/n_specs when computing the required Sharpe by hand
  - Human: consider making power.estimate_effective_trials use max(history ratio, 1/n_grid) (code change, human-only)
- 참고: camp-000030, camp-000031, src/btc_quant/research/power.py


## 무결성

- OK (내용 해시·id 순서 일치)
