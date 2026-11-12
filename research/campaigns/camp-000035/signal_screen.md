# 신호 선별 camp-000035 — r2c_xasset_btc_lead_4h_u3_screen

- 가설 hyp-000034: On the fixed Coinone universe [BTC-KRW, XRP-KRW, ETH-KRW] (pooled, per-asset matched null) at 4h decisions, donchian_breakout(48) entries taken only while Coinone BTC-KRW is in a 4h up-trend (close > EMA 42 or 90 bars = 7 or 15 days; experimental btc_trend_filter, aux coinone_btc) are followed by 8-48 h returns above the matched null, net of per-asset coinone_conservative cost.
- 데이터: coinone 유니버스 ['BTC-KRW', 'XRP-KRW', 'ETH-KRW'] 1m (Dev), 결정 봉 240분 — 자산별 이벤트 풀링, 귀무는 자산별 매칭(D15)
- 지평 [240, 480, 1440, 2880], 사전등록 주 지평 [480, 1440, 2880], 최소 간격 480분, 귀무 1000회 (hour_vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 3·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 2 + 같은 계열 이전 0 = 2
- **통과 2 / 2**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | n_events[BTC-KRW] | n_events[XRP-KRW] | n_events[ETH-KRW] | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| donchian_breakout(lookback=48) & btc_trend_filter(span=42,band_bp=0.0) | 757 | 0.0058 | 1440 | 61.27 | 2.89 | 101.61 | 88.71 | 1440 | peaked | 303 | 170 | 284 | True | True | None |
| donchian_breakout(lookback=48) & btc_trend_filter(span=90,band_bp=0.0) | 742 | 0.0015 | 1440 | 62.28 | 3.30 | 103.57 | 90.67 | 1440 | peaked | 302 | 160 | 280 | True | True | None |

## 신호별 상세

**donchian_breakout(lookback=48) & btc_trend_filter(span=42,band_bp=0.0)** — 이벤트 757 (트리거 1014, 적격 15294, 결정 봉 240분, 최소 간격 480분) · 신호 p=0.0058 · 정보 지평 1440분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 240 | 23.0 | 4.4 | 0.515 | 8.4 | 14.6 | 1.85 | 0.0321 | 0.0080 | 92.3 | -76.5 | 16.1 |
| 480 | 38.8 | 17.1 | 0.550 | 16.5 | 22.3 | 1.93 | 0.0268 | 0.0040 | 129.4 | -96.7 | 31.9 |
| 1440 | 108.5 | 47.0 | 0.573 | 47.3 | 61.3 | 2.89 | 0.0019 | 0.0010 | 239.4 | -163.1 | 101.6 |
| 2880 | 172.0 | 76.4 | 0.571 | 92.0 | 80.0 | 2.35 | 0.0094 | 0.0030 | 329.5 | -214.0 | 165.1 |

제안(최적화 아님): 최대보유 1440분, 익절 ≈ MFE 중앙 2.39% (1.50×ATR), 손절 ≈ |MAE| 75% 3.16% (1.80×ATR, ATR 중앙 1.626%)

자산별 (보고 전용 — 판정은 풀링 결과):

| 자산 | 이벤트 | 적격 | 신호 p | 정보 지평 |
|---|---|---|---|---|
| BTC-KRW | 303 | 5318 | 0.0028 | 1440 |
| XRP-KRW | 170 | 5067 | 0.2962 | 1440 |
| ETH-KRW | 284 | 4909 | 0.0777 | 2880 |

**donchian_breakout(lookback=48) & btc_trend_filter(span=90,band_bp=0.0)** — 이벤트 742 (트리거 996, 적격 15664, 결정 봉 240분, 최소 간격 480분) · 신호 p=0.0015 · 정보 지평 1440분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 240 | 19.4 | 3.9 | 0.513 | 7.6 | 11.9 | 1.58 | 0.0568 | 0.0270 | 90.2 | -76.1 | 12.6 |
| 480 | 42.5 | 19.8 | 0.558 | 16.8 | 25.8 | 2.58 | 0.0049 | 0.0030 | 128.1 | -95.7 | 35.7 |
| 1440 | 110.4 | 47.4 | 0.574 | 48.2 | 62.3 | 3.30 | 0.0005 | 0.0010 | 240.1 | -159.3 | 103.6 |
| 2880 | 177.5 | 85.6 | 0.580 | 97.0 | 80.4 | 2.48 | 0.0065 | 0.0020 | 329.7 | -207.6 | 170.5 |

제안(최적화 아님): 최대보유 1440분, 익절 ≈ MFE 중앙 2.40% (1.50×ATR), 손절 ≈ |MAE| 75% 3.14% (1.80×ATR, ATR 중앙 1.624%)

자산별 (보고 전용 — 판정은 풀링 결과):

| 자산 | 이벤트 | 적격 | 신호 p | 정보 지평 |
|---|---|---|---|---|
| BTC-KRW | 302 | 5446 | 0.0084 | 480 |
| XRP-KRW | 160 | 5193 | 0.1625 | 1440 |
| ETH-KRW | 280 | 5025 | 0.0365 | 2880 |


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.