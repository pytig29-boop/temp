# 신호 선별 camp-000023 — c3c_liq_meanrev_capvol_1h_screen

- 가설 hyp-000022: On Coinone BTC-KRW at 1h decisions, closes >= 2.5 sd below the 24 h mean on abnormally high volume (volume_z(48) >= 1 / >= 2 vs the trailing 2 days) are followed by 4-24 h returns above a matched (KST hour x vol) null, net of coinone_conservative cost, unlike the price-only 1h reversion that was NO_SIGNAL (camp-000012).
- 데이터: coinone BTC-KRW 1m (Dev), 결정 봉 60분
- 지평 [60, 120, 240, 480, 1440], 사전등록 주 지평 [240, 480, 1440], 최소 간격 120분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 2 + 같은 계열 이전 23 = 25
- **통과 0 / 2**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bollinger_z_revert(window=24,z=2.5) & volume_z(window=48,min_z=1.0) | 541 | 1.0000 | 240 | 2.94 | 0.41 | -1.33 | -15.32 | 120 | peaked | False | False | not significant after correction |
| bollinger_z_revert(window=24,z=2.5) & volume_z(window=48,min_z=2.0) | 314 | 1.0000 | 240 | -1.78 | -0.19 | 0.03 | -13.96 | 60 | decaying (z maximal at the shortest horizon) | False | False | not significant after correction |

## 신호별 상세

**bollinger_z_revert(window=24,z=2.5) & volume_z(window=48,min_z=1.0)** — 이벤트 541 (트리거 741, 적격 7161, 결정 봉 60분, 최소 간격 120분) · 신호 p=1.0000 · 정보 지평 120분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 0.7 | 4.9 | 0.536 | -0.8 | 1.4 | 0.32 | 0.3753 | 0.3287 | 40.8 | -38.4 | -3.3 |
| 120 | 3.8 | 5.1 | 0.527 | -1.5 | 5.3 | 0.96 | 0.1695 | 0.1079 | 49.8 | -54.1 | -0.2 |
| 240 | 2.7 | 0.0 | 0.495 | -0.3 | 2.9 | 0.41 | 0.3418 | 0.3077 | 63.0 | -67.0 | -1.3 |
| 480 | -10.1 | -5.3 | 0.479 | -0.8 | -9.3 | -0.90 | 0.8162 | 0.8911 | 83.8 | -97.0 | -14.1 |
| 1440 | -31.1 | -11.2 | 0.473 | 17.1 | -48.2 | -2.50 | 0.9939 | 1.0000 | 134.0 | -181.5 | -35.1 |

제안(최적화 아님): 최대보유 120분, 익절 ≈ MFE 중앙 0.50% (0.85×ATR), 손절 ≈ |MAE| 75% 1.17% (1.77×ATR, ATR 중앙 0.611%)

**bollinger_z_revert(window=24,z=2.5) & volume_z(window=48,min_z=2.0)** — 이벤트 314 (트리거 392, 적격 1847, 결정 봉 60분, 최소 간격 120분) · 신호 p=1.0000 · 정보 지평 60분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 3.0 | 2.1 | 0.516 | 2.5 | 0.6 | 0.10 | 0.4614 | 0.4446 | 42.7 | -42.1 | -1.0 |
| 120 | 3.5 | 3.7 | 0.513 | 4.2 | -0.8 | -0.12 | 0.5467 | 0.5465 | 50.3 | -54.1 | -0.5 |
| 240 | 4.0 | 0.1 | 0.500 | 5.8 | -1.8 | -0.19 | 0.5762 | 0.5984 | 63.9 | -72.1 | 0.0 |
| 480 | -0.1 | 1.8 | 0.503 | 3.8 | -3.9 | -0.31 | 0.6205 | 0.6833 | 86.5 | -104.3 | -4.1 |
| 1440 | -24.6 | -12.1 | 0.468 | 13.6 | -38.2 | -1.62 | 0.9470 | 0.9930 | 143.3 | -188.2 | -28.6 |

제안(최적화 아님): 최대보유 60분, 익절 ≈ MFE 중앙 0.43% (0.69×ATR), 손절 ≈ |MAE| 75% 0.83% (1.22×ATR, ATR 중앙 0.606%)


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.