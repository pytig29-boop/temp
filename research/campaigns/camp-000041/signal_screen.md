# 신호 선별 camp-000041 — r4e_kst09_open_5m_screen

- 가설 hyp-000040: 코인원 BTC-KRW 에서 매일 00:05 UTC(09:05 KST)에 사서 60분 보유한 수익이, 직전 변동성으로 짝지은 다른 시각의 60분 보유(귀무)보다 높고 coinone_conservative 비용을 뺀 평균이 0 보다 크다.
- 데이터: coinone BTC-KRW 1m (Dev), 결정 봉 5분
- 지평 [30, 60, 120], 사전등록 주 지평 [60], 최소 간격 60분, 귀무 1000회 (vol 층화)
- 판정 규칙: pass ⇔ BH(α=0.1) rejects p_signal = min(1, 1·min_H p_H) over the screening family, n_events ≥ 300, mean net of coinone_conservative > 0 at the best primary horizon
- 다중검정 집합: 이번 1 + 같은 계열 이전 1 = 2
- **통과 0 / 1**

## 요약

| label | n_events | p_signal | best_h | excess_bp | z | net_bp[coinone_conservative] | net_bp[upbit_conservative] | info_h | profile | significant | passed | fail_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| clock_entry_utc(hour=0,minute=5) | 1616 | 0.2032 | 60 | 1.58 | 0.83 | -2.09 | -16.08 | 30 | decaying (z maximal at the shortest horizon) | False | False | not significant after correction |

## 신호별 상세

**clock_entry_utc(hour=0,minute=5)** — 이벤트 1616 (트리거 1616, 적격 383618, 결정 봉 5분, 최소 간격 60분) · 신호 p=0.2032 · 정보 지평 30분 (decaying (z maximal at the shortest horizon))

| H(분) | 평균bp | 중앙bp | 적중 | 귀무bp | 초과bp | z | p | p_emp | MFE중앙bp | MAE중앙bp | 순평균bp[coinone_conservative] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 30 | 2.9 | 1.5 | 0.512 | 0.1 | 2.8 | 1.80 | 0.0358 | 0.0040 | 22.2 | -19.5 | -1.1 |
| 60 | 1.9 | 1.0 | 0.505 | 0.3 | 1.6 | 0.83 | 0.2032 | 0.1439 | 30.6 | -27.9 | -2.1 |
| 120 | -0.1 | 0.2 | 0.500 | 0.7 | -0.9 | -0.36 | 0.6424 | 0.6733 | 40.1 | -40.8 | -4.1 |

제안(최적화 아님): 최대보유 30분, 익절 ≈ MFE 중앙 0.22% (1.67×ATR), 손절 ≈ |MAE| 75% 0.42% (2.77×ATR, ATR 중앙 0.137%)


> 선별 통과 = 정보가 있다는 1차 증거일 뿐. 전략화(원칙적 청산 4~6 패턴) 후 G0~G6 를 다시 거친다. 선별 시도는 같은 계열의 시도 수에 누적된다.