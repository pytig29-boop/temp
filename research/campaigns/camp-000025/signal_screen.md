# 신호 선별 camp-000025 — c4_liq_meanrev_dip_5m_u3_screen

- 가설 hyp-000024: On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled) at 5m decisions, sharp downside dislocations - close >= 2.5 sd below the 96-bar mean (bollinger_z_revert(96,2.5)) or an equal-weight reversion composite score >= 2.0 (composite_z reversion: -r_6, -boll_48, +log1p vol) - are followed by 30-120 min returns above a per-asset matched (KST hour x vol) null, net of per-asset coinone_conservative cost (D17).
- 데이터: coinone 유니버스 ['BTC-KRW', 'XRP-KRW', 'ETH-KRW'] 1m (Dev), 결정 봉 5분 — 자산별 이벤트 풀링, 귀무는 자산별 매칭(D15)
- 지평 [5, 15, 30, 60, 120, 240], 사전등록 주 지평 [30, 60, 120], 최소 간격 60분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 2 + 같은 계열 이전 25 = 27
- **통과 1 / 2**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | n_events[BTC-KRW] | n_events[XRP-KRW] | n_events[ETH-KRW] | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bollinger_z_revert(window=96,z=2.5) | 8942 | 0.0040 | 120 | 6.27 | 3.00 | 0.71 | -12.03 | 120 | peaked | 2999 | 3025 | 2918 | True | True | - |
| composite_z(preset=reversion,threshold=2.0) | 7080 | 0.0113 | 60 | 5.07 | 2.67 | -1.20 | -13.97 | 60 | peaked | 2661 | 2043 | 2376 | True | False | net mean ≤ 0 under coinone_conservative |

## 신호별 상세

**bollinger_z_revert(window=96,z=2.5)** — 이벤트 8942 (트리거 38999, 적격 936816, 결정 봉 5분, 최소 간격 60분) · 신호 p=0.0040 · 정보 지평 120분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | -0.2 | 0.5 | 0.503 | -0.1 | -0.1 | -0.19 | 0.5770 | 0.6264 | 12.9 | -12.1 | -7.5 |
| 15 | -0.1 | 2.8 | 0.523 | 0.1 | -0.2 | -0.21 | 0.5822 | 0.6384 | 22.8 | -22.1 | -7.5 |
| 30 | 2.2 | 5.4 | 0.546 | 0.3 | 1.8 | 1.48 | 0.0695 | 0.0060 | 31.2 | -30.6 | -5.2 |
| 60 | 5.4 | 8.9 | 0.566 | 0.8 | 4.6 | 2.95 | 0.0016 | 0.0010 | 42.5 | -40.9 | -2.0 |
| 120 | 8.0 | 11.8 | 0.566 | 1.8 | 6.3 | 3.00 | 0.0013 | 0.0010 | 57.1 | -54.2 | 0.7 |
| 240 | 10.6 | 15.1 | 0.561 | 3.9 | 6.7 | 2.33 | 0.0100 | 0.0010 | 77.8 | -73.2 | 3.3 |

제안(최적화 아님): 최대보유 120분, 익절 ≈ MFE 중앙 0.57% (2.48×ATR), 손절 ≈ |MAE| 75% 1.22% (4.71×ATR, ATR 중앙 0.234%)

자산별 (보고 전용 — 판정은 풀링 결과):

| 자산 | 이벤트 | 적격 | 신호 p | 정보 지평 |
|---|---|---|---|---|
| BTC-KRW | 2999 | 383616 | 0.0013 | 240 |
| XRP-KRW | 3025 | 304937 | 0.0215 | 120 |
| ETH-KRW | 2918 | 248263 | 0.1131 | 60 |

**composite_z(preset=reversion,threshold=2.0)** — 이벤트 7080 (트리거 18228, 적격 936816, 결정 봉 5분, 최소 간격 60분) · 신호 p=0.0113 · 정보 지평 60분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 0.7 | 2.5 | 0.532 | -0.1 | 0.8 | 1.22 | 0.1112 | 0.0160 | 16.6 | -14.4 | -6.3 |
| 15 | 0.7 | 4.8 | 0.551 | 0.1 | 0.6 | 0.46 | 0.3216 | 0.1888 | 27.1 | -24.8 | -6.4 |
| 30 | 3.5 | 7.0 | 0.555 | 0.4 | 3.1 | 2.09 | 0.0183 | 0.0010 | 35.6 | -33.5 | -3.6 |
| 60 | 5.9 | 9.5 | 0.567 | 0.8 | 5.1 | 2.67 | 0.0038 | 0.0010 | 46.2 | -44.0 | -1.2 |
| 120 | 6.9 | 12.0 | 0.564 | 1.9 | 5.1 | 2.06 | 0.0198 | 0.0040 | 60.6 | -58.0 | -0.1 |
| 240 | 9.7 | 15.8 | 0.564 | 3.8 | 6.0 | 1.83 | 0.0336 | 0.0060 | 79.3 | -75.6 | 2.7 |

제안(최적화 아님): 최대보유 60분, 익절 ≈ MFE 중앙 0.46% (1.95×ATR), 손절 ≈ |MAE| 75% 0.98% (3.63×ATR, ATR 중앙 0.242%)

자산별 (보고 전용 — 판정은 풀링 결과):

| 자산 | 이벤트 | 적격 | 신호 p | 정보 지평 |
|---|---|---|---|---|
| BTC-KRW | 2661 | 383616 | 0.0032 | 60 |
| XRP-KRW | 2043 | 304937 | 0.0168 | 60 |
| ETH-KRW | 2376 | 248263 | 0.6945 | 5 |


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.