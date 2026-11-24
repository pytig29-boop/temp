# 최종 보고 부록 (자동 생성 — 손으로 고치지 않음)

생성 2026-11-24T04:11:45+00:00 · 집계 시작 원장 처음

## 1. 시도 수

| 계열 | 시도(고유 캠페인×스펙) |
|---|---|
| krw_premium | 1 |
| liq_meanrev | 68 |
| managed_hold | 4 |
| session_premium | 28 |
| vol_breakout | 104 |
| xasset_btc_lead | 4 |
| xvenue_upbit_lead | 16 |

합계 **225**, 기준 느슨화 L = 0

## 2. 캠페인 깔때기

| 캠페인 | 계열 | 스펙 | 후보 | 개발 통과 | 검증 통과 |
|---|---|---|---|---|---|
| camp-000001 | xvenue_upbit_lead |  |  |  |  |
| camp-000002 | xvenue_upbit_lead |  |  |  |  |
| camp-000003 | vol_breakout |  |  |  |  |
| camp-000004 | liq_meanrev |  |  |  |  |
| camp-000005 | liq_meanrev |  | 2 | 0 | 0 |
| camp-000006 | vol_breakout |  | 1 | 0 | 0 |
| camp-000007 | xvenue_upbit_lead |  | 1 | 0 | 0 |
| camp-000008 | liq_meanrev |  | 2 | 0 | 0 |
| camp-000009 | vol_breakout |  |  |  |  |
| camp-000010 | vol_breakout |  |  |  |  |
| camp-000011 | liq_meanrev |  |  |  |  |
| camp-000012 | liq_meanrev |  |  |  |  |
| camp-000013 | vol_breakout |  | 1 | 1 | 0 |
| camp-000014 | vol_breakout |  | 1 | 0 | 0 |
| camp-000015 | vol_breakout |  |  |  |  |
| camp-000016 | vol_breakout |  |  |  |  |
| camp-000017 | vol_breakout |  | 1 | 1 | 0 |
| camp-000018 | vol_breakout |  |  |  |  |
| camp-000019 | vol_breakout |  |  |  |  |
| camp-000020 | vol_breakout |  |  |  |  |
| camp-000021 | vol_breakout |  |  |  |  |
| camp-000022 | liq_meanrev |  |  |  |  |
| camp-000023 | liq_meanrev |  |  |  |  |
| camp-000024 | vol_breakout |  | 1 | 0 | 0 |
| camp-000025 | liq_meanrev |  |  |  |  |
| camp-000026 | liq_meanrev |  |  |  |  |
| camp-000027 | vol_breakout |  |  |  |  |
| camp-000028 | liq_meanrev |  | 1 | 0 | 0 |
| camp-000029 | liq_meanrev |  | 1 | 0 | 0 |
| camp-000030 | vol_breakout |  | 1 | 0 | 0 |
| camp-000031 | vol_breakout |  | 1 | 0 | 0 |
| camp-000032 | managed_hold |  |  |  |  |
| camp-000033 | vol_breakout |  |  |  |  |
| camp-000034 | vol_breakout |  | 1 | 1 | 1 |
| camp-000035 | xasset_btc_lead |  |  |  |  |
| camp-000036 | xasset_btc_lead |  | 1 | 0 | 0 |
| camp-000037 | liq_meanrev |  |  |  |  |
| camp-000038 | session_premium |  |  |  |  |
| camp-000039 | vol_breakout |  |  |  |  |
| camp-000040 | session_premium |  |  |  |  |
| camp-000041 | session_premium |  |  |  |  |
| camp-000042 | krw_premium |  |  |  |  |
| camp-000043 | vol_breakout |  |  |  |  |
| camp-000044 | session_premium |  | 1 | 0 | 0 |

## 3. 기준 변경 이력

| id | 시각 | 종류 | 구간 | 키 | 인증 | 이유 |
|---|---|---|---|---|---|---|
| crv-000001 | 2026-11-12T01:39:01+00:00 | initial | paper+val | gates.G6.size_includes_dev, gates.G6.worst_month_min, gates.G6.max_val_per_campaign, gates.G8.paper_mode | cert-rs001 | rs-001 initial autonomous ruleset (G6 size incl. Dev drawdown and worst month -8%, 1 Val per campaign, G8 replay paper) |
| crv-000002 | 2026-11-12T02:55:12+00:00 | neutral | dev | code:research/campaign.py aux loaders (coinone_btc) | cert-rs002 | track C: cross-symbol aux coinone_btc; verdicts unchanged on bank-v1, consistency 51/51 |
| crv-000003 | 2026-11-12T03:18:01+00:00 | neutral | dev | code: LIVE_AUX coinone_btc + paper wiring | cert-rs003 | track C: coinone_btc live-available (paper shares BTC sleeve history); bank verdicts identical, consistency 51/51 |

## 4. 판정 데이터 사용

검증(Val) 새 사용 3회 · 잠금 개봉 1회 (자율 1)

## 5. 단계 전환·정비 창구

| 시각 | 종류 | 내용 |
|---|---|---|
| 2026-11-12T01:56:19+00:00 | phase | {"frm": "build", "to": "research", "reason": "Phase 0-3 done: rs-001 locked, dry run passed"} |
| 2026-11-12T02:31:35+00:00 | maintenance | {"op": "open", "reason": "track C: coinone_btc aux for alt sleeves + experimental btc_trend filter block"} |
| 2026-11-12T02:55:14+00:00 | maintenance | {"op": "close", "reason": ""} |
| 2026-11-12T02:55:38+00:00 | maintenance | {"op": "open", "reason": "track C: make coinone_btc live-available (paper aux wiring + LIVE_AUX)"} |
| 2026-11-12T03:18:03+00:00 | maintenance | {"op": "close", "reason": ""} |
| 2026-11-24T04:10:03+00:00 | phase | {"frm": "research", "to": "final", "reason": "R4 closed with no new T1; sole candidate r1b_..._0dce65e919_v1 passed skeptic/judge/paper; plan §5.3 final round"} |
| 2026-11-24T04:11:43+00:00 | phase | {"frm": "final", "to": "report", "reason": "final round 1: 0 candidates passed; no round 2 before 2026-12-01 deadline"} |

## 6. 레드팀 리포트

(없음)


## 7. 바닥 규칙

해시 `f3df533cf92e637269bd6a83277b23b6a20d0324e7963f040a9c3a38be874f2c` · 이력 1줄


## 8. 탈락 후보 (graveyard)

| 캠페인 | 스펙 | 탈락 관문 |
|---|---|---|
| camp-000005 | c1_liq_meanrev_atr_v1_ff60d1a7c3 | G4 |
| camp-000005 | c1_liq_meanrev_atr_v1_0b89e1f404 | G3 |
| camp-000006 | c1_vol_breakout_atr_v1_dd008ca9c6 | G3 |
| camp-000007 | c1_xvenue_lead_5m_atr_v1_ea21283709 | G1 |
| camp-000008 | c2_liq_meanrev_wide_atr_v1_728b01519b | G4 |
| camp-000008 | c2_liq_meanrev_wide_atr_v1_7cf92d03f9 | G5 |
| camp-000013 | c2b_vol_breakout_atrch_1h_atr_72bbc9dae5 | fail |
| camp-000014 | c2b_vol_breakout_donch_1h_atr_ca225bbff7 | G5 |
| camp-000017 | c3_vol_breakout_atrch_1h_hivol_atr_3247d3023d | fail |
| camp-000024 | c3c_vol_breakout_roc_volz_1h_atr_36b866681f | G2 |
| camp-000028 | c4_liq_meanrev_ladder_15m_u3_7207679868 | G2 |
| camp-000029 | c4_liq_meanrev_ladder_5m_u3_1fd10db577 | G2 |
| camp-000030 | c4_vol_breakout_atrch_1h_u3_half_4fc37395fd | G3 |
| camp-000031 | c4_vol_breakout_roc_1h_u3_half_91a6706e89 | G2 |
| camp-000036 | r2c_xasset_btc_lead_donch_4h_u3_half_d27e04b72e | G5 |
| camp-000044 | r4e_session_2123utc_1h_320ca54e51 | G3 |
