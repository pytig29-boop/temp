# AI 연구 체계 — 연구 모드 운용 가이드

> PLAN §3.3(AI 권한), §5(캠페인·관문·잠금), §6(레지스트리), §8(AI 연구 체계)의 Claude Code 쪽 구현.
> 구성 파일: `.claude/settings.json`, `.claude/hooks/guard.py`, `.claude/agents/*.md`, `.claude/skills/*/SKILL.md`.

## 1. 개요

| 장치 | 파일 | 역할 |
|---|---|---|
| Hook (PreToolUse) | `.claude/hooks/guard.py` | 연구 모드에서 보호 경로 쓰기·사람 전용 명령·잠금 데이터 읽기 차단, 모든 도구 호출 로그 |
| 권한 규칙 | `.claude/settings.json` `permissions.deny` | 모드와 무관하게 항상: `.env`, `runtime/secrets/**`, 키 파일, `~/.ssh` 읽기 금지, 강제 push 금지 등 |
| Subagent | `researcher` | 가설 → 캠페인 실행 → 리포트 (연구 도구만 사용) |
| | `skeptic` | 후보를 깨뜨리려 시도, 구조화된 판정 출력 (파일 쓰기 도구 없음) |
| | `data-steward` | 데이터 품질·커버리지 점검 |
| Skill | `research-campaign` | 가설 사전등록 → 격자·예산 고정 → 실행 → 관문 → 기여도 → 리포트 |
| | `candidate-review` | 반대 논리 심사 체크리스트 + 리포트 양식 |
| | `block-authoring` | 새 블록 제안(계약·인과성 테스트·BLOCKS.md) — 사람 머지 필요 |
| | `daily-report` | 아침 리포트 |

MCP 서버(`list_blocks`, `register_hypothesis`, `create_campaign`, `run_campaign`, `run_gates`, `run_ablation`,
`query_ledger`, `compare`, `request_lockbox_eval`, `write_report`)는 이후 마일스톤에서 구현된다.
그전까지 skill 문서에 적힌 대응 Python API(`btc_quant.registry.ledger.Ledger`,
`btc_quant.registry.splits.DataAccess`, `btc_quant.backtest.fast`, `btc_quant.evaluation.*`)를 쓴다.
MCP 도구 호출(`mcp__*`)도 hook 로그 대상이다.

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

## 4. 무엇이 차단되나 (연구 모드)

### 4.1 쓰기 차단 (Write/Edit/MultiEdit/NotebookEdit, Bash 리다이렉션·tee·sed -i·mv·cp·rm 등)

| 경로 | 이유 |
|---|---|
| `strategies/{active,review,retired}/**` | 승인·운용 전략은 레지스트리 코드 + 사람 승인으로만 변경 (§6) |
| `config/gates.yaml`, `config/splits.yaml`, `config/risk*.yaml` | 관문·분할·리스크 공식은 고정, 변경은 사람만 (§2.1, §5.2, §7) |
| `research/ledger/**` | 원장은 append-only, `Ledger` Python API로만 추가 |
| `research/lockbox_approvals/**`, `research/activation_approvals/**` | 사람 승인 기록 |
| `src/btc_quant/{backtest,evaluation,registry,data}/**` | 엔진·관문·레지스트리·데이터 코드는 AI 수정 불가 (§3.3) |
| `data/raw/**` | 원천 데이터는 수집기만 기록 |
| `.claude/{settings*.json,hooks,agents,skills}` | 연구 세션이 자기 감시 장치를 바꾸지 못하게 |
| `logs/research_tool_calls.jsonl` | 감사 로그 위변조 방지 (hook만 추가) |

허용: `src/btc_quant/strategy/blocks/**`(새 블록 **제안** — block-authoring skill이 테스트·문서를 요구하고
사람이 머지), `tests/**`, `strategies/draft/**` 등 나머지 상태 폴더(전이는 레지스트리 코드가 검증),
`research/reports/**`, `docs/BLOCKS.md` 등.

### 4.2 명령 차단 (Bash)
- 사람 전용 관리 CLI: `admin_cli`(읽기 전용 `status` 포함), `approve-lockbox`, `activate`, `rollback`,
  Python에서의 `activate(`/`rollback(`/`write_lockbox_approval`/`write_activation_approval` 호출.
  레지스트리 상태는 `StrategyRegistry().status()`, 잠금 현황은 `DataAccess().lockbox_status()`로 본다.
- `git push`, 그리고 작업 트리를 덮어쓰거나 이력을 바꾸는 git 명령(checkout, restore, reset, stash,
  clean, rebase, merge, pull 등). 허용: status/log/diff/show/blame/ls-files/branch/fetch 등 읽기 + add/commit.
- `.env`, `runtime/secrets`를 언급하는 명령.

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

## 5. 한계 (정직하게)

- **hook은 경보선(tripwire)이지 샌드박스가 아니다.** Bash 검사는 명령 문자열에 대한 정규식 휴리스틱이다.
  `cd config && sed -i … gates.yaml`, 변수·글롭으로 조립한 경로, Python 코드 안에서 문자열을 만들어
  여는 경우, 다른 스크립트를 경유한 쓰기 등은 놓칠 수 있다.
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

## 6. 개발자용

- 테스트: `uv run pytest -q tests/test_guard_hook.py` (hook을 하위 프로세스로 실행, 모드 on/off·허용/거부 사례).
- 보호 경로 추가: `guard.py`의 `PROTECTED_WRITE` 목록에 (정규식, 이유) 추가 후 테스트 보강.
- hook 프로토콜 참고: <https://code.claude.com/docs/en/hooks> (PreToolUse 입력 `tool_name`/`tool_input`/`cwd`,
  출력 `hookSpecificOutput.permissionDecision = "deny"` + `permissionDecisionReason`),
  권한 규칙: <https://code.claude.com/docs/en/permissions>, 서브에이전트: <https://code.claude.com/docs/en/sub-agents>,
  skills: <https://code.claude.com/docs/en/skills>.
