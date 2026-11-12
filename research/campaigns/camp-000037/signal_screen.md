# 신호 선별 camp-000037 — r2d_liq_meanrev_trend_dip_4h_u3_screen

- 가설 hyp-000036: On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (D15, pooled, per-asset matched null) at 4h decisions, multi-day oversold dips (close >= 2 sd below the 24-bar = 4-day mean, bollinger_z_revert(24,2.0), or RSI(14) <= 30 bounce) taken only while the asset is above its own 90-bar 4h EMA (experimental trend_ema_filter span 90 = 15 days; trend_regime_1h is invalid on 4h bars) are followed by 8-48 h returns above the matched null, net of per-asset coinone_conservative cost.
- 데이터: coinone 유니버스 ['BTC-KRW', 'XRP-KRW', 'ETH-KRW'] 1m (Dev), 결정 봉 240분 — 자산별 이벤트 풀링, 귀무는 자산별 매칭(D15)
- 지평 [240, 480, 1440, 2880], 사전등록 주 지평 [480, 1440, 2880], 최소 간격 480분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 2 + 같은 계열 이전 30 = 32
- **통과 0 / 2**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | n_events[BTC-KRW] | n_events[XRP-KRW] | n_events[ETH-KRW] | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bollinger_z_revert(window=24,z=2.0) & trend_ema_filter(span=90,band_bp=0.0) | 150 | 1.0000 | 1440 | -1.26 | -0.03 | 40.60 | 27.82 | - | none (excess ≤ 0 at all horizons) | 61 | 28 | 61 | False | False | not significant after correction |
| rsi_oversold_bounce(period=14,level=30) & trend_ema_filter(span=90,band_bp=0.0) | 17 | 1.0000 | 480 | 20.46 | 0.23 | 30.39 | 17.47 | 480.00 | peaked | 7 | 5 | 5 | False | False | not significant after correction |

## 신호별 상세

**bollinger_z_revert(window=24,z=2.0) & trend_ema_filter(span=90,band_bp=0.0)** — 이벤트 150 (트리거 208, 적격 14656, 결정 봉 240분, 최소 간격 480분) · 신호 p=1.0000 · 정보 지평 None분 (none (excess ≤ 0 at all horizons))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 240 | 9.1 | 10.3 | 0.553 | 9.4 | -0.3 | -0.02 | 0.5080 | 0.5115 | 83.9 | -82.3 | 2.4 |
| 480 | -14.4 | -8.0 | 0.480 | 18.5 | -32.9 | -1.60 | 0.9452 | 0.9630 | 100.5 | -129.3 | -21.1 |
| 1440 | 47.4 | 58.9 | 0.600 | 48.6 | -1.3 | -0.03 | 0.5137 | 0.5165 | 196.0 | -185.5 | 40.6 |
| 2880 | 73.5 | 42.6 | 0.560 | 95.5 | -22.1 | -0.42 | 0.6623 | 0.6623 | 264.1 | -261.0 | 66.7 |

자산별 (보고 전용 — 판정은 풀링 결과):

| 자산 | 이벤트 | 적격 | 신호 p | 정보 지평 |
|---|---|---|---|---|
| BTC-KRW | 61 | 5446 | 1.0000 | 2880 |
| XRP-KRW | 28 | 4522 | 1.0000 | 240 |
| ETH-KRW | 61 | 4688 | 1.0000 | 240 |

**rsi_oversold_bounce(period=14,level=30) & trend_ema_filter(span=90,band_bp=0.0)** — 이벤트 17 (트리거 17, 적격 14656, 결정 봉 240분, 최소 간격 480분) · 신호 p=1.0000 · 정보 지평 480분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 240 | -23.9 | -30.6 | 0.412 | 6.7 | -30.6 | -0.68 | 0.7503 | 0.7802 | 71.2 | -87.7 | -30.8 |
| 480 | 37.4 | 89.2 | 0.706 | 16.9 | 20.5 | 0.23 | 0.4081 | 0.3447 | 136.9 | -166.9 | 30.4 |
| 1440 | 45.7 | -37.8 | 0.471 | 47.7 | -2.0 | -0.02 | 0.5073 | 0.4765 | 210.9 | -206.9 | 38.7 |
| 2880 | -25.6 | 27.6 | 0.529 | 89.8 | -115.4 | -0.70 | 0.7570 | 0.7552 | 285.4 | -298.5 | -32.5 |

제안(최적화 아님): 최대보유 480분, 익절 ≈ MFE 중앙 1.37% (0.82×ATR), 손절 ≈ |MAE| 75% 2.07% (0.86×ATR, ATR 중앙 2.223%)

자산별 (보고 전용 — 판정은 풀링 결과):

| 자산 | 이벤트 | 적격 | 신호 p | 정보 지평 |
|---|---|---|---|---|
| BTC-KRW | 7 | 5446 | 0.4079 | 480 |
| XRP-KRW | 5 | 4522 | 0.5638 | 480 |
| ETH-KRW | 5 | 4688 | 1.0000 | None |


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.