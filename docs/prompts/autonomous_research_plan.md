# 자율 연구 체제 — 실행 계획 (Opus 실행용)

> 작성 2026-11-11. 저장소 `pytig29-boop/temp`, 브랜치 `claude/btc-scalping-quant-system-o6n5w7`.
> **이 문서는 계획이다.** 작성 시점에 코드·설정·원장은 바뀌지 않았고, 캠페인·선별·Val·잠금 사용도 없다.
> 실행자는 이 문서를 처음부터 끝까지 읽고 **Phase 0 → 7 순서대로** 수행한다. 사용자에게 묻지 않는다.
> 막히면 §12 "스스로 정하는 규칙"을 따른다. 사용자에게 가는 것은 §9 의 알림과 §10 의 최종 보고 한 번뿐이다.
> 직전 계획 `docs/prompts/opus_r1_implementation_plan.md` 의 Phase 6~7 은 이 문서로 대체한다.

---

## 0. 한눈에 보기

| 무엇 | 내용 |
|---|---|
| 목표 | "수익이 나고 손실 위험이 작은 전략" 묶음(최대 3개)을 찾아 **사용자에게 한 번 승인**받는다. 없으면 "없음 — 보유가 낫다/현금이 낫다"로 끝낸다 |
| 누가 결정 | 연구 계열·가설·캠페인·구조 변경·판정 기준 변경·후보 선택 = **AI**. 바닥 규칙과 실제 돈 = **사용자** |
| 수익의 진위 | 과거 데이터 중 고르는 데 쓰지 않은 12개월(검증 6개월 + 잠금 6개월)로 판정. 페이퍼 1~2주는 "백테스트와 같게 움직이는가"만 본다 |
| 안전장치 | 바닥 규칙(A) · 기준 변경 자동 인증(B) · 사전 잠금(C) · 시도 전부 계수(D) · 판정 데이터 예산(E) · 독립 심사 + 레드팀(F) · 미래 데이터 최종 심판(G) · 전부 보고(H) · 추가 3개(I~K) |
| 기간 | 구현·인증 약 5~7일 → 연구 2~3주(최대 5주) → 결선 1~2일 → 보고. 페이퍼는 연구와 겹쳐 돈다. 전체 약 4~6주 |

단계 요약

| Phase | 이름 | 소요(예상) | 끝나는 조건 |
|---|---|---|---|
| 0 | 복원·운영 기반·결정 기록 | 0.5~1일 | 데이터 복원, 수집기·재개 장치 동작, PLAN D26~D33 기록 |
| 1 | 안전장치 구현 | 2~3일 | §4 의 코드·테스트 전부 녹색 (기존 2,572개 + 신규) |
| 2 | 판정 구조 구현 + 합성 인증 | 1.5~2.5일(계산 포함) | 기준 묶음 `rs-001` 인증 통과·잠금 |
| 3 | 총연습(합성 저장소) | 0.5일 | 캠페인→T1→페이퍼→결선→보고가 합성에서 끝까지 돈다 |
| 4 | 연구 프로그램 | 2~3주 (최대 5주) | §7.6 중단 조건 중 하나 |
| 5 | 결선(잠금 구간 판정) | 1~2일 + 남은 페이퍼 | 최종 후보 묶음 또는 "없음" 확정 |
| 6 | 최종 보고 | 0.5일 | §10 리포트 + 알림 → 사용자 승인 대기 |
| 7 | 승인 대기 중 감시 | 승인까지 | 페이퍼 계속, 이탈 시 자동 강등·알림 |

---

## 1. 사용자 결정 (2026-11-11 채팅) — 전제

1. **자율 위임**: 중간 결정은 AI. 사용자는 **최종 후보 묶음만 한 번** 승인. 사소한 확인 질문 금지.
2. **판정 기준도 AI 가 바꿀 수 있다.** 대신 §4 안전장치 필수.
3. **최대 낙폭 20% 유지**(운용 크기 기준, 넘으면 크기 조절, 크기 하한 25% 미만이면 탈락 — D21 그대로).
4. **페이퍼 1~2주** — 수익 증명이 아니라 "백테스트와 같게 움직이는가".
5. **실제 돈**(T2 승격, 실거래 실행기)은 계속 사용자 승인.

이 결정이 바꾸는 기존 규칙

| 기존 | 바뀌는 것 |
|---|---|
| class ② 증거 기준 변경 = 사람 승인 (docs/PARAMS.md) | **자동 인증 통과 + 원장 기록**으로 대체 (§4-B). 바닥 규칙에 닿는 값만 사용자 |
| 잠금 개봉 = AI 요청 + 사람 승인 (PLAN §5.3, D4) | **기계 검사로 발급되는 자율 개봉 승인**으로 대체 (§4-C·E). 결선에서만, 예산 5회 |
| G7 = 누적 Holm 유지 (D25-1) | 사용자 초안 §2-1 "세 구간 결합 증거"에 따라 **3구간 결합 + 표본 밖 12개월 검정**으로 대체 (§5.2). 인증 통과가 조건 |
| G8 페이퍼 ≥ 3주·30거래 (managed_hold 8주) | **7일(연장 14일)** + 일치·비용·손익 범위 판정 (§5.4) |
| 실험 블록 후보는 잠금 전 사람 검토 (D11) | **독립 심사 에이전트의 블록 심사**로 대체 (§4-F) |
| 새 계열 이름·동결 계열 해제 = 사람 | AI 단독 + 사유 기록 (§3) |
| "아무것도 통과 못 하면 기준은 낮추지 않는다" (PLAN §10) | 기준 변경은 가능하되 §4-B·C·D 를 통과해야 하고 느슨화는 **프로그램 전체 3회까지** |

그대로 남는 것: 낙폭 20%·하한 25%, 고정 3자산·롱 온리 현물, 데이터 분할, 비용 하한, 원장 전수 기록, Val/잠금 수치는 연구 에이전트에게 결과 코드만, 이미 Val 에서 떨어진 스펙의 재평가 금지, 단계별 승격의 T2 이상 사람 승인.

---

## 2. 먼저 읽을 것 (실행자)

`docs/PLAN.md`(§0, §5, §6, §7), `docs/PARAMS.md`, `docs/AI_RESEARCH.md`, `docs/MCP.md`, `docs/MANAGED_HOLD.md`, `docs/OPERATIONS.md`(§11, §12),
`research/reports/{gate_review_20261110, gate_review_calibration_20261110(§4·§6·§7·§10·§12), gate_review_calibration_addendum_20261110, g7_design_20261110, managed_hold_profile_20261110, cycle4_20261110}.md`,
`research/lessons/LESSONS.md`, `research/knowledge/*.md`, `config/{gates,params,costs,splits}.yaml`,
`src/btc_quant/evaluation/gates.py`, `src/btc_quant/research/{pipeline,campaign,gate_review_study,g7_study,managed_hold_gates,managed_hold_study,robustness,forward,lessons,power}.py`,
`src/btc_quant/registry/{tiers,strategies,ledger,splits,admin_cli}.py`, `src/btc_quant/params/{registry,proposals,criteria}.py`,
`src/btc_quant/paper/service.py`, `src/btc_quant/mcp/tools.py`, `.claude/{agents,skills,hooks,settings.json}`.

읽으면 안 되는 것(연구 단계 내내): `research/reports/human/**`, `research/campaigns/*/pipeline.json`, 원장의 `runs/val_evaluations/lockbox/transitions.jsonl` 직접 열람, `strategies/**/*.risk.yaml`, 잠금 구간 원천 데이터. Phase 6(보고)에서 프로그램 종료 기록을 남긴 뒤에만 사람용 리포트를 읽는다(§10).

현재 사실(작성 시 확인): 원장 캠페인 31개(선별 포함), 검증 통과 0, Val 사용 2/20, 잠금 개봉 0/10. 계열 누적 시도 — `liq_meanrev` 66, `vol_breakout` 96, `xvenue_upbit_lead` 16, `managed_hold` 0. **`data/` 는 git 에 없고 이 컨테이너에서 비어 있다**(`.gitignore /data/*`), `runtime/` 도 git 밖이다. **잠금 구간을 실제로 평가하는 실행 코드는 아직 없다**(`DataAccess.open_lockbox` 를 부르는 곳이 없음) — Phase 2 에서 만든다.

---

## 3. 자율 권한 문서화

### 3.1 권한 표 (새 문서 `docs/AUTONOMY.md` 로 옮겨 적고 PLAN §3.3·§8 에서 가리킨다)

| 항목 | AI 단독 | 기록 필수 | 사용자 승인 |
|---|---|---|---|
| 가설·계열 선택, 캠페인 설계·실행, 선별, 탐색 계산 | ✅ | 원장(사전등록) | – |
| 새 계열 만들기, 동결 계열 해제 | ✅ | `research/program/families.yaml` + 원장 가설 (사유·메커니즘·재검토 조건) | – |
| 새 블록(실험 블록) 작성·사용 | ✅ | `docs/BLOCKS.md` + 독립 심사 기록 `research/block_reviews/<블록>.md` | – |
| 엔진·평가·레지스트리 코드 수정(버그·구조 변경) | ✅ (정비 창구 §4-C) | 기준 묶음 해시 변경 → 재인증 기록 | – |
| 증거 기준(class ②) 엄격화 | ✅ | `criteria_revisions` 원장 + 인증 기록 | – |
| 증거 기준(class ②) 느슨화·새 판정 방식 | ✅ 단 **인증 통과**·주 1회 점검 때만·**프로그램 전체 3회** | 위 + 시도 수 벌점(L) | – |
| 시장 측정치(class ①) 엄격화 | ✅ (기존 자동) | costs.yaml 버전 | – |
| 시장 측정치 느슨화 | ✅ 단 바닥 값 이상 + 표본 '확정'(72시간·24 KST 시각·7일) | 제안·적용 기록 | 바닥 아래로는 사용자 |
| 위험 선호(class ③) 엄격화(더 작은 낙폭·더 작은 크기) | ✅ | 기록 | – |
| 위험 선호 느슨화 / 바닥 규칙 변경 | – | – | ✅ |
| 페이퍼 올리기·내리기, T0↔T1 | ✅ (관문 코드) | 원장 transitions | – |
| 잠금 구간 개봉 | ✅ 결선에서만, 기계 검사 통과 시, **5회까지** | 잠금 원장 + 규칙 잠금 id | 예산 증액·분할 변경은 사용자 |
| 최종 후보 묶음 선택 | ✅ (사전 등록 순위 규칙) | 최종 리포트 | **한 번 승인** |
| T2 이상 승격, 실거래 실행기 구현, 주문, 키 | – | – | ✅ |
| 데이터 분할(`config/splits.yaml`), 자산 목록, 롱 온리·현물 | – | – | ✅ |

### 3.2 `docs/PLAN.md` 결정 기록에 추가할 행 (Phase 0 에서 문서만 수정)

| # | 항목 | 요지 |
|---|---|---|
| D26 | 자율 위임 | 사용자 결정 2026-11-11. 권한 표 = docs/AUTONOMY.md. 최종 후보 묶음만 승인 |
| D27 | 최종 판정 F1~F5·결선 | §5.2. 인증 `rs-001` 통과 후 효력. D25(1)·§5.3 Holm 을 대체 |
| D28 | 페이퍼 7~14일 | §5.4. G8 개정(사용자 결정 4) |
| D29 | 바닥 규칙 | `config/floor.yaml` — 사용자만 변경 |
| D30 | 기준 변경 자동 인증 | class ② "사람 승인" → 합성 인증(오탐 ≤ 2%) + 시도 계수 + 사전 잠금 |
| D31 | 잠금 자율 개봉·판정 예산 | 결선에서만, Val 추가 10회·잠금 5회 |
| D32 | 실험 블록 독립 심사 | D11 의 사람 검토 대체 |
| D33 | 가정한 값 | §13 의 표(사용자가 나중에 바꿀 수 있는 위험 선호 가정) |

PLAN §3.3, §5.2(G7·G8 행), §5.3, §6.1(T2 조건의 G7·G8 설명), §8, §10 "아무것도 통과하지 못하면" 문단도 같은 내용으로 고친다. 효력 시점: 문서는 Phase 0, 실제 설정 전환은 Phase 2 인증 통과 뒤.

### 3.3 "class ② 사람 승인"을 대체하는 수정 범위

| 대상 | 수정 |
|---|---|
| `docs/PARAMS.md` | §1 표의 ② 적용 규칙을 "합성 인증 통과 + 원장 기록, 느슨화는 주간 점검·3회 상한·벌점 L"로. ③ 은 "엄격화는 AI, 느슨화·바닥은 사용자". §3 에 `auto-apply` 흐름 추가. 기존 `approve`(대화형) 경로는 사용자용으로 남긴다 |
| `src/btc_quant/params/` | 새 `floor.py`, `ruleset.py`, `autoapply.py`; CLI 하위 명령 `floor-verify`, `ruleset {hash,lock,status}`, `auto-apply <제안 id>`. `proposals._apply` 재사용, `by="ai:autonomy (cert-<id>)"` |
| `config/params.yaml` | 새 키 등록(§4·§5 의 모든 새 설정). `owner` 값에 `ai-certified` 추가(② 중 바닥이 아닌 것), 바닥 항목은 `owner: user-floor` |
| `.claude/hooks/guard.py` | §4-A 의 '바닥 보호(항상)' + §4-I 의 '자율 모드' 규칙 추가. 연구 모드 규칙은 하위 에이전트용으로 유지 |
| `src/btc_quant/mcp/tools.py`, `docs/MCP.md` | 읽기 도구 `program_status`(예산·단계·기준 묶음 id) 1개 추가. **잠금 개봉·승인·적용 도구는 계속 없음**(`tests/test_mcp_safety.py` 유지). `propose_criterion` 설명을 "제안 → 인증 → 주간 점검 때 적용"으로. `request_lockbox_eval` 은 "결선 후보 추천 기록"으로 의미만 바꿈(사람 승인 문구 삭제) |
| `.claude/agents/` | `researcher.md`·`skeptic.md`: "사람 승인" 문구를 새 절차로, 계열·예산 규칙 갱신. 새 `judge.md`(독립 심사), `redteam.md`(레드팀) |
| `.claude/skills/` | `research-campaign`·`candidate-review`·`lessons-review`·`daily-report`·`block-authoring` 문구 갱신. 새 `autonomy-cycle`(주간 사이클), `criteria-change`(기준 변경 절차), `final-round`(결선) |
| `docs/AI_RESEARCH.md`, `docs/OPERATIONS.md` | 자율 모드·운영 스크립트·페이퍼 후보 인스턴스 설명 |

---

## 4. 안전장치 — 구현 방법

### A. 바닥 규칙 (사용자만 변경)

파일 `config/floor.yaml` (새로 만듦, 값은 현재 설정에서 복사):

```yaml
version: 1
set_by: "user (chat 2026-11-11)"
costs_min_roundtrip_bp: {coinone_conservative: 4, "coinone_conservative@XRP-KRW": 10, ...}  # costs.yaml 현재 18개 모델 전부(base·conservative·stress)의 왕복 bp — 이 값 아래로 못 내림
primary_cost_role: conservative        # 판정 비용은 conservative 이상
max_mdd: 0.20                          # 운용 크기 기준
max_mc_mdd95: 0.20
min_position_scale: 0.25
worst_month_min: -0.08                 # 운용 크기 기준 최악 달력 월
universe: [BTC-KRW, XRP-KRW, ETH-KRW]
long_only_spot: true
splits_sha256: <config/splits.yaml 해시>
lockbox_peek: forbidden                # 결선 판정 외 열람 금지
ledger_all_trials: true
real_money: user_only                  # T2 이상, 주문, 키
cert_max_fpr: 0.02                     # 인증 오탐 목표 — AI 가 올릴 수 없음
max_loosenings: 3
verdict_budget_max: {val_new_uses: 10, lockbox_openings: 5}
oos_alpha_max: 0.15                    # F1c 의 상한 (§5.2)
```

강제 장치
1. **코드**: `btc_quant.params.floor.check_floor(gate_cfg, costs, splits)` → 위반 시 `FloorViolation`. 호출 지점: `campaign.register_campaign`, `Pipeline.run` 시작, `auto-apply`, `final_verdict.run`, 페이퍼 시작(`paper_verdict.start`), `ruleset.lock`. 바닥은 "최소한"이다 — 설정이 더 엄격한 것은 허용.
2. **테스트**: `tests/test_floor.py` 가 floor.yaml 의 값을 **글자 그대로** 고정(값이 바뀌면 실패). `tests/test_floor_enforced.py`: 각 호출 지점에서 낙폭 0.25·비용 하향·크기 하한 0.2·분할 해시 불일치·세계 밖 자산을 넣으면 거부.
3. **guard hook (모드와 무관하게 항상)**: `FLOOR_PROTECTED = config/floor.yaml, tests/test_floor*.py, src/btc_quant/params/floor.py, config/splits.yaml, strategies/tiers.yaml, .claude/hooks/guard.py, .claude/settings*.json` 쓰기·삭제·이동을 거부. 환경 변수 `BTCQ_FLOOR_EDIT=1`(사용자가 환경 설정에 직접 넣을 때만)이 있을 때만 통과. 실행자는 Phase 1 에서 guard.py 를 고친 **뒤에** 이 규칙을 켠다(마지막 커밋에서 `config/autonomy.yaml: floor_guard: on`).
4. **감사 흔적**: `python -m btc_quant.params floor-verify` 가 floor.yaml 해시와 `git log --follow` 이력을 출력. 독립 심사자가 후보마다 확인하고, 최종 리포트에 "바닥 규칙 해시·변경 이력"을 싣는다(이력이 1줄이 아니면 리포트 맨 위 경고).
5. 한계(정직하게): hook 은 경보선이다. 저장소에 쓸 수 있는 AI 를 암호학적으로 막지는 못한다. 그래서 코드 검사 + 고정 테스트 + 심사자 확인 + 최종 보고 공개를 겹친다.

### B. 기준 변경 자동 인증

모듈 `src/btc_quant/research/certify.py` (기존 `gate_review_study`·`g7_study`·`managed_hold_study` 의 시장 생성기·오프라인 평가기를 재사용).

**인증 은행 `bank-v1`** (합성 시장 원시 기록, 한 번 만들고 재사용 — 기준만 바꾼 재평가는 몇 분):

| 묶음 | 내용 | 수 |
|---|---|---|
| 표준 귀무 | 5분·저회전·초저회전·1분 ATR·손익분기 (기존 `a_*`) | 200 |
| 어려운 귀무 | 국면 귀무(상승·하락·횡보 Val) 60, 3코인 공통요인 귀무 40, **상승 표류만 있는 시장**(베타 귀무) 40, 두꺼운 꼬리·점프(복권형) 20, 호가 튐·멈춘 가격(1봉 지연이면 사라지는 가짜 회귀) 20, 계절성만 있고 비용 후 0 인 시장 20 | 200 |
| 소멸 엣지 | 개발 구간에는 참 Sharpe 2, **검증·잠금 구간에서는 0** | 40 |
| 심은 엣지 | 참 Sharpe 1.5 / 2 / 3 × 4 시나리오 각 15 (=180) + 3코인 Sharpe 2 20 + 국면 조건부 20 | 220 |
| 누적 프로그램 귀무 | 8캠페인 연속(기존 `CUM_SEQUENCE8`) 20 + **기준 쇼핑**(중간에 허용된 느슨화 3회를 모두 쓰는 각본) 10 | 30 |
| managed_hold | 귀무(null_flat·null_switch) 80, 추세 지속 L45·L90 각 40 | 160 |

- 모든 시장은 개발 4.8년 + 검증 6개월 + **잠금 6개월**까지 생성(`g7_study` 방식)해 최종 판정(F)까지 평가한다.
- 실행: `python -m btc_quant.research.certify bank --out research/certifications/bank-v1 --jobs 3` — 작업 id·seed 고정, 시장마다 파일 1개(이어서 실행 가능). 예상 4~6시간(4코어). 압축해서 50MB 이하면 저장소에 커밋, 넘으면 후보 단위 요약 필드만 커밋(오프라인 평가기에 필요한 값).
- 없는 생성기(베타 귀무·점프·호가 튐·소멸 엣지·기준 쇼핑)는 `certify.py` 에 추가하고 각자 "정말 귀무인가" 단위 테스트를 둔다(참 Sharpe 0 ± 표본오차).

**인증 통과 조건** (`certify run --ruleset <설정> --bank bank-v1` → `research/certifications/cert-<기준 묶음 해시>/cert.json`):

| # | 지표 | 조건 |
|---|---|---|
| C1 | 엣지 없는 귀무(표준 + 어려운, 400)에서 T1 진입(검증 통과) 오탐 | 단측 95% 상한(Clopper–Pearson) ≤ **2%** → 400개면 3건 이하 |
| C2 | 어려운 귀무(200)만 | 점추정 ≤ 2% (4건 미만) 그리고 상한 ≤ 4% (3건 이하) |
| C3 | 같은 귀무에서 **최종 후보**(F1~F3 통과) 오탐 | ≤ 0.5% (400개 중 2건 이하) |
| C4 | 누적 프로그램 귀무(30): 프로그램 안에 최종 후보가 하나라도 나오는 비율 | 1/30 이하. 기준 쇼핑 각본 10 에서는 0 |
| C5 | 소멸 엣지(40)의 최종 통과 | ≤ 10% (4건 이하) — §5.2 F1c 가 정하는 한계. 따로 보고 |
| C6 | 검출 최소선 | T1: Sharpe 2 ≥ 35%. 최종: Sharpe 2 ≥ 25%, Sharpe 3 ≥ 45% (각 n ≥ 60) |
| C7 | 일치 점검 | 실제 파이프라인 vs 오프라인 평가기 후보 단위 불일치 0 (시장 ≥ 24, 판정 코드가 바뀌었으면 ≥ 36) |
| C8 | 레드팀 각본 묶음(§4-F) | 귀무 세계에서 최종 통과 0 |
| C9 | managed_hold 프로필을 건드리면 | 귀무 80 에서 T1 오탐 ≤ 2건, 최종 0 |

변경 종류별 절차

| 종류 | 판별 | 절차 | 벌점 |
|---|---|---|---|
| 엄격화 | 키별 방향표(`params.yaml` 새 필드 `stricter: up\|down`) 또는 은행 기록에서 **통과 집합 ⊆ 이전 통과 집합** | C6·C7 확인 후 즉시 적용 | 없음 |
| 중립(버그 수정·리팩터) | 은행 전체에서 통과 집합 동일 + 전체 테스트 녹색 | C7 후 적용 | 없음 |
| 느슨화·새 판정 방식 | 위 둘이 아님 | C1~C9 전부 + **T1 Sharpe 2 검출이 현재보다 5%p 이상 증가** + 주간 점검 때만 적용 + 누적 3회 이하 | L += 1 |

- 적용: `python -m btc_quant.params auto-apply <제안 id>` — cert.json 이 (a) 통과이고 (b) 적용 뒤 설정의 해시와 같을 때만 `gates.yaml`·`params.yaml` 을 고친다. `applies_from_campaign = 다음 캠페인`(소급 금지, `gate_config_for_campaign` 그대로).
- 기존 anti-goalpost 검사(`params.criteria.check_anti_goalpost`: 실제 캠페인·후보 언급 거부)는 그대로 쓴다. 근거는 합성만.
- 느슨화 제안에는 코드가 만든 **"구제 효과 표"**(최근 2개 캠페인의 개발 구간 탈락 후보 중 새 기준이면 개발 관문을 통과했을 수)를 붙여 기록·최종 보고에 공개한다(거부 사유는 아님 — 숨기지 않는 것이 목적).
- 인증 실패 = 거부. 같은 변경을 값만 조금 바꿔 다시 시도하는 것은 **주당 1회**로 제한(은행에 대한 과최적화 방지). 은행은 seed 짝수(TUNE)/홀수(HOLD-OUT)로 나누고, 느슨화는 **HOLD-OUT 에서도** C1·C2 를 따로 만족해야 한다.

### C. 사전 잠금

- **기준 묶음(ruleset)** = `gates.yaml` + `floor.yaml` + `autonomy.yaml` 의 판정 관련 절 + 비용 모델 현재 버전 + `splits.yaml` + **판정 코드 집합의 파일 해시**(`src/btc_quant/{backtest,evaluation}/**`, `research/{pipeline,robustness,managed_hold_gates,final_verdict,paper_verdict,nulls,campaign}.py`, `registry/{ledger,splits}.py`). `params.ruleset.ruleset_hash()` 가 계산.
- **잠금**: `python -m btc_quant.params ruleset lock --id rs-NNN` → `research/rule_locks/rs-NNN.json`(해시·git 커밋·시각·인증 id) 작성 + 커밋 + **푸시**. 잠금 파일의 커밋이 원격에 있어야 유효.
- **검사 `require_locked_ruleset()`** — 호출 지점: `register_campaign`, `Pipeline._open_val`(Val 열기 직전), `final_verdict.open`(잠금 열기 직전), `paper_verdict.start`. 확인: (1) 지금 계산한 해시 = 유효한 잠금의 해시, (2) 그 잠금 커밋이 HEAD 의 조상(`git merge-base --is-ancestor`), (3) 판정 코드·설정에 커밋 안 된 변경 없음, (4) 인증 기록 존재. 통과하면 잠금 id 를 `val_evaluations`·`lockbox` 원장 줄에 함께 적는다.
- **소진 규칙** (코드 `ruleset.segment_state(segment_id)`):
  - 구간을 읽는 규칙: 검증 = G6 절·선택 규칙·프로필의 G6 부분 / 잠금 = 최종 판정(F) 절.
  - 그 구간을 **한 번이라도 연 뒤** 그 구간을 읽는 규칙을 느슨하게 바꾸면 → 그 구간은 `consumed`: 새 판정에 쓸 수 없다(잠금이면 은퇴, 검증이면 새 `val_id` 가 생길 때까지 G6 중단). 엄격화·중립 변경은 소진시키지 않는다.
  - 결과: 프로그램 중 AI 가 실질적으로 느슨하게 바꿀 수 있는 것은 **개발 구간 쪽 규칙**(G1~G5, 견고성, 선별, 예산)뿐이다. 검증·잠금을 읽는 규칙은 **첫 사용 전에 정하고**(Phase 2) 그 뒤로는 조이기만 한다.
  - 새 관문 프로필(새 계열용)은 "그 프로필로 검증을 연 적이 없음"이면 사전 잠금 충족으로 본다(인증 + L 계수는 필요).
  - 같은 스펙(logic_hash)의 재평가는 이전 결정과 AND(기존 Val 재사용 규칙) — 그대로.
- **정비 창구**: 판정 코드 수정이 필요하면 `python -m btc_quant.research.program maintenance open --reason "..."` → 캠페인 등록·Val·잠금이 막힌다 → 수정 → 인증(중립/엄격/느슨 판별) → 새 잠금 → `maintenance close`. 원장 `criteria_revisions` 에 남는다.
- 테스트 `tests/test_ruleset_lock.py`: 잠금 없이 Val 열기 거부 / 잠금 뒤 gates.yaml 한 글자 변경 시 거부 / 연 뒤 G6 느슨화 → `consumed` / 엄격화는 유지 / 잠금 커밋이 조상이 아니면 거부.

### D. 시도 전부 계수

- 원장 새 표 `criteria_revisions`(추가 전용): `{id, ts, kind: stricter|neutral|looser|profile_new, keys, from, to, cert_id, ruleset_before, ruleset_after, reason, rescue_table}`. `Ledger.record_criteria_revision`, `Ledger.loosenings(scope=None)`.
- **L** = 프로그램 시작 이후 적용된 `looser`·`profile_new` 수. 반영 두 곳:
  1. **개발 구간 DSR**: 시도 수 `N_eff = N_계열 × (1 + L_dev)` (L_dev = 개발 관문·견고성·선별을 건드린 느슨화 수). `Pipeline.g5` 가 사용, 리포트에 표시.
  2. **최종 결합 증거 문턱**: `α_최종 = 0.05 / (잠금 예산 5 × (1 + L))` — §5.2 F1b.
- 연구 쪽 시도는 기존 그대로(전략 변형·선별 구성·`log_exploration`). 추가: 기준선 managed_hold 참조 스펙의 개발 구간 계산도 `log_exploration`(계열 `managed_hold`, 1건).
- 테스트 `tests/test_trial_counting.py`: L 이 오르면 같은 후보의 DSR 이 내려가고 최종 문턱이 엄격해진다 / 엄격화는 L 불변.

### E. 판정 데이터 예산

`config/autonomy.yaml`:
```yaml
verdict_budget: {val_new_uses: 10, lockbox_openings: 5, final_rounds_max: 2, bundle_max: 3}
program: {start: <Phase 4 시작일>, max_weeks: 5, base_weeks: 3, max_trials: 400, weekly_trials: 150, family_weekly_trials: 60, max_loosenings: 3}
```
- `registry.ledger`: `val_status` 에 프로그램 기준 카운트 추가(프로그램 시작 뒤 연 Val 수). `Pipeline._open_val` 이 10회를 넘으면 `ValBudgetExhaustedError`(기존 상한 20 은 그대로 두 번째 벽).
- 잠금: `final_verdict.open` 이 5회를 넘으면 거부. 기존 `lockbox_cap: 10` 은 건드리지 않는다(분할 파일은 바닥).
- 소진 시: 그 종류의 판정 중단 → 사용자 알림 "판정 데이터 소진, 새 실시간 데이터가 쌓여야 함"(잠금 롤링은 분할 변경이므로 사용자 결정).
- Val 을 아끼는 규칙: 캠페인당 Val 로 보내는 후보는 상관 군집 대표 **1개**(기존 `selection.cluster_best` 의 상위 K 를 1로), 개발 관문 + 견고성 + skeptic `SURVIVES` 를 모두 통과한 것만.

### F. 독립 심사 + 레드팀

**독립 심사 에이전트 `.claude/agents/judge.md`**
- 입력은 **후보 서류 묶음 한 폴더**뿐(`research/dossiers/<후보>/`: 스펙, 사전등록 원장 줄, `pipeline_public.json`, 견고성 출력, 계열 시도 수, 기준 묶음·잠금 id, 블록 코드 경로). 연구자의 설명·대화는 넣지 않는다(새 에이전트로 호출, 프롬프트는 서류 경로만).
- 도구: 읽기 + Bash(재계산) + 읽기 전용 MCP. 쓰기·캠페인·기준 제안 도구 없음(`disallowedTools` 는 skeptic 과 같게).
- 할 일(체크리스트, 모두 수치·명령 인용): ① 사전등록이 실행보다 먼저인가(원장 시각) ② 기준 묶음 잠금이 Val 사용보다 먼저이고 해시가 맞는가 ③ 바닥 규칙 해시·이력 ④ 기준 엔진으로 개발 구간 재실행 → 거래 목록·Sharpe 가 서류와 같은가 ⑤ 계열 시도 수·L 이 DSR 에 맞게 들어갔는가 ⑥ 실험 블록이면 **블록 심사**: 수식을 독립적으로 다시 짜서(스크래치패드) 출력 일치, 미래 변조 테스트, 수용 검사 재실행 → `research/block_reviews/<블록>.md` 초안을 결과로 반환 ⑦ 최종 판정 규칙을 서류 수치에 기계적으로 적용(개발 구간 몫만).
- 출력: `JUDGE: PASS | FAIL` + 표. FAIL 이면 그 후보는 결선에 못 간다.
- skeptic(전략을 깨뜨리는 역할)과 judge(절차·계산을 감사하는 역할)를 **둘 다** 통과해야 결선 후보.

**레드팀**
1. 각본 묶음(재현 가능, 매 인증에 포함) — `src/btc_quant/research/redteam.py`, 합성 **귀무** 저장소에서 실제 MCP 도구 계층(`ResearchTools`)으로 실행:
   | 각본 | 공격 |
   |---|---|
   | brute | 주간 예산을 끝까지 써서 모든 블록 계열·최대 격자를 5주 돌림 |
   | beta | 상승 표류 귀무에서 롱 온리 추세 필터·보유형 전략(프로필 포함)으로 통과 시도 |
   | lottery | 점프 귀무에서 큰 익절·낮은 승률 전략 |
   | stale | 호가 튐 귀무에서 1분 평균회귀 |
   | rename | 계열 이름을 바꿔 시도 수 초기화 시도(거부돼야 함) |
   | shop | 허용된 느슨화 3회를 전부 쓰고 다시 채굴 |
   | reuse | Val 에서 떨어진 스펙을 파라미터 표기만 바꿔 재제출(같은 logic_hash·유사 스펙 탐지 확인) |
   | sidecalc | `log_exploration` 없이 사이드 계산 → 탐지·기록 여부 |
   성공(귀무 세계에서 최종 후보 발생) = 인증 실패.
2. 에이전트 레드팀 `.claude/agents/redteam.md`: 합성 귀무 저장소(임시 루트)와 연구 도구만 주고 "최종 후보를 만들어 보라"고 지시. 기준 묶음이 바뀔 때마다 1회, **6시간 상한**. 성공하면 "발견" → 원인 기준을 조이는 변경(엄격화)을 인증해 적용하고 그 공격을 각본 묶음에 추가. 발견·조치는 `research/reports/redteam_<날짜>.md` 와 최종 보고에 싣는다.
3. 레드팀·심사 에이전트는 실제 연구 저장소의 Val·잠금에 접근하지 않는다(임시 루트·서류 묶음만).

### G. 미래 데이터 최종 심판

- 페이퍼 판정(§5.4)이 첫 관문. 판정 뒤에도 페이퍼는 **사용자 승인 때까지, 승인 뒤에도** 계속 돈다.
- 매일 `python -m btc_quant.research.paper_verdict monitor`: 규칙 잠금 이후의 누적 페이퍼 손익(운용 크기 기준)이 경과 일수의 **예상 범위 99% 하한** 아래이거나, 재현 불일치율이 5% 를 넘거나, §7 의 사람-해제 정지가 나면 → **자동 강등**(`final → suspended`, 원장 transitions `actor=monitor`) + 알림. 위쪽 99.5% 초과는 강등이 아니라 "버그 의심" 점검.
- 예상 범위 = 개발+검증 일간 수익의 정상 블록 부트스트랩으로 만든 n일 누적 수익 분포(기존 `forward.expectation_band` 의 정규 근사를 부트스트랩으로 교체, `n_boot` 5,000).
- 전진 인큐베이션(`research.forward`)은 그대로 매일 실행해 페이퍼가 꺼져 있던 구간을 사후 보완한다.

### H. 전부 보고

- `research.final_report` 가 원장에서 **자동 생성**: 전체 시도 수(계열별·선별·탐색·기준 변경), 캠페인 깔때기, 기준 변경 이력(이유·인증 수치·구제 효과 표·L), 탈락 후보 전부(graveyard: 어느 관문·실패 유형), 판정 예산 사용, 레드팀 발견, 정비 창구 이력, 바닥 규칙 해시 이력, 과정 실수(사후분석).
- 손으로 고칠 수 없는 부록(표)과 AI 가 쓰는 본문을 분리한다. 본문의 숫자는 부록 표에서만 인용.
- 테스트 `tests/test_final_report.py`: 원장에 있는 캠페인·기준 변경이 하나라도 빠지면 실패.

### I. (추가) 자율 모드 guard

`config/autonomy.yaml: phase` ∈ `build | research | final | report | closed`. guard.py 가 이 파일을 읽는다(환경 변수 없이도 동작).

| 대상 | 규칙 |
|---|---|
| 모든 세션, 항상 | §4-A 바닥 보호 |
| 하위 에이전트(researcher·skeptic·data-steward·judge·redteam) | 지금의 연구 모드 규칙 그대로(hook 입력의 `agent_type` 으로 판별. 값이 안 오면 주 세션 규칙 + 에이전트 정의의 `disallowedTools` 로 대신하고 그 사실을 docs/AI_RESEARCH.md §8 에 적는다) |
| 주 세션, phase = research·final | 거부: `gates.yaml`·`params.yaml`·`costs.yaml` 직접 쓰기(`auto-apply` 만), 원장·오답노트 직접 쓰기, `research/{rule_locks,certifications/*/cert.json,param_approvals}` 직접 쓰기, 정비 창구 밖의 판정 코드 쓰기, 잠금 데이터 경로·사람 전용 Val/잠금 세부 읽기, `promote-tier`·`activate`·`rollback`, 원장을 쓰는 연구 진입점 직접 실행(MCP·`program`·`final_verdict` CLI 만), 강제 푸시. 허용: 지정 브랜치로의 일반 `git push`(컨테이너가 사라지므로 필수) |
| phase = report | 사람용 리포트 읽기 허용(프로그램 종료 기록이 원장에 있을 때만). 캠페인·기준 변경·잠금 개봉은 코드가 거부 |
| phase = build | 지금의 개발 세션과 같음(Phase 0~3) |

phase 전환은 `python -m btc_quant.research.program phase <다음>` 만(되돌리기 불가, 원장 기록). 테스트는 `tests/test_guard_hook.py` 에 모드별 허용·거부 사례 추가.

### J. (추가) 결선 한 번에 열기

잠금 구간은 후보가 생길 때마다 열지 않는다. 연구를 **멈춘 뒤** 결선 후보를 한꺼번에 고정하고 한 묶음으로 연다(§5.3). 잠금 결과를 본 뒤 다음 후보를 고르는 길을 없앤다. 결선은 최대 2번, 개봉 합계 5회.

### K. (추가) 총연습

실제 데이터에 쓰기 전에 합성 저장소에서 전 과정(캠페인 → T1 → 페이퍼 판정 → 결선 → 보고)을 끝까지 돌린다(Phase 3). 잠금 평가 코드가 처음 실제로 도는 곳이 실제 잠금 구간이 되지 않게 한다.

---

## 5. 판정 구조

### 5.1 전체 그림 (기존 관문과의 관계)

```
연구(개발 구간)            검증 구간               실시간               잠금 구간(결선)          보고
G0~G5 + 기여도 + 견고성 → skeptic → G6(결합 증거, 크기) → T1 = 페이퍼 7~14일(G8') → judge → F1~F5 → 묶음 → 사용자 승인 → (T2)
```

| 단계 | 내용 | 바뀌는 점 |
|---|---|---|
| G0~G5, 기여도 | 그대로 | DSR 시도 수에 (1+L_dev) |
| 견고성 | 비용 ×1.5, 지연, 상위 거래 제거, 연도 제외, 국면 비중 ≤ 70%, 알파 t ≥ 1.5, 거래소 재현, 꼬리(사다리) | **연도 손익 비중 ≤ 70%** 추가(`robustness.year_max_pnl_share: 0.70`, 엄격화). managed_hold 프로필은 능동 수익 기준으로 적용 |
| G6 | Dev+Val 결합 p ≤ 0.05, Val Sharpe > 0, 크기 조절 | 크기 계산에 **개발 구간 낙폭·최악 월**도 넣음(엄격화, §5.2 F2 와 같은 식) |
| T1 | validated → 페이퍼 | 페이퍼 판정 규칙 §5.4 |
| G7 | 잠금 | **F1 로 대체**(결선에서만) |
| G8 | 페이퍼 | **§5.4 로 대체** |
| 단계별 승격 | T0~T3 | T2 조건 = "최종 후보 묶음 + 사용자 승인". 코드(`registry/tiers.py`)의 사람 전용 승격은 그대로 |

### 5.2 최종 판정 F1~F5 (모두 통과해야 최종 후보)

모듈 `src/btc_quant/research/final_verdict.py`, 설정 `config/gates.yaml` 새 절 `final:`.

**F1 수익의 진위** (비용 = conservative, 자산별 모델, MTM 일간 수익. managed_hold 프로필은 보유 대비 능동 수익으로 같은 식)
- F1a: 검증 구간 Sharpe > 0 **그리고** 잠금 구간 Sharpe > 0.
- F1b: 세 구간 결합 증거 `z = (z_dev + z_val + z_lb)/√3` 의 단측 p ≤ **0.05 / (5 × (1 + L))**. `z_dev` = 개발 구간 DSR 의 z(시도 수·L 반영, 기존 `gates.dsr_z`), `z_val`·`z_lb` = 각 구간 일평균 > 0 의 블록 부트스트랩 p 의 z. 5 = 잠금 예산(실제 연 수와 무관하게 고정 — 가장 단순하고 어떤 순서로 열어도 유효). L=0 이면 문턱 0.01.
- F1c: **표본 밖 12개월만**(검증 + 잠금 일간 수익을 이어 붙임)의 일평균 > 0 부트스트랩 p ≤ **0.10**. 이유: F1b 는 개발 구간 증거가 크면 뒤 구간이 0 이어도 통과할 수 있다("예전엔 됐지만 지금은 죽은 전략"). F1c 가 이 경우의 통과를 약 10% 로 묶는다.
- 사전 등록 대안(합성 인증에서만 고름, 잠금을 열기 전에 확정): 인증에서 최종 검출이 C6 최소선에 못 미치면 F1c 를 0.15 로(소멸 엣지 통과 상한 15% 로 C5 도 같이 조정). 그래도 미달이면 **0.15 로 확정하고 미달 사실을 보고**한다 — 그 이상 풀지 않는다.

**F2 손실** (운용 크기 기준)
- `position_scale = min(1, 0.20/MDD_전체, 0.20/MC_MDD95, 0.08/|최악 달력 월 손실|)`, MDD·최악 월은 개발·검증·잠금 **각 구간 값 중 최악**, MC MDD95 는 전 구간 거래(사다리는 포지션) 재표본(포지션 중간 MTM 저점 포함, 기존 `bootstrap_mdd`).
- `position_scale ≥ 0.25` 여야 통과. 통과하면 이 크기가 운용 크기다(G6 에서 정한 값보다 작아질 수만 있다).
- 묶음 수준: 최종 묶음(전략 간 동일 비중, 각자 자기 크기)의 합성 일간 수익으로 같은 식을 한 번 더 적용해 **묶음 배수**를 구한다.

**F3 견고성**: 개발 구간 견고성 묶음 전부 통과(§5.1) + G3 인접 안정 + skeptic `SURVIVES` + judge `PASS`. 잠금 구간에서는 견고성을 다시 돌리지 않는다(표본 6개월, 잡음).

**F4 페이퍼**: §5.4 `PASS`.

**F5 기준선** (3코인 동일 비중 단순 보유 `buy_hold_universe`, managed_hold 참조 스펙)
- managed_hold 참조 스펙 = **4시간 EMA(180), band 0, 3코인** — 결과를 보기 전에 여기서 고정한다(준비된 격자의 가장 단순한 값).
- 전 구간(개발+검증+잠금 ≈ 5.85년), 같은 비용·MTM, 운용 크기 기준으로: 후보의 Sharpe > 보유의 Sharpe **그리고** Calmar(연수익 ÷ 최대 낙폭) > 보유의 Calmar. 참조 스펙에 대해서도 같은 두 비교.
- 짝지은 부트스트랩의 "후보 Sharpe − 보유 Sharpe > 0" 확률을 함께 보고(판정 아님).
- F1~F4 를 통과했지만 F5 에서 지면 최종 후보가 아니다 → 리포트에 **"통과했지만 보유(또는 관리형 보유)가 낫다"**로 분류.
- 아무 후보도 F5 까지 못 가면 결론은 "보유가 낫다" 또는 (보유 자체가 낙폭 20% 를 맞추려면 크기 약 1/4 가 된다는 사실과 함께) "지금은 권할 전략이 없다".
- 기준선의 검증·잠금 수치는 결선 때 코드가 계산해 사람용 리포트에만 쓴다(후보 판정이 아니므로 개봉 예산에 세지 않되 원장에 `benchmark` 읽기로 기록).

**순위** (결선 후보 고르기·묶음 정하기 — 백테스트 수익률은 쓰지 않는다)
1. 보수적 Sharpe = `SR̂ − 1.645 × SE(SR̂) − SR₀` (연율, SE 는 왜도·첨도 보정식, SR₀ = 시도 수로 정해지는 "우연 최고치" — 기존 `selection.expected_max_sharpe`). 결선 후보 고를 때는 개발 구간, 묶음 순서는 전 구간.
2. 동률이면 Calmar(운용 크기 기준) → 3. 비용 등급 A(업비트 비용에서도 통과) 우선.

### 5.3 결선 절차 (잠금 구간은 최종 후보에만)

`python -m btc_quant.research.final_verdict run --round 1` (주 세션만, phase = final)
1. 조건: 연구 중단 기록(`program phase final`), 기준 묶음 잠금 유효, 레드팀 미해결 발견 0.
2. 후보 풀: T1 + skeptic `SURVIVES` + judge `PASS` + 페이퍼 `PASS`. 개발 구간 일간 수익 상관 군집(절단 0.5)별 순위 1위만 → 순위 순 **최대 5 − (이미 연 수)** 개.
3. `registry.authorize.lockbox_authorization(candidate)`: 위 조건을 코드로 재확인해 **자율 승인 기록**(`research/lockbox_approvals/auto/<후보>.yaml`, 검사 결과·잠금 id 포함)을 만든다. `DataAccess.open_lockbox` 는 사람 승인 파일 **또는** 이 자율 승인 기록(서명 = 검사 재실행으로 검증)을 받는다. 그 밖의 호출은 지금처럼 거부.
4. 한 묶음으로 열어 F1~F5 계산 → 잠금 원장 기록. 연구 에이전트용 출력은 결과 코드만.
5. 결과가 0개이고 개봉 여유가 남았으면 2차 결선 가능 — 단 **새 연구 주기를 최소 1주** 더 한 뒤(같은 풀에서 다음 순위를 여는 것은 금지: 1차에서 순위로 탈락한 군집 대표가 아닌 것은 다시 못 연다).
6. 결선 뒤 `program phase report`.

### 5.4 페이퍼 판정 (정량)

모듈 `src/btc_quant/research/paper_verdict.py`, 설정 `gates.yaml G8:` 개정(사용자 결정 4 — AI 느슨화로 세지 않음, L 불변).

| 항목 | 규칙 |
|---|---|
| 기간 | T1 진입 다음 UTC 일부터 **7일**. 그때 '활동 충분'이 아니면 **14일**까지 한 번 연장. 14일에 있는 자료로 판정 |
| 활동 충분 | 체결(분할 포함) ≥ 10건 또는 왕복 ≥ 5회. managed_hold 프로필은 결정 봉(4시간) ≥ 42개 |
| P1 체결 비용 | 실시간 호가 기준 체결 비용(`fills.jsonl book_cost_bps`, 실시간 체결만): 자산별 중앙값 ≤ conservative 편도 가정, p90 ≤ stress 편도 가정. 체결 < 10건이면 결정 시각 호가 표본(≥ 200 스냅샷, `params sample`)의 같은 통계로 대신 |
| P2 재현 일치 | `paper.service.build_report`(같은 기간 기준 엔진 재현)로: 진입 시각 일치율 ≥ 95%, 일치한 거래 중 청산 시각·사유 동일 ≥ 90%, 설명 안 되는 한쪽만 있는 거래(리스크 차단 제외) ≤ max(1건, 5%), 결정 봉별 보유/현금 상태 일치 ≥ 95% |
| P3 손익 | (a) 같은 기간 재현 대비: \|페이퍼 누적 − 재현 누적\| ≤ max(거래당 10bp × 거래 수, 0.3%p). (b) 예상 범위: 누적 손익(운용 크기)이 n일 부트스트랩 분포의 2.5~97.5% 안. 아래로 벗어나면 FAIL, 위로 99.5% 초과면 점검 |
| P4 운영 | 실시간 가동 비율 ≥ 70%(나머지는 REST 재현으로 메움, 따로 표시), 결정 봉 누락 ≤ 1%(재현 포함), 사람-해제 리스크 정지 0 |
| 결과 | `PASS` / `FAIL` / `EXTEND`(1회). FAIL 원인이 하네스 버그로 확인되면 수정·사후분석 후 **시계 재시작 1회** 허용. 그 밖의 FAIL → 후보 탈락(`paper_mismatch`) |
| 크기 | 페이퍼 명목 = 기본 명목 × position_scale (기존 동작) |

페이퍼는 수익을 증명하지 않는다 — 7일 손익이 플러스인지는 판정에 쓰지 않는다(P3 는 "범위 안인가"만).

### 5.5 합성 보정 목표 (Phase 2 에서 확인, 실제 연구 결과는 쓰지 않는다)

| 단계 | 엣지 없는 귀무 오탐 | 소멸 엣지 통과 | Sharpe 1.5 / 2 / 3 검출 | 참고(기존 합성) |
|---|---|---|---|---|
| T1 진입 | 상한 ≤ 2% (n=400) | (보고만) | 보고 / ≥ 35% / 보고 | 현재 기준 1/232, Sharpe 2 42% (n=84) |
| 최종(F1~F3) | ≤ 0.5% (n=400) | ≤ 10% (n=40) | 보고 / ≥ 25% / ≥ 45% | 결합 방식 Sharpe 2 72% (n=25, 단일 시나리오) |
| 프로그램(8캠페인) | 최종 후보 발생 ≤ 1/30 | – | – | 0/18 |
| 페이퍼 판정 | 합성 페이퍼: 같은 엔진·잡음 없는 체결에서 PASS ≥ 95%(n=40), 체결 지연 1봉·비용 2배·신호 1봉 어긋남 주입 시 FAIL ≥ 90%(각 n=20) | | | 새로 측정 |

---

## 6. Phase 별 작업

각 Phase 끝: `uv run pytest -q` 전체 녹색 → 커밋 → 푸시. 긴 계산은 작업 단위 파일로 이어서 실행 가능하게. 중간에도 자주 커밋·푸시(컨테이너가 사라지면 푸시 안 된 것은 없어진다).

### Phase 0 — 복원·운영 기반·결정 기록 (0.5~1일)

| 작업 | 완료 조건 |
|---|---|
| 0.1 환경: `uv sync`, `uv run pytest -q` (2,572개 통과 확인) | 녹색 |
| 0.2 **데이터 복원 스크립트** `scripts/restore_data.sh`: 코인원·업비트 BTC/XRP/ETH 1분봉 2021-01~현재를 받아 `data/raw` 재구성(이전 세션이 쓴 수집 명령은 README·`btc_quant.data`·`research/reports/data_audit_*.md` 에서 찾는다). 멱등·이어받기. 잠금 구간 파티션은 받되 읽지 않는다(DataAccess 가 차단) | 스냅샷 해시가 camp-000031 매니페스트의 해시와 같음. 다르면 차이(기간·행 수)를 `research/reports/data_restore_<날짜>.md` 에 적고 "새 스냅샷"으로 취급(오답노트 재검토 조건 기록) — 조용히 넘어가지 않는다 |
| 0.3 **운영 스크립트**(§9): `scripts/{session_start,collector_watchdog,paper_watchdog,persist_runtime}.sh`, `.claude/settings.json` 에 SessionStart hook 등록 | 새 세션에서 수집기가 자동으로 뜨고 `runtime/collector.heartbeat` 갱신 |
| 0.4 프로그램 상태 파일 `research/program/state.json` + `python -m btc_quant.research.program {status,resume,phase}` 뼈대 | `status` 가 단계·예산·다음 할 일을 출력 |
| 0.5 문서: `docs/AUTONOMY.md`(§3.1 표), `docs/PLAN.md` D26~D33 및 관련 절, `docs/PARAMS.md` 예고 문단. 사용자 결정 기록 `research/param_approvals/user-20261111-autonomy.json`(채팅 결정 5개 원문 요지) | 문서 커밋 |
| 0.6 재개용 예약 작업 등록(§9.1) | 3시간마다 이 세션에 "재개" 메시지 |

### Phase 1 — 안전장치 구현 (2~3일, phase = build)

순서: A 바닥 → D 원장 표 → C 기준 묶음·잠금 → E 예산 → B 인증 뼈대(은행 생성기·오프라인 평가 연결) → F 에이전트·각본 → I guard → H 보고 생성기.

| 작업 | 파일 | 테스트 |
|---|---|---|
| 1.1 바닥 | `config/floor.yaml`, `params/floor.py`, 호출 지점 | `test_floor.py`, `test_floor_enforced.py` |
| 1.2 원장 | `registry/ledger.py`: `criteria_revisions`, 프로그램 기준 Val 카운트, `benchmark` 읽기 기록 | `test_ledger_autonomy.py` (추가 전용·해시 사슬 유지) |
| 1.3 기준 묶음 | `params/ruleset.py`, CLI, `require_locked_ruleset`, 소진 규칙, 정비 창구 | `test_ruleset_lock.py` |
| 1.4 자동 적용 | `params/autoapply.py`, 방향표(`stricter`), 구제 효과 표 | `test_autoapply.py` (인증 없이 거부·해시 불일치 거부·느슨화 4번째 거부·바닥 침범 거부) |
| 1.5 시도 계수 | `Pipeline.g5` N_eff, `final` 문턱 | `test_trial_counting.py` |
| 1.6 예산 | `config/autonomy.yaml`, `Pipeline._open_val`, `research/program.py`(주간 150·계열 60·전체 400 강제 — 기존 2,000/600 보다 작은 쪽 적용), 새 계열·동결 해제 기록 | `test_program_budget.py` |
| 1.7 인증 | `research/certify.py`(은행·평가·판정·cert.json), 새 귀무 생성기 | `test_certify.py` (작은 은행으로 C1~C9 판정 논리, 생성기 귀무성) |
| 1.8 레드팀 각본 | `research/redteam.py`, `.claude/agents/redteam.md` | `test_redteam_scripts.py` (rename·reuse·sidecalc 는 빠른 단위 테스트) |
| 1.9 독립 심사 | `.claude/agents/judge.md`, 서류 묶음 생성 `research/program.py dossier` | `test_dossier.py` (Val 수치가 서류에 없음) |
| 1.10 guard | `guard.py` 바닥 보호·자율 모드, `config/autonomy.yaml: phase` | `test_guard_hook.py` 추가 사례 |
| 1.11 보고 생성기 | `research/final_report.py` | `test_final_report.py` |
| 1.12 MCP·문서·에이전트·skills | §3.3 | `test_mcp_tools.py`, `test_mcp_safety.py` 갱신 |

원칙: 새 옵션의 기본값은 현재 동작. 이 Phase 에서 `gates.yaml` 의 **값**은 바꾸지 않는다(키 추가만, 기본 = 현재). 기존 캠페인 재계산 불변 테스트 유지.

### Phase 2 — 판정 구조 구현 + 합성 인증 (1.5~2.5일)

| 작업 | 완료 조건 |
|---|---|
| 2.1 `final_verdict.py`(F1~F5·순위·결선·자율 승인 기록), `registry/authorize.py`, `DataAccess.open_lockbox` 확장, 잠금 구간 평가 실행기(기준 엔진·자산별 비용·MTM, 프로필 지원) | `test_final_verdict.py`(합성 저장소): 승인 없이 열기 거부 / 6번째 개봉 거부 / 결과 코드만 공개 / 같은 후보 두 번 열기 거부 / F1b 문턱이 L 에 따라 변함 |
| 2.2 `paper_verdict.py`(P1~P4·monitor·예상 범위 부트스트랩), 페이퍼 후보 인스턴스(`paper run --specs …`, 레지스트리 challenger 3개 제한과 별개로 T1 후보 최대 6개) | `test_paper_verdict.py`: 합성 페이퍼 통과·주입 결함 탐지 |
| 2.3 G6 크기 식에 개발 구간 낙폭·최악 월, 견고성 연도 비중, 캠페인당 Val 후보 1개 | 옵션 기본 = 현재, 단위 테스트 |
| 2.4 **은행 생성** `certify bank` (백그라운드, 이어서 실행) | 850개 시장 기록, 커밋 |
| 2.5 **기준 묶음 `rs-001` 후보** = 현재 기준 + 2.3 엄격화 + `final:`(F1~F5) + G8 개정 + 예산. `certify run` | cert.json C1~C9 통과. F1c 는 0.10 먼저, 미달이면 0.15(§5.2) |
| 2.6 일치 점검 36개 시장(실제 파이프라인·결선 코드 vs 오프라인 평가) | 불일치 0 |
| 2.7 레드팀: 각본 묶음 전체 + 에이전트 레드팀 1회(6시간 상한) | 최종 통과 0. 발견이 있으면 조이고 2.5 부터 다시 |
| 2.8 적용·잠금: 제안 파일 → `auto-apply` → `ruleset lock --id rs-001` → 푸시. `applies_from_campaign` = 다음 캠페인 | `ruleset status` = locked, 원장 `criteria_revisions` 에 기록(초기 묶음은 L 에 세지 않는다 — 잠금·검증 첫 사용 전의 사전 확정. 단 종류·수치는 전부 보고) |
| 2.9 리포트 `research/reports/certification_rs001_<날짜>.md`(표본 수 포함 수치, 한계) | 커밋 |

인증이 끝내 통과하지 못할 때(순서대로, 사용자에게 묻지 않는다): ① 오탐 원인이 되는 귀무 묶음을 보고 **조이는** 변경을 추가(예: 알파 t 1.5 → 2.0, 연도 비중) ② 그래도 C1 미달이면 R1-plus 를 되돌려 R1 로 ③ 그래도 미달이면 2026-11-10 이전 기준(탈락 방식 낙폭)으로. 검출 최소선(C6)과 오탐(C1~C5)이 충돌하면 **오탐을 지킨다** — 검출 미달은 보고 사항.

### Phase 3 — 총연습 (0.5일)

합성 저장소(임시 루트, 심은 Sharpe 3 시장 1개 + 귀무 1개)에서: MCP 로 선별 → 캠페인 → skeptic → judge → T1 → 합성 페이퍼 7일(시간 압축 재생) → 결선 → 최종 리포트 생성. 귀무 쪽은 "후보 없음" 리포트가 나와야 한다.
완료 조건: 두 경로 모두 사람 개입 없이 끝, 재시작 주입(중간에 프로세스 강제 종료 3번) 뒤 `program resume` 으로 이어짐, 테스트 `tests/test_e2e_autonomy.py`(축소판) 녹색.
끝나면 `program phase research` → guard 자율 모드 시작. **알림 1회**("준비 완료, 연구 시작").

### Phase 4 — 연구 프로그램 (§7)

### Phase 5 — 결선 (§5.3) · Phase 6 — 최종 보고 (§10) · Phase 7 — 감시 (§4-G)

---

## 7. 연구 프로그램

### 7.1 지금까지의 교훈 (개발 구간 기록·오답노트만 근거)

| 교훈 | 근거 | 프로그램에 주는 뜻 |
|---|---|---|
| 짧은 지평 평균회귀는 비용 전 정보는 있으나 비용 후 0 근처 | 3코인 풀링 선별 5건 z 2.7~3.3, 순수익 ±3bp (cycle4) | 5분~15분 회귀는 더 파지 않는다 |
| 분할 매수 사다리(지정가)도 음의 기대값, 떨어지는 칼 재고 | camp-000028·029, 16 스펙 전부 Sharpe < 0 | 사다리 계열 중단(`liq_meanrev` 동결 유지) |
| 1시간 돌파·모멘텀은 선별에서 순수익 +11~23bp(8시간 지평)지만 캠페인에서 인접 불안정·선택편향 | camp-000027 통과 4건, camp-000030 G3, camp-000031 G2 | 느린 지평·적은 파라미터·3코인 풀링으로 재설계할 가치 |
| 하루 12회 이상 거래 엣지는 비용 ×1.5 에서 사라짐 | 합성 0/42 | 고회전 계열 제외(비용 바닥은 사용자 규칙) |
| 병목은 롱 온리의 국면 의존 | 연도별 표: 2021 만 플러스인 후보 다수 | 보유 대비 평가(managed_hold)와 국면 필터가 핵심 |
| 거래소 간 선행은 실재하나 1분 지평·4bp 미만 | 선별 8건 전부 비용에 먹힘 | 제외 |
| 계열 시도가 쌓일수록 필요한 Sharpe 가 오른다 | `vol_breakout` 96, `liq_meanrev` 66 시도 | 시도 예산을 크게 줄이고 새 계열은 선별부터 |
| 코인원 거래량 정보는 쓸모없음(봇성 체결) | 선별 volume_momentum p 0.33 | 거래량 블록 제외 |

### 7.2 계열과 순서

| 순위 | 트랙 | 계열 | 내용 | 시도 예산(전체) | 비고 |
|---|---|---|---|---|---|
| 1 | **A 관리형 보유** | `managed_hold`(새 계열, 시도 0) | 준비된 선별(4 구성) → 통과한 span 만 캠페인(4 스펙), 프로필 `managed_hold`. 참조 스펙(EMA 180)이 F5 기준선 | 선별 4 + 캠페인 4 + 참조 1 | 첫 주기에 실행. 실험 블록 → judge 블록 심사 |
| 2 | **A2 보유 + 변동성 회피** | `managed_hold`(파생) | 추세 켜짐 **그리고** 단기 변동성이 낮을 때만 보유(기존 `vol_regime` 필터 상한). A 의 사실 기록에서 파생 가설 | ≤ 12 | A 선별이 타이밍 정보를 보일 때만 |
| 3 | **B 느린 추세(스윙)** | `vol_breakout`(파생) | 4시간 봉 돌파·모멘텀(atr_channel, donchian, roc, composite momentum), 3코인, 절반 크기, 지평 1~7일, 청산 ≤ 4종. 1시간에서 걸린 "인접 불안정"을 피하려 격자 2×2 이하 | 선별 ≤ 8 + 캠페인 ≤ 16 | 검정력 계산 통과 시만. 계열 시도 96 을 물려받으므로 필요한 Sharpe 를 먼저 계산 |
| 4 | **C BTC 선행 국면 필터** | 새 계열 `xasset_btc_lead` | XRP·ETH 슬리브의 진입을 BTC 4시간 추세가 켜져 있을 때로 제한(같은 거래소 BTC 를 보조 데이터로 쓰는 새 실험 블록). 메커니즘: 알트는 BTC 국면을 뒤따른다 | 선별 ≤ 6 + 캠페인 ≤ 8 | 새 계열 → 선별부터, 주간 60 |
| 5 | **D 상승 국면 한정 저점 매수** | `liq_meanrev`(동결 해제 — 재검토 조건 "결정 주기 변경" 충족: 1시간·4시간) | 4시간 추세 켜짐 + 1시간 과매도 반등, 시장가, 사다리 없음 | 선별 ≤ 6 + 캠페인 ≤ 8 | A~C 에서 T1 후보가 2개 미만일 때만 |
| – | 제외 | – | 1분·5분 고회전, 사다리, 거래소 간 선행, 거래량 기반, 김치 프리미엄(외부 데이터 수신 미확인) | 0 | 제외 사유는 최종 보고에 |

각 트랙의 모든 가설은 기존 규칙대로: 메커니즘·반대편·무엇이 죽이나, 선별 먼저, 검정력 계산, 청산 변형 ≤ 6, 사전등록, 사이드 계산 기록.

### 7.3 주기와 예산

- 주기(사이클) 1개 = 계획 → 실행 → 심사 → 정리. 달력 1주에 맞추되 계산이 일찍 끝나도 **7일 창 150 시도 상한**(전략 스펙 + 선별 구성 + 탐색 기록 합계), 계열당 60, 프로그램 전체 400. 전략 캠페인 주당 ≤ 6개, 캠페인당 스펙 ≤ 8.
- Val 사용: 주당 ≤ 4, 프로그램 10. 군집 대표 1개만.
- 기준 점검: 주기 끝 한 번. 느슨화 제안은 이때만 적용(§4-B).
- 주기별 계획(기본)

| 주기 | 내용 |
|---|---|
| R1 | A 선별·캠페인, 참조 스펙 개발 구간 계산(기준선), B 선별 |
| R2 | A2(조건부), B 캠페인, C 블록 작성·심사·선별 |
| R3 | C 캠페인, D(조건부), 남은 파생 가설. 끝에서 중단 조건 판정 |
| R4~R5 | 연장 시에만(§7.6) |

### 7.4 에이전트 병렬 운용

- 주 세션 = **운영자**(계획·예산·기준 점검·커밋/푸시·알림). 직접 캠페인을 짜지 않는다.
- 트랙마다 `researcher` 하위 에이전트 1개, 동시에 최대 **2개**(4코어, 캠페인 `--jobs 2` 씩). 운영자가 트랙 지시서 `research/program/track_<X>_R<n>.md`(가설 범위·예산·금지 사항)를 먼저 쓰고 그 경로만 넘긴다.
- 원장은 파일 잠금으로 동시 쓰기에 안전하다(기존 `Ledger.lock`). 같은 계열을 두 에이전트가 동시에 다루지 않는다.
- 개발 구간 생존자가 나오면: `skeptic`(새 에이전트) → 통과 시 파이프라인 G6 → T1 이면 `judge`(새 에이전트, 서류만) + 페이퍼 시작. 데이터가 의심되면 `data-steward`.
- 에이전트 결과는 운영자가 그대로 믿지 않는다: 원장·`pipeline_public.json`·리포트 파일로 확인하고 `research/reports/cycleR<n>_<날짜>.md` 에 정리.

### 7.5 주기 끝 정리(매번)

오답노트 사실 확인·해석 추가(`lessons-review`), 예산·판정 데이터 사용 현황, graveyard 갱신, 기준 점검, `program status` 저장, 커밋·푸시. 알림은 §9.4 조건일 때만.

### 7.6 중단 조건

| 조건 | 판정 시점 | 다음 |
|---|---|---|
| **조기 성공**: 서로 다른 군집의 T1 후보 3개가 skeptic·judge·페이퍼를 모두 통과 | 언제든 | 결선 |
| **기본 종료**: R3 끝, T1 후보 ≥ 1 | R3 끝 | 진행 중인 페이퍼가 끝나는 대로 결선 |
| **연장**: R3 끝, T1 후보 0 이지만 최근 2주기에 개발 구간 생존자(견고성까지 통과)가 있는 계열이 있음 | R3 끝 | R4~R5 (그 계열과 파생만). R5 끝에 재판정 |
| **후보 없음 종료**: R3 끝에 연장 조건 미충족, 또는 R5 끝에 T1 0, 또는 전체 400 시도·Val 10회·느슨화 3회를 다 쓰고도 T1 0 | 해당 시점 | 결선 없이 "통과 후보 없음" 최종 보고 |
| **안전 정지**: 바닥 위반 탐지, 원장 검증(`verify`) 실패, 기준 묶음 해시 불일치로 2회 연속 거부, 레드팀 미해결 발견 | 즉시 | 연구 중단 + 알림, 원인 사후분석 후 재개 여부를 운영자가 판단(바닥 위반이면 사용자 보고 후 대기) |

"통과 후보 없음"은 실패가 아니라 정상 결론이다. 그때 보고에 넣을 것: 가장 멀리 간 후보와 막힌 관문, 보유·관리형 보유의 개발 구간 위험 지표, 무엇이 달라지면 다시 볼지(비용 확정, 새 데이터 6개월, 상시 실행 환경).

---

## 8. 기준 변경을 실제로 하게 될 때의 절차 (요약, skill `criteria-change`)

1. 동기 쓰기 — 합성 근거만(예: "은행의 Sharpe 2 탈락 원인 1위가 X 관문"). 실제 캠페인·후보 언급 금지(코드가 거부).
2. `propose_criterion`(MCP) 또는 제안 파일 → `certify run`.
3. 판별(엄격/중립/느슨) → 조건(§4-B 표) 확인 → 느슨화면 주기 끝까지 대기.
4. 그 변경이 검증·잠금을 읽는 규칙이고 이미 열었다면: 엄격화만 가능. 느슨화는 구간을 소진시키므로 **하지 않는다**(코드가 경고 후 확인 인자 없이는 거부).
5. `auto-apply` → `ruleset lock rs-00N` → 푸시 → 원장 기록 → 알림 1회.
6. 다음 캠페인부터 적용. 이미 떨어진 스펙은 구제되지 않는다(새 캠페인에서 새 시도로는 가능 — 시도 수에 쌓인다).

---

## 9. 운영

### 9.1 재개 가능한 실행

- **상태는 전부 git 에**: `research/program/state.json`(phase, 주기, 대기열, 진행 중 작업의 출력 폴더), 원장, 리포트, 인증 은행. 원자 단위(캠페인 1개, 은행 시장 50개, 판정 1건)마다 커밋·푸시.
- `python -m btc_quant.research.program resume`(멱등): ① 데이터 복원 확인(없으면 `restore_data.sh`) ② 수집기·페이퍼 감시 확인 ③ 원장 `verify` ④ 대기열의 다음 작업 출력. 중간에 죽은 캠페인은 원장에 등록만 되고 결과가 없을 수 있다 → 같은 캠페인 id 로 결과만 이어 계산(새 시도로 세지 않음; 그 경로가 없으면 `program.py` 에 추가하고 테스트).
- **SessionStart hook**(`scripts/session_start.sh`): `uv sync` → `restore_data.sh`(백그라운드) → 감시 스크립트 2개 기동 → `program status` 출력. 새 컨테이너·새 세션마다 자동.
- **예약 작업**: 3시간마다 이 세션으로 "`program resume` 실행 후 다음 작업 계속" 메시지(세션이 쉬고 있을 때 깨우는 용도). 프로그램 종료 시 삭제. 예약이 불가능한 환경이면 긴 작업을 백그라운드로 돌리고 그 사실을 `state.json` 에 적는다.
- 긴 계산(은행·인증·캠페인)은 백그라운드 실행 + 작업별 결과 파일. 최대 실행 시간에 걸리면 이어서.

### 9.2 실시간 수집기

- `scripts/collector_watchdog.sh`(저장소에 둔다 — 이전 것은 스크래치패드에 있어 사라졌다): `python -m btc_quant.live healthcheck --heartbeat runtime/collector.heartbeat --max-age 90 --max-data-age 300` 실패 시 `python -m btc_quant.live collect --exchanges coinone,upbit --symbols BTC-KRW,XRP-KRW,ETH-KRW …` 재기동. 1분 간격 루프, `nohup`, 로그 `logs/collector_watchdog.log`.
- 컨테이너가 사라지면 호가·체결 원자료는 없어진다. 대응: (a) 1분봉은 REST 로 되받는다(수집기 백필·`research.forward`), (b) 비용 표본 요약(시간 버킷별 분위수)을 `persist_runtime.sh` 가 6시간마다 `research/runtime_snapshots/costs/` 에 커밋, (c) 공백은 `collector_gaps.jsonl` 요약으로 남겨 보고.

### 9.3 페이퍼 상시 실행

- `scripts/paper_watchdog.sh`: T1 후보 목록(`research/program/paper_slots.yaml`)으로 `python -m btc_quant.paper run --specs … --runtime runtime` 유지, 하트비트 검사, 죽으면 재기동(재기동 시 REST 워밍업 + 꺼져 있던 구간 재현 — 기존 `replayed` 표시).
- `persist_runtime.sh`: `runtime/paper/*/{state.json,trades.jsonl,fills.jsonl,decisions.jsonl,meta.json}` 를 30분마다 `research/paper_runs/<키>/` 로 복사·커밋·푸시. 새 컨테이너에서는 반대로 복원한 뒤 페이퍼를 띄운다.
- 가동 비율이 P4(70%)에 못 미치면 14일로 연장. 그래도 미달이면 그 후보의 F4 는 "환경 미달로 판정 불가"로 두고 최종 보고에 **상시 실행 환경(PC/VPS)이 필요하다**고 적는다(§13 가정). 이 경우 후보는 "조건부"로 보고한다.

### 9.4 알림 (큰 진전·실패 때만)

보낸다: Phase 3 완료(연구 시작), T1 후보 첫 발생과 이후 발생(하루 최대 1회로 묶음), 페이퍼 판정 결과, 기준 느슨화 적용, 레드팀 발견, 판정 데이터 소진, 안전 정지, 6시간 넘는 운영 장애, 결선 결과, 최종 보고 준비 완료, 승인 대기 중 자동 강등.
보내지 않는다: 캠페인 개별 결과, 탈락, 주기 정리, 엄격화·중립 변경, 재시작 복구.
수단: 휴대폰 알림 도구(없으면 `btc_quant.live.notify` 텔레그램, 그것도 없으면 `research/reports/NOTICE_<날짜>.md`). 문장은 한국어 3줄 이내, 숫자에 표본 크기.

---

## 10. 최종 보고 형식

파일: `research/reports/final/final_report_<날짜>.md`(본문, 한국어·쉬운 말) + `final_appendix_<날짜>.md`(자동 생성 표) + 사람용 세부 `research/reports/human/final_<날짜>.md`.
작성 순서: `program phase report`(프로그램 종료 기록 — 이후 연구·기준 변경·개봉 불가) → 생성기 실행 → 운영자가 본문 작성(이때부터 검증·잠금 수치를 읽어도 된다. 더 고를 것이 없기 때문) → 알림.

본문 구성 (첫 화면에서 결정할 수 있게)

1. **결론 한 줄 + 승인 요청**: "후보 N개 묶음을 소액 실전(T2, 자본 10% × 운용 크기)에 올릴지" 또는 "권할 전략 없음".
2. **한 장 표** — 후보별: 무엇을 하는 전략인지 한 문장 / 운용 크기 / 구간별 수익·Sharpe(개발·검증·잠금, 일수·거래 수) / 보수적 Sharpe / 결합 증거 p 와 문턱 / 표본 밖 12개월 p / 최대 낙폭·최악 월(운용 크기) / 예상 낙폭(MC 95%) / 보유 대비 Sharpe·Calmar / 페이퍼 결과(P1~P4, 일수·체결 수) / 비용 등급.
3. **위험**: 후보별 "이럴 때 진다"(국면·비용·지연), 묶음 낙폭과 묶음 배수, 자동 정지선(§7 한도), "죽은 전략일 확률"에 대한 정직한 설명(소멸 엣지 통과 상한 10~15%, 그래서 소액부터).
4. **기준선**: 3코인 보유·관리형 보유의 같은 표. "보유가 낫다"에 해당하는 후보 표시.
5. **얼마나 많이 시도했나**: 전체 시도 수(계열별), 캠페인 수, Val·잠금 사용 수/예산, 깔때기(선별 → 캠페인 → 개발 생존 → T1 → 페이퍼 → 결선 → 최종).
6. **기준을 어떻게 바꿨나**: 변경마다 무엇을·왜·합성 수치(오탐 x/n, 검출 y/n)·벌점 L·구제 효과 표. 인증 실패로 거부된 제안도.
7. **탈락 목록(graveyard)**: 결선·페이퍼·검증에서 떨어진 후보 전부와 사유, 계열별 실패 유형 요약.
8. **심사 기록**: skeptic·judge 판정, 레드팀 발견과 조치, 과정 실수(사후분석), 바닥 규칙 해시 이력, 정비 창구 이력.
9. **한계**: 합성 인증의 범위, 페이퍼 가동 비율, 컨테이너 환경 제약, 잠금 구간 사전 노출(PLAN §11 — 해당 블록 계열 후보 표시).
10. **승인하면 일어나는 일 / 거절하면**: T2 는 실거래 실행기 구현(별도 승인)이 전제라는 점, 승인 전까지 페이퍼·감시 계속.
11. **가정한 값**(§13) — 사용자가 바꾸고 싶을 수 있는 것.

대화 요약(10줄 이내)도 같은 순서: 결론 → 후보 표 축약 → 시도 수·기준 변경 수 → 리포트 경로.

---

## 11. 완료 정의

- Phase 0~3: §6 표의 완료 조건 + 전체 테스트 녹색 + `rs-001` 잠금 + 총연습 통과.
- Phase 4: §7.6 조건 중 하나로 종료, 주기 리포트 전부 존재.
- Phase 5: 결선 기록(또는 "결선 없음" 기록), 판정 예산 초과 0.
- Phase 6: 최종 리포트 3개 파일 + 알림 + 대화 요약. 리포트 생성 테스트 통과(원장과 불일치 0).
- 전 기간: 바닥 위반 0, 잠금 사전 열람 0(guard 로그·잠금 원장으로 확인), 원장에 없는 시도 0(사후분석으로 발견되면 기록·보고).

---

## 12. 스스로 정하는 규칙 (사용자에게 묻지 않는다)

| 상황 | 결정 |
|---|---|
| 안전과 검출이 충돌 | 안전(오탐·바닥)을 지키고 검출 미달은 보고 |
| 두 구현 방법 중 선택 | 기본값이 현재 동작인 쪽, 테스트하기 쉬운 쪽 |
| 계획의 수치가 코드와 안 맞음(키 이름·함수 위치 등) | 코드에 맞추고 차이를 `docs/AUTONOMY.md` "계획과 다른 점"에 한 줄 |
| 데이터 스냅샷이 이전과 다름 | 새 스냅샷으로 취급, 보고, 진행 |
| 인증 은행 계산이 예상의 3배 넘게 걸림 | 묶음별 수를 절반으로(귀무는 400 → 최소 320 유지: 상한 2% 조건은 2건 이하로 자동 엄격), 사실 보고 |
| hook 이 `agent_type` 을 안 줌 | §4-I 의 대체 방식 |
| 예약 작업·알림 도구가 없음 | §9 의 대체 수단 |
| 사용자만 정할 수 있는 새 위험 선호 | §13 표에 "가정한 값"으로 추가하고 가장 보수적인 값으로 진행 |
| 이 문서가 다루지 않은 판단 | 목표("수익이 나고 손실 위험이 작은 전략", 가짜를 실전에 올리지 않기)에 비추어 정하고 `research/program/decisions.md` 에 날짜·이유 기록 → 최종 보고 부록 |

---

## 13. 가정한 값 (사용자가 나중에 바꿀 수 있음 — 바꾸지 않으면 그대로 진행)

| # | 항목 | 가정한 값 | 왜 사용자 몫인가 |
|---|---|---|---|
| 1 | "오탐 ≤ 2%"의 범위 | 엣지가 **전혀 없는** 가짜(어려운 가짜 포함)의 T1 진입 기준. "예전엔 됐지만 죽은 전략"은 별도 상한 **10%**(F1c 0.10, 대안 0.15) | 12개월 표본 밖 자료로는 이 경우를 2% 까지 못 막는다. 더 낮추려면 진짜도 더 많이 버려야 한다 |
| 2 | 최악 월 −8% 의 적용 | 달력 월, 평가 기준(MTM), 운용 크기, 개발·검증·잠금 중 최악. 넘으면 크기 조절 | 초안의 수치를 식으로 옮긴 것 |
| 3 | 판정 데이터 예산 | 검증 추가 10회, 잠금 5회, 결선 2번 | 예시("후보 10개")보다 잠금을 더 아꼈다 |
| 4 | 기준 느슨화 횟수 | 프로그램 전체 3회 | 많이 바꿀수록 가짜 위험 |
| 5 | 최종 묶음 크기 | 최대 3개, 전략 간 동일 비중, 묶음 낙폭 20% 에 맞춘 묶음 배수 | 자본 배분은 위험 선호 |
| 6 | 페이퍼 | 7일, 부족하면 14일. 실시간 가동 ≥ 70% | 이 클라우드 환경의 한계. 미달이면 "조건부" 후보로 보고 |
| 7 | 프로그램 길이 | 기본 3주, 최대 5주, 전체 400 시도 | 시간·비용 |
| 8 | 비용 바닥 | 2026-11-11 현재 비용 모델 값(18개) 아래로 내리지 않음. BTC 비용 하향(보류 중)은 계속 사용자 | 비용을 낮추는 실수는 실제 손실로 이어짐 |
| 9 | 기준선 참조 스펙 | 4시간 EMA(180)·band 0·3코인 | 결과를 보기 전에 고정해야 해서 여기서 정함 |
| 10 | 상시 실행 환경 | 없음(클라우드 컨테이너 + 재개 장치) | PC/VPS 결정은 이전부터 사용자 대기 항목 |

---

## 14. 실행자가 첫 번째로 할 일

1. 브랜치 `claude/btc-scalping-quant-system-o6n5w7` 최신 상태 확인, `uv sync && uv run pytest -q` (2,572개 통과 확인).
2. §2 의 문서를 읽는다(사람 전용 경로는 열지 않는다).
3. Phase 0.2 — `scripts/restore_data.sh` 를 만들어 데이터를 복원하고 스냅샷 해시를 확인한다(`data/` 가 비어 있어 이것 없이는 아무것도 못 돌린다). 복원이 도는 동안 0.3~0.5 를 진행한다.
