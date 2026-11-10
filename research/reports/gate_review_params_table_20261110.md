# 기준값 전수 표 (2026-11-10)

`config/params.yaml` 의 132개 항목. '실제 탈락 수'는 공식 캠페인의 **개발 구간** 대표 후보 14개 중 해당 조건에 걸린 수(중복 포함)이며, 조정 근거가 아니라 병목 파악용이다.

분류: ① 시장 측정값 · ② 증거 기준 · ③ 위험 선호

| 분류 | 항목 | 현재값 | 근거 유형 | 메모 | 버전·변경일 | 실제 탈락 수 |
|---|---|---|---|---|---|---|
| ① | `collector.health.max_age` | 90.0 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `collector.health.max_data_age` | 300.0 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `collector.stale_seconds` | 30.0 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `costs.coinone_base` | 왕복 2.0bp (수수료 0bp×2, 스프레드 1.0, 슬립 0.5) v2 | 실측+여유(잠정) | 20분→14시간 표본. 실측 왕복 p90≈1.9bp, 사용자 B안(2/4/8bp) | v1 2026-11-09 |  |
| ① | `costs.coinone_base@ETH-KRW` | 왕복 5.0bp (수수료 0bp×2, 스프레드 3.5, 슬립 0.75) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `costs.coinone_base@XRP-KRW` | 왕복 8.0bp (수수료 0bp×2, 스프레드 6.7, 슬립 0.65) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `costs.coinone_conservative` | 왕복 4.0bp (수수료 0bp×2, 스프레드 2.0, 슬립 1.0) v2 | 실측+여유(잠정) | 20분→14시간 표본. 실측 왕복 p90≈1.9bp, 사용자 B안(2/4/8bp) | v1 2026-11-09 |  |
| ① | `costs.coinone_conservative@ETH-KRW` | 왕복 8.0bp (수수료 0bp×2, 스프레드 3.5, 슬립 2.25) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `costs.coinone_conservative@XRP-KRW` | 왕복 10.0bp (수수료 0bp×2, 스프레드 6.7, 슬립 1.65) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `costs.coinone_stress` | 왕복 8.0bp (수수료 0bp×2, 스프레드 4.0, 슬립 2.0) v2 | 실측+여유(잠정) | 20분→14시간 표본. 실측 왕복 p90≈1.9bp, 사용자 B안(2/4/8bp) | v1 2026-11-09 |  |
| ① | `costs.coinone_stress@ETH-KRW` | 왕복 14.0bp (수수료 0bp×2, 스프레드 3.5, 슬립 5.25) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `costs.coinone_stress@XRP-KRW` | 왕복 14.0bp (수수료 0bp×2, 스프레드 6.7, 슬립 3.65) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `costs.upbit_base` | 왕복 13.4bp (수수료 5bp×2, 스프레드 1.4, 슬립 1.0) v1 | 초기 가정(실측과 근접) | 14시간 표본 왕복 12.8/17.6/28.3bp | v1 2026-11-09 |  |
| ① | `costs.upbit_base@ETH-KRW` | 왕복 15.5bp (수수료 5bp×2, 스프레드 3.5, 슬립 1.0) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `costs.upbit_base@XRP-KRW` | 왕복 18.7bp (수수료 5bp×2, 스프레드 6.7, 슬립 1.0) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `costs.upbit_conservative` | 왕복 18.0bp (수수료 5bp×2, 스프레드 2.0, 슬립 3.0) v1 | 초기 가정(실측과 근접) | 14시간 표본 왕복 12.8/17.6/28.3bp | v1 2026-11-09 |  |
| ① | `costs.upbit_conservative@ETH-KRW` | 왕복 19.5bp (수수료 5bp×2, 스프레드 3.5, 슬립 3.0) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `costs.upbit_conservative@XRP-KRW` | 왕복 22.7bp (수수료 5bp×2, 스프레드 6.7, 슬립 3.0) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `costs.upbit_stress` | 왕복 31.0bp (수수료 5bp×2, 스프레드 5.0, 슬립 8.0) v1 | 초기 가정(실측과 근접) | 14시간 표본 왕복 12.8/17.6/28.3bp | v1 2026-11-09 |  |
| ① | `costs.upbit_stress@ETH-KRW` | 왕복 36.4bp (수수료 5bp×2, 스프레드 10.4, 슬립 8.0) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `costs.upbit_stress@XRP-KRW` | 왕복 39.4bp (수수료 5bp×2, 스프레드 13.4, 슬립 8.0) v1 | 실측(15분~4시간, 잠정) | XRP 스트레스는 실측 규칙(p99×1.5)보다 낮음 → 상향 제안 | v1 2026-11-10 |  |
| ① | `exchanges.coinone.taker_fee` | 0.0 | 공시값 | 코인원 0%는 이벤트 | v1 2026-11-09 |  |
| ① | `exchanges.upbit.taker_fee` | 0.0005 | 공시값 | 코인원 0%는 이벤트 | v1 2026-11-09 |  |
| ① | `quality.check_ohlcv.extreme_logret` | 0.05 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `quality.clean_wicks.coinone.k` | 0.01 | 데이터 점검 | data_audit_20261109 | v2 2026-11-09 |  |
| ① | `quality.clean_wicks.coinone.window` | 15 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `quality.clean_wicks.k` | 0.03 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `quality.clean_wicks.window` | 15 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `quality.flag_wick_outliers.k` | 0.03 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `quality.flag_wick_outliers.window` | 15 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `quality.gap_warmup.bars` | 1 | 데이터 점검 | data_audit_20261109 | v2 2026-11-09 |  |
| ① | `quality.gap_warmup.min_gap_minutes` | 30 | 데이터 점검 | data_audit_20261109 | v1 2026-11-09 |  |
| ① | `risk.api_error_streak` | 3 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `risk.data_stall_minutes` | 2 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `risk.price_anomaly_quantile` | 0.999 | 초기 가정 | 측정 대상이나 아직 미측정 | v1 2026-11-09 |  |
| ① | `robustness.latency_delays_min` | [1] | 초기 가정 | 체결 지연 실측 대상 | v1 2026-11-09 |  |
| ① | `ticks.coinone.KRW` | 1 | 거래소 API | 코인원 KRW 시장 가격대별 호가 단위표 버전 (페이퍼 지정가·사다리 주문의 호가 단위;  | v1 2026-11-10 |  |
| ② | `calibration.min_days_final` | 7 | 방법론 상수 | 확정 보정 최소 일수 | v1 2026-11-09 |  |
| ② | `calibration.min_hours_final` | 72 | 방법론 상수 | 확정(non-provisional) 보정 최소 시간 버킷 수 | v1 2026-11-09 |  |
| ② | `calibration.min_hours_provisional` | 2 | 방법론 상수 | 보정 거부 기준: 관측 시간 버킷 수 하한 | v1 2026-11-09 |  |
| ② | `calibration.min_kst_hours_final` | 24 | 방법론 상수 | 확정 보정 최소 KST 시각 종류 수(하루 전체) | v1 2026-11-09 |  |
| ② | `calibration.min_snapshots_per_hour` | 60 | 방법론 상수 | 한 시간 버킷을 '관측됨'으로 칠 최소 스냅샷 수 | v1 2026-11-09 |  |
| ② | `calibration.min_snapshots_provisional` | 300 | 방법론 상수 | 보정 거부 기준: 스냅샷 수 하한 | v1 2026-11-09 |  |
| ② | `calibration.n_boot` | 2000 | 방법론 상수 | 분위수 부트스트랩 반복 수 | v1 2026-11-09 |  |
| ② | `calibration.notional_krw` | 300000 | 방법론 상수 | 보정 기준 주문 금액(페이퍼 DEFAULT_NOTIONAL 과 같게) | v1 2026-11-09 |  |
| ② | `calibration.provisional_auto_apply_stricter` | False | 방법론 상수 | 잠정 보정에서도 엄격 변경 자동 적용 여부 | v1 2026-11-09 |  |
| ② | `calibration.provisional_ci` | 0.95 | 방법론 상수 | 잠정 보정: 분위수 부트스트랩 상한 신뢰수준 | v1 2026-11-09 |  |
| ② | `calibration.quantiles` | {'base': 0.5, 'conservative': 0.9, 'stress': 0.99} | 방법론 상수 | 비용 보정 분위(base 중앙값·conservative p90·stress p99) — 방 | v1 2026-11-09 |  |
| ② | `calibration.stress_safety_factor` | 1.5 | 방법론 상수 | stress = p99 × 안전계수 — 관측 못 한 국면(급락·점검) 여유 | v1 2026-11-09 |  |
| ② | `calibration.tolerance_bps` | 0.05 | 방법론 상수 | 변경 없음으로 볼 왕복 비용 차이 | v1 2026-11-09 |  |
| ② | `evaluation.daily_returns` | mtm | 사용자 승인(엄격 방향) | 임계값 자체는 사전 지정 | v2 2026-11-10 |  |
| ② | `gates.G0.max_filters` | 2 | 사전 지정(관례값, 미보정) | 필터 수 상한 — 자유도 제한 | v1 2026-11-09 |  |
| ② | `gates.G0.max_params` | 8 | 사전 지정(관례값, 미보정) | 파라미터 수 상한(청산 3개 포함) — 자유도 제한 | v1 2026-11-09 |  |
| ② | `gates.G1.min_recent_trades` | 60 | 사전 지정(관례값, 미보정) | 최근 12개월 최소 거래 수 | v1 2026-11-09 |  |
| ② | `gates.G1.min_trades` | 300 | 사전 지정(관례값, 미보정) | Dev 최소 거래 수 — 표본 크기 | v1 2026-11-09 | 1/14 (G1 전체) |
| ② | `gates.G1.recent_days` | 365 | 사전 지정(관례값, 미보정) | G1 최근 구간 길이 | v1 2026-11-09 |  |
| ② | `gates.G2.min_weighted_sharpe` | 0.0 | 사전 지정(관례값, 미보정) | 가중 Sharpe 하한(초과) | v1 2026-11-09 | 3/14 |
| ② | `gates.G2.null_runs` | 1000 | 사전 지정(관례값, 미보정) | 무작위 진입 귀무 반복 수(p 해상도) | v1 2026-11-09 |  |
| ② | `gates.G2.null_top_fraction` | 0.05 | 사전 지정(관례값, 미보정) | 귀무 대비 유의수준(상위 5%) | v1 2026-11-09 | 2/14 |
| ② | `gates.G3.neighbor_median_ratio` | 0.5 | 사전 지정(관례값, 미보정) | 인접 조합 고원 비율 | v1 2026-11-09 | 6/14 |
| ② | `gates.G4.min_recent_sharpe` | 0.0 | 사전 지정(관례값, 미보정) | 최근 12개월 Sharpe 하한 | v1 2026-11-09 | 1/14 |
| ② | `gates.G4.min_rolling_positive_frac` | 0.6 | 사전 지정(관례값, 미보정) | 롤링 양수 비율 하한 | v1 2026-11-09 | 8/14 |
| ② | `gates.G4.recent_days` | 365 | 사전 지정(관례값, 미보정) | G4 최근 구간 길이 | v1 2026-11-09 |  |
| ② | `gates.G4.rolling_step_days` | 30 | 사전 지정(관례값, 미보정) | 롤링 간격 | v1 2026-11-09 |  |
| ② | `gates.G4.rolling_window_days` | 182 | 사전 지정(관례값, 미보정) | 롤링 안정성 창 | v1 2026-11-09 |  |
| ② | `gates.G5.cluster_threshold` | 0.5 | 사전 지정(관례값, 미보정) | 실질 시도 수 군집 절단 높이 | v1 2026-11-09 |  |
| ② | `gates.G5.max_pbo` | 0.3 | 사전 지정(관례값, 미보정) | 과적합 확률(PBO) 상한 | v1 2026-11-09 | 7/14 |
| ② | `gates.G5.min_dsr` | 0.95 | 사전 지정(관례값, 미보정) | Deflated Sharpe 하한 — 선택편향 보정 유의수준 | v1 2026-11-09 | 11/14 |
| ② | `gates.G5.pbo_splits` | 16 | 사전 지정(관례값, 미보정) | CSCV 분할 수 | v1 2026-11-09 |  |
| ② | `gates.G6.mc_sims` | 10000 | 사전 지정(관례값, 미보정) | MC 반복 수 | v1 2026-11-09 |  |
| ② | `gates.G6.min_sharpe` | 0.0 | 사전 지정(관례값, 미보정) | Val Sharpe 하한(conservative 비용) | v1 2026-11-09 | Val 도달 2건 모두 탈락(세부 비공개) |
| ② | `gates.G6.min_stress_sharpe` | 0.0 | 사용자 결정(b)+합성 | pipeline_calibration | v1 2026-11-09 |  |
| ② | `gates.G6.val_budget.alpha` | 0.1 | 사전 지정(관례값, 미보정) | Val 누적 Holm α | v1 2026-11-09 |  |
| ② | `gates.G6.val_budget.cap` | 20 | 사전 지정(관례값, 미보정) | Val 사용 상한 | v1 2026-11-09 |  |
| ② | `gates.G6.val_budget.holm_mode` | cumulative | 사용자 결정 | D9 | v1 2026-11-09 |  |
| ② | `gates.G6.val_budget.n_boot` | 2000 | 사전 지정(관례값, 미보정) | Val p 부트스트랩 반복 수 | v1 2026-11-09 |  |
| ② | `gates.G7.alpha` | 0.1 | 사전 지정(관례값, 미보정) | 잠금 구간 누적 Holm α(D9) | v1 2026-11-09 |  |
| ② | `gates.G7.cap` | 10 | 사전 지정(관례값, 미보정) | 잠금 구간 개봉 상한 | v1 2026-11-09 |  |
| ② | `gates.G7.holm_mode` | cumulative | 사용자 결정 | D9 | v1 2026-11-09 |  |
| ② | `gates.G8.forward_warmup_days` | 14 | 사전 지정(관례값, 미보정) | 전진 인큐베이션 워밍업 | v1 2026-11-09 |  |
| ② | `gates.G8.min_paper_trades` | 30 | 사전 지정(관례값, 미보정) | 페이퍼 최소 거래 수 | v1 2026-11-09 |  |
| ② | `gates.G8.min_paper_weeks` | 3 | 사전 지정(관례값, 미보정) | 페이퍼 최소 기간 | v1 2026-11-09 |  |
| ② | `gates.G8.pnl_band` | 0.9 | 사전 지정(관례값, 미보정) | 페이퍼 손익 허용 구간(백테스트 분포) | v1 2026-11-09 |  |
| ② | `gates.exposure_aware.G4.enabled` | True | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.exposure_aware.G4.min_active_windows` | 20 | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.exposure_aware.G4.neutral_max_exposure` | 0.1 | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.exposure_aware.G6.enabled` | True | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.exposure_aware.G6.flat_max_exposure` | 0.25 | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.exposure_aware.G6.flat_max_mdd_vs_bh` | 0.5 | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.exposure_aware.G6.flat_min_return` | -0.02 | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.exposure_aware.G6.flat_min_stress_return` | -0.04 | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.exposure_aware.G6.flat_min_trades` | 10 | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.exposure_aware.G6.flat_require_bh_down` | True | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.exposure_aware.enabled` | False | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.exposure_aware.min_dev_exposure` | 0.2 | 합성 검증(효과 없음 → OFF) | calibration_regime | v1 2026-11-10 |  |
| ② | `gates.primary_metric.cost` | conservative | 사전 지정(관례값, 미보정) | 기준 지표 비용 역할(conservative, D3) | v1 2026-11-09 |  |
| ② | `gates.primary_metric.half_life_days` | 365 | 사전 지정(관례값, 미보정) | 최근 가중 반감기(D1) | v1 2026-11-09 |  |
| ② | `gates.selection.cluster_threshold` | 0.5 | 설계 선택(합성 검증) | 후보 선택 군집 절단 높이 | v1 2026-11-09 |  |
| ② | `gates.selection.method` | cluster_best | 설계 선택(합성 검증) | 후보 선택 방식(군집 대표) | v1 2026-11-09 |  |
| ② | `robustness.alpha_min_tstat` | 2.0 | 사전 지정(관례값, 미보정) | BTC 일간 수익 대비 알파 t ≥ 2 (Newey–West) — 단순 베타(보유) 노출과 | v1 2026-11-09 |  |
| ② | `robustness.alpha_nw_lags` | 5 | 사전 지정(관례값, 미보정) | Newey–West HAC 시차(일) — 일간 수익 자기상관 보정 | v1 2026-11-09 |  |
| ② | `robustness.cost_mults` | [1.5, 2.0] | 사전 지정(관례값, 미보정) | 비용(수수료·스프레드·슬리피지) +50%/+100% 민감도 시나리오 — 비용 추정 오차 여 | v1 2026-11-09 |  |
| ② | `robustness.cost_pass_mult` | 1.5 | 사전 지정(관례값, 미보정) | 비용 ×1.5 에서 Sharpe > 0 이어야 통과 — 비용 모델이 50% 과소추정돼도 살 | v1 2026-11-09 |  |
| ② | `robustness.cross_venue_min_ratio` | 0.5 | 사전 지정(관례값, 미보정) | 다른 거래소 데이터에서 Sharpe 비 ≥ 0.5 (다른 거래소 Sharpe > 0) —  | v1 2026-11-09 |  |
| ② | `robustness.latency_min_retention` | 0.5 | 사전 지정(관례값, 미보정) | 지연(1분 실시간 체결·1봉) 시 Sharpe 유지율 하한 — 엣지가 체결 지연에 무너지지 | v1 2026-11-09 |  |
| ② | `robustness.loyo_min_days` | 60 | 사전 지정(관례값, 미보정) | 연도 하나 제외 검사에서 제외 대상으로 칠 연도의 최소 일수 — 짧은 꼬리 연도로 검사가  | v1 2026-11-09 |  |
| ② | `robustness.outlier_top_frac` | 0.01 | 사전 지정(관례값, 미보정) | 상위 1% 거래(최소 1건)를 0 으로 해도 Sharpe > 0 — 소수 대박 거래 의존( | v1 2026-11-09 |  |
| ② | `robustness.outlier_top_n` | 5 | 사전 지정(관례값, 미보정) | 상위 5건 거래를 0 으로 해도 Sharpe > 0 — 거래 수가 적을 때 1% 규칙의 보 | v1 2026-11-09 |  |
| ② | `robustness.regime_dominant_min_excess` | 0.0 | 사전 지정(관례값, 미보정) | regime_mode=dominant_alpha 전용 — 지배 국면 안 노출(보유 시간)- | v1 2026-11-10 |  |
| ② | `robustness.regime_lookback_days` | 30 | 사전 지정(관례값, 미보정) | 국면 라벨 특성 창(직전 30일 수익률·변동성, 전일 종가까지 = 인과) — 월 단위 추세 | v1 2026-11-09 |  |
| ② | `robustness.regime_max_pnl_share` | 0.7 | 사전 지정 → 합성 검증(유지) | calibration_regime_check: 가짜의 유일 방어선 | v1 2026-11-09 |  |
| ② | `robustness.regime_mode` | pnl_share | 사전 지정 → 합성 검증(유지) | calibration_regime_check: 가짜의 유일 방어선 | v1 2026-11-10 |  |
| ② | `robustness.tail_enabled` | ladder | 사용자 승인(엄격 방향) | 임계값 자체는 사전 지정 | v2 2026-11-10 |  |
| ② | `robustness.tail_max_cvar5` | 0.03 | 사용자 승인(엄격 방향) | 임계값 자체는 사전 지정 | v1 2026-11-10 |  |
| ② | `robustness.tail_max_full_inventory_days` | 30 | 사용자 승인(엄격 방향) | 임계값 자체는 사전 지정 | v1 2026-11-10 |  |
| ② | `robustness.tail_max_worst_day` | 0.1 | 사용자 승인(엄격 방향) | 임계값 자체는 사전 지정 | v1 2026-11-10 |  |
| ② | `robustness.tail_max_worst_week` | 0.15 | 사용자 승인(엄격 방향) | 임계값 자체는 사전 지정 | v1 2026-11-10 |  |
| ② | `robustness.tail_min_bear_return` | -0.2 | 사용자 승인(엄격 방향) | 임계값 자체는 사전 지정 | v1 2026-11-10 |  |
| ② | `splits.lockbox_cap` | 10 | 사전 지정(관례값, 미보정) | 잠금 구간 개봉 상한(splits.yaml) | v1 2026-11-09 |  |
| ③ | `gates.G6.max_mc_mdd95` | 0.2 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `gates.G6.max_mdd` | 0.2 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `gates.G6.max_stress_mdd` | 0.2 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `gates.risk.daily_loss_floor` | -0.03 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `gates.risk.drawdown_floor` | -0.12 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `gates.risk.drawdown_mc_mult` | 1.2 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `gates.risk.loss_percentile` | 1 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `gates.risk.min_max_trades_per_day` | 5 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `gates.risk.trades_per_day_mult` | 3 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `gates.risk.weekly_loss_floor` | -0.06 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `paper.notional_krw` | 300000.0 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `risk_prefs.capital_fraction` | None | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-09 |  |
| ③ | `risk_prefs.max_exposure_per_sleeve` | 1.0 | 위험 선호(사람) | 합성으로 정할 수 없음 | v1 2026-11-10 |  |

## 근거 유형 집계

| 분류 | 근거 유형 | 개수 |
|---|---|---|
| ① | 거래소 API | 1 |
| ① | 공시값 | 2 |
| ① | 데이터 점검 | 3 |
| ① | 실측(15분~4시간, 잠정) | 12 |
| ① | 실측+여유(잠정) | 3 |
| ① | 초기 가정 | 13 |
| ① | 초기 가정(실측과 근접) | 3 |
| ② | 방법론 상수 | 13 |
| ② | 사용자 결정 | 2 |
| ② | 사용자 결정(b)+합성 | 1 |
| ② | 사용자 승인(엄격 방향) | 7 |
| ② | 사전 지정 → 합성 검증(유지) | 2 |
| ② | 사전 지정(관례값, 미보정) | 43 |
| ② | 설계 선택(합성 검증) | 2 |
| ② | 합성 검증(효과 없음 → OFF) | 12 |
| ③ | 위험 선호(사람) | 13 |
