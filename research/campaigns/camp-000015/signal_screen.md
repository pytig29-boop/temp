# 신호 선별 camp-000015 — c3_vol_breakout_atrch_1h_hivol_screen

- 가설 hyp-000015: On Coinone BTC-KRW at 1h decisions, atr_channel_breakout(24,3) carries more information per event when 24h realised vol is in the upper half / tercile of its trailing 7-day range (vol_regime 0.5 / 0.67), and that information is present in down- and sideways-30d regimes, not only up-trends.
- 데이터: coinone BTC-KRW 1m (Dev), 결정 봉 60분
- 지평 [60, 120, 240, 480, 1440], 사전등록 주 지평 [240, 480, 1440], 최소 간격 120분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 2 + 같은 계열 이전 25 = 27
- **통과 2 / 2**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| atr_channel_breakout(span=24,mult=3.0) & vol_regime(window=24,min_pct=0.5) | 850 | 0.0003 | 480 | 31.22 | 3.72 | 33.34 | 19.30 | 480 | peaked | True | True | None |
| atr_channel_breakout(span=24,mult=3.0) & vol_regime(window=24,min_pct=0.67) | 678 | 0.0006 | 480 | 34.71 | 3.54 | 39.03 | 24.98 | 480 | peaked | True | True | None |

## 신호별 상세

**atr_channel_breakout(span=24,mult=3.0) & vol_regime(window=24,min_pct=0.5)** — 이벤트 850 (트리거 1426, 적격 19858, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.0003 · 정보 지평 480분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 4.1 | -0.6 | 0.494 | 0.4 | 3.6 | 1.42 | 0.0782 | 0.0799 | 33.1 | -32.4 | 0.1 |
| 120 | 9.0 | 1.5 | 0.506 | 1.0 | 8.0 | 2.22 | 0.0131 | 0.0120 | 48.5 | -46.0 | 5.0 |
| 240 | 19.1 | 5.1 | 0.526 | 2.2 | 16.9 | 3.26 | 0.0006 | 0.0010 | 70.6 | -61.3 | 15.1 |
| 480 | 37.4 | 16.4 | 0.551 | 6.1 | 31.2 | 3.72 | 0.0001 | 0.0010 | 109.6 | -83.9 | 33.3 |
| 1440 | 67.8 | 42.9 | 0.581 | 25.8 | 42.0 | 2.26 | 0.0120 | 0.0010 | 200.2 | -121.7 | 63.7 |

제안(최적화 아님): 최대보유 480분, 익절 ≈ MFE 중앙 1.10% (1.38×ATR), 손절 ≈ |MAE| 75% 1.41% (1.86×ATR, ATR 중앙 0.744%)

**atr_channel_breakout(span=24,mult=3.0) & vol_regime(window=24,min_pct=0.67)** — 이벤트 678 (트리거 1136, 적격 13439, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.0006 · 정보 지평 480분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 3.8 | -1.3 | 0.490 | 0.7 | 3.1 | 1.10 | 0.1360 | 0.1339 | 34.7 | -31.3 | -0.2 |
| 120 | 9.6 | 4.6 | 0.524 | 1.6 | 8.0 | 2.03 | 0.0212 | 0.0140 | 50.2 | -44.5 | 5.6 |
| 240 | 22.2 | 8.7 | 0.541 | 3.5 | 18.7 | 3.09 | 0.0010 | 0.0020 | 74.2 | -59.9 | 18.1 |
| 480 | 43.0 | 23.2 | 0.571 | 8.3 | 34.7 | 3.54 | 0.0002 | 0.0010 | 115.1 | -83.1 | 39.0 |
| 1440 | 74.5 | 45.0 | 0.583 | 27.0 | 47.6 | 2.32 | 0.0101 | 0.0010 | 212.2 | -119.8 | 70.5 |

제안(최적화 아님): 최대보유 480분, 익절 ≈ MFE 중앙 1.15% (1.40×ATR), 손절 ≈ |MAE| 75% 1.42% (1.80×ATR, ATR 중앙 0.785%)


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.