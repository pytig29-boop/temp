# 신호 선별 camp-000021 — c3c_vol_breakout_kst_morning_1h_screen

- 가설 hyp-000020: On Coinone BTC-KRW at 1h decisions at the KST morning (decision hour 09/10 KST, session_kst asia_open), an overnight rise of >= 100 bp over the last 12 h / 24 h is followed by 4-24 h returns above a matched null drawn from the same morning hours, net of coinone_conservative cost, and this excess is larger than for the same entry at the US open (placebo, us_open) and than the unfiltered roc(12,100) config of screen c3c_vol_breakout_roc_volz_1h.
- 데이터: coinone BTC-KRW 1m (Dev), 결정 봉 60분
- 지평 [60, 120, 240, 480, 1440], 사전등록 주 지평 [240, 480, 1440], 최소 간격 120분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 3 + 같은 계열 이전 33 = 36
- **통과 0 / 3**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| roc_threshold(lookback=12,thr_bp=100) & session_kst(session=asia_open) | 493 | 0.1113 | 240 | 10.48 | 1.79 | -7.78 | -21.76 | 240 | peaked | False | False | not significant after correction |
| roc_threshold(lookback=12,thr_bp=100) & session_kst(session=us_open) | 742 | 0.9705 | 1440 | 6.77 | 0.46 | 15.83 | 1.82 | 1440 | rising (z still increasing at the longest horizon — information may persist longer) | False | False | not significant after correction |
| roc_threshold(lookback=24,thr_bp=100) & session_kst(session=asia_open) | 579 | 0.1371 | 1440 | 19.06 | 1.69 | 26.24 | 12.22 | 1440 | rising (z still increasing at the longest horizon — information may persist longer) | False | False | not significant after correction |

## 신호별 상세

**roc_threshold(lookback=12,thr_bp=100) & session_kst(session=asia_open)** — 이벤트 493 (트리거 830, 적격 3473, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.1113 · 정보 지평 240분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 1.8 | -6.1 | 0.452 | -1.1 | 2.9 | 0.83 | 0.2047 | 0.1728 | 36.5 | -39.8 | -2.2 |
| 120 | 1.9 | -7.1 | 0.465 | -4.2 | 6.1 | 1.26 | 0.1031 | 0.0669 | 48.8 | -52.9 | -2.1 |
| 240 | -3.8 | -12.1 | 0.440 | -14.3 | 10.5 | 1.79 | 0.0371 | 0.0240 | 60.6 | -68.4 | -7.8 |
| 480 | -3.6 | -10.2 | 0.460 | -13.4 | 9.8 | 1.39 | 0.0829 | 0.0629 | 77.8 | -82.7 | -7.6 |
| 1440 | 32.7 | 16.8 | 0.525 | 11.8 | 20.9 | 1.53 | 0.0628 | 0.0310 | 155.6 | -146.7 | 28.7 |

제안(최적화 아님): 최대보유 240분, 익절 ≈ MFE 중앙 0.61% (0.69×ATR), 손절 ≈ |MAE| 75% 1.25% (1.38×ATR, ATR 중앙 0.801%)

**roc_threshold(lookback=12,thr_bp=100) & session_kst(session=us_open)** — 이벤트 742 (트리거 1047, 적격 5189, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.9705 · 정보 지평 1440분 (rising (z still increasing at the longest horizon — information may persist longer))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | -0.6 | -1.3 | 0.491 | 1.3 | -1.9 | -0.65 | 0.7416 | 0.7722 | 35.4 | -37.2 | -4.6 |
| 120 | -4.1 | -6.7 | 0.464 | -0.0 | -4.1 | -1.06 | 0.8548 | 0.8821 | 54.7 | -52.6 | -8.1 |
| 240 | 0.1 | -6.0 | 0.481 | 1.2 | -1.1 | -0.19 | 0.5753 | 0.5874 | 70.0 | -74.7 | -3.9 |
| 480 | 9.0 | -5.0 | 0.484 | 13.5 | -4.5 | -0.51 | 0.6958 | 0.7692 | 99.7 | -96.3 | 5.0 |
| 1440 | 19.8 | 4.0 | 0.504 | 13.1 | 6.8 | 0.46 | 0.3235 | 0.2517 | 167.4 | -146.0 | 15.8 |

제안(최적화 아님): 최대보유 1440분, 익절 ≈ MFE 중앙 1.67% (2.13×ATR), 손절 ≈ |MAE| 75% 2.98% (3.70×ATR, ATR 중앙 0.735%)

**roc_threshold(lookback=24,thr_bp=100) & session_kst(session=asia_open)** — 이벤트 579 (트리거 990, 적격 3473, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.1371 · 정보 지평 1440분 (rising (z still increasing at the longest horizon — information may persist longer))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 1.5 | -3.0 | 0.473 | -0.9 | 2.4 | 0.80 | 0.2111 | 0.1788 | 33.3 | -35.2 | -2.5 |
| 120 | 0.6 | -6.2 | 0.458 | -3.8 | 4.3 | 1.06 | 0.1446 | 0.0999 | 44.4 | -50.2 | -3.4 |
| 240 | -3.9 | -7.8 | 0.459 | -12.7 | 8.7 | 1.66 | 0.0488 | 0.0150 | 59.9 | -60.1 | -7.9 |
| 480 | -3.3 | -8.7 | 0.458 | -12.0 | 8.8 | 1.42 | 0.0778 | 0.0460 | 75.6 | -76.1 | -7.3 |
| 1440 | 30.3 | 0.2 | 0.501 | 11.2 | 19.1 | 1.69 | 0.0457 | 0.0290 | 145.7 | -138.4 | 26.2 |

제안(최적화 아님): 최대보유 1440분, 익절 ≈ MFE 중앙 1.46% (1.82×ATR), 손절 ≈ |MAE| 75% 2.50% (3.00×ATR, ATR 중앙 0.740%)


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.