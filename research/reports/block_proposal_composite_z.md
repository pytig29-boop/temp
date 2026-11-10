# 블록 제안 — `composite_z`, `composite_z_upbit` (실험 블록, 합성 점수)

작성 2026-11-10 · claude (AI) · 상태 **experimental** (PLAN D11, 자동 수용 검사 통과) · 코드
`src/btc_quant/strategy/blocks/experimental/composite.py` · 테스트 `tests/test_block_composite.py` · 문서 `docs/BLOCKS.md`
"합성 점수 블록" 절.

## 동기
사이클 1~3C 에서 개별로는 약하지만 Dev 에서 귀무보다 나은 신호가 여럿 나왔다. 업비트 선행 충격(fact-000001/2), 단기
되돌림 z(camp-000004/011), 1h 모멘텀(camp-000009/013/017), 거래량 동반 충격(camp-000020). 각각은 비용·G2·G6 를 넘지
못했다(사이클 3C 결론: Coinone 1h 롱 온리 단일 신호는 Dev Sharpe_w ≈ 0.9–1.15 에서 막힘). 약한 신호를 묶으면 잡음이
평균되어 정보 대비 잡음이 나아질 수 있다. 하지만 가중치를 적합하면 그 자체가 원장에 잡히지 않는 시도가 된다.
그래서 **적합 가중치 없는 동일 가중 z 평균**으로, 파라미터를 `{preset, threshold}` 두 개로 제한했다.

기존 블록으로는 표현할 수 없다. 필터 AND 결합(최대 2개)은 "모든 조건이 동시에 문턱 이상"만 표현한다. 연속 점수의
평균(한 성분이 강하면 다른 성분이 약해도 됨)은 표현할 수 없고, 필터 2개 제한도 3성분 결합을 막는다.

가설 id: 아직 없음. 이 블록을 쓰는 첫 선별은 `lessons_derive_hypothesis` 로 **기존 family**(모멘텀 → vol_breakout,
되돌림 → liq_meanrev, 업비트 선행 → xvenue_upbit_lead)에서 파생해야 한다. 새 family 이름을 쓰면 같은 효과의 시도 수가
초기화된다(함정 P-S2).

## 수식
`docs/BLOCKS.md` 참조. 성분 z = 자기 직전 288봉 평균·표준편차로 표준화(현재 봉 제외), ±3 클립, 프리셋 성분의 부호
붙인 평균 ≥ threshold. 프리셋 3 + 3개, threshold {0.5, 1, 1.5, 2}.

## 수용 검사·테스트
- `acceptance_check`: 두 블록 모두 PASS (metadata, signature, output_types 22, purity, causality_future 12 + 1m 2,
  causality_trunc 3 + 1m 2, front_window, non_degenerate 5분 중앙 격자 4.9% / 6.2%, non_degenerate_1m, docs).
- `tests/test_block_composite.py` (18): 손계산 z(현재 봉 제외, 최소 W/2, std 0 → NaN, 클립, 비유한값), 성분 특징 손계산,
  점수 = 부호 붙인 z 의 동일 가중 평균. 동일 가중 불변성 4종: 성분 순서, 양의 아핀 변환, 가격 단위, 기여 계수 = 1/|P|.
  극단 성분 독차지 방지, 모든 프리셋 × 1m/5m/15m/1h/4h 의 미래 변조·절단 인과성과 비퇴화, 후행 창 재현,
  업비트 결측·누락, 거래량 일정, 잘못된 프리셋, 문턱 단조성.

## 진입 비율 (Dev, Coinone, 업비트 aux; 신호 비율만 — 수익·결과는 계산하지 않음, 원장 시도 없음)

| 봉 | 프리셋 | thr 0.5 | 1.0 | 1.5 | 2.0 |
|---|---|---|---|---|---|
| 5m | reversion | 20.17% | 8.24% | 3.48% | 1.43% |
| 5m | momentum | 29.62% | 13.63% | 5.07% | 1.69% |
| 5m | volume_momentum | 18.96% | 8.13% | 3.58% | 1.58% |
| 5m | lead | 24.55% | 10.02% | 3.56% | 1.08% |
| 5m | lead_gap | 24.91% | 9.58% | 3.01% | 0.75% |
| 5m | upbit_capitulation | 20.32% | 8.70% | 3.67% | 1.53% |
| 1h | reversion | 20.13% | 8.93% | 3.87% | 1.55% |
| 1h | momentum | 29.69% | 14.10% | 5.64% | 1.92% |
| 1h | volume_momentum | 19.44% | 8.38% | 3.72% | 1.45% |
| 1h | lead | 20.50% | 6.83% | 2.25% | 0.61% |
| 1h | lead_gap | 20.86% | 7.10% | 2.17% | 0.58% |
| 1h | upbit_capitulation | 19.72% | 8.65% | 3.75% | 1.48% |

상태형 신호(점수가 문턱 위인 동안 True)다. 실제 진입 수는 보유 중 신호를 무시하므로 이보다 훨씬 적다. 합성 5분봉에서의
점수 표준편차는 0.70–0.99 이고, 성분 상관 때문에 프리셋마다 다르다.

## 사용 시 주의
- 이 블록을 쓴 후보는 잠금 요청 전 사람 검토가 필요하다(`spec_uses_experimental`).
- ablation: 합성 점수의 정보가 성분 하나(예: momentum 의 r_12)에서만 오는지 확인하려면 같은 문턱대의 단일 성분 블록
  (`roc_threshold`, `bollinger_z_revert`, `upbit_lead_impulse`, `venue_gap_revert`)과 선별 단계에서 대조한다.
- 격자를 결과를 보고 바꾸지 않는다. 성분이나 창을 바꾸면 새 이름의 새 블록이 된다.
