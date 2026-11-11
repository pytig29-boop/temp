# 트랙 A — 관리형 보유 (managed_hold), 주기 R1

운영자 지시서 (autonomous_research_plan §7.2·§7.4). 연구자 에이전트는 이 범위 안에서만 일한다.

- 계열: `managed_hold` (새 계열, 시도 0). 관문 프로필 `managed_hold` (D25, G5 능동 DSR 0.90).
- 순서: (1) 선별 `research/campaign_templates/screens/managed_hold_4h_u3_screen.yaml` (4 구성: span 42/90/180/360, band 0)
  → (2) 선별을 통과한 span 만 캠페인 `research/campaign_templates/managed_hold_4h_u3.yaml` (span 90/180 × band 0/100,
  4 스펙, `gate_profile: managed_hold`, `requires_screen_pass`). 통과 span 이 템플릿 격자와 다르면 통과한 것만 남긴 새 템플릿을
  사전등록(파라미터 늘리지 말 것).
- 예산: 선별 4 + 캠페인 ≤ 4 + 기준선 참조 스펙(4h EMA(180), band 0) 개발 구간 계산 1 (`log_exploration`, 계열 managed_hold).
- 금지: 격자 확장, 사다리·부분 크기 변경, 다른 블록 추가, Val 수치 열람(결과 코드만).
- 생존자가 나오면 운영자에게 보고 → skeptic → (G6 통과 시) judge + 페이퍼.
- 결과 정리: 오답노트 사실·해석(최대 3개), `research/reports/cycleR1_<날짜>.md` 의 트랙 A 절.
