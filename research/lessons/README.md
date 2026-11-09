# 오답노트 (lessons notebook) — PLAN §10b-9

코드: `src/btc_quant/research/lessons.py` · 사람이 읽는 판: [`LESSONS.md`](LESSONS.md) (자동 생성, 직접 편집 금지)

## 파일 (모두 append-only JSONL, `research/lessons/.lock` fcntl 잠금, 줄마다 `content_hash`)

| 파일 | 내용 | 누가 쓰나 |
|---|---|---|
| `facts.jsonl` | **사실** — 파이프라인 결과(pipeline.json)·신호 선별 결과에서 자동 추출. 캠페인·가설·계열, 깔때기, 실패 관문, 핵심 수치, 자동 실패 유형, 범위(scope)·재검토 기한 | `record_facts_from_pipeline` / `record_facts_from_signal_study` (자동) |
| `interpretations.jsonl` | **해석** — "왜 실패했나". 항상 `status: unverified`, 신뢰도·인용 근거·검증 방법 | `add_interpretation` (AI) |
| `marks.jsonl` | 해석의 verified / refuted 표시. 근거 = 사실이 기록된 **후속 캠페인 id** | `mark` |
| `derivations.jsonl` | 파생 가설 — 부모(사실·해석·캠페인)와 **상속 계열** | `derive_hypothesis` |
| `postmortems.jsonl` | 과정 사후분석 — 버그·데이터·절차 위반·설계 문제, 근본 원인, 재발 방지 | `postmortem` / `seed_postmortems` |

연구 모드(`BTCQ_RESEARCH_MODE=1`)에서는 guard hook 이 이 디렉터리의 JSONL·`.lock`·`LESSONS.md` 쓰기
(Write/Edit/Bash 리다이렉션 등)를 막는다. 이 README 만 편집 가능. `LessonBook().verify()` 가 직접 편집
(내용 해시 불일치·id 순서·중복 사실)을 잡는다.

## 규칙
1. **사실은 불변** — 고치는 API 가 없다. 잘못 기록됐으면 사후분석(`postmortem`)으로 남긴다.
2. **해석은 가설일 뿐** — `unverified` 로 시작, 후속 캠페인 결과로만 verified/refuted.
3. **실패 유형은 규칙으로** — 관문·견고성 결과 → 고정 코드(아래). 모르는 결과는 `UNCLASSIFIED`.
4. **파생 가설은 같은 계열** — `derive_hypothesis` → `apply_derivation(template, drv_id)`; 다른 계열 이름은
   사람의 override(연구 모드 밖)만. 실행 전 `check_template(template)` 이 계열 일치·중복 격자를 점검한다.
5. **Val/잠금은 결과 코드만** — `val_outcome`(pass/fail/not_run/engine_mismatch/budget_exhausted),
   `val_aggregate`(정수 집계), `lockbox_outcome`. 사실 기록기는 세부를 제거하고, AI 입력(해석·사후분석)은 거부한다.
6. **범위·만료** — 사실마다 scope(거래소·시간축·구간·블록)와 `review_after`(기본 180일)·`reexamine_if`.
   교훈은 영구 금지 규칙이 아니다.

## 실패 유형 매핑

| 결과 | 코드 |
|---|---|
| G0 실패(검증·인과성·live_ready), G6 엔진 불일치, 파이프라인 오류 | `IMPL_OR_DATA_ISSUE` |
| G1 실패 | `LOW_SAMPLE` |
| G2 실패, 신호 선별 실패, 전체 스펙 중 SR_w>0 없음 | `NO_SIGNAL` |
| G2 실패인데 비용 전 수익 양(+)(gross 지표/zero-cost 견고성), 비용 +50% 견고성 실패, 업비트 비용 G2/G4 실패 | `COST_EATEN` |
| G3 실패 | `PARAM_SPIKE` |
| G4 실패 | `UNSTABLE` |
| G5 실패 | `SELECTION_BIAS` |
| G6 실패 | `VAL_FAIL` (세부 없음) |
| 견고성: 상위 거래 제거 | `OUTLIER_DEPENDENT` |
| 견고성: 연도 제외·국면별·시간대·거래소 재현 | `REGIME_DEPENDENT` |
| 견고성: 1봉 지연 | `LATENCY_SENSITIVE` |
| Val 예산 소진(평가 안 됨), 그 밖 | `UNCLASSIFIED` |

## 사용 예
```python
from btc_quant.research.lessons import LessonBook
B = LessonBook()                                   # research/lessons, research/ledger
f = B.record_facts_from_pipeline("research/campaigns/camp-000001/pipeline.json")
B.add_interpretation(f["id"], "돌파 신호가 저변동 시간대에 몰려 비용을 못 넘음", "low",
                     evidence=[f"{f['id']} candidates[0].gates_dev.G2", "research/knowledge/phase0_findings.md#K-P0-3"],
                     proposed_test="KST 22~01 한정 신호 선별", reexamine_if=["코인원 0% 종료"])
d = B.derive_hypothesis(f["id"], "…", "…", "…")   # 계열 상속
tpl = B.apply_derivation(template_dict, d["id"]); B.check_template(tpl)
B.find_similar(template=tpl, text=tpl["hypothesis"]["text"])
B.trial_budget(d["family"]); B.render_markdown()
```
