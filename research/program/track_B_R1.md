# 트랙 B — 느린 추세(스윙), 주기 R1: 선별만

운영자 지시서 (autonomous_research_plan §7.2). 계열 `vol_breakout`(파생, 누적 시도 96 — 필요한 Sharpe 를 먼저 `power_check` 로 계산).

- 4시간 결정 주기, 3코인 풀링, 절반 크기(entry_fraction 0.5), 지평 1~7일.
- R1 은 **선별만**: 4h 돌파·모멘텀 블록(atr_channel_breakout, donchian_breakout, roc_momentum, composite momentum) 중
  메커니즘(M3 장기판: 추세 추종 자금·청산 연쇄)과 맞는 것 ≤ 8 구성. 기존 선별 `c2b_vol_breakout_4h_screen.yaml` 결과(오답노트 사실)를
  먼저 읽고 반복하지 말 것.
- 통과 구성은 R2 에서 캠페인(격자 2×2 이하, 청산 ≤ 4종).
- 금지: 1시간 이하 결정 주기, 사다리, 거래량 블록.
