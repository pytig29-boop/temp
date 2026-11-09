# 비용 보정 (class ①) — 2026-11-09

생성 2026-11-09T14:05:10+00:00 · 주문 금액 300,000원 · 코드 `src/btc_quant/params/calibrate.py` · 규칙 docs/PARAMS.md

유효 편도 비용 = 호가 VWAP 의 mid 대비 불리분(스프레드/2 + 충격, 수수료 제외, bp). 매수·매도 양쪽을 합친 분포.
base = q0.5, conservative = q0.9, stress = q0.99 × 1.5. 잠정(provisional) 표본은 분위수 대신 시간 버킷 블록 부트스트랩 95% 상한을 쓴다.

> **잠정(provisional)**: 표본이 확정 기준(관측 시간 72개, KST 시각 24종, 7일)에 못 미친다. 느슨한 변경은 물론 엄격 변경도 자동 적용하지 않고 제안으로 남긴다.

## 표본

| 거래소 | 판정 | 스냅샷 | 관측 시간(버킷) | KST 시각 | 일수 | 기간(UTC) | 출처 | 깊이 부족 |
|---|---|---|---|---|---|---|---|---|
| coinone | **provisional** | 965 | 4 | [20, 21, 22, 23] | 1 | 2026-11-09 11:46:00.045000+00:00 ~ 2026-11-09 14:05:01.238000+00:00 | {'snap': 503, 'rest': 462} | 0.00% |
| upbit | **provisional** | 656 | 3 | [17, 22, 23] | 1 | 2026-11-09 08:09:25.122000+00:00 ~ 2026-11-09 14:05:00.552000+00:00 | {'rest': 421, 'snap': 235} | 0.00% |

- coinone: 4 observed hours (final needs 72), 4 distinct KST hours (needs 24), 1 day(s) (needs 7)
- upbit: 3 observed hours (final needs 72), 3 distinct KST hours (needs 24), 1 day(s) (needs 7)

## 유효 편도 비용 분포 (bp)

| 거래소 | n(편도) | 평균 | p50 | p75 | p90 | p95 | p99 | 최대 | 스프레드 p50 | p90 | p99 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| coinone | 1,427 | 0.56 | 0.49 | 0.49 | 0.81 | 1.01 | 1.45 | 2.10 | 0.99 | 0.99 | 1.98 |
| upbit | 1,077 | 1.51 | 1.24 | 1.77 | 3.59 | 5.22 | 6.92 | 8.73 | 1.39 | 5.53 | 10.36 |

쪽별:

- coinone buy: n=965, p50 0.49, p90 0.71, p99 1.45
- coinone sell: n=462, p50 0.49, p90 0.99, p99 1.45
- upbit buy: n=656, p50 1.05, p90 3.52, p99 7.07
- upbit sell: n=421, p50 1.48, p90 3.89, p99 5.86

## 도출 비용 모델과 결정 (비대칭 규칙)

| 모델 | 방법 | 현재 편도/왕복(bp) | 제안 편도/왕복(bp) | spread/slip | 방향 | 처리 |
|---|---|---|---|---|---|---|
| coinone_base | q0.5 upper 95% hour-block bootstrap bound | 1.50 / 3.00 | 0.50 / 1.00 | 0.99 / 0.01 | looser | 제안(사람 승인) |
| coinone_conservative | q0.9 upper 95% hour-block bootstrap bound | 4.00 / 8.00 | 0.99 / 1.98 | 0.99 / 0.49 | looser | 제안(사람 승인) |
| coinone_stress | q0.99 upper 95% hour-block bootstrap bound × safety 1.5 | 10.50 / 21.00 | 2.17 / 4.34 | 1.98 / 1.18 | looser | 제안(사람 승인) |
| upbit_base | q0.5 upper 95% hour-block bootstrap bound | 1.70 / 13.40 | 1.48 / 12.96 | 2.46 / 0.25 | looser | 제안(사람 승인) |
| upbit_conservative | q0.9 upper 95% hour-block bootstrap bound | 4.00 / 18.00 | 4.09 / 18.18 | 6.80 / 0.69 | stricter | 제안(사람 승인) |
| upbit_stress | q0.99 upper 95% hour-block bootstrap bound × safety 1.5 | 10.50 / 31.00 | 10.63 / 31.26 | 11.24 / 5.01 | stricter | 제안(사람 승인) |

**제안 `pp-20261109-cost-calibration-2026-11-09-coinone-upbi-57ac18`** (research/param_proposals/pp-20261109-cost-calibration-2026-11-09-coinone-upbi-57ac18.yaml) — 사람 승인 필요:

    python -m btc_quant.params approve pp-20261109-cost-calibration-2026-11-09-coinone-upbi-57ac18

## 해석·한계

- 호가 비용은 '결정 시점 호가에 즉시 시장가'의 비용이다. 결정→체결 지연 동안의 가격 이동(역선택)은 포함하지 않는다 — 견고성 묶음의 지연 검사(robustness.latency)와 페이퍼 G8 슬리피지 비교가 따로 다룬다.
- 표시 호가(15단계)만 보며 숨은 주문·자기 주문의 영향은 없다. 주문 금액이 커지면 다시 보정해야 한다.
- stress 의 안전계수는 관측하지 못한 국면(급변·점검 직후·새벽 얇은 호가)에 대한 여유이며, 표본이 길어지면 실제 p99 가 이를 대체한다.
