# 신호 선별 camp-000022 — c3c_liq_meanrev_capvol_15m_screen

- 가설 hyp-000021: On Coinone BTC-KRW at 15m decisions, closes >= 2.5 sd below the 24-bar (6 h) mean on abnormally high volume (volume_z(96) >= 1 / >= 2 vs the trailing day) - the price+volume proxy for liquidation cascades in mechanisms.md M4 - are followed by 30-120 min returns above a matched (KST hour x vol) null, net of coinone_conservative cost, with larger excess than the unfiltered bollinger_z_revert(24, 2.5) of screen camp-000011.
- 데이터: coinone BTC-KRW 1m (Dev), 결정 봉 15분
- 지평 [15, 30, 60, 120, 240, 480], 사전등록 주 지평 [30, 60, 120], 최소 간격 60분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 2 + 같은 계열 이전 21 = 23
- **통과 0 / 2**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bollinger_z_revert(window=24,z=2.5) & volume_z(window=96,min_z=1.0) | 1474 | 0.1066 | 30 | 3.50 | 1.81 | 0.22 | -13.77 | 30 | peaked | False | False | not significant after correction |
| bollinger_z_revert(window=24,z=2.5) & volume_z(window=96,min_z=2.0) | 889 | 0.9716 | 120 | 1.78 | 0.46 | 0.53 | -13.46 | 120 | peaked | False | False | not significant after correction |

## 신호별 상세

**bollinger_z_revert(window=24,z=2.5) & volume_z(window=96,min_z=1.0)** — 이벤트 1474 (트리거 2346, 적격 26890, 결정 봉 15분, 최소 간격 60분) · 신호 p=0.1066 · 정보 지평 30분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 15 | 1.5 | 3.8 | 0.553 | 0.4 | 1.1 | 0.65 | 0.2576 | 0.1838 | 21.6 | -19.3 | -2.5 |
| 30 | 4.2 | 5.7 | 0.558 | 0.7 | 3.5 | 1.81 | 0.0355 | 0.0150 | 30.0 | -26.5 | 0.2 |
| 60 | 5.1 | 7.1 | 0.560 | 1.0 | 4.1 | 1.74 | 0.0408 | 0.0180 | 38.4 | -35.5 | 1.1 |
| 120 | 5.8 | 8.5 | 0.559 | 1.0 | 4.8 | 1.56 | 0.0592 | 0.0380 | 49.9 | -47.9 | 1.8 |
| 240 | 6.7 | 12.0 | 0.560 | 1.7 | 5.1 | 1.40 | 0.0814 | 0.0769 | 64.1 | -62.0 | 2.7 |
| 480 | 10.4 | 15.1 | 0.564 | 3.9 | 6.5 | 1.31 | 0.0943 | 0.0739 | 88.5 | -78.7 | 6.4 |

제안(최적화 아님): 최대보유 30분, 익절 ≈ MFE 중앙 0.30% (1.00×ATR), 손절 ≈ |MAE| 75% 0.60% (1.77×ATR, ATR 중앙 0.302%)

**bollinger_z_revert(window=24,z=2.5) & volume_z(window=96,min_z=2.0)** — 이벤트 889 (트리거 1324, 적격 7776, 결정 봉 15분, 최소 간격 60분) · 신호 p=0.9716 · 정보 지평 120분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 15 | -2.9 | 1.6 | 0.517 | 1.3 | -4.2 | -1.85 | 0.9676 | 0.9950 | 22.6 | -23.5 | -6.9 |
| 30 | 1.5 | 2.9 | 0.530 | 1.9 | -0.4 | -0.16 | 0.5652 | 0.5664 | 30.0 | -31.4 | -2.5 |
| 60 | 1.1 | 3.7 | 0.529 | 2.6 | -1.5 | -0.48 | 0.6844 | 0.7323 | 37.2 | -40.4 | -2.9 |
| 120 | 4.5 | 7.8 | 0.542 | 2.8 | 1.8 | 0.46 | 0.3239 | 0.2887 | 48.6 | -55.9 | 0.5 |
| 240 | -1.1 | 6.0 | 0.535 | 3.0 | -4.1 | -0.87 | 0.8084 | 0.8422 | 62.6 | -71.2 | -5.1 |
| 480 | 1.1 | 9.7 | 0.539 | 5.8 | -4.7 | -0.75 | 0.7726 | 0.8042 | 85.4 | -86.5 | -2.9 |

제안(최적화 아님): 최대보유 120분, 익절 ≈ MFE 중앙 0.49% (1.64×ATR), 손절 ≈ |MAE| 75% 1.04% (3.24×ATR, ATR 중앙 0.308%)


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.