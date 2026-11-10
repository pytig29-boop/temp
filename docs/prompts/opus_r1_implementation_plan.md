# 관문 재검토 후속 — 구현 계획 (Opus 실행용)

> 작성 2026-11-10. 저장소 `pytig29-boop/temp`, 브랜치 `claude/btc-scalping-quant-system-o6n5w7`.
> 이 문서를 처음부터 끝까지 읽고 **Phase 순서대로** 수행한다. 각 Phase 끝에서 전체 테스트(`uv run pytest -q`)를 통과시키고 커밋·푸시한다.

## 0. 사용자 결정 (2026-11-10 채팅, "추천대로 가자")

근거: `research/reports/gate_review_20261110.md` §7, 합성 근거 `research/reports/gate_review_calibration_20261110.md`.

| # | 결정 | 분류 |
|---|---|---|
| Q1 | **R1 + plus 채택**: (b) G6 스트레스 비용 Sharpe>0 조건 제거, (c) 검증 구간 증거 = 개발+검증 결합(Stouffer p ≤ 0.05 그리고 Val Sharpe > 0), plus: G3 이웃 비 0.5 → 0.25, 알파 t 2.0 → 1.5 | ② |
| Q2 | **낙폭 초과 시 탈락 대신 크기 조절**, 크기 하한 25% | ③ |
| Q3 | 낙폭 한도 20% 유지 | ③ |
| Q4 | **단계별 승격 도입**: T0 연구 → T1 페이퍼 → T2 소액 실전(자본 10%) → T3 증액 | 구조 |
| Q5 | 오탐 약 1% 수준(R1)으로 시작, 6개월 뒤 재검토 | ② |
| Q6 | 위험 관리형 보유(managed_hold)는 **보유 대비** 기준의 전용 관문 세트 | ② |
| Q7 | XRP 스트레스 비용 14 → 20bp **상향 승인**. BTC 비용 하향은 72시간 표본까지 **보류** | ① |

승인 기록 방식: 기존과 같다 — 제안 파일(`btc_quant.params.proposals.write_proposal`) → `approve(..., by="user (explicit chat approval 2026-11-10)", confirm=lambda e,w: None, note="사용자가 채팅에서 승인; 비대화형 확인 사용")`. 예시: `research/param_approvals/`.

## 1. 지켜야 할 원칙

1. `docs/prompts/fable_gate_review.md` §1 그대로: 골대 옮기기 금지, 소급 적용 금지(모든 변경은 **다음 캠페인부터**, `gate_config_for_campaign`), 검증·잠금 구간 세부(사람 전용 경로) 열람 금지, 실제 연구 결과를 기준 변경 근거로 쓰지 않음.
2. 새 옵션의 **기본값은 현재 동작**. 승인 적용(설정 값 전환)은 일치 점검(Phase 2)을 통과한 뒤에만.
3. 이미 검증 구간에서 떨어진 후보(camp-000013, camp-000017)는 재평가되지 않는다 — 같은 logic_hash 의 Val 재사용 규칙(이전 판정이 통과였을 때만 재사용)을 깨지 말 것. 테스트로 확인.
4. Phase 5 이전에는 공식 캠페인을 돌리지 않는다(원장 시도·Val 사용 금지).
5. 컨테이너가 예고 없이 재시작된다: 긴 실행은 재개 가능하게, Phase 중간에도 자주 커밋. 실시간 수집기(`collector_watchdog.sh`, 스크래치패드)가 꺼져 있으면 다시 켠다.
6. 보고는 한국어·쉬운 말. 숫자에는 표본 크기 포함.

## 2. 먼저 읽을 것

`research/reports/gate_review_20261110.md`, `gate_review_calibration_20261110.md`(§4, §6, §7, §10, §12), `docs/PARAMS.md`, `docs/PLAN.md`(§0, §5, §6, §7), `docs/MANAGED_HOLD.md`, `config/{gates,params,costs}.yaml`,
`src/btc_quant/evaluation/gates.py`, `src/btc_quant/research/{pipeline,gate_review_study,robustness,forward,report,campaign,calibration_study}.py`, `src/btc_quant/params/*`, `src/btc_quant/registry/strategies.py`, `src/btc_quant/paper/service.py`.

---

## Phase 1 — 버그·기반 수정 (기준과 무관)

| 작업 | 완료 조건 |
|---|---|
| 1.1 `backtest/fast.py::_mtm_core` 0-나눗셈: 자산이 0 이하가 되면 그날 −100%, 이후 수익 0 (기준 엔진 경로도 같게) | 재현 테스트 + 두 엔진 일치 테스트. `gate_review_study.install_mtm_guard` 는 제거하거나 무해하게 |
| 1.2 임시 저장소 설정 해석 통일: 캠페인과 파이프라인이 **같은 루트**에서 gates/costs 를 읽는다(임시 루트에 없으면 둘 다 저장소 설정) | 테스트: 캠페인 표 Sharpe = 파이프라인 재계산 Sharpe |
| 1.3 이전 합성 리포트 4개(`pipeline_calibration.md`, `calibration_regime_20261110.md`, `calibration_regime_check_20261110.md`, `val_length_design_20261110.md`) 맨 위에 "실현 기준 회계로 실행됨, MTM 기준 수치는 gate_review_calibration 참고" 주석 (코드로 확인된 경우에만, 아니면 "미확인") | 문서 |
| 1.4 XRP 스트레스 비용 상향(Q7): `coinone_stress@XRP-KRW` 왕복 14 → 20.2bp (스프레드 6.7 유지, 슬립 조정), 업비트 XRP 스트레스도 같은 규칙(p99×1.5)으로 점검해 낮으면 상향. 근거 = 4시간 WS 표본(리포트 §4.1). 제안 → 승인 기록 → 적용 | `costs.yaml` 검증 통과, 승인 기록, 근거 리포트 `research/reports/params_costs_xrp_stress_20261110.md` |

## Phase 2 — R1 옵션 구현 (기본 = 현재 동작)

| 옵션 (config/gates.yaml) | 의미 |
|---|---|
| `G6.stress_mode: sharpe \| off` | off = 스트레스 비용 Val Sharpe 는 계산·표시만, 통과 조건 아님 |
| `G6.val_budget.evidence: holm \| fixed \| combined` (+ `fixed_p`, `combined_alpha: 0.05`) | combined = `gate_review_study.py` 의 오프라인 'Stouffer' 변형과 **같은 식**(z_dev = DSR 증거, z_val = Val 부트스트랩 p), 결합 p ≤ combined_alpha 그리고 Val Sharpe > 0. Val 사용 기록(k)·상한·재사용 규칙은 그대로 |
| `G6.drawdown_mode: reject \| size` + `G6.min_position_scale` (③, 0.25) | size = `position_scale = min(1, max_mdd/ValMDD, max_mc_mdd95/MCMDD95)`; `position_scale ≥ min_position_scale` 이면 낙폭 조건 통과. 후보·위험 사이드카(`*.risk.yaml`)에 `position_scale` 기록, §7 리스크 한도는 축소된 수익 기준. 페이퍼·전진 관찰이 notional/수익에 적용(없으면 1.0). 에이전트용 리포트에는 통과/탈락만 |

추가 작업:
- 새 키를 `config/params.yaml` 에 등록(②: stress_mode, evidence, fixed_p, combined_alpha, drawdown_mode / ③: min_position_scale). 레지스트리 검증 통과.
- **오프라인 평가기에 size 모드 추가** → 기존 원시 기록(`/tmp/claude-0/-home-user-temp/6c68d243-afba-544a-81d3-998fbfe8910d/scratchpad/gate_review/raw/`; 없으면 `gate_review_study run` 으로 재생성, 약 2시간)으로 하한 0.25 / 0.5 의 오탐·검출 표 계산 → `research/reports/gate_review_calibration_addendum_20261110.md`.
- **일치 점검(필수)**: 설정 = R1+plus+size(0.25) 로 실제 파이프라인을 합성 시장 ≥ 24개에서 돌려 오프라인 평가기와 후보 단위로 일치하는지 확인. 불일치 0 이어야 Phase 3 진행.
- 테스트: 기본값에서 기존 결과 불변, 각 모드 의미, 사이드카·페이퍼·전진 적용, Val 재사용 규칙 불변.

**중단 조건**: 추가 표에서 R1+plus+size(0.25) 의 전체 오탐이 5% 를 넘거나 어려운 귀무 오탐 상한이 리포트 수치(1/74)보다 뚜렷이 나쁘면(예: ≥ 4/74) 적용하지 말고 사용자에게 보고.

## Phase 3 — 승인 적용 (다음 캠페인부터)

제안 파일 3개 생성 → 채팅 승인 기록 → 적용:
1. R1(②): `G6.stress_mode → off`, `G6.val_budget.evidence → combined`
2. R1-size(③): `G6.drawdown_mode → size`, `min_position_scale 0.25` (max_mdd·max_mc_mdd95 는 0.20 유지)
3. R1-plus(②): `G3.neighbor_median_ratio 0.5 → 0.25`, `robustness.alpha_min_tstat 2.0 → 1.5`

근거 참조는 합성 리포트만(`gate_review_calibration_20261110.md`, addendum). 적용 후: `docs/PLAN.md` 결정표에 D20(R1), D21(크기 조절), D22(R1-plus), D23(단계별 승격), D24(XRP 스트레스) 추가, `docs/PARAMS.md` 현황 갱신, 연구 에이전트·skills 문서(`.claude/agents`, `.claude/skills`, `docs/AI_RESEARCH.md`)에 새 관문 설명 반영. 테스트: 새 캠페인은 새 기준, 기존 캠페인 재계산은 옛 기준.

## Phase 4 — 단계별 승격 (Q4)

| 단계 | 진입 조건 | 자본 |
|---|---|---|
| T0 연구 | — | 0 |
| T1 페이퍼 | 개발 구간 관문 + 견고성 + G6(R1) 통과 = 지금의 `validated` | 0 |
| T2 소액 실전 | T1 + G8(페이퍼 ≥ 3주·30거래·체결 품질) + 잠금 구간 '모순 없음'(G7, 아래) + **사람 승인** | 총자본의 10% × position_scale |
| T3 증액 | T2 + 누적 전진 증거(사전 등록 순차 검정) + **사람 승인** | 단계적(예: 10 → 30 → 100%) |

구현:
- `registry/strategies.py`: 상태에 tier(T1/T2/T3)와 `capital_fraction` 을 기록, 승격은 사람 전용 admin CLI(`btc-quant-admin promote-tier`), 강등·정지는 자동(리스크 관리자). 단계 이력은 원장 transitions.
- `config/params.yaml` ③: `risk_prefs.tier_capital: {T2: 0.10, T3: [0.30, 1.0]}` (사용자 결정 기록).
- 페이퍼·리스크 관리자가 `capital_fraction × position_scale` 을 notional 상한으로 강제.
- 연구 모드 hook·MCP: 승격 도구 없음 유지, admin 명령 차단 목록에 추가.
- **G7(잠금) 재설계는 합성 검증 후 제안**: `gate_review_study` 에 잠금 구간(6개월)을 추가해 (i) 현행 단독 Holm, (ii) '모순 없음'(잠금 Sharpe > 0), (iii) 개발+검증+잠금 결합 증거의 오탐·검출을 측정 → 리포트 `research/reports/g7_design_<날짜>.md` + 제안 파일(적용은 사용자 승인 대기).
- 실거래 주문 실행기(M10)는 **만들지 않는다** — T2 는 정의·강제 장치까지만. 실행기는 별도 승인 과제.
- 문서: `docs/PLAN.md` §6 갱신, `docs/OPERATIONS.md` 에 단계별 운영 절차.

## Phase 5 — managed_hold 전용 관문 (Q6)

`docs/MANAGED_HOLD.md` 의 Q1~Q11 을 설계로 확정:
- 관문 세트 `gate_profile: managed_hold` (기본 프로필은 그대로): 표본 = 전환 횟수·일수(G1), 귀무 = 같은 노출의 무작위 전환 시점(순환 이동/런 길이 순열)(G2), 유의성·DSR·결합 증거는 **3코인 동일비중 보유 대비 초과(액티브) 수익**에 적용(G5·G6), 낙폭 = 크기 조절 + 보유 대비 낙폭 비율 보고, G8 = 기간 기준.
- 합성 보정: 보유 대비 귀무(추세 필터가 무의미한 시장) / 심은 추세 지속 시장으로 오탐·검출 측정, 목표 오탐 ≤ 5%. 리포트 + 제안 파일(② 신규 프로필) → **사용자 승인 대기**.
- EMA 워밍업 상한(120일) 문제 해결(페이퍼·전진에서 4시간 EMA(180) 재현).
- 승인 후: 준비된 템플릿(`research/campaign_templates/screens/managed_hold_4h_u3_screen.yaml`, `managed_hold_4h_u3.yaml`)으로 첫 캠페인.

## Phase 6 — 연구 재개 (R1 적용 후)

1. 새 관문 아래 연구 사이클(연구자 에이전트, MCP 도구만): 동결되지 않은 계열 + managed_hold(Phase 5 승인 시). 이미 Val 에서 떨어진 스펙은 재사용 불가.
2. validated 가 나오면: 심사자(skeptic) → T1 페이퍼. 실험 블록 사용 후보는 사람의 블록 검토 필요.
3. 매 사이클 리포트 `research/reports/cycle<N>_<날짜>.md`, 오답노트 기록.

## Phase 7 — 후속 (우선순위 낮음, 별도 승인)

- DSR 시도 수 공식을 '계열 내 캠페인별 실질 군집 수의 합'으로 바꾸는 합성 검증(pm-000007).
- 크기 조절 하한·낙폭 한도 선택지 표(`params.preferences`) 갱신.
- 상시 실행 환경(PC/VPS) — 비용 72시간 확정(BTC 하향 재검토), 미측정 측정값 16개, 전진 관찰, 지정가 역선택 측정이 모두 여기에 묶여 있음. 사용자 결정 대기.
- 6개월 뒤 관문 재검토(Q5): 통과 수·T1 결과로 오탐 수준 재평가.

## 사용자에게 다시 물어야 하는 지점

| 시점 | 질문 |
|---|---|
| Phase 2 중단 조건 발생 시 | 적용 보류 여부 |
| Phase 4 G7 설계 결과 | 잠금 구간 판정 방식 선택 |
| Phase 5 합성 결과 | managed_hold 관문 프로필 승인 |
| T1 → T2 승격 때마다 | 실제 자금 투입 승인 (실거래 실행기 구현 승인 포함) |

## 완료 정의

- Phase 1~3: R1+plus+size 가 다음 캠페인부터 적용, 일치 점검 통과, 승인 기록·문서 갱신, 전체 테스트 녹색.
- Phase 4: tier 상태·자본 상한 강제·admin 명령·G7 설계 리포트.
- Phase 5: managed_hold 프로필 제안(승인 대기) 또는 승인 후 첫 캠페인 리포트.
- 각 Phase 끝에 사용자에게 한국어 요약 + 휴대폰 알림.
