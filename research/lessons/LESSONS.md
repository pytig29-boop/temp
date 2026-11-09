# 오답노트 (LESSONS) — 자동 생성

> `btc_quant.research.lessons.render_markdown()` 생성물. **직접 편집 금지** (원본: `research/lessons/*.jsonl`).
> 사실(Facts)은 파이프라인 결과에서 자동 추출되어 불변이다. 해석(Interpretations)은 AI 작성이며
> 후속 캠페인으로 verified/refuted 되기 전까지 **unverified** 이다. Val/잠금은 결과 코드만.
> 생성: 2026-11-09T22:51:38+00:00 · 사실 8 · 해석 5 · 사후분석 5 · 파생 3

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
| `liq_meanrev` | 3 | 30 | passed 1, failed 2 | `PARAM_SPIKE`×1, `UNSTABLE`×1 |
| `vol_breakout` | 2 | 17 | passed 1, failed 1 | `PARAM_SPIKE`×1 |
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

## 파생 가설 (계열 상속)

| id | 부모 | family | override | 가설 |
|---|---|---|---|---|
| drv-000001 | fact-000001 | `xvenue_upbit_lead` | – | On Coinone 1m, only large Upbit impulses (upbit_lead_impulse thr_bp 40, k 1/3, non-quiet KST hours) produce a Coinone catch-up larger than the 4 bp conservative round trip within 5-30 min. |
| drv-000002 | fact-000005 | `liq_meanrev` | – | Coinone 5m bollinger_z_revert(96, 2.5) with/without trend_regime_1h(24), held 120-240 min with wide ATR exits (SL 6, TP 6 ATR) that approximate the event study's time exit, passes G0-G5 on Dev. |
| drv-000003 | fact-000006 | `vol_breakout` | – | Coinone 5m donchian_breakout(288) carries materially more net information when 5m realised vol is in the top 20% of its trailing 7-day range (vol_regime min_pct 0.8). |

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


## 무결성

- OK (내용 해시·id 순서 일치)
