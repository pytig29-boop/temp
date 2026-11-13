# 트랙 E (R4) — 시계 전략 (계열 `session_premium`)

원문: `docs/prompts/opus_r4_plan.md` 단계 2·3 (그 내용이 이 지시서다. 숫자는 거기 적힌 값만).

- 선별 2개: E1 `research/campaign_templates/screens/r4e_session_2123utc_1h_screen.yaml` (21:00 UTC 매수, 1h 결정, 주 지평 120분),
  E2 `research/campaign_templates/screens/r4e_kst09_open_5m_screen.yaml` (00:05 UTC 매수, 5m 결정, 주 지평 60분). `stratify: vol`.
- 통과한 구성만 캠페인 1개씩: BTC-KRW, 진입 그대로, 필터 없음, ATR 청산 = 선별 제안값 (손절 MAE p75, 익절 MFE 중앙) 과 익절×2,
  최대 보유 = 주 지평, 전액, target Sharpe = 선별 z/√4.83 (작성 시 계산). 데이터 점검 보고서(단계 1.4) 조건 (ii) 해당 시 stress 순 Sharpe > 0 추가.
- 예산: 계열 30 (문헌 계상 24 + 선별 2 + 캠페인 4), Val 최대 2.
- 금지: 다른 시각·요일·추세·변동성 조건 판, 실패 후 방향·보유 변경 재등록, 검정력 실패 후 목표/격자 수정, 판정 코드 수정, 사람 전용 자료 열람.
- 도구: MCP `find_similar`, `screen_signals`, `power_check`, `create_and_run_campaign`, `get_campaign_report` 만.
