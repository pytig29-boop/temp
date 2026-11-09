# 신호 선별 camp-000001 — c1_xvenue_lag_1m_screen

- 가설 hyp-000001: On Coinone BTC-KRW at 1m decisions, after Upbit moves up and Coinone has not followed (upbit_lead_impulse) or Coinone trades at an unusually large discount to Upbit vs its own causal rolling basis (venue_gap_revert), Coinone forward returns over 5-30 min exceed a matched (KST hour x volatility) null, net of coinone_conservative cost.
- 데이터: coinone BTC-KRW 1m (Dev), 결정 봉 1분
- 지평 [1, 5, 15, 30, 60, 120], 사전등록 주 지평 [5, 15, 30], 최소 간격 30분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 8 + 같은 계열 이전 0 = 8
- **통과 0 / 8**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| upbit_lead_impulse(k=1,thr_bp=10) & session_kst(session=all_but_quiet) | 24941 | 0.0000 | 5 | 1.37 | 9.25 | -2.74 | -16.73 | 1 | decaying (z maximal at the shortest horizon) | True | False | net mean ≤ 0 under coinone_conservative |
| upbit_lead_impulse(k=1,thr_bp=20) & session_kst(session=all_but_quiet) | 5618 | 0.0001 | 5 | 2.08 | 4.03 | -2.02 | -16.01 | 1 | decaying (z maximal at the shortest horizon) | True | False | net mean ≤ 0 under coinone_conservative |
| upbit_lead_impulse(k=3,thr_bp=10) & session_kst(session=all_but_quiet) | 32450 | 0.0000 | 5 | 1.26 | 10.63 | -2.86 | -16.84 | 1 | decaying (z maximal at the shortest horizon) | True | False | net mean ≤ 0 under coinone_conservative |
| upbit_lead_impulse(k=3,thr_bp=20) & session_kst(session=all_but_quiet) | 10152 | 0.0000 | 5 | 2.70 | 8.66 | -1.41 | -15.40 | 1 | decaying (z maximal at the shortest horizon) | True | False | net mean ≤ 0 under coinone_conservative |
| venue_gap_revert(window=60,z=2.0) & session_kst(session=all_but_quiet) | 23097 | 0.0000 | 5 | 1.39 | 10.68 | -2.74 | -16.72 | 1 | decaying (z maximal at the shortest horizon) | True | False | net mean ≤ 0 under coinone_conservative |
| venue_gap_revert(window=60,z=3.0) & session_kst(session=all_but_quiet) | 5656 | 0.0000 | 5 | 2.00 | 5.64 | -2.12 | -16.11 | 1 | decaying (z maximal at the shortest horizon) | True | False | net mean ≤ 0 under coinone_conservative |
| venue_gap_revert(window=240,z=2.0) & session_kst(session=all_but_quiet) | 17624 | 0.0000 | 5 | 1.33 | 8.04 | -2.80 | -16.79 | 1 | decaying (z maximal at the shortest horizon) | True | False | net mean ≤ 0 under coinone_conservative |
| venue_gap_revert(window=240,z=3.0) & session_kst(session=all_but_quiet) | 3499 | 0.0000 | 5 | 2.87 | 5.18 | -1.25 | -15.24 | 1 | decaying (z maximal at the shortest horizon) | True | False | net mean ≤ 0 under coinone_conservative |

## 신호별 상세

**upbit_lead_impulse(k=1,thr_bp=10) & session_kst(session=all_but_quiet)** — 이벤트 24941 (트리거 60510, 적격 1708684, 결정 봉 1분, 최소 간격 30분) · 신호 p=0.0000 · 정보 지평 1분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.0 | 0.3 | 0.516 | -0.1 | 1.2 | 17.45 | 0.0000 | 0.0010 | 3.2 | -2.2 | -3.0 |
| 5 | 1.3 | 0.7 | 0.519 | -0.1 | 1.4 | 9.25 | 0.0000 | 0.0010 | 8.3 | -6.8 | -2.7 |
| 15 | 1.5 | 0.8 | 0.513 | -0.1 | 1.6 | 6.18 | 0.0000 | 0.0010 | 15.1 | -13.0 | -2.5 |
| 30 | 1.4 | 0.5 | 0.506 | -0.2 | 1.6 | 4.45 | 0.0000 | 0.0010 | 21.7 | -19.6 | -2.6 |
| 60 | 1.5 | 0.3 | 0.501 | -0.2 | 1.6 | 3.16 | 0.0008 | 0.0010 | 30.6 | -28.8 | -2.5 |
| 120 | 1.6 | 0.4 | 0.502 | -0.1 | 1.7 | 1.87 | 0.0307 | 0.0040 | 43.3 | -42.5 | -2.4 |

제안(최적화 아님): 최대보유 5분, 익절 ≈ MFE 중앙 0.03% (0.40×ATR), 손절 ≈ |MAE| 75% 0.06% (0.69×ATR, ATR 중앙 0.079%)

**upbit_lead_impulse(k=1,thr_bp=20) & session_kst(session=all_but_quiet)** — 이벤트 5618 (트리거 9871, 적격 1708684, 결정 봉 1분, 최소 간격 30분) · 신호 p=0.0001 · 정보 지평 1분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2.1 | 1.4 | 0.546 | -0.1 | 2.2 | 9.68 | 0.0000 | 0.0010 | 6.9 | -4.8 | -1.9 |
| 5 | 2.0 | 1.1 | 0.520 | -0.1 | 2.1 | 4.03 | 0.0000 | 0.0010 | 15.6 | -12.6 | -2.0 |
| 15 | 2.2 | 1.5 | 0.515 | -0.1 | 2.3 | 2.76 | 0.0029 | 0.0010 | 26.4 | -22.6 | -1.8 |
| 30 | 2.7 | 1.0 | 0.507 | -0.2 | 2.9 | 2.59 | 0.0048 | 0.0010 | 36.8 | -33.5 | -1.3 |
| 60 | 2.1 | 0.2 | 0.501 | -0.2 | 2.3 | 1.46 | 0.0716 | 0.0270 | 50.4 | -48.3 | -1.9 |
| 120 | -0.0 | 0.0 | 0.500 | -0.3 | 0.3 | 0.11 | 0.4556 | 0.4386 | 68.9 | -68.4 | -4.0 |

제안(최적화 아님): 최대보유 5분, 익절 ≈ MFE 중앙 0.07% (0.48×ATR), 손절 ≈ |MAE| 75% 0.12% (0.74×ATR, ATR 중앙 0.143%)

**upbit_lead_impulse(k=3,thr_bp=10) & session_kst(session=all_but_quiet)** — 이벤트 32450 (트리거 105637, 적격 1708684, 결정 봉 1분, 최소 간격 30분) · 신호 p=0.0000 · 정보 지평 1분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.7 | 0.1 | 0.504 | -0.1 | 0.8 | 15.46 | 0.0000 | 0.0010 | 2.5 | -1.9 | -3.3 |
| 5 | 1.1 | 0.7 | 0.525 | -0.1 | 1.3 | 10.63 | 0.0000 | 0.0010 | 7.0 | -5.8 | -2.9 |
| 15 | 1.2 | 0.8 | 0.515 | -0.1 | 1.3 | 6.44 | 0.0000 | 0.0010 | 13.0 | -11.1 | -2.8 |
| 30 | 1.4 | 0.7 | 0.507 | -0.2 | 1.6 | 5.66 | 0.0000 | 0.0010 | 18.8 | -16.8 | -2.6 |
| 60 | 1.4 | 0.4 | 0.503 | -0.2 | 1.5 | 3.62 | 0.0001 | 0.0010 | 27.2 | -25.5 | -2.6 |
| 120 | 1.4 | 0.0 | 0.499 | -0.1 | 1.4 | 1.98 | 0.0239 | 0.0030 | 39.0 | -38.1 | -2.6 |

제안(최적화 아님): 최대보유 5분, 익절 ≈ MFE 중앙 0.02% (0.35×ATR), 손절 ≈ |MAE| 75% 0.05% (0.67×ATR, ATR 중앙 0.069%)

**upbit_lead_impulse(k=3,thr_bp=20) & session_kst(session=all_but_quiet)** — 이벤트 10152 (트리거 21566, 적격 1708684, 결정 봉 1분, 최소 간격 30분) · 신호 p=0.0000 · 정보 지평 1분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.6 | 0.8 | 0.541 | -0.1 | 1.7 | 12.27 | 0.0000 | 0.0010 | 4.7 | -3.4 | -2.4 |
| 5 | 2.6 | 1.5 | 0.530 | -0.1 | 2.7 | 8.66 | 0.0000 | 0.0010 | 11.7 | -9.2 | -1.4 |
| 15 | 2.5 | 1.3 | 0.515 | -0.1 | 2.6 | 5.14 | 0.0000 | 0.0010 | 20.9 | -17.2 | -1.5 |
| 30 | 2.0 | 0.3 | 0.502 | -0.2 | 2.2 | 3.15 | 0.0008 | 0.0020 | 29.4 | -25.9 | -2.0 |
| 60 | 1.5 | -0.7 | 0.492 | -0.2 | 1.8 | 1.79 | 0.0366 | 0.0100 | 40.3 | -38.8 | -2.5 |
| 120 | 1.5 | 0.2 | 0.500 | -0.2 | 1.7 | 1.09 | 0.1369 | 0.0549 | 56.0 | -55.4 | -2.5 |

제안(최적화 아님): 최대보유 5분, 익절 ≈ MFE 중앙 0.05% (0.43×ATR), 손절 ≈ |MAE| 75% 0.08% (0.68×ATR, ATR 중앙 0.112%)

**venue_gap_revert(window=60,z=2.0) & session_kst(session=all_but_quiet)** — 이벤트 23097 (트리거 61789, 적격 1708684, 결정 봉 1분, 최소 간격 30분) · 신호 p=0.0000 · 정보 지평 1분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.7 | 0.0 | 0.489 | -0.1 | 0.9 | 13.85 | 0.0000 | 0.0010 | 2.1 | -1.4 | -3.3 |
| 5 | 1.3 | 0.7 | 0.530 | -0.1 | 1.4 | 10.68 | 0.0000 | 0.0010 | 6.1 | -4.6 | -2.7 |
| 15 | 1.5 | 1.1 | 0.527 | -0.1 | 1.7 | 7.54 | 0.0000 | 0.0010 | 10.8 | -8.9 | -2.5 |
| 30 | 1.3 | 0.9 | 0.513 | -0.2 | 1.5 | 4.91 | 0.0000 | 0.0010 | 15.7 | -13.7 | -2.7 |
| 60 | 1.1 | 0.7 | 0.506 | -0.2 | 1.3 | 3.08 | 0.0010 | 0.0020 | 22.7 | -20.7 | -2.9 |
| 120 | 1.5 | 0.6 | 0.504 | -0.0 | 1.5 | 2.31 | 0.0104 | 0.0040 | 32.9 | -30.8 | -2.5 |

제안(최적화 아님): 최대보유 5분, 익절 ≈ MFE 중앙 0.02% (0.37×ATR), 손절 ≈ |MAE| 75% 0.04% (0.68×ATR, ATR 중앙 0.055%)

**venue_gap_revert(window=60,z=3.0) & session_kst(session=all_but_quiet)** — 이벤트 5656 (트리거 8124, 적격 1708684, 결정 봉 1분, 최소 간격 30분) · 신호 p=0.0000 · 정보 지평 1분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.4 | 0.4 | 0.522 | -0.1 | 1.5 | 8.69 | 0.0000 | 0.0010 | 3.1 | -1.8 | -2.6 |
| 5 | 1.9 | 1.2 | 0.548 | -0.1 | 2.0 | 5.64 | 0.0000 | 0.0010 | 7.6 | -5.3 | -2.1 |
| 15 | 1.8 | 1.3 | 0.531 | -0.1 | 1.9 | 3.27 | 0.0005 | 0.0010 | 12.9 | -10.2 | -2.2 |
| 30 | 1.9 | 1.2 | 0.516 | -0.1 | 2.1 | 2.76 | 0.0029 | 0.0010 | 18.3 | -15.5 | -2.1 |
| 60 | 1.8 | 0.9 | 0.510 | -0.1 | 1.9 | 1.98 | 0.0240 | 0.0130 | 26.1 | -23.0 | -2.2 |
| 120 | 2.2 | 1.0 | 0.508 | 0.1 | 2.1 | 1.54 | 0.0618 | 0.0330 | 37.2 | -34.1 | -1.8 |

제안(최적화 아님): 최대보유 5분, 익절 ≈ MFE 중앙 0.03% (0.46×ATR), 손절 ≈ |MAE| 75% 0.05% (0.71×ATR, ATR 중앙 0.067%)

**venue_gap_revert(window=240,z=2.0) & session_kst(session=all_but_quiet)** — 이벤트 17624 (트리거 56598, 적격 1708684, 결정 봉 1분, 최소 간격 30분) · 신호 p=0.0000 · 정보 지평 1분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.8 | 0.0 | 0.491 | -0.1 | 0.9 | 11.59 | 0.0000 | 0.0010 | 2.2 | -1.4 | -3.2 |
| 5 | 1.2 | 0.7 | 0.523 | -0.1 | 1.3 | 8.04 | 0.0000 | 0.0010 | 6.2 | -4.8 | -2.8 |
| 15 | 1.3 | 1.0 | 0.523 | -0.1 | 1.5 | 5.59 | 0.0000 | 0.0010 | 10.9 | -9.3 | -2.7 |
| 30 | 1.6 | 0.8 | 0.514 | -0.2 | 1.8 | 4.83 | 0.0000 | 0.0010 | 16.1 | -14.2 | -2.4 |
| 60 | 1.8 | 0.6 | 0.505 | -0.2 | 2.0 | 4.00 | 0.0000 | 0.0010 | 22.8 | -21.2 | -2.2 |
| 120 | 2.2 | 0.6 | 0.503 | -0.2 | 2.4 | 3.00 | 0.0014 | 0.0010 | 33.3 | -31.4 | -1.8 |

제안(최적화 아님): 최대보유 5분, 익절 ≈ MFE 중앙 0.02% (0.37×ATR), 손절 ≈ |MAE| 75% 0.04% (0.68×ATR, ATR 중앙 0.057%)

**venue_gap_revert(window=240,z=3.0) & session_kst(session=all_but_quiet)** — 이벤트 3499 (트리거 7062, 적격 1708684, 결정 봉 1분, 최소 간격 30분) · 신호 p=0.0000 · 정보 지평 1분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2.2 | 0.7 | 0.539 | -0.1 | 2.3 | 7.71 | 0.0000 | 0.0010 | 3.8 | -2.2 | -1.8 |
| 5 | 2.8 | 1.4 | 0.556 | -0.1 | 2.9 | 5.18 | 0.0000 | 0.0010 | 8.8 | -6.1 | -1.2 |
| 15 | 2.2 | 1.3 | 0.528 | -0.1 | 2.3 | 2.83 | 0.0023 | 0.0010 | 15.2 | -11.9 | -1.8 |
| 30 | 2.5 | 0.7 | 0.510 | -0.2 | 2.7 | 2.40 | 0.0082 | 0.0020 | 21.2 | -18.4 | -1.5 |
| 60 | 3.7 | 0.7 | 0.507 | -0.2 | 3.9 | 2.79 | 0.0026 | 0.0010 | 29.1 | -26.5 | -0.3 |
| 120 | 3.2 | 0.9 | 0.507 | -0.2 | 3.4 | 1.76 | 0.0396 | 0.0170 | 40.0 | -39.1 | -0.8 |

제안(최적화 아님): 최대보유 5분, 익절 ≈ MFE 중앙 0.04% (0.50×ATR), 손절 ≈ |MAE| 75% 0.06% (0.70×ATR, ATR 중앙 0.079%)


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.