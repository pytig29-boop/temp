# 신호 선별 camp-000043 — r4f_vol_breakout_volcap_4h_u3_screen

- 가설 hyp-000042: On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (pooled, per-asset matched null) at 4h decisions, donchian_breakout(48) entries taken only while the 48-bar realised volatility is at or below its trailing 7-day 67th percentile (experimental vol_cap(48, 0.67)) are followed by 8-48 h returns above the matched null, net of per-asset coinone_conservative cost.
- 데이터: coinone 유니버스 ['BTC-KRW', 'XRP-KRW', 'ETH-KRW'] 1m (Dev), 결정 봉 240분 — 자산별 이벤트 풀링, 귀무는 자산별 매칭(D15)
- 지평 [240, 480, 1440, 2880], 사전등록 주 지평 [480, 1440, 2880], 최소 간격 480분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 1 + 같은 계열 이전 45 = 46
- **통과 1 / 1**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | n_events[BTC-KRW] | n_events[XRP-KRW] | n_events[ETH-KRW] | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| donchian_breakout(lookback=48) & vol_cap(window=48,max_pct=0.67) | 425 | 0.0042 | 1440 | 69.80 | 2.99 | 77.95 | 65.08 | 1440 | peaked | 175 | 88 | 162 | True | True | None |

## 신호별 상세

**donchian_breakout(lookback=48) & vol_cap(window=48,max_pct=0.67)** — 이벤트 425 (트리거 554, 적격 17598, 결정 봉 240분, 최소 간격 480분) · 신호 p=0.0042 · 정보 지평 1440분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 240 | 14.6 | 3.7 | 0.508 | 2.1 | 12.5 | 1.32 | 0.0931 | 0.0280 | 75.2 | -72.8 | 7.8 |
| 480 | 32.5 | 12.9 | 0.544 | 4.6 | 28.0 | 2.32 | 0.0102 | 0.0040 | 116.3 | -90.2 | 25.7 |
| 1440 | 84.8 | 31.1 | 0.560 | 15.0 | 69.8 | 2.99 | 0.0014 | 0.0020 | 213.6 | -163.1 | 77.9 |
| 2880 | 142.6 | 50.3 | 0.541 | 35.5 | 107.1 | 2.36 | 0.0091 | 0.0010 | 280.1 | -220.8 | 135.8 |

제안(최적화 아님): 최대보유 1440분, 익절 ≈ MFE 중앙 2.14% (1.34×ATR), 손절 ≈ |MAE| 75% 3.07% (1.90×ATR, ATR 중앙 1.503%)

자산별 (보고 전용 — 판정은 풀링 결과):

| 자산 | 이벤트 | 적격 | 신호 p | 정보 지평 |
|---|---|---|---|---|
| BTC-KRW | 175 | 6219 | 0.0002 | 1440 |
| XRP-KRW | 88 | 5708 | 0.3532 | 2880 |
| ETH-KRW | 162 | 5671 | 0.1740 | 2880 |


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.