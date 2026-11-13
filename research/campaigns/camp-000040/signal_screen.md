# 신호 선별 camp-000040 — r4e_session_2123utc_1h_screen

- 가설 hyp-000039: 코인원 BTC-KRW 에서 매일 21:00 UTC(06:00 KST)에 사서 120분 보유한 수익이, 직전 변동성으로 짝지은 다른 시각의 120분 보유(귀무)보다 높고 coinone_conservative 비용을 뺀 평균이 0 보다 크다.
- 데이터: coinone BTC-KRW 1m (Dev), 결정 봉 60분
- 지평 [60, 120, 240], 사전등록 주 지평 [120], 최소 간격 120분, 귀무 1000회 (vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 1·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 1 + 같은 계열 이전 0 = 1
- **통과 1 / 1**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| clock_entry_utc(hour=21,minute=0) | 1630 | 0.0001 | 120 | 8.06 | 3.83 | 4.67 | -9.32 | 120 | peaked | True | True | None |

## 신호별 상세

**clock_entry_utc(hour=21,minute=0)** — 이벤트 1630 (트리거 1630, 적격 41009, 결정 봉 60분, 최소 간격 120분) · 신호 p=0.0001 · 정보 지평 120분 (peaked)

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 5.3 | 3.8 | 0.548 | 0.1 | 5.2 | 3.59 | 0.0002 | 0.0010 | 23.1 | -20.9 | 1.3 |
| 120 | 8.7 | 4.7 | 0.552 | 0.6 | 8.1 | 3.83 | 0.0001 | 0.0010 | 34.4 | -28.2 | 4.7 |
| 240 | 4.7 | 2.2 | 0.518 | 1.6 | 3.2 | 1.05 | 0.1458 | 0.1379 | 52.5 | -45.9 | 0.7 |

제안(최적화 아님): 최대보유 120분, 익절 ≈ MFE 중앙 0.34% (0.56×ATR), 손절 ≈ |MAE| 75% 0.61% (0.89×ATR, ATR 중앙 0.660%)


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.