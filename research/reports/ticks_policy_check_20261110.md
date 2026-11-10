# 호가 단위: 데이터 추정 vs 거래소 정책 (Dev 점검) — 2026-11-10

엔지니어링 수정 (사용자 승인 2026-11-10): 페이퍼(실시간)의 지정가·사다리 주문 호가 단위를 1분봉 종가 추정 대신 **거래소 정책표**로 바꿨다
(`src/btc_quant/data/ticks.py`, 캐시 `config/ticks.yaml` v1, params.yaml `ticks.coinone.KRW` class ①). 백테스트는 데이터 추정
(`signals.infer_ticks`)을 그대로 쓴다 — 아래처럼 과거 표가 달랐으므로 Dev 전체에 지금 표를 적용하면 과거 체결 규칙이 틀린다.

- 정책 출처: 코인원 공개 API v2 `GET /public/v2/range_units/KRW/{BTC,XRP,ETH}` (2026-11-10 수신; 세 심볼 같은 표).
  KRW 가격대 [하한, 단위]: [0, 0.0001] [1, 0.001] [5, 0.005] [10, 0.01] [50, 0.05] [100, 0.1] [500, 0.5] [1천, 1] [5천, 5] [1만, 10]
  [5만, 50] [10만, 100] [50만, 500] [100만, 1천] [500만, 5천] [1천만, 1만]. 갱신: `python -m btc_quant.data.ticks refresh`
  (표가 바뀌면 새 버전 + 레지스트리 이력). 점검: `python -m btc_quant.data.ticks check`.
- 비교 규칙: UTC 일자마다 추정 단위(그날 실제 봉 종가의 99% 이상이 배수인 가장 큰 후보) vs 그날 실제 봉 종가들의 정책 단위 중 최솟값
  (추정 규칙과 같은 '하루 한 값, 가격대가 섞이면 작은 쪽').
- Dev = 2021-01-01 ~ 2025-10-31 (DataAccess Dev 구간만; Val·잠금 미접근).

## 요약

- **2024-09-29 이전**: 코인원은 지금보다 촘촘한 표를 썼다(BTC 1,000원 vs 지금 10,000원, XRP 500~1,000원대 0.1원 vs 0.5/1원,
  ETH 100만~200만 원대 100원 등). 이 구간 불일치는 정책 변경이며 데이터 추정이 맞다 → 백테스트는 추정 유지가 옳다.
  일부 2021~2022 XRP 일자는 반대로 추정이 더 거칠다(1원 vs 0.5원 — 그 시기 실제 체결이 정수 원에 몰림).
- **2024-09-30 이후 (현재 표 시기)**: BTC 0일, XRP 0일, ETH 3일만 불일치 — 모두 ETH 가 500만 원 경계(단위 1,000 ↔ 5,000)를
  넘나든 날(2024-12-26, 2024-12-28, 2025-08-06)로, 추정이 5,000(경계 위 가격이 대부분)·정책 최솟값이 1,000. 그날 백테스트는
  더 거친 단위(관통 폭이 더 큼 = 체결이 더 어려움, 보수적)를 썼다. 페이퍼는 봉 시가의 가격대 단위를 쓴다.
- 결론: 현재 표 시기에는 추정과 정책이 사실상 같다(경계일 3일 제외). 페이퍼가 정책표를 쓰는 것으로 '자정 직후 추정 불안정'
  (그날 몇 분의 종가만으로 추정) 문제가 사라진다.

## Dev 결과 (전체)

| 심볼 | Dev 일수 | 일치 | 불일치 | 기간 |
|---|---|---|---|---|
| BTC-KRW | 1765 | 397 | 1368 | 2021-01-01 ~ 2025-10-31 |
| XRP-KRW | 1765 | 804 | 961 | 2021-01-01 ~ 2025-10-31 |
| ETH-KRW | 1765 | 1644 | 121 | 2021-01-01 ~ 2025-10-31 |

불일치 구간 (연속 일자, 같은 (추정, 정책) 쌍):

| 심볼 | 시작 | 끝 | 일수 | 추정 tick | 정책 tick | 종가 범위 | 종류 |
|---|---|---|---|---|---|---|---|
| BTC-KRW | 2021-01-01 | 2024-09-29 | 1368 | 1000 | 10000 | 2.0748e+07 ~ 1.04342e+08 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2021-02-11 | 2021-02-22 | 12 | 0.1 | 0.5 | 543.8 ~ 744.1 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2021-02-24 | 2021-02-24 | 1 | 0.1 | 0.5 | 510.1 ~ 564.3 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2021-03-04 | 2021-03-11 | 8 | 0.1 | 0.5 | 503.6 ~ 565.6 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2021-03-13 | 2021-03-14 | 2 | 0.1 | 0.5 | 505.1 ~ 534 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2021-03-17 | 2021-04-05 | 20 | 0.1 | 0.5 | 520.2 ~ 1,228.5 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2021-04-06 | 2021-04-12 | 7 | 0.1 | 1 | 1,062 ~ 1,939.5 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2021-04-23 | 2021-04-23 | 1 | 1 | 0.5 | 999 ~ 1,406 | inferred coarser than policy |
| XRP-KRW | 2021-05-23 | 2021-05-24 | 2 | 1 | 0.5 | 815 ~ 1,219 | inferred coarser than policy |
| XRP-KRW | 2021-05-29 | 2021-05-30 | 2 | 1 | 0.5 | 962 ~ 1,135 | inferred coarser than policy |
| XRP-KRW | 2021-06-07 | 2021-07-11 | 35 | 1 | 0.5 | 601 ~ 1,131 | inferred coarser than policy |
| XRP-KRW | 2021-07-12 | 2021-08-11 | 31 | 0.1 | 0.5 | 613.4 ~ 1,235 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2021-08-12 | 2021-09-08 | 28 | 0.1 | 1 | 1,083.3 ~ 1,626 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2021-12-04 | 2021-12-06 | 3 | 1 | 0.5 | 922 ~ 1,167 | inferred coarser than policy |
| XRP-KRW | 2021-12-11 | 2021-12-11 | 1 | 1 | 0.5 | 999 ~ 1,047 | inferred coarser than policy |
| XRP-KRW | 2021-12-13 | 2021-12-15 | 3 | 1 | 0.5 | 955 ~ 1,051 | inferred coarser than policy |
| XRP-KRW | 2021-12-17 | 2021-12-18 | 2 | 1 | 0.5 | 979 ~ 1,036 | inferred coarser than policy |
| XRP-KRW | 2021-12-29 | 2021-12-31 | 3 | 1 | 0.5 | 986 ~ 1,056 | inferred coarser than policy |
| XRP-KRW | 2022-01-05 | 2022-02-08 | 35 | 1 | 0.5 | 690 ~ 1,111 | inferred coarser than policy |
| XRP-KRW | 2022-02-11 | 2022-03-09 | 27 | 1 | 0.5 | 774 ~ 1,037 | inferred coarser than policy |
| XRP-KRW | 2022-03-10 | 2022-03-21 | 12 | 0.1 | 0.5 | 903 ~ 1,053 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-03-22 | 2022-03-25 | 4 | 0.1 | 1 | 1,001.5 ~ 1,045.5 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-03-26 | 2022-03-26 | 1 | 0.1 | 0.5 | 996.4 ~ 1,013.7 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-03-27 | 2022-03-30 | 4 | 0.1 | 1 | 1,002.8 ~ 1,086.3 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-03-31 | 2022-04-01 | 2 | 0.1 | 0.5 | 977 ~ 1,048 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-04-02 | 2022-04-02 | 1 | 0.1 | 1 | 1,005 ~ 1,030 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-04-03 | 2022-04-04 | 2 | 0.1 | 0.5 | 996 ~ 1,033.7 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-04-05 | 2022-04-05 | 1 | 0.1 | 1 | 1,003 ~ 1,026.6 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-04-06 | 2022-05-11 | 36 | 0.1 | 0.5 | 522 ~ 1,010.1 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-05-13 | 2022-05-25 | 13 | 0.1 | 0.5 | 500.4 ~ 603 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-05-31 | 2022-05-31 | 1 | 0.1 | 0.5 | 512.6 ~ 542.6 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-06-06 | 2022-06-06 | 1 | 0.1 | 0.5 | 500.5 ~ 514.5 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-06-08 | 2022-06-09 | 2 | 0.1 | 0.5 | 503.4 ~ 518.1 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-07-31 | 2022-07-31 | 1 | 0.1 | 0.5 | 500.7 ~ 523.8 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-08-11 | 2022-08-11 | 1 | 0.1 | 0.5 | 500.9 ~ 508.5 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-08-13 | 2022-08-14 | 2 | 0.1 | 0.5 | 501.8 ~ 520 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-08-18 | 2022-08-18 | 1 | 0.1 | 0.5 | 503.3 ~ 515.1 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-09-18 | 2022-09-18 | 1 | 0.1 | 0.5 | 503.2 ~ 559.4 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-09-20 | 2022-11-08 | 50 | 0.1 | 0.5 | 523.3 ~ 791.7 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-11-11 | 2022-11-11 | 1 | 0.1 | 0.5 | 511.7 ~ 561.9 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-11-15 | 2022-11-20 | 6 | 0.1 | 0.5 | 500.6 ~ 545.2 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-11-23 | 2022-12-11 | 19 | 0.1 | 0.5 | 503.4 ~ 571.6 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2022-12-13 | 2022-12-13 | 1 | 0.1 | 0.5 | 500.2 ~ 520.3 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2023-01-21 | 2023-01-21 | 1 | 0.1 | 0.5 | 501.1 ~ 517.6 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2023-01-23 | 2023-01-24 | 2 | 0.1 | 0.5 | 500.8 ~ 541.2 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2023-01-26 | 2023-01-29 | 4 | 0.1 | 0.5 | 503 ~ 522.7 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2023-02-02 | 2023-02-05 | 4 | 0.1 | 0.5 | 500.3 ~ 527.3 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2023-02-08 | 2023-02-08 | 1 | 0.1 | 0.5 | 503.6 ~ 512.1 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2023-02-18 | 2023-02-18 | 1 | 0.1 | 0.5 | 504.4 ~ 512.3 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2023-02-21 | 2023-02-23 | 3 | 0.1 | 0.5 | 500.8 ~ 518.9 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2023-03-20 | 2023-07-19 | 122 | 0.1 | 0.5 | 500.5 ~ 1,111.3 | inferred finer than policy (or 0 = not inferable) |
| XRP-KRW | 2023-07-21 | 2024-09-29 | 437 | 0.1 | 0.5 | 561.7 ~ 1,034 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2021-01-01 | 2021-01-03 | 3 | 100 | 500 | 790,300 ~ 1.1406e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2021-01-04 | 2021-02-15 | 43 | 100 | 1000 | 1.025e+06 ~ 2.0121e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2021-10-29 | 2021-10-31 | 3 | 1000 | 5000 | 5e+06 ~ 5.342e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2021-11-02 | 2021-11-17 | 16 | 1000 | 5000 | 5.003e+06 ~ 5.815e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2021-11-20 | 2021-12-03 | 14 | 1000 | 5000 | 5.075e+06 ~ 5.88e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2021-12-05 | 2021-12-05 | 1 | 1000 | 5000 | 5.053e+06 ~ 5.356e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2021-12-07 | 2021-12-09 | 3 | 1000 | 5000 | 5.1e+06 ~ 5.49e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2024-03-07 | 2024-03-16 | 10 | 1000 | 5000 | 5.101e+06 ~ 5.749e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2024-03-18 | 2024-03-18 | 1 | 1000 | 5000 | 5.091e+06 ~ 5.311e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2024-03-26 | 2024-03-26 | 1 | 1000 | 5000 | 5.065e+06 ~ 5.175e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2024-03-31 | 2024-03-31 | 1 | 1000 | 5000 | 5.026e+06 ~ 5.17e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2024-04-11 | 2024-04-11 | 1 | 1000 | 5000 | 5e+06 ~ 5.136e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2024-05-22 | 2024-06-10 | 20 | 1000 | 5000 | 5.015e+06 ~ 5.487e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2024-06-16 | 2024-06-16 | 1 | 1000 | 5000 | 5.019e+06 ~ 5.157e+06 | inferred finer than policy (or 0 = not inferable) |
| ETH-KRW | 2024-12-26 | 2024-12-26 | 1 | 5000 | 1000 | 4.998e+06 ~ 5.235e+06 | inferred coarser than policy |
| ETH-KRW | 2024-12-28 | 2024-12-28 | 1 | 5000 | 1000 | 4.998e+06 ~ 5.12e+06 | inferred coarser than policy |
| ETH-KRW | 2025-08-06 | 2025-08-06 | 1 | 5000 | 1000 | 4.997e+06 ~ 5.145e+06 | inferred coarser than policy |
