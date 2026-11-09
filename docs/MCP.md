# 연구 MCP 서버 (`btcq`)

> PLAN §8(AI 연구 체계)·§10b(v4)의 MCP 서버 구현. 코드: `src/btc_quant/mcp/`
> (`tools.py` = 검증 래퍼, `server.py` = 등록, `__main__.py` = stdio 진입점), 설정: `.mcp.json`.
> 의존성: 공식 MCP Python SDK `mcp>=2.3.0,<3` (2.x 의 `mcp.server.mcpserver.MCPServer` — 1.x 의 FastMCP 후속).

## 1. 개요

연구 에이전트(researcher·skeptic·data-steward)가 Python 코드를 직접 짜지 않고 **도구만으로** 캠페인 1회를
완주하게 한다(M5 완료 조건). 각 도구는 기존 연구 API(`research.campaign`·`pipeline`·`signal_study`·`power`·
`lessons`, `registry.ledger`·`splits`·`strategies`, `strategy.blocks.acceptance`, `params`) 위의 **얇은 검증
래퍼**이며, 결과는 항상 JSON 요약이다(큰 프레임·수익 행렬은 반환하지 않는다).

- 성공: `{"ok": true, "tool": "<이름>", ...}`
- 실패: `{"ok": false, "tool": "<이름>", "error": {"code": "...", "type": "...", "message": "...", ...}}`
  — 예외를 던지지 않는다(MCP `isError` 가 아니라 구조화된 결과). 주요 코드: `weekly_budget_exceeded`,
  `grid_budget_exceeded`, `screen_gate`, `power_check_failed`, `repeat_trial`, `invalid_template`,
  `lockbox_denied`, `restricted_info`, `family_inheritance`, `lessons_error`, `anti_goalpost`, `not_eligible`,
  `table_not_allowed`, `column_not_allowed`, `path_outside_root`, `path_not_allowed`, `module_error`,
  `not_found`, `invalid_argument`, `internal_error`.

## 2. 연구 세션 시작

```bash
cd <repo>
uv sync                                   # mcp 의존성 설치 (최초 1회)
export BTCQ_RESEARCH_MODE=1               # guard hook 활성화 (docs/AI_RESEARCH.md §2)
claude --agent researcher                 # .mcp.json 의 btcq 서버가 프로젝트 MCP 서버로 뜬다
#   첫 실행 시 프로젝트 MCP 서버 사용 승인 프롬프트 → 승인. 확인: /mcp → btcq (도구 20개)
#   예: "/research-campaign upbit_lead_impulse 계열로 신호 선별부터"
claude -p --agent researcher "research-campaign skill로 다음 가설 캠페인 1회 실행"   # 무인
```

- `.mcp.json`: `uv run --quiet --project ${CLAUDE_PROJECT_DIR:-.} python -m btc_quant.mcp`, 서버 환경에
  `BTCQ_RESEARCH_MODE=1`. 서버 프로세스는 시작 시 스스로도 `BTCQ_RESEARCH_MODE=1` 을 설정한다
  (계열 override 등 사람 전용 경로 거부). Claude Code 에서의 도구 이름은 `mcp__btcq__<도구>`.
- 수동 실행·점검: `uv run python -m btc_quant.mcp [--root DIR]` (stdout 은 MCP 전용, 로그는 stderr).
  `BTC_QUANT_ROOT` 또는 `--root` 로 다른 연구 저장소를 가리킬 수 있다.
- 오래 걸리는 도구(`create_and_run_campaign`, `screen_signals`, `propose_criterion`)는 수 분이 걸릴 수 있다.
  Claude Code 의 MCP 도구 시간 제한은 환경 변수 `MCP_TOOL_TIMEOUT`(ms)로 늘린다.
- 모든 MCP 호출도 guard hook 로그(`logs/research_tool_calls.jsonl`) 대상이다.

## 3. 도구 목록 (20개)

| 도구 | 하는 일 | 쓰기 | 대응 Python API (대체 경로) |
|---|---|---|---|
| `list_blocks` | 블록 목록: kind, status(official/experimental), 격자·기본값, requires, live_ready | – | `REGISTRY.all(kind, include_experimental=True)` |
| `get_data_summary` | dev/val/forward 커버리지(행 수·기대 봉·공백·filled 비율). 잠금은 개봉 횟수만 | – | `DataAccess().read(...)` |
| `register_hypothesis` | 가설 사전등록(텍스트+메커니즘+반대편+성공 기준, 범위·노출), 유사 선행 연구 반환 | 원장 | `Ledger().register_hypothesis` |
| `find_similar` | 반복 점검(exact/same_blocks/same_entry/fuzzy) + 계열 주간 예산 + 템플릿 점검 | – | `LessonBook().find_similar`, `trial_budget`, `check_template` |
| `screen_signals` | 신호 선별(이벤트 스터디, 청산 없음, Dev). 주간 예산 강제, 원장·오답노트 사실 기록 | 원장·캠페인 폴더·오답노트 | `campaign.run_screen` |
| `power_check` | 격자 크기/템플릿 → G5 통과 최소 연 Sharpe, 목표 대비 판정 | – | `power.budget_report` |
| `create_and_run_campaign` | 선별 연결 캠페인 사전등록·실행 → 관문 G0~G6 → 견고성 → 기본 리포트 | 원장·캠페인 폴더·레지스트리(pipeline)·리포트·오답노트 | `run_campaign` + `run_pipeline` + `write_report` |
| `list_campaigns` | 캠페인·선별 목록(최신 먼저, 계열·예산·판정) | – | `campaign.list_campaigns` |
| `get_campaign_report` | 캠페인/선별 구조화 요약: 격자·검정력·선별 연결, Dev 깔때기·관문, 견고성·기여도, **G6 = pass/fail**, 오답노트 사실 | – | `pipeline.json` (정제) |
| `query_ledger` | 원장 읽기 전용 조회(화이트리스트 표·열, 동등 필터) + `trials` 집계 | – | `Ledger().records`, `trials_count` |
| `lessons_view` | 사실·해석(현재 상태)·무결성·주간 예산 | – | `LessonBook().facts/interpretations/verify` |
| `lessons_add_interpretation` | 해석 추가(항상 unverified, 근거 필수, Val/잠금 수치 거부) | 오답노트 | `add_interpretation` |
| `lessons_mark` | 후속 캠페인으로 해석 verified/refuted 표시 | 오답노트 | `mark` |
| `lessons_derive_hypothesis` | 파생 가설(부모 계열 상속, override 인자 없음) + 템플릿 적용·점검 | 오답노트 | `derive_hypothesis`, `apply_derivation` |
| `propose_block` | 에이전트가 쓴 `blocks/experimental/<name>.py` 에 수용 검사(새 프로세스) — 코드는 쓰지 않음 | – (통과 시 서버 프로세스에 적재) | `acceptance.acceptance_check` |
| `request_lockbox_eval` | 잠금(G7) 평가 **요청** 기록(validated/challenger + skeptic `VERDICT: SURVIVES`) — 열지 않음 | `research/lockbox_requests/` | (사람: `admin_cli approve-lockbox`) |
| `registry_status` | 전략 상태, 잠금 개봉 사용/상한, 대기 중 요청, Val 사용 예산(횟수) | – | `StrategyRegistry().status()`, `lockbox_status`, `val_status` |
| `params_status` | params_version·검증·현재 비용 모델 버전·대기 제안 | – | `python -m btc_quant.params status` |
| `propose_criterion` | class ② 관문 기준 제안(합성 보정만, anti-goalpost) → 사람 승인 대기 제안 파일 | `research/param_proposals/`, 리포트 | `params.criteria.propose_criterion` |
| `forward_status` | 전진 인큐베이션 현황(최근 일일 기록, ok 일수, 기대 구간 안 비율) | – | `Ledger().forward_results` |

템플릿 인자(`template`)는 dict 또는 **저장소 안의** YAML 경로(`research/campaign_templates/x.yaml`)다.
형식은 `research/campaigns/README.md`, 선별은 `docs/SIGNAL_STUDY.md`.

## 4. 안전 속성

### 4.1 의도적으로 없는 도구 (사람 전용)
승인(잠금·활성화·파라미터), 활성화, 롤백, 상태 전이·거부, 실험 블록 승격, **잠금 구간 개봉·열람**,
관문(`config/gates.yaml`)·비용(`config/costs.yaml`)·분할(`config/splits.yaml`)·리스크·파라미터 레지스트리 편집,
원장/오답노트 파일 직접 편집, 주문, 키·비밀. 테스트(`tests/test_mcp_safety.py`)가 강제한다:
도구 목록 = 정확히 위 20개, 이름에 금지 기능 패턴 없음, 인자 이름에 override/approve/force/key 류 없음,
`tools.py` 가 `open_lockbox`·`write_*_approval`·`activate`·`rollback`·`transition`·`approve`·`add_version(s)`·
`record_change`·`LockboxKey`·원장 저수준 쓰기를 호출하지 않음(정적 검사).
guard hook 도 연구 모드에서 이런 이름의 MCP 도구(다른 서버 포함)를 거부한다.

### 4.2 데이터·Val·잠금
- 데이터는 `DataAccess`(분할 강제)로만 읽는다. `get_data_summary` 는 dev/val/forward 만 받으며 `lockbox` 요청은
  `lockbox_denied`. 잠금 구간은 개봉 횟수·상한만 보고한다.
- **Val(G6)·잠금은 결과 코드만**: 캠페인 요약의 후보마다 `G6_val ∈ {pass, fail, not evaluated}` 와
  `val_budget_exhausted`; G6 세부(Val Sharpe·MDD·p 값·Holm)·`grade_checks`·Val 기반 `risk_limits` 는 반환하지
  않는다. `query_ledger` 의 `val_evaluations`·`lockbox` 는 `outcome` 코드만, `transitions` 는 `evidence` 제외,
  `runs` 는 dev 외 구간(G6 가 남긴 Val 실행) 지표를 `withheld` 로 숨긴다.
- 주의: 사람용 기본 리포트 파일(`research/reports/campaign_<id>.md`)에는 Val 세부 줄이 있다(기존 설계).
  에이전트는 그 수치를 인용·해석·가설 근거로 쓰지 않는다(skill 규칙). 요약은 `get_campaign_report` 를 쓴다.

### 4.3 원장 쓰기 전 거부 (MCP 경로에서 강제)
`create_and_run_campaign` 순서: 템플릿 검증 → 격자 ≤ 템플릿 예산 → **선별 연결 필수**(`from_screen` 또는
`requires_screen_pass`) → 파생 태그·계열 일치(`check_template`) → **정확 중복이면 `repeat_justification` 필수**
→ **주간 시도 예산**(계열·전역, 문서상 자문이지만 MCP 에서는 강제) → 선별 통과 구성 매칭 → **엄격 검정력**
(`power.target_sharpe` 선언 + 필요 Sharpe ≤ 목표). 하나라도 실패하면 원장에 아무것도 쓰지 않는다.
`screen_signals` 도 계열별 주간 예산을 강제한다. 기준선(선별 없는) 캠페인이 필요하면 사람이 CLI 로 돌린다.

### 4.4 쓰기 범위
서버가 쓰는 곳: 원장(Python API), `research/campaigns/<id>/`, `research/reports/`, 오답노트(API),
`strategies/{draft,candidate,validated}`(파이프라인의 기존 전이 규칙), `research/lockbox_requests/`,
`research/param_proposals/`. guard hook 은 연구 세션의 `research/lockbox_requests/**` 직접 쓰기를 막는다
(요청은 자격 검사를 거치는 도구로만).

### 4.5 실험 블록 (`propose_block`)
코드를 쓰지 않는다. 에이전트가 block-authoring skill 대로 `src/btc_quant/strategy/blocks/experimental/<name>.py`
(+ `docs/BLOCKS.md` 행)을 먼저 쓴 뒤 경로를 넘기면, 새 Python 프로세스에서 모듈을 import 해 그 모듈이 등록한
블록마다 `acceptance_check` 를 실행한다. 경로는 그 디렉터리 바로 아래 `[a-z][a-z0-9_]*.py` 만 허용.
모두 통과하면 서버 프로세스에도 적재해 같은 세션의 캠페인에서 쓸 수 있다(이미 적재된 모듈을 고쳤다면 서버
재시작 필요 — `/mcp` 에서 재연결). 실험 블록을 쓴 후보는 잠금 요청 전 사람 블록 검토가 필요하다(D11).

### 4.6 잠금 요청 (`request_lockbox_eval`)
조건: 레지스트리 상태 validated/challenger, `research/reports/` 의 skeptic 리뷰에 `VERDICT: SURVIVES`,
이 후보가 현재 잠금에서 개봉된 적 없음, 남은 개봉 > 0, 사유에 Val/잠금 수치 없음.
결과: `research/lockbox_requests/<id>_v<ver>.yaml` (status `pending_human_approval`, 사람이 실행할
`admin_cli approve-lockbox <id>_v<ver> --logic-hash …` 명령 포함). `open_lockbox`·승인 파일·원장 lockbox
기록은 건드리지 않는다(테스트가 확인).

## 5. 한계

- MCP 서버는 연구 API 를 감싸는 경로일 뿐 샌드박스가 아니다. 같은 세션의 Bash 로 Python API 를 직접 부를 수
  있으며(대체 경로로 문서화), 그 경우 주간 예산·선별 필수·엄격 검정력은 MCP 만큼 강제되지 않는다(skill 규칙이
  여전히 구속). 잠금의 실제 강제 장치는 여전히 `DataAccess`, 상태 전이는 `btc_quant.registry` 다.
- 도구 호출은 동기 실행(서버의 작업 스레드)이며 진행 알림을 보내지 않는다. 긴 캠페인은 시간 제한을 늘린다.
- `propose_criterion` 은 합성 보정을 실제로 돌린다(기본 bounded: 값마다 30개 시장) — 매우 오래 걸릴 수 있다.

## 6. 개발자용

- 테스트: `uv run pytest -q tests/test_mcp_tools.py tests/test_mcp_safety.py tests/test_guard_hook.py`
  (`test_mcp_tools`: 합성 지연 시장에서 공식 SDK `Client` ↔ `MCPServer` in-process 호출로 선별→캠페인→리포트
  왕복, 원장 쓰기 전 거부, 원장 조회 제한, 잠금 요청이 잠금을 열지 않음, 오답노트, 실험 블록 수용 검사, 파라미터;
  `test_mcp_safety`: 금지 기능 부재·정적 검사·stdio 기동 스모크).
- 도구 추가: `ResearchTools` 에 `@tool` 메서드(반환 `dict[str, Any]`) → `TOOL_NAMES` 와 `server.DESCRIPTIONS`
  에 추가 → 금지 기능 패턴(`FORBIDDEN_CAPABILITIES`, guard `MCP_FORBIDDEN_RE`)에 걸리지 않는지 테스트.
- SDK 문서: <https://py.sdk.modelcontextprotocol.io/> (v2 마이그레이션: FastMCP → MCPServer),
  Claude Code MCP 설정: <https://code.claude.com/docs/en/mcp>.
