# AI 연구 체계 — 연구 모드 운용 가이드

> PLAN §3.3(AI 권한), §5(캠페인·관문·잠금), §6(레지스트리), §8(AI 연구 체계), §10b(v4 개선)의 Claude Code 쪽 구현.
> 구성 파일: `.claude/settings.json`, `.claude/hooks/guard.py`, `.claude/agents/*.md`, `.claude/skills/*/SKILL.md`.
> 연구 입력: `research/knowledge/`(사전 지식, 노출 태그), `research/lessons/`(오답노트, `btc_quant.research.lessons`).

## 1. 개요

| 장치 | 파일 | 역할 |
|---|---|---|
| Hook (PreToolUse) | `.claude/hooks/guard.py` | 연구 모드에서 보호 경로 쓰기·사람 전용 명령·잠금 데이터 읽기·사람 전용 Val 세부 읽기·MCP 우회(원장 쓰는 연구 진입점 직접 실행) 차단, 모든 도구 호출 로그 |
| 권한 규칙 | `.claude/settings.json` `permissions.deny` | 모드와 무관하게 항상: `.env`, `runtime/secrets/**`, 키 파일, `~/.ssh` 읽기 금지, 강제 push 금지 등 |
| Subagent | `researcher` | 지식 베이스·오답노트·유사 탐색 → 메커니즘·반대편 → 신호 선별 → 검정력 → 원칙적 청산 캠페인 → 관문 → 견고성 → 오답노트 → 리포트 |
| | `skeptic` | **결과를 보기 전에 예상 실패 유형을 먼저 기록**, 자동 견고성 묶음을 증거로 판정(수치 인용 필수). 파일 쓰기 도구 없음 |
| | `data-steward` | 데이터 품질·커버리지 점검 (코인원 봇성 체결 오염 주의) |
| Skill | `research-campaign` | v4 캠페인 전체 절차 (§5) |
| | `lessons-review` | 오답노트: 사실 기록·해석(미검증)·검증 표시·파생 가설·유사 탐색·사후분석·주간 점검 (§6) |
| | `candidate-review` | 예상 실패 유형 사전 기록 + 견고성 묶음 + 반대 논리 체크리스트 + 리포트 양식 |
| | `block-authoring` | 새 블록 = 실험 블록(D11, 자동 수용 검사), 근거에 메커니즘·노출 표기 |
| | `daily-report` | 아침 리포트 (오답노트·주간 예산 포함) |
| MCP 서버 | `.mcp.json` → `btc_quant.mcp` (`btcq`, docs/MCP.md) | 연구 도구 20개: `list_blocks`, `get_data_summary`, `register_hypothesis`, `find_similar`, `screen_signals`, `power_check`, `create_and_run_campaign`, `list_campaigns`, `get_campaign_report`, `query_ledger`, `lessons_view`, `lessons_add_interpretation`, `lessons_mark`, `lessons_derive_hypothesis`, `propose_block`, `request_lockbox_eval`, `registry_status`, `params_status`, `propose_criterion`, `forward_status`. 승인·활성화·롤백·잠금 개봉·설정/원장 편집·주문·키 도구는 없음 |

에이전트는 MCP 도구를 쓰고(주간 예산·선별 연결·엄격 검정력·중복 점검을 원장 쓰기 전에 강제, Val/잠금은
결과 코드만 — 원장에 쓰는 연구 진입점의 Bash 직접 실행은 연구 모드 hook 이 거부, §4.2), MCP 가 다루지 않는 일(사후분석, 개별 스펙 견고성 재계산 등)만 skill 문서의 Python API
(`btc_quant.research.lessons`, `btc_quant.research.robustness`, `btc_quant.registry.ledger.Ledger` 읽기,
`btc_quant.registry.splits.DataAccess`)로 한다. MCP 도구 호출(`mcp__*`)도 hook 로그 대상이며, 연구 모드에서
사람 전용 기능처럼 보이는 이름의 MCP 도구(approve/activate/rollback/open_lockbox/order/key 등)는 hook 이 거부한다.

## 2. 연구 모드란

hook은 **환경 변수 `BTCQ_RESEARCH_MODE=1`이 Claude Code 프로세스에 설정된 경우에만** 동작한다.
설정되지 않은 일반 개발 세션에서는 즉시 종료(출력 없음)하므로 개발 작업(엔진·관문·레지스트리 코드,
설정, 이 hook 자체 수정)에 아무 영향이 없다.

hook 프로세스는 Claude Code의 환경을 물려받는다. 세션 안에서 AI가 Bash로 `export`/`unset` 해도
그 셸에만 적용되고 hook의 환경은 바뀌지 않는다. `.claude/settings*.json` 쓰기는 연구 모드에서 차단된다.

## 3. 연구 세션 시작

```bash
cd <repo>
export BTCQ_RESEARCH_MODE=1

# 대화형: researcher 에이전트를 메인 에이전트로
claude --agent researcher
#   → "/research-campaign donchian 돌파 + vol_regime" 처럼 skill 호출

# 무인 실행(야간 캠페인 등)
claude -p --agent researcher "research-campaign skill로 다음 가설 캠페인 1회 실행"
claude -p "daily-report skill로 오늘 아침 리포트 작성"
```

- 후보가 나오면 researcher가 `skeptic` 서브에이전트(candidate-review)를 호출하고, 데이터가 의심스러우면
  `data-steward`를 호출한다.
- 클라우드 환경에서는 환경 설정의 환경 변수에 `BTCQ_RESEARCH_MODE=1`을 넣는다.
- 확인: 연구 모드에서 아무 도구나 한 번 쓰면 `logs/research_tool_calls.jsonl`에 줄이 추가된다.
- MCP 서버 `btcq`(`.mcp.json`)는 프로젝트 MCP 서버로 자동 등록된다(첫 실행 시 승인, `/mcp` 로 확인, 도구 20개).
  사용법·안전 속성은 docs/MCP.md.

## 4. 무엇이 차단되나 (연구 모드)

### 4.1 쓰기 차단 (Write/Edit/MultiEdit/NotebookEdit, Bash 리다이렉션·tee·sed -i·mv·cp·rm 등)

| 경로 | 이유 |
|---|---|
| `strategies/{active,review,retired}/**` | 승인·운용 전략은 레지스트리 코드 + 사람 승인으로만 변경 (§6) |
| `config/gates.yaml`, `config/splits.yaml`, `config/risk*.yaml` | 관문·분할·리스크 공식은 고정, 변경은 사람만 (§2.1, §5.2, §7) |
| `research/ledger/**` | 원장은 append-only, `Ledger` Python API로만 추가 |
| `research/lessons/*.jsonl`, `research/lessons/.lock`, `research/lessons/LESSONS.md` (README.md 제외) | 오답노트 사실은 불변, 해석·표시·사후분석은 `btc_quant.research.lessons` API로만 추가, LESSONS.md는 생성물 |
| `research/lockbox_approvals/**`, `research/activation_approvals/**` | 사람 승인 기록 |
| `research/lockbox_requests/**` | 잠금 평가 요청은 MCP `request_lockbox_eval`(자격 검사)로만 기록 |
| `src/btc_quant/{backtest,evaluation,registry,data}/**` | 엔진·관문·레지스트리·데이터 코드는 AI 수정 불가 (§3.3) |
| `data/raw/**` | 원천 데이터는 수집기만 기록 |
| `.claude/{settings*.json,hooks,agents,skills}` | 연구 세션이 자기 감시 장치를 바꾸지 못하게 |
| `logs/research_tool_calls.jsonl` | 감사 로그 위변조 방지 (hook만 추가) |

허용: `src/btc_quant/strategy/blocks/**`(새 블록 **제안** — block-authoring skill이 테스트·문서를 요구하고
사람이 머지), `tests/**`, `strategies/draft/**` 등 나머지 상태 폴더(전이는 레지스트리 코드가 검증),
`research/reports/**`, `research/knowledge/**`, `research/lessons/README.md`, `docs/BLOCKS.md` 등.

### 4.2 명령 차단 (Bash)
- 사람 전용 관리 CLI: `admin_cli`(읽기 전용 `status` 포함), `approve-lockbox`, `activate`, `rollback`,
  Python에서의 `activate(`/`rollback(`/`write_lockbox_approval`/`write_activation_approval` 호출.
  레지스트리 상태는 `StrategyRegistry().status()`, 잠금 현황은 `DataAccess().lockbox_status()`로 본다.
- `git push`, 그리고 작업 트리를 덮어쓰거나 이력을 바꾸는 git 명령(checkout, restore, reset, stash,
  clean, rebase, merge, pull 등). 허용: status/log/diff/show/blame/ls-files/branch/fetch 등 읽기 + add/commit.
- `.env`, `runtime/secrets`를 언급하는 명령.
- **MCP 우회 차단** (2026-11-09): 원장·오답노트에 쓰는 연구 진입점의 직접 실행 — `btc_quant.research.cli run|screen|gates`
  (`btc-quant-research` 포함), Python 의 `run_campaign`/`run_pipeline`/`run_screen`/`run_forward`/`record_runs_batch`/
  `signal_study.screen`, `Ledger` 쓰기(`register_hypothesis`, `create_campaign`, `record_*`, `open_val_evaluation`,
  `append`), `LessonBook` 의 `add_interpretation`/`mark`/`derive_hypothesis`/`apply_derivation`. 명령 문자열(`python -c`,
  heredoc)과 `python <script.py>`·`python -m <btc_quant 밖 모듈>` 대상 파일 내용을 검사한다. 이 일들은 MCP 도구
  (`screen_signals`, `create_and_run_campaign`, `register_hypothesis`, `lessons_*`)로만 한다 — 주간 예산·선별 필수·
  엄격 검정력·중복 점검이 원장 쓰기 전에 강제되는 경로다(docs/MCP.md §4.3). 허용으로 남는 것: 원장·오답노트 읽기,
  `find_similar`/`check_template`/`trial_budget`, `study_signal`(순수 계산), `robustness_for_spec`, `postmortem`,
  `render_markdown`, `record_facts_from_*`(멱등), `research.cli list|report`, `calibration_study`(임시 저장소).

### 4.2b 사람 전용 Val/잠금 세부 읽기 차단 (Read/Grep/Glob/Bash)
PLAN §10b-9 "Val/잠금은 결과 코드만"을 파일 수준에서 강제한다.

| 경로 | 대신 쓸 것 |
|---|---|
| `research/reports/human/**` (사람용 전체 캠페인 리포트: G6 세부·리스크 한도) | `research/reports/campaign_<id>.md` (에이전트 안전), MCP `get_campaign_report` |
| `research/campaigns/<id>/pipeline.json` | `research/campaigns/<id>/pipeline_public.json` |
| `research/ledger/{runs,val_evaluations,lockbox,transitions}.jsonl` | MCP `query_ledger` (dev 지표, 결과 코드만) |
| `strategies/**/*.risk.yaml` (Val 기반 §7 한도) | – (사람 전용) |

- 그 파일을 덮을 수 있는 **넓은 검색**도 거부한다: `Grep` 의 `path` 가 저장소 루트·`research`·`research/reports`·
  `research/campaigns`·`research/ledger`·`strategies` 이고 `glob`/`type` 이 *.md/*.json/*.jsonl/*.yaml 을 배제하지
  않으면 거부(예: `path: src`, `glob: "*.py"`, `type: py`, `path: research/knowledge` 는 허용). Bash 의 재귀 검색
  (`grep -r/-R`, `rg`, `ag`, `ack`, `git grep`)도 같은 규칙(경로 없음 = `.`; `--include`/`-g`/`-t` 로 좁히면 허용),
  그런 파일로 펼쳐질 수 있는 셸 글롭(`research/reports/*/campaign_*.md`)과 파일 이름 언급(`pipeline.json`,
  `reports/human`, `runs.jsonl` …)도 거부한다. `Glob` 은 그 경로를 직접 겨냥할 때만 거부(이름만 나열하므로).
- Python 으로 같은 파일을 여는 것(`LessonBook.record_facts_from_pipeline(<캠페인 폴더>)`, `power` 압축비 등)은 코드
  내부 경로라 막지 않는다 — 결과 코드만 남기는 정제는 그 API 들이 이미 한다.

### 4.3 잠금 구간 읽기 차단 (Read/Grep/Glob/Bash, MCP 인자)
- 잠금 구간 = 2026-05-01 ~ 2026-11-08 → `data/raw/**/year=2026/month=05..11/**` 경로.
  (11월 파티션에는 Forward(11-09~)도 섞여 있지만 파티션 전체를 막는다.)
- 연도·월이 고정되지 않은 `data/raw` 경로(예: `data/raw/**/*.parquet`, `data/raw/exchange=upbit`,
  `year=2026/` 월 미지정)도 잠금 구간을 포함할 수 있으므로 차단한다.
  Dev/Val 파티션을 명시한 경로(`year=2024/...`, `year=2026/month=03/...`)는 허용.
- 연구 코드는 원천 파일 대신 `DataAccess().read("dev"|"val", …)`를 쓴다.

### 4.4 로그
연구 모드의 **모든 평가된 호출**(허용·거부)을 `logs/research_tool_calls.jsonl`에 JSONL로 남긴다:
`ts, session_id, agent_type, tool_name, tool_input(긴 값은 400자 절단), decision, reason`.
`logs/`는 gitignore 대상이다.

### 4.5 항상 적용 (모드 무관, `permissions.deny`)
`.env`·`.env.*` 읽기/편집, `runtime/secrets/**`, `runtime/**/*.key|*.pem`, `~/.ssh`, `~/.aws` 읽기,
`git push --force/-f`, `rm -rf /`·`rm -rf ~`. 일반 개발 작업은 막지 않는다.

## 5. v4 연구 절차 (PLAN §10b)

```
⓪ 지식 베이스 + 오답노트 + find_similar + 주간 예산 ─▶ ① 가설: 메커니즘·반대편·범위·노출 표기
   ─▶ ② 신호 선별(이벤트 스터디, 청산 없음) ─▶ ③ 검정력 계산 ─▶ ④ 전략 캠페인(진입 고정, ATR 청산 ≤ 6)
   ─▶ ⑤ G0~G5 → 기여도 → 견고성 묶음 → skeptic → G6(Val, 결과 코드만) ─▶ ⑥ 오답노트 사실·해석 → 리포트
```
| 단계 | 내용 | API |
|---|---|---|
| ⓪ 읽기 | `research/knowledge/*.md`(잠금·Val 노출 태그), `research/lessons/LESSONS.md`, 원장 계열 시도 수 | `LessonBook().find_similar(template=, text=)`, `trial_budget(family)` |
| ① 가설 | 한 문장 + **메커니즘 + 반대편(누가 잃어 주나)** + 무엇이 죽이나 + 범위. 교훈에서 나온 가설은 파생 가설(계열 상속) | `derive_hypothesis` → `apply_derivation` → `check_template` |
| ② 신호 선별 | 진입(+필터) 이벤트 스터디 vs 매칭 귀무, 계열 단위 다중검정(BH). 통과 신호만 전략화 | MCP `screen_signals` (연구 모드에서 `signal_study.screen`·`run_screen` 직접 실행은 hook 거부) |
| ③ 검정력 | 계획 격자의 실질 시도 수에서 G5 통과 최소 Sharpe | `btc_quant.research.power` (CLI `--days --grid --family --target`) |
| ④ 전략 캠페인 | 진입·필터 고정, 청산 = ATR 배수 SL/TP + 정보 지평 기반 최대 보유, **청산 변형 ≤ 6** | MCP `create_and_run_campaign` (사람: `research.cli run <template>`) |
| ⑤ 관문·견고성 | G0~G6 파이프라인(Val 예산·누적 Holm), 견고성 묶음(지연·상위 거래 제거·연도 제외·비용 ×1.5·국면·알파·거래소 재현), skeptic | `create_and_run_campaign` 안에서 실행, 결과 `get_campaign_report`; 개별 재계산 `robustness.robustness_for_spec` |
| ⑥ 오답노트 | 사실 자동 기록 → 해석 0~3개(미검증) → 이전 해석 검증/반박 표시 → 사후분석 | 사실은 파이프라인이 자동 기록, MCP `lessons_add_interpretation`·`lessons_mark`·`lessons_derive_hypothesis`, Python `postmortem`, `render_markdown` |

## 6. 오답노트 (`research/lessons/`, `btc_quant.research.lessons`)

비판적 검토에서 나온 요구를 코드로 강제한다. 상세: `research/lessons/README.md`.

| 요구 | 구현 |
|---|---|
| 사실과 해석 분리 | 사실 = pipeline.json·신호 선별 결과에서 자동 추출, 수정 API 없음, 줄마다 내용 해시(`verify()`), 연구 모드에서 파일 직접 쓰기 차단. 해석 = AI 작성, 항상 `unverified`로 시작, 신뢰도·근거 필수 |
| 검증 표시 | `mark(int_id, verified/refuted, by_campaign)` — 사실이 기록된 **후속 캠페인**(원 캠페인과 다르고 해석보다 나중)만 근거 |
| 자동 실패 분류 | G0→IMPL_OR_DATA_ISSUE, G1→LOW_SAMPLE, G2→NO_SIGNAL(비용 전 +·비용 후 −면 COST_EATEN), G3→PARAM_SPIKE(비용 후 인접 ≤ 0 < 비용 전 인접이면 COST_EATEN; 비용 전 = 캠페인 `gross_sharpe_w`), G4→UNSTABLE, G5→SELECTION_BIAS, G6→VAL_FAIL(엔진 불일치는 IMPL_OR_DATA_ISSUE 추가), 견고성: 상위 거래→OUTLIER_DEPENDENT, 연도·국면·거래소→REGIME_DEPENDENT, 지연→LATENCY_SENSITIVE, 비용→COST_EATEN, 알파→NO_SIGNAL, 그 밖→UNCLASSIFIED |
| 파생 가설 시도 비용 상속 | `derive_hypothesis(parent, …)` 는 부모 계열을 강제(`FamilyInheritanceError`). 새 계열 이름은 `human_override=True` + 사유, **연구 모드에서는 거부**. 템플릿에 `[derived-from drv-…]` 태그 → `check_template`·사실 기록 때 계열 일치 점검(`family_check`) |
| Val/잠금은 결과 코드만 | 사실 기록기는 G6/G7·grade_checks·risk_limits·val_* 필드를 제거하고 `val_outcome`·`val_aggregate`(정수)·`lockbox_outcome` 만 남김. AI 입력(해석·근거·사후분석)은 Val/잠금 수치 문장을 거부(`RestrictedInfoError`, 휴리스틱) |
| 범위·만료 | 사실마다 scope(거래소·시간축·구간·블록·스냅샷·커밋), `review_after`(180일), `reexamine_if` |
| 과정 사후분석 | `postmortem(...)` 별도 레코드(근본 원인·재발 방지). 실제 사건 4건 기록됨: Phase 0 잠금 노출, G2 귀무 거래 수 편향, `.gitignore` data/ 패턴, DSR 검정력(청산 격자 ×80 → v4) |
| 중복 방지 | `find_similar`: 같은 진입·필터 블록 + 모든 격자 겹침 → exact, 블록만 같음 → same_blocks/same_entry, 텍스트 토큰 Jaccard → fuzzy. 결과(사실 기록의 taxonomy)·계열 누적 시도 수를 함께 보여 줌 |

통합: 파이프라인 끝에서 `btc_quant.research.lessons.record_facts_from_pipeline(out_or_path, root=…)`
(멱등, LESSONS.md 재생성). 선별은 `record_facts_from_screen(result)`.

## 7. 주간 시도 예산 (계열별 배분 규칙)

`LessonBook().trial_budget(family)` 가 계산한다(자문용 — 코드가 실행을 막지는 않지만 researcher 는 지켜야 한다).
시도 = 원장 runs 의 고유 (campaign_id, logic_hash), 최근 7일 기준.

| 규칙 | 값 |
|---|---|
| 전역 상한 | 2,000 새 시도 / 7일 |
| 기본 계열 | 600 / 7일 |
| 새 계열(사실 기록 없음) | 300 / 7일 — 신호 선별부터 |
| 유망 계열(최근 캠페인에 Dev 생존자 또는 Val 통과) | 900 / 7일 |
| 동결 계열(최근 2개 캠페인 주 실패 유형이 모두 NO_SIGNAL·COST_EATEN) | 0 — 재검토 조건 충족 또는 사람 해제까지 |
| 남은 예산 | min(계열 허용 − 계열 사용, 전역 상한 − 전역 사용) |

근거: 실질 시도가 늘수록 G5 통과에 필요한 Sharpe 가 오른다(PLAN §10b 표). 예산은 "많이 돌려 하나 걸리기"를
막고, 신호 선별(구성당 1시도)과 원칙적 청산(≤ 6)으로 시도를 아끼게 한다.

## 8. 한계 (정직하게)

- **hook은 경보선(tripwire)이지 샌드박스가 아니다.** Bash 검사는 명령 문자열에 대한 정규식 휴리스틱이다.
  `cd config && sed -i … gates.yaml`, 변수·글롭으로 조립한 경로, Python 코드 안에서 문자열을 만들어
  여는 경우, 다른 스크립트를 경유한 쓰기 등은 놓칠 수 있다.
- MCP 우회 차단·사람 전용 읽기 차단도 같은 휴리스틱이다: 별칭(`from … import run_campaign as r` 는 잡지만
  `getattr(mod, "run_" + "campaign")` 는 못 잡음), 동적 경로 조립, 다른 스크립트를 거쳐 import 하는 파일, 셸 변수로
  만든 경로, `find … -exec cat` 류는 놓칠 수 있다. 반대로 넓은 검색은 좁혀야 통과한다(`Grep` 에 `path`/`glob`/`type`).
- 반대로 과잉 차단도 있다: 쓰기 동사(`rm`, `cp`, `mv` …)와 보호 경로가 한 명령에 함께 있으면
  보호 경로가 읽기 대상이어도 거부한다(예: `cp config/gates.yaml /tmp/`). 명령을 나누면 된다.
- **잠금 구간의 실제 강제 장치는 Python `DataAccess` 계층**(`btc_quant.registry.splits`)이다.
  `LockboxKey` 없이 잠금 구간과 겹치는 범위를 읽으면 `LockboxAccessError`. hook의 경로 차단은 보조 수단이다.
  `btc_quant.data.store`를 직접 쓰는 기존 CLI(`btc-quant quality`, `cost-floor`)는 분할을 강제하지 않으므로
  연구 모드에서는 `--start/--end`를 잠금 밖으로 명시하거나 `DataAccess`를 쓴다.
- 레지스트리 전이·활성화·잠금 개봉의 실제 강제 장치는 `btc_quant.registry`(승인 파일·대화형 확인·원장)이다.
- hook은 `python3`가 필요하다. hook 내부 오류·입력 파싱 실패 시 연구 모드에서는 **거부(fail closed)**.
  다만 타임아웃(10초) 시 Claude Code 기본 동작은 차단하지 않는 것이다.
- 개발 세션에 `BTCQ_RESEARCH_MODE=1`이 실수로 남아 있으면 개발 작업도 막힌다 — 의도된 동작.
- hook은 `.claude/settings.json`에 등록되어 있으므로, 이 파일이 로드되지 않는 실행 방식(설정 파일을
  무시하는 옵션, 프로젝트 밖에서 시작 등)에서는 동작하지 않는다.

- 오답노트의 Val/잠금 문장 필터는 정규식 휴리스틱이다(지표 단어 + Val/잠금 단어 + 숫자). 우회 가능하므로
  규칙 자체(결과 코드만)를 지키는 것이 우선이다. 주간 예산은 자문 값이며 실행을 막지 않는다.
- 계열 override 의 연구 모드 거부는 `BTCQ_RESEARCH_MODE` 환경 변수에 의존한다(셸에서 unset 가능 — 경보선).

## 9. 개발자용

- MCP 서버: `uv run pytest -q tests/test_mcp_tools.py tests/test_mcp_safety.py` (docs/MCP.md §6).
- 테스트: `uv run pytest -q tests/test_guard_hook.py` (hook을 하위 프로세스로 실행, 모드 on/off·허용/거부 사례),
  `uv run pytest -q tests/test_research_lessons.py` (오답노트: 분류·불변성·계열 상속·Val 차단·유사 탐색·사후분석).
- 보호 경로 추가: `guard.py`의 `PROTECTED_WRITE` 목록에 (정규식, 이유) 추가 후 테스트 보강.
- hook 프로토콜 참고: <https://code.claude.com/docs/en/hooks> (PreToolUse 입력 `tool_name`/`tool_input`/`cwd`,
  출력 `hookSpecificOutput.permissionDecision = "deny"` + `permissionDecisionReason`),
  권한 규칙: <https://code.claude.com/docs/en/permissions>, 서브에이전트: <https://code.claude.com/docs/en/sub-agents>,
  skills: <https://code.claude.com/docs/en/skills>.
