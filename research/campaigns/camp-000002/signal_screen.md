# 신호 선별 camp-000002 — c1_xvenue_lag_5m_screen

- 가설 hyp-000002: On Coinone BTC-KRW at 5m decisions, after Upbit rises by thr_bp over k 5m bars while Coinone lags by at least half of it (upbit_lead_impulse), Coinone forward returns over 30-120 min exceed a matched (KST hour x volatility) null, net of coinone_conservative cost.
- 데이터: coinone BTC-KRW 1m (Dev), 결정 봉 5분
- 지평 [5, 15, 30, 60, 120, 240], 사전등록 주 지평 [30, 60, 120], 최소 간격 60분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 4 + 같은 계열 이전 8 = 12
- **통과 1 / 4**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| upbit_lead_impulse(k=1,thr_bp=20) & session_kst(session=all_but_quiet) | 3905 | 0.0038 | 30 | 3.62 | 3.02 | -0.61 | -14.60 | 15 | peaked | True | False | net mean ≤ 0 under coinone_conservative |
| upbit_lead_impulse(k=1,thr_bp=40) & session_kst(session=all_but_quiet) | 488 | 0.0124 | 30 | 15.06 | 2.64 | 10.58 | -3.42 | 5 | decaying (z maximal at the shortest horizon) | True | True | - |
| upbit_lead_impulse(k=2,thr_bp=20) & session_kst(session=all_but_quiet) | 4945 | 0.0580 | 30 | 1.88 | 2.07 | -2.33 | -16.32 | 5 | decaying (z maximal at the shortest horizon) | True | False | net mean ≤ 0 under coinone_conservative |
| upbit_lead_impulse(k=2,thr_bp=40) & session_kst(session=all_but_quiet) | 718 | 0.3989 | 30 | 3.96 | 1.11 | -0.26 | -14.25 | 15 | peaked | False | False | not significant after correction |

## 신호별 상세

**upbit_lead_impulse(k=1,thr_bp=20) & session_kst(session=all_but_quiet)** — 이벤트 3905 (트리거 5561, 적격 300411, 결정 봉 5분, 최소 간격 60분) · 신호 p=0.0038 · 정보 지평 15분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 1.6 | 0.6 | 0.511 | -0.1 | 1.7 | 3.29 | 0.0005 | 0.0010 | 10.7 | -9.5 | -2.4 |
| 15 | 3.0 | 1.4 | 0.521 | -0.1 | 3.1 | 3.50 | 0.0002 | 0.0010 | 20.3 | -17.8 | -1.0 |
| 30 | 3.4 | 0.9 | 0.507 | -0.2 | 3.6 | 3.02 | 0.0013 | 0.0010 | 29.6 | -26.1 | -0.6 |
| 60 | 3.2 | 0.3 | 0.501 | -0.4 | 3.6 | 2.32 | 0.0102 | 0.0070 | 41.7 | -39.4 | -0.8 |
| 120 | 2.1 | 2.6 | 0.512 | -0.5 | 2.6 | 1.23 | 0.1098 | 0.0649 | 57.6 | -55.2 | -1.9 |
| 240 | 1.5 | 2.1 | 0.505 | 0.6 | 0.9 | 0.27 | 0.3924 | 0.3497 | 79.2 | -79.2 | -2.5 |

제안(최적화 아님): 최대보유 15분, 익절 ≈ MFE 중앙 0.20% (0.78×ATR), 손절 ≈ |MAE| 75% 0.38% (1.30×ATR, ATR 중앙 0.266%)

**upbit_lead_impulse(k=1,thr_bp=40) & session_kst(session=all_but_quiet)** — 이벤트 488 (트리거 643, 적격 300411, 결정 봉 5분, 최소 간격 60분) · 신호 p=0.0124 · 정보 지평 5분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 7.3 | 3.8 | 0.539 | -0.2 | 7.5 | 2.65 | 0.0040 | 0.0010 | 24.1 | -16.5 | 3.3 |
| 15 | 11.2 | 7.7 | 0.561 | -0.2 | 11.4 | 2.57 | 0.0052 | 0.0010 | 39.3 | -29.7 | 7.2 |
| 30 | 14.6 | 9.0 | 0.551 | -0.5 | 15.1 | 2.64 | 0.0041 | 0.0010 | 56.4 | -45.8 | 10.6 |
| 60 | 9.4 | 4.0 | 0.504 | -0.8 | 10.2 | 1.49 | 0.0675 | 0.0060 | 75.2 | -60.2 | 5.4 |
| 120 | 14.8 | 3.5 | 0.506 | -1.5 | 16.4 | 1.84 | 0.0329 | 0.0030 | 89.9 | -94.3 | 10.8 |
| 240 | 4.6 | 1.5 | 0.508 | -0.9 | 5.4 | 0.48 | 0.3158 | 0.2418 | 116.7 | -128.1 | 0.6 |

제안(최적화 아님): 최대보유 5분, 익절 ≈ MFE 중앙 0.24% (0.52×ATR), 손절 ≈ |MAE| 75% 0.40% (0.81×ATR, ATR 중앙 0.460%)

**upbit_lead_impulse(k=2,thr_bp=20) & session_kst(session=all_but_quiet)** — 이벤트 4945 (트리거 7637, 적격 300411, 결정 봉 5분, 최소 간격 60분) · 신호 p=0.0580 · 정보 지평 5분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 1.7 | 0.6 | 0.511 | -0.1 | 1.8 | 4.45 | 0.0000 | 0.0010 | 9.5 | -8.2 | -2.3 |
| 15 | 2.9 | 0.6 | 0.510 | -0.1 | 3.0 | 4.35 | 0.0000 | 0.0010 | 17.4 | -15.6 | -1.1 |
| 30 | 1.7 | -0.1 | 0.497 | -0.2 | 1.9 | 2.07 | 0.0193 | 0.0110 | 25.2 | -23.4 | -2.3 |
| 60 | 1.7 | -0.3 | 0.495 | -0.4 | 2.1 | 1.70 | 0.0445 | 0.0350 | 36.0 | -33.8 | -2.3 |
| 120 | 2.3 | -0.6 | 0.495 | -0.5 | 2.8 | 1.61 | 0.0537 | 0.0280 | 51.5 | -50.5 | -1.7 |
| 240 | 2.1 | 0.0 | 0.500 | 0.7 | 1.3 | 0.51 | 0.3053 | 0.2448 | 71.8 | -71.5 | -1.9 |

제안(최적화 아님): 최대보유 5분, 익절 ≈ MFE 중앙 0.09% (0.41×ATR), 손절 ≈ |MAE| 75% 0.18% (0.71×ATR, ATR 중앙 0.238%)

**upbit_lead_impulse(k=2,thr_bp=40) & session_kst(session=all_but_quiet)** — 이벤트 718 (트리거 1024, 적격 300411, 결정 봉 5분, 최소 간격 60분) · 신호 p=0.3989 · 정보 지평 15분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 2.9 | 0.5 | 0.506 | -0.1 | 3.1 | 1.58 | 0.0573 | 0.0020 | 18.4 | -15.3 | -1.1 |
| 15 | 5.0 | 1.6 | 0.508 | -0.1 | 5.1 | 1.70 | 0.0450 | 0.0040 | 31.3 | -26.5 | 1.0 |
| 30 | 3.7 | -2.5 | 0.487 | -0.2 | 4.0 | 1.11 | 0.1330 | 0.0659 | 43.0 | -40.3 | -0.3 |
| 60 | 1.7 | -3.1 | 0.485 | -0.6 | 2.3 | 0.48 | 0.3141 | 0.2418 | 58.1 | -60.4 | -2.3 |
| 120 | -1.9 | -1.8 | 0.492 | -1.1 | -0.7 | -0.11 | 0.5433 | 0.5784 | 83.8 | -90.2 | -5.9 |
| 240 | -1.8 | -0.5 | 0.496 | -0.4 | -1.3 | -0.15 | 0.5599 | 0.5874 | 106.5 | -115.4 | -5.8 |

제안(최적화 아님): 최대보유 15분, 익절 ≈ MFE 중앙 0.31% (0.78×ATR), 손절 ≈ |MAE| 75% 0.58% (1.34×ATR, ATR 중앙 0.396%)


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.