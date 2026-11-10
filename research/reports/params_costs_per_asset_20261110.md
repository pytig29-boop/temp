# 자산별 비용 모델 (class ①, PLAN D17) — 2026-11-10

결정: **사용자 명시 승인 (채팅 2026-11-10)**. 기록: 제안 + 승인 기록 (`research/param_proposals/`, `research/param_approvals/`,
제목 "per-asset cost models XRP-KRW / ETH-KRW (D17)"). 코드: `btc_quant.params.costs` (자산별 모델 `<name>@<SYM>`, 생성 = 사람 승인 제안만),
조회 `research.campaign.sleeve_cost_name` (없으면 기본 모델 — BTC-KRW 는 항상 기본 모델).

## 표본

- 15분 REST 호가 표본 (2026-11-10, 3초 간격, 거래소·심볼마다 n = 126 스냅샷). 원자료(세션 임시 파일, 커밋 안 함):
  `cost_sample.json` (스크립트 `cost_sample.py`, 아래 표가 그 전부).
- 지표: `spread` = (최우선 매도 − 최우선 매수)/mid (bp), `편도` = 100,000원 시장가 매수의 mid 대비 불리분
  (스프레드/2 + 충격, 수수료 제외, bp; `data.orderbook.market_buy_cost`).
- 한계: 15분·한 시간대(KST 오후)·매수 쪽만. 확정 보정 기준(72 관측 시간, KST 24시각, 7일)에 크게 못 미치는 **잠정** 값이다.
  XRP 는 호가 단위(1원)가 스프레드를 정한다(1 tick = 6.7bp @ ~1,484원) — 표본 길이보다 호가 단위가 지배하는 비용.

| 거래소:심볼 | n | spread p50 | spread p90 | spread p99 | 편도 p50 | 편도 p90 | 편도 p99 |
|---|---|---|---|---|---|---|---|
| coinone:KRW-BTC | 126 | 0.99 | 0.99 | 1.97 | 0.49 | 0.49 | 1.38 |
| coinone:KRW-XRP | 126 | 6.73 | 6.75 | 6.75 | 3.36 | 3.37 | 3.37 |
| coinone:KRW-ETH | 126 | 3.47 | 3.48 | 6.95 | 1.74 | 1.74 | 5.20 |
| upbit:KRW-BTC | 126 | 2.46 | 7.19 | 9.46 | 1.28 | 4.44 | 5.85 |
| upbit:KRW-XRP | 126 | 6.73 | 6.75 | 13.45 | 3.36 | 3.37 | 9.01 |
| upbit:KRW-ETH | 126 | 3.47 | 6.95 | 10.42 | 1.74 | 3.48 | 5.21 |

(원값: coinone XRP spread p50 6.7272, 편도 p50 3.3636 / p99 3.3750; ETH spread p50 3.4716, 편도 p50 1.7358 / p99 5.2038;
BTC 편도 p50 0.4929 / p99 1.3799. 업비트 XRP spread p99 13.4499 = 2 tick, ETH spread p99 10.4221 = 3 tick.)

## 모델 (편도 = spread/2 + slip, 왕복 = 2 × (편도 + 수수료))

### 코인원 (수수료 0 — `exchanges.coinone.taker_fee`)

사용자 지정: 왕복 XRP 8 / 10 / 14 bp, ETH 5 / 8 / 14 bp. 스프레드 = 측정 중앙값(XRP 6.7 = 1 tick, ETH 3.5), 나머지를 slip 에.

| 모델 | spread | slip | 편도 | 왕복 | 측정 편도 대비 |
|---|---|---|---|---|---|
| coinone_base@XRP-KRW | 6.7 | 0.65 | 4.00 | **8.0** | p50 3.36 + 0.64 |
| coinone_conservative@XRP-KRW | 6.7 | 1.65 | 5.00 | **10.0** | p99 3.37 × 1.48 |
| coinone_stress@XRP-KRW | 6.7 | 3.65 | 7.00 | **14.0** | p99 × 2.1 |
| coinone_base@ETH-KRW | 3.5 | 0.75 | 2.50 | **5.0** | p50 1.74 + 0.76 |
| coinone_conservative@ETH-KRW | 3.5 | 2.25 | 4.00 | **8.0** | p50 × 2.3 (p99 5.20 미만 — p99 는 2 tick 순간) |
| coinone_stress@ETH-KRW | 3.5 | 5.25 | 7.00 | **14.0** | p99 × 1.35 |

검산: XRP 2·(3.35 + 0.65) = 8, 2·(3.35 + 1.65) = 10, 2·(3.35 + 3.65) = 14; ETH 2·(1.75 + 0.75) = 5, 2·(1.75 + 2.25) = 8,
2·(1.75 + 5.25) = 14. BTC 모델(coinone_* v2: 2 / 4 / 8 bp)은 **변경 없음**.

### 업비트 (등급 A 판정 비용 — grade / grade_stress; 수수료 0.05% 편도)

보수적 도출: 수수료 0.05% + 측정 스프레드(base·conservative = 중앙값 XRP 6.7 / ETH 3.5, stress = 측정 p99 XRP 13.4 (2 tick) /
ETH 10.4 (3 tick)) + 업비트 BTC 프리셋과 같은 slip (base 1.0 / conservative 3.0 / stress 8.0).

| 모델 | fee | spread | slip | 편도(수수료 제외) | 왕복(수수료 포함) |
|---|---|---|---|---|---|
| upbit_base@XRP-KRW | 0.0005 | 6.7 | 1.0 | 4.35 | **18.7** |
| upbit_conservative@XRP-KRW | 0.0005 | 6.7 | 3.0 | 6.35 | **22.7** |
| upbit_stress@XRP-KRW | 0.0005 | 13.4 | 8.0 | 14.70 | **39.4** |
| upbit_base@ETH-KRW | 0.0005 | 3.5 | 1.0 | 2.75 | **15.5** |
| upbit_conservative@ETH-KRW | 0.0005 | 3.5 | 3.0 | 4.75 | **19.5** |
| upbit_stress@ETH-KRW | 0.0005 | 10.4 | 8.0 | 13.20 | **36.4** |

stress 편도(수수료 제외)는 측정 업비트 편도 p99(XRP 9.01, ETH 5.21) 이상. 업비트 BTC 모델(13.4 / 18 / 31 bp)은 변경 없음.

## 검증·적용 범위

- `config/costs.yaml` 검증: 거래소 × 심볼마다 base ≤ conservative ≤ stress (왕복) — 위 표 모두 통과 (coinone XRP 8 ≤ 10 ≤ 14, ETH 5 ≤ 8 ≤ 14,
  upbit XRP 18.7 ≤ 22.7 ≤ 39.4, ETH 15.5 ≤ 19.5 ≤ 36.4). 자산별 모델은 보정(calibrate)의 거래소 기본 모델 조회(`model_for`)에 섞이지 않는다.
- 사용처: 캠페인(슬리브별 primary·grade), 파이프라인(Dev 재실행·G2 귀무·G6 primary/stress/grade·견고성), 신호 선별(풀링 이벤트마다 자기 자산 비용,
  단일 비-BTC 자산 선별도), 전진 인큐베이션, 페이퍼(슬리브 트레이더) — `tests/test_costs_per_asset.py`.
- 방향: 모두 새 모델(version 1, direction initial). 그 슬리브가 지금까지 쓰던 대체 모델(BTC 수준 비용)보다 모두 **비싸다**(엄격).
- D17 이전 캠페인의 기록은 그대로다(원장 cost_versions 에 그 시점 버전). 새 캠페인부터 XRP/ETH 슬리브가 이 모델을 쓴다.
