# 신호 선별 camp-000012 — c2b_liq_meanrev_1h_screen

- 가설 hyp-000012: On Coinone BTC-KRW at 1h decisions, closes far below the rolling mean (bollinger_z_revert window 24/96 h, z 2.5) or RSI(14) crossing back above 25 are followed by 4-24 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost, with and without a 1-week bull-regime filter (trend_regime_1h 168), and the excess is not concentrated in one Dev year.
- 데이터: coinone BTC-KRW 1m (Dev), 결정 봉 60분
- 지평 [60, 120, 240, 480, 1440], 사전등록 주 지평 [240, 480, 1440], 최소 간격 120분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 5 + 같은 계열 이전 16 = 21
- **통과 0 / 5**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bollinger_z_revert(window=24,z=2.5) | 691 | 0.7871 | 240 | 3.72 | 0.64 | 0.22 | -13.77 | 120.00 | peaked | False | False | not significant after correction |
| bollinger_z_revert(window=24,z=2.5) & trend_regime_1h(span=168) | 141 | 1.0000 | 240 | -10.22 | -1.02 | -11.44 | -25.42 | - | none (excess ≤ 0 at all horizons) | False | False | not significant after correction |
| bollinger_z_revert(window=96,z=2.5) | 835 | 1.0000 | 240 | -3.57 | -0.55 | -6.00 | -19.98 | - | none (excess ≤ 0 at all horizons) | False | False | not significant after correction |
| bollinger_z_revert(window=96,z=2.5) & trend_regime_1h(span=168) | 1 | 1.0000 | 240 | - | - | - | - | - | no events | False | False | not significant after correction |
| rsi_oversold_bounce(period=14,level=25) | 284 | 1.0000 | 240 | -4.96 | -0.51 | -8.16 | -22.14 | 120.00 | peaked | False | False | not significant after correction |

## 신호별 상세

**bollinger_z_revert(window=24,z=2.5)** — 이벤트 691 (트리거 980, 적격 40990, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.7871 · 정보 지평 120분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 2.4 | 4.2 | 0.530 | -0.1 | 2.5 | 0.68 | 0.2490 | 0.1888 | 36.5 | -36.5 | -1.6 |
| 120 | 4.1 | 5.9 | 0.541 | 0.1 | 4.0 | 0.87 | 0.1931 | 0.1409 | 48.7 | -49.9 | 0.1 |
| 240 | 4.2 | 5.2 | 0.524 | 0.5 | 3.7 | 0.64 | 0.2624 | 0.2298 | 62.3 | -64.3 | 0.2 |
| 480 | -8.5 | -1.1 | 0.495 | 3.2 | -11.7 | -1.27 | 0.8980 | 0.9570 | 84.1 | -90.9 | -12.4 |
| 1440 | -17.7 | -1.1 | 0.498 | 17.2 | -34.9 | -2.04 | 0.9795 | 0.9990 | 137.0 | -168.4 | -21.7 |

제안(최적화 아님): 최대보유 120분, 익절 ≈ MFE 중앙 0.49% (0.82×ATR), 손절 ≈ |MAE| 75% 1.14% (1.71×ATR, ATR 중앙 0.595%)

**bollinger_z_revert(window=24,z=2.5) & trend_regime_1h(span=168)** — 이벤트 141 (트리거 188, 적격 21387, 결정 봉 60분, 최소 간격 120분) · 신호 p=1.0000 · 정보 지평 None분 (none (excess ≤ 0 at all horizons))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | -2.5 | 5.6 | 0.539 | 0.4 | -2.9 | -0.49 | 0.6889 | 0.7153 | 36.3 | -38.1 | -6.5 |
| 120 | -3.3 | 12.8 | 0.624 | 1.5 | -4.8 | -0.61 | 0.7305 | 0.7413 | 49.6 | -46.0 | -7.3 |
| 240 | -7.4 | 7.2 | 0.539 | 2.8 | -10.2 | -1.02 | 0.8462 | 0.8511 | 57.8 | -58.3 | -11.4 |
| 480 | -16.3 | 7.1 | 0.532 | 7.5 | -23.8 | -1.71 | 0.9566 | 0.9620 | 79.4 | -79.4 | -20.3 |
| 1440 | -31.3 | 6.3 | 0.518 | 22.8 | -54.1 | -1.54 | 0.9379 | 0.9860 | 127.7 | -145.4 | -35.3 |

**bollinger_z_revert(window=96,z=2.5)** — 이벤트 835 (트리거 1417, 적격 40990, 결정 봉 60분, 최소 간격 120분) · 신호 p=1.0000 · 정보 지평 None분 (none (excess ≤ 0 at all horizons))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | -1.8 | 3.9 | 0.529 | 0.2 | -2.0 | -0.54 | 0.7055 | 0.7962 | 35.2 | -35.0 | -5.8 |
| 120 | -1.7 | 5.6 | 0.533 | 0.8 | -2.5 | -0.51 | 0.6935 | 0.7842 | 48.8 | -52.7 | -5.7 |
| 240 | -2.0 | 5.7 | 0.525 | 1.6 | -3.6 | -0.55 | 0.7077 | 0.7772 | 68.5 | -70.9 | -6.0 |
| 480 | -15.8 | -4.0 | 0.484 | 4.1 | -19.9 | -1.91 | 0.9722 | 1.0000 | 87.2 | -107.8 | -19.8 |
| 1440 | -1.1 | 6.3 | 0.509 | 16.1 | -17.2 | -0.67 | 0.7482 | 0.9351 | 149.2 | -175.2 | -5.1 |

**bollinger_z_revert(window=96,z=2.5) & trend_regime_1h(span=168)** — 이벤트 1 (트리거 1, 적격 21387, 결정 봉 60분, 최소 간격 120분) · 신호 p=1.0000 · 정보 지평 None분 (no events)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|

**rsi_oversold_bounce(period=14,level=25)** — 이벤트 284 (트리거 284, 적격 40990, 결정 봉 60분, 최소 간격 120분) · 신호 p=1.0000 · 정보 지평 120분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | -0.9 | -3.6 | 0.461 | 0.3 | -1.1 | -0.22 | 0.5880 | 0.6124 | 29.4 | -34.7 | -4.9 |
| 120 | 0.9 | 0.4 | 0.504 | 0.3 | 0.6 | 0.08 | 0.4686 | 0.4496 | 40.5 | -50.1 | -3.1 |
| 240 | -4.2 | -1.9 | 0.486 | 0.8 | -5.0 | -0.51 | 0.6958 | 0.7453 | 61.4 | -80.8 | -8.2 |
| 480 | -17.6 | -8.8 | 0.451 | 3.3 | -21.0 | -1.62 | 0.9478 | 0.9810 | 83.0 | -117.5 | -21.6 |
| 1440 | 1.4 | 7.8 | 0.514 | 15.8 | -14.5 | -0.59 | 0.7239 | 0.7822 | 159.2 | -185.5 | -2.6 |

제안(최적화 아님): 최대보유 120분, 익절 ≈ MFE 중앙 0.41% (0.46×ATR), 손절 ≈ |MAE| 75% 1.04% (1.19×ATR, ATR 중앙 0.814%)


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.