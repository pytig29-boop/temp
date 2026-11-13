# 트랙 G (R4) — 김치 프리미엄 데이터 준비 (선별 없음)

원문: `docs/prompts/opus_r4_plan.md` 단계 5. 2시간 상한.

1. 원/달러 일별 환율 2021-01-01~2025-10-31 공개 원천 수집 시도 (스크립트·출처·받은 날짜 기록). 2시간 안에 못 받으면 "수집 불가".
2. 바이낸스 외 실시간 해외 BTC 달러 가격 1회 호출 확인.
3. 1 성공 시만: log_exploration(krw_premium, 1) 뒤 4h z ≤ −2 독립 사건 수만 셈 (수익 미계산).
4. 보고서 `research/reports/r4_krw_premium_data_<날짜>.md`.
