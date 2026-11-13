# 운영 결정 기록 (자율 프로그램, 계획 §12) — 최종 보고 부록에 실린다

- 2026-11-12 camp-000034 검정력 목표: 처음 템플릿에 1.40(두 진입을 뭉뚱그린 어림값)을 적었다가, strict power check 실패(필요 1.41) 뒤
  격자를 lookback 48 하나로 줄이고(→ 필요 1.49) 목표를 선별 z 규칙(3.46/√4.83 = 1.57)으로 다시 계산했다. 결과(캠페인 실행) 전의 일이지만
  **검정력 점검에 걸린 뒤 입력을 다시 고른 것**이다(skeptic 지적, 타당). 판정 규칙(rs-001)은 바뀌지 않았고 시도 수는 원장에 그대로 남았다.
  재발 방지: 앞으로 목표 Sharpe 는 템플릿 작성 시 선별 표의 z 로 계산해 적고, 검정력 실패 시 목표를 고치지 않고 격자를 바꾸거나 중단한다.

## 2026-11-12 R2 결과 (트랙 C·D)

- 트랙 C (xasset_btc_lead): 스크린 camp-000035 2/2 통과 → 캠페인 camp-000036 (span 90, 출구 격자 2) **G5 탈락 (PBO 0.378, DSR 0.999)**. 알트 슬리브 Dev Sharpe XRP 0.72 / ETH 0.89 — BTC 필터가 알트를 살리지 못함. 같은 가설을 출구만 바꿔 재실행하지 않는다(결과 고르기). int-000018.
- 트랙 D (liq_meanrev 4h 추세 속 눌림목): 동결 해제(`research/program/families.yaml`, 사유: 결정 주기 변경) → 실험 필터 `trend_ema_filter` 추가(4h 에서 trend_regime_1h 무효) → 스크린 camp-000037 **0/2 (NO_SIGNAL, 이벤트 150·17)** → 해제 조건대로 재동결.
- 결론: R2 에서 새 T1 후보 없음. 현재 후보는 r1b_vol_breakout_donch_4h_u3_half_0dce65e919 하나(페이퍼 진행 중).

## 2026-11-13 R4 개시 (사용자 결정)

- 사용자 지시로 R4 개시 — R3 "기본 종료" 결정(2026-11-12)을 사용자 지시로 대체. 계획: `docs/prompts/opus_r4_plan.md`, 근거 조사 `research/knowledge/mechanism_survey_20261112.md`.
- 결선은 R4 종료 뒤 **한 번** (모든 T1 후보 페이퍼 판정 뒤, 늦어도 2026-12-01). 기존 후보 페이퍼 7일차 판정(11-19)은 예정대로.
- §1.1 코드 사실 확인: final_verdict 가 Pipeline.campaign_stats() 재계산(67행) ✓, signal_study.STRATIFY = (hour_vol, hour, none) ✓, program.set_phase 앞으로만 ✓.
- 단계 1.3: 문헌(Quantpedia 2023)의 24개 시각 탐색을 `session_premium` 시도로 계상 (log_exploration camp-000038, 24건; 프로그램 시도 40/400). 09:05 KST(C4)는 관행에서 온 값이라 계상하지 않음 — 표본 안에서 고른 통계가 아님.
