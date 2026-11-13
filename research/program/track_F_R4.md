# 트랙 F (R4) — 변동성 조절 (계열 `vol_breakout`)

원문: `docs/prompts/opus_r4_plan.md` 단계 4.

- F-1 (운영자 직접): log_exploration 1건 → 기존 T1 후보 Dev MTM 일간 수익 × w_d = min(1, σ*/σ_{d-1}) (30일, σ* = 첫 365일 σ 중앙값),
  고정 배수 대조. 통과 규칙: Calmar(w) > 원래·고정 대조 AND Sharpe(w) ≥ 0.95×원래. 보고서 `research/reports/r4_vol_overlay_<날짜>.md`.
- F-2 (통과 시만): 실험 필터 `vol_cap(window, max_pct)` (격자 window [12,24,48], max_pct [0.5,0.67,0.8], 쓰는 값 (48, 0.67)) →
  선별 `screens/r4f_vol_breakout_volcap_4h_u3_screen.yaml` (4h, 3코인, donchian 48 + vol_cap, hour_vol, camp-000034 사실에서 파생).
- F-3 (선별 통과 시만): camp-000034 와 같은 청산·크기 + 필터, 스펙 2. 대조: 관문 통과 + Dev Calmar > 기존 후보, Sharpe 낮지 않음.
- 예산: 계열 추가 4, Val 최대 1. 금지: 연속 비중(엔진 변경), 다른 파라미터 값.
