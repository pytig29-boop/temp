# 신호 선별 camp-000020 — c3c_vol_breakout_roc_volz_1h_screen

- 가설 hyp-000019: On Coinone BTC-KRW at 1h decisions, a rise of >= 100 bp over the last 3 h or 12 h (roc_threshold) is followed by 4-24 h returns above a matched (KST hour x vol) null net of coinone_conservative cost, and more so when the decision bar's volume is abnormally high vs its trailing 2-day distribution (volume_z(48) >= 1 / >= 2).
- 데이터: coinone BTC-KRW 1m (Dev), 결정 봉 60분
- 지평 [60, 120, 240, 480, 1440], 사전등록 주 지평 [240, 480, 1440], 최소 간격 120분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 6 + 같은 계열 이전 27 = 33
- **통과 5 / 6**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| roc_threshold(lookback=3,thr_bp=100) | 2545 | 0.7439 | 1440 | 8.27 | 0.68 | 22.27 | 8.25 | 1440 | rising (z still increasing at the longest horizon — information may persist longer) | False | False | not significant after correction |
| roc_threshold(lookback=3,thr_bp=100) & volume_z(window=48,min_z=1.0) | 981 | 0.0575 | 1440 | 28.78 | 2.07 | 43.05 | 29.00 | 1440 | rising (z still increasing at the longest horizon — information may persist longer) | True | True | - |
| roc_threshold(lookback=3,thr_bp=100) & volume_z(window=48,min_z=2.0) | 371 | 0.0769 | 1440 | 39.52 | 1.95 | 49.97 | 35.91 | 1440 | rising (z still increasing at the longest horizon — information may persist longer) | True | True | - |
| roc_threshold(lookback=12,thr_bp=100) | 5033 | 0.0303 | 480 | 8.50 | 2.32 | 7.73 | -6.27 | 480 | peaked | True | True | - |
| roc_threshold(lookback=12,thr_bp=100) & volume_z(window=48,min_z=1.0) | 1419 | 0.0749 | 1440 | 24.33 | 1.96 | 37.34 | 23.29 | 1440 | rising (z still increasing at the longest horizon — information may persist longer) | True | True | - |
| roc_threshold(lookback=12,thr_bp=100) & volume_z(window=48,min_z=2.0) | 440 | 0.0456 | 1440 | 36.16 | 2.17 | 44.76 | 30.71 | 1440 | rising (z still increasing at the longest horizon — information may persist longer) | True | True | - |

## 신호별 상세

**roc_threshold(lookback=3,thr_bp=100)** — 이벤트 2545 (트리거 3777, 적격 40990, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.7439 · 정보 지평 1440분 (rising (z still increasing at the longest horizon — information may persist longer))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | -1.3 | -6.6 | 0.454 | 0.5 | -1.7 | -1.10 | 0.8647 | 0.8921 | 36.4 | -41.5 | -5.2 |
| 120 | -3.2 | -8.0 | 0.448 | 1.1 | -4.3 | -1.94 | 0.9740 | 0.9920 | 51.1 | -57.0 | -7.2 |
| 240 | -6.8 | -9.6 | 0.462 | 1.8 | -8.7 | -2.65 | 0.9959 | 0.9990 | 72.7 | -78.7 | -10.8 |
| 480 | 1.1 | -9.2 | 0.471 | 4.1 | -3.0 | -0.59 | 0.7221 | 0.7972 | 102.8 | -108.4 | -2.9 |
| 1440 | 26.3 | 6.1 | 0.509 | 18.0 | 8.3 | 0.68 | 0.2480 | 0.0879 | 184.4 | -171.5 | 22.3 |

제안(최적화 아님): 최대보유 1440분, 익절 ≈ MFE 중앙 1.84% (1.91×ATR), 손절 ≈ |MAE| 75% 3.20% (3.23×ATR, ATR 중앙 0.918%)

**roc_threshold(lookback=3,thr_bp=100) & volume_z(window=48,min_z=1.0)** — 이벤트 981 (트리거 1337, 적격 7161, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.0575 · 정보 지평 1440분 (rising (z still increasing at the longest horizon — information may persist longer))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | -0.3 | -6.1 | 0.454 | -0.7 | 0.4 | 0.13 | 0.4473 | 0.4456 | 39.2 | -43.7 | -4.3 |
| 120 | 0.9 | -5.2 | 0.476 | -1.3 | 2.2 | 0.63 | 0.2633 | 0.2408 | 51.4 | -54.7 | -3.1 |
| 240 | 5.0 | -1.4 | 0.493 | -0.1 | 5.1 | 1.05 | 0.1467 | 0.1129 | 71.9 | -71.4 | 1.0 |
| 480 | 14.9 | -1.5 | 0.496 | 1.7 | 13.2 | 1.90 | 0.0289 | 0.0110 | 105.1 | -96.9 | 10.9 |
| 1440 | 47.1 | 20.0 | 0.529 | 18.3 | 28.8 | 2.07 | 0.0192 | 0.0040 | 178.0 | -153.6 | 43.0 |

제안(최적화 아님): 최대보유 1440분, 익절 ≈ MFE 중앙 1.78% (2.19×ATR), 손절 ≈ |MAE| 75% 2.82% (3.22×ATR, ATR 중앙 0.760%)

**roc_threshold(lookback=3,thr_bp=100) & volume_z(window=48,min_z=2.0)** — 이벤트 371 (트리거 471, 적격 1847, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.0769 · 정보 지평 1440분 (rising (z still increasing at the longest horizon — information may persist longer))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | -1.5 | -2.1 | 0.482 | 1.8 | -3.3 | -0.73 | 0.7679 | 0.7972 | 41.2 | -43.5 | -5.5 |
| 120 | 7.5 | 0.7 | 0.501 | 2.8 | 4.6 | 0.73 | 0.2329 | 0.1878 | 52.8 | -53.7 | 3.5 |
| 240 | 9.3 | 1.9 | 0.509 | 2.4 | 6.9 | 0.89 | 0.1857 | 0.1508 | 67.5 | -67.0 | 5.3 |
| 480 | 22.6 | 8.6 | 0.531 | 5.3 | 17.3 | 1.75 | 0.0401 | 0.0240 | 103.5 | -90.9 | 18.6 |
| 1440 | 54.0 | 14.7 | 0.531 | 14.5 | 39.5 | 1.95 | 0.0256 | 0.0010 | 186.3 | -140.9 | 50.0 |

제안(최적화 아님): 최대보유 1440분, 익절 ≈ MFE 중앙 1.86% (2.35×ATR), 손절 ≈ |MAE| 75% 2.52% (3.23×ATR, ATR 중앙 0.686%)

**roc_threshold(lookback=12,thr_bp=100)** — 이벤트 5033 (트리거 8851, 적격 40990, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.0303 · 정보 지평 480분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 0.6 | -2.1 | 0.479 | 0.0 | 0.6 | 0.65 | 0.2566 | 0.2298 | 28.4 | -29.6 | -3.4 |
| 120 | 0.9 | -3.2 | 0.475 | 0.6 | 0.2 | 0.18 | 0.4278 | 0.4296 | 40.9 | -42.8 | -3.1 |
| 240 | 4.1 | -2.9 | 0.482 | 1.4 | 2.7 | 1.30 | 0.0962 | 0.0450 | 59.5 | -60.1 | 0.1 |
| 480 | 11.7 | -1.1 | 0.495 | 3.2 | 8.5 | 2.32 | 0.0101 | 0.0010 | 87.7 | -84.7 | 7.7 |
| 1440 | 30.5 | 7.2 | 0.516 | 13.6 | 16.9 | 1.75 | 0.0404 | 0.0010 | 159.5 | -141.7 | 26.5 |

제안(최적화 아님): 최대보유 480분, 익절 ≈ MFE 중앙 0.88% (1.10×ATR), 손절 ≈ |MAE| 75% 1.57% (1.88×ATR, ATR 중앙 0.765%)

**roc_threshold(lookback=12,thr_bp=100) & volume_z(window=48,min_z=1.0)** — 이벤트 1419 (트리거 1993, 적격 7161, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.0749 · 정보 지평 1440분 (rising (z still increasing at the longest horizon — information may persist longer))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | -0.2 | -5.1 | 0.459 | -0.7 | 0.5 | 0.29 | 0.3874 | 0.3826 | 31.9 | -37.0 | -4.2 |
| 120 | -0.3 | -5.2 | 0.474 | -1.0 | 0.7 | 0.25 | 0.4016 | 0.3906 | 45.4 | -48.4 | -4.3 |
| 240 | 4.5 | -2.3 | 0.489 | 0.1 | 4.4 | 1.16 | 0.1233 | 0.0829 | 62.9 | -64.6 | 0.5 |
| 480 | 12.8 | -0.5 | 0.498 | 1.8 | 11.0 | 1.88 | 0.0302 | 0.0140 | 90.5 | -86.3 | 8.8 |
| 1440 | 41.4 | 10.7 | 0.514 | 17.0 | 24.3 | 1.96 | 0.0250 | 0.0010 | 160.9 | -138.4 | 37.3 |

제안(최적화 아님): 최대보유 1440분, 익절 ≈ MFE 중앙 1.61% (2.20×ATR), 손절 ≈ |MAE| 75% 2.54% (3.33×ATR, ATR 중앙 0.706%)

**roc_threshold(lookback=12,thr_bp=100) & volume_z(window=48,min_z=2.0)** — 이벤트 440 (트리거 561, 적격 1847, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.0456 · 정보 지평 1440분 (rising (z still increasing at the longest horizon — information may persist longer))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 0.3 | -1.9 | 0.482 | 1.1 | -0.8 | -0.22 | 0.5866 | 0.6124 | 37.5 | -39.8 | -3.7 |
| 120 | 6.3 | 1.7 | 0.509 | 2.1 | 4.1 | 0.76 | 0.2231 | 0.1898 | 50.7 | -50.2 | 2.3 |
| 240 | 7.3 | 2.1 | 0.516 | 2.1 | 5.3 | 0.79 | 0.2156 | 0.1828 | 65.7 | -62.5 | 3.3 |
| 480 | 18.8 | 9.8 | 0.539 | 4.6 | 14.3 | 1.63 | 0.0519 | 0.0270 | 97.2 | -79.0 | 14.8 |
| 1440 | 48.8 | 20.9 | 0.539 | 12.6 | 36.2 | 2.17 | 0.0152 | 0.0040 | 180.5 | -129.3 | 44.8 |

제안(최적화 아님): 최대보유 1440분, 익절 ≈ MFE 중앙 1.81% (2.54×ATR), 손절 ≈ |MAE| 75% 2.39% (3.38×ATR, ATR 중앙 0.659%)


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.