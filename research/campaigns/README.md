# 연구 캠페인 (PLAN §5) — `btc_quant.research`

캠페인 = **사전등록한 가설 하나 + 고정 격자·예산** 의 Dev 전수 실행 → 관문 G0~G6 → 리포트.
결과(통과든 실패든)는 원장(`research/ledger/*.jsonl`)과 리포트(`research/reports/campaign_<id>.md`)에 그대로 남는다.

## 실행

```bash
uv run python -m btc_quant.research.cli screen research/campaign_templates/screens/upbit_lead_1m.yaml  # ① 신호 선별 (v4)
uv run python -m btc_quant.research.cli run research/campaign_templates/upbit_lead_1m_atr.yaml [--strict-power]  # ② 전략 캠페인
uv run python -m btc_quant.research.cli run research/campaign_templates/donchian_breakout.yaml --max-candidates 5
uv run python -m btc_quant.research.cli gates camp-000001        # (run --no-pipeline 했던 캠페인의 관문만)
uv run python -m btc_quant.research.cli report camp-000001       # 리포트 재작성
uv run python -m btc_quant.research.cli list
```
`--root <dir>` 로 다른 저장소 루트(config/splits.yaml, data/raw, research/, strategies/)를 쓸 수 있다(테스트용).

## 템플릿 형식 (`research/campaign_templates/*.yaml`)

```yaml
name: donchian_breakout_v1          # [A-Za-z0-9_-]; 스펙 id = <name>_<logic_hash 10자>
family: donchian_breakout           # 선택 (기본 = 진입 블록 이름). 원장 시도 수(DSR)를 세는 단위
hypothesis:                         # 모두 필수, 원장 hypotheses 에 그대로 기록
  text: "..."
  rationale: "..."
  success_criteria: "..."
data: {exchange: coinone, symbol: BTC-KRW, timeframe: 1m, aux: []}   # 원천 데이터(항상 1분봉). aux: [] | [upbit]
timeframe: 5m                       # (v4) 결정 주기 5m(기본) | 1m — StrategySpec.timeframe (D10) | 15m · 1h · 4h (스윙, max_hold_min 은 B 배수; 4h 에서 session_kst·trend_regime_1h·range_compression_break 거부 — docs/BLOCKS.md)
entry:
  block: donchian_breakout
  grid: {lookback: [48, 96, 144]}   # 생략한 파라미터는 블록 기본 격자(docs/BLOCKS.md)
filters:                            # 필터 '세트' 목록. [] = 필터 없음. 세트당 0~2개
  - []
  - [{block: vol_regime, grid: {window: [24], min_pct: [0.33, 0.5]}}]
exits: {sl_pct: [0.5, 0.8], tp_pct: [0.6, 1.0], max_hold_min: [60, 120]}  # 고정 % (기존, mode: pct 생략 가능)
# (v4) ATR 모드: exits: {mode: atr, sl_atr: [1.5, 2.5], tp_atr: [2, 3], max_hold_min: [60], atr_bars: 14}
#   sl/tp = 결정 주기 봉 ATR(atr_bars, 단일 정수) 배수 (ENGINE_SPEC "ATR 청산"). max_hold 는 결정 주기의 배수
#   (5m: 5의 배수, 1m: 임의 분). 청산 변형은 4~6개 권장(§10b-2).
from_screen: camp-000012            # (v4, 선택) 이 선별에서 통과한 신호 구성만 허용 (아래 매칭 규칙)
requires_screen_pass: true          # (v4, 선택) 원장의 어떤 선별에서든 통과한 구성만 허용
power: {target_sharpe: 2.0}         # (v4, 선택) 기대 연 Sharpe — 검정력 점검의 비교 기준
costs:                              # 선택 (아래가 기본값)
  primary: coinone_conservative     # 순위·관문 기준 (D3, D8)
  grade: upbit_conservative         # A/B 등급 재확인
  stress: coinone_stress            # G6 stress MDD
  grade_stress: upbit_stress        # 등급용 G6 stress (types.py 에 없어 research.campaign 에서 정의)
budget: 700                         # 조합 수 > budget 이면 원장에 아무것도 쓰지 않고 거부
```
블록이 보조 데이터를 요구하면(`requires: upbit`, 예: `upbit_lead_impulse`, `venue_gap_revert`) `data.aux` 에
선언해야 한다. 실시간 수신 가능한 `upbit` 만 허용(바이낸스는 실시간 피드가 없어 거부).
조합 수 = 진입 격자 × Σ(필터 세트별 격자) × 청산 격자. 모든 조합은 `StrategySpec.validate`
(필터 ≤ 2, 파라미터 ≤ 8 — 청산 3개 포함)를 통과해야 한다. 같은 값 중복·알 수 없는 키는 거부.
**격자를 넓혀 다시 해보기는 새 캠페인**(새 원장 레코드, 시도 수 누적)이다.

## v4 단계 (PLAN §10b)

### 신호 선별 (`cli screen`, `campaign.run_screen`)
`signal_study.screen` 래퍼. 형식은 docs/SIGNAL_STUDY.md §7 + `data.aux: [upbit]` 지원(캠페인과 같은 구성으로
ctx 를 만들어 넘김; 원장 grid·template.yaml 에 aux 기록). 끝나면 `LessonBook.record_facts_from_screen` (오답노트 사실).
예: `research/campaign_templates/screens/*.yaml` (screen 템플릿은 캠페인 템플릿 디렉터리 바로 아래에 두지 않는다).

### 선별 연결 — 매칭 규칙 (`from_screen` / `requires_screen_pass`)
캠페인 격자의 **모든** 신호 구성 = (진입 블록·파라미터) × (필터 세트의 블록·파라미터) 이 원장 runs 의
선별 시도(`metrics.kind == signal_screen`) 중 `decision.passed == true` 인 것과 다음이 모두 같아야 한다:
진입 블록·파라미터, 필터 블록·파라미터 **집합**(순서 무관), 결정 주기(`timeframe` ↔ 선별 `bar_minutes`),
대상 거래소(선별 캠페인 grid.data.exchange). 숫자 파라미터는 float 로 비교(48 = 48.0).
`from_screen` 이면 그 선별 캠페인 안에서만, `requires_screen_pass` 만 있으면 어떤 선별이든. 하나라도 없으면
**원장에 쓰기 전에** `ScreenGateError`(CLI 종료 코드 2). 매칭 근거(선별 id·run id·p_signal)는 manifest `screen`
과 리포트 "Signal screen linkage" 에 남는다. 청산은 선별 대상이 아니다(선별은 청산 없는 이벤트 스터디).

### 검정력 점검 (등록 시)
`power.budget_report(격자 스펙 수, Dev 일수, family)` → G5 통과 최소 연 Sharpe 를 manifest `power` 와 리포트에
기록. 기본은 경고(`POWER WARNING`)만; `--strict-power` 면 `power.target_sharpe` 미선언 또는 필요 Sharpe > 목표일 때
원장 쓰기 전 `PowerCheckError`.

### 파이프라인 v4 변경
- 1m/5m·ATR: Dev/Val 실행, G2 귀무(결정 주기 봉 기준 신호 비율, 같은 ATR 배수), G3(ATR 격자 한 칸 이동),
  G6(run_fast/run_reference 에 스펙 그대로) 모두 스펙의 결정 주기·청산 모드를 따른다.
- G0: 실시간 가능한 aux(`upbit`, 템플릿에 선언)를 요구하는 live_ready 블록 허용. 실험 블록 사용은
  `uses_experimental` 로 기록 → 리포트에 "human block review required before lockbox" (D11).
- 보조 데이터 구성 = 페이퍼와 동일: 업비트 1분봉 `prepare_m1(raw, "upbit")` (거래소별 꼬리 정리 → regularize → 공백 워밍업) → `to_tf(·, B)` → 블록이 대상 bars 에
  reindex(ffill 없음). Val 도 같은 범위(워밍업 포함)로.
- **견고성 묶음**(`robustness.robustness_suite`, Dev): G6 통과 후보마다 실행해 `candidate["robustness"]` 에 첨부.
  거래소 재현 = 대상이 코인원이면 업비트 Dev(업비트면 코인원). 업비트 aux 스펙은 거래소 재현 skipped.
  실패(failed ≥ 1) → status `validated (robustness FAIL)`, 레지스트리는 **candidate 에서 멈춤**(validated 전이·
  risk.yaml 없음). skipped 만 있으면 통과.
- **risk.yaml**: validated 전이 시 `strategies/validated/<id>_v<ver>.risk.yaml` =
  `{grade, limits: {daily_loss, weekly_loss, max_drawdown, max_trades_per_day, expected_fee_rate}, source}`
  (paper.service.load_risk_meta 형식, §7 공식 `gates.risk_limits_from_validation`).
- **오답노트**: 파이프라인 끝에 `lessons.record_facts_from_pipeline(pipeline.json, root=…)` (멱등).
- 원장 실행 기록은 `Ledger.record_runs`(일괄) 로.

### 매수보유(B&H) 기준 지표 (2026-11-10, 정보 전용 — 관문 아님)
롱 온리 전략이 하락장에서 쉬는 것이 맞는지, 상승장 베타만 먹는지 보려고 모든 실행에 BTC 매수보유(같은 거래소 1분봉
close, 비용 없음)와의 비교를 붙인다(`metrics.benchmark_metrics`, `summarize(…, benchmark=buy_hold_daily(...))`).

| 키 | 뜻 |
|---|---|
| `exposure_time` / `exposure_days` / `days_in_market` | 보유 분 합 / 구간 분 · 포지션이 있던 날 비율 · 그 일수 |
| `bh_total_return`, `bh_sharpe`, `bh_max_dd` | 같은 기간 B&H (캠페인 상수 → 원장 runs 에는 쓰지 않고 `manifest.json` `benchmark`) |
| `excess_return_vs_bh`, `sharpe_minus_bh` | 전략 − B&H |
| `bh_matched_total_return`, `excess_return_vs_matched_bh` | 노출 맞춤 B&H(= exposure_time × BTC 일간 수익 복리)와 그 대비 초과 |
| `up_capture`, `down_capture` | BTC 상승일/하락일의 전략 평균 ÷ BTC 평균 (일간) |
| `up_regime_*`, `down_regime_*` | 인과 국면(직전 30일 BTC 수익 부호, 전일까지)별 전략 복리 수익·capture |
| `downside_dev` | 0 기준 하방편차(연율) |
| `bh_dd{1..3}_{depth,start,end,strategy_return}`, `worst_bh_dd_strategy_return` | B&H 최악 낙폭 구간 3개(겹침 없음, 고점 다음날~저점)와 그 동안 전략 수익 |
| `rolling_n_neutral`, `rolling_n_active`, `rolling_frac_positive_active` | 6개월 롤링 창 중 시장 참여일 < 10% 인 '쉬는 창' 수와 그 밖의 창의 양수 비율 (G4 변형 입력) |

- 에이전트 리포트·MCP `get_campaign_report`: Dev 값 전부. Val 의 B&H 비교는 G6 세부 안(`benchmark_val`)이라
  **사람용 리포트·pipeline.json 에만** 있고 에이전트 사본에는 G6 결과 코드만 남는다. Val 원장 runs 에는 낙폭
  구간 날짜(`bh_dd*_start/end`)를 쓰지 않는다(Val 안 시점 = 구조 누설).

### 노출 인식 관문 변형 (`exposure_aware`, **기본 OFF**, PLAN D14 초안 — 사람 승인 대기)
`config/gates.yaml` `exposure_aware.enabled: false` 인 동안 G4/G6 판정은 그대로이고, 변형 결과는 G4/G6 세부의
`exposure_aware` 에 **shadow(정보)** 로만 남는다(리포트에 "shadow, OFF — information only"). 설계·보정은 docs/PARAMS.md §8.
- G4 변형: 쉬는 창(시장 참여일 < `neutral_max_exposure`)을 양수 비율 분모에서 제외. 비중립 창 ≥ `min_active_windows`,
  Dev 시장 참여일 ≥ `min_dev_exposure`. 기존 통과 OR 변형 통과. 최근 12개월 Sharpe > 0 은 그대로.
- G6 변형('쉬어서 피함' 경로): 기존 조건 OR [Val 시장 참여일 ≤ `flat_max_exposure`, B&H Val 수익 < 0, Val 수익 ≥
  `flat_min_return`, stress 수익 ≥ `flat_min_stress_return`, MDD ≤ `flat_max_mdd_vs_bh` × B&H MDD, 기존 MDD·MC MDD95,
  Dev 시장 참여일 ≥ `min_dev_exposure`, Val 거래 ≥ `flat_min_trades`(모자라면 `insufficient_evidence` = 통과 아님)].
  이 경로는 Val Holm(평균 > 0 증거)을 요구하지 않는다 — 사용은 원장에 k번째로 남는다. 에이전트 결과 코드는 pass/fail 뿐
  (경로·insufficient_evidence 는 Val 국면을 드러내므로 사람 전용).
- **Val 재사용 = 이전 결정**: 같은 logic_hash 의 재평가는 지금 설정으로 다시 판정한 결과와 이전 결정을 AND 한다 →
  이전에 G6 를 떨어진 스펙은 나중에 승인된 기준(이 변형 포함)으로도 같은 Val 에서 통과할 수 없다.

### 탐색적 시도 기록 (`log_exploration`, 2026-11-10)
캠페인·선별 밖의 사이드 계산(Dev 연도별 이벤트 스터디 등)은 `research.exploration.log_exploration(family, description,
configs_evaluated, segment="dev")`(연구 모드에서는 MCP `log_exploration`)로 원장에 남긴다: campaigns 행(`grid.kind =
"exploration"`, 캠페인 폴더 없음) + runs `configs_evaluated` 줄(스펙 없음) → `trials_count(family)` 와 주간 예산에 더해진다.
Dev 만 허용. 상세 docs/AI_RESEARCH.md §7.1.

## 무엇이 어디에 저장되나

`research/campaigns/<campaign_id>/` (campaign_id = 원장 id, 예: `camp-000001`)

| 파일 | 내용 | git |
|---|---|---|
| `template.yaml` | 실행한 템플릿 원문 | 커밋 |
| `manifest.json` | 가설·캠페인 id, 기간, 데이터 스냅샷 id(aux 포함 시 `…|upbit:…`), 코드 커밋, 스펙·실행 수, 결정 주기·청산 모드, 검정력(`power`), 선별 연결(`screen`), Dev B&H 기준(`benchmark`), 실행 시간 | 커밋 |
| `summary_top50.csv` | primary 비용 상위 50개 스펙의 파라미터·지표 | 커밋 |
| `pipeline.json` | 관문 결과(상위 K 후보별 G0~G6 수치, 기여도, 등급, 레지스트리 전이) — **사람 전용**(G6 세부; 연구 모드 읽기 거부) | 커밋 |
| `pipeline_public.json` | 에이전트 안전 사본(`pipeline.public_pipeline`): G6·등급 G6 = 결과 코드, risk_limits 생략, Val 예산 = 횟수 | 커밋 |
| `results.parquet` | 전체 스펙 × 평가 비용(primary, grade)의 `metrics.summarize` 결과 + 스펙 JSON | **커밋 안 함** |
| `returns_<primary>.parquet` | primary 비용 일간 수익 행렬(일자 × logic_hash) — PBO·실질 시도 수 입력 | **커밋 안 함** |
| `.gitignore` | `*.parquet` (러너가 생성) | 커밋 |

parquet 는 같은 데이터 스냅샷·코드 커밋이면 재실행으로 결정적으로 재현된다. 원장 runs 에는 모든 실행의
지표·스냅샷·커밋이 남으므로 parquet 가 없어도 감사 가능하다. 리포트는 2벌: 에이전트 안전 `research/reports/campaign_<id>.md`(Val/잠금 = 결과 코드만), 사람용 전체 `research/reports/human/campaign_<id>.md`.

## 절차와 관문 결정 (요약; 상세는 `src/btc_quant/research/pipeline.py`, `nulls.py` docstring)

1. 예산 확인 → 데이터 스냅샷 id(파일 해시) → **가설·캠페인 원장 등록** → Dev 데이터 로드
   (`DataAccess.read("dev")` → `prepare_m1(·, exchange)`) → (진입 파라미터 × 필터 파라미터)마다 신호 1회 →
   청산 격자 `simulate_many` → 스펙 × {primary, grade} 비용 `summarize` → 원장 runs 에 전부 기록.
2. primary 가중 Sharpe 순 상위 K(기본 5) 후보에 대해:
   - **G0** validate + live_ready + 허용된 aux(upbit, 템플릿 선언)만 + 이 데이터에서 절단 인과성 점검.
   - **G1/G4** `gates.check_g1/g4` (summarize 지표).
   - **G2** 같은 필터·청산 + `random_entry` 귀무 1,000 seed. p 는 필터 통과 봉 중 진입 신호 비율에서 출발해
     귀무 거래 수 중앙값이 후보 거래 수와 같도록 조정(신호 비율만 맞추면 군집형 진입이 '덜 거래해서' 이기는
     편향 — 무작위 보행에서 p≈0.004 로 확인). n_null < config 이면 실패.
   - **G3** 같은 필터 세트에서 파라미터 하나를 격자 한 칸 이동한 스펙들의 가중 Sharpe 중앙값 ≥ 50%.
   - **G5** DSR: 시도 수 N = max(K, N_family·K/N_campaign) (K = 상관 군집 수, N_family = `Ledger.trials_count(family)`),
     V = 군집 대표 SR 분산(군집 1개면 열 분산). PBO = CSCV(16) on 캠페인 행렬.
3. G0~G5 통과(Dev 생존)만: **기여도** — 필터 하나 제거 스펙과 짝지은 정상 블록 부트스트랩, Δ가중Sharpe < 1 s.e. 면
   "제거 가능". 스펙은 바꾸지 않고 단순화 스펙을 **새 스펙**으로 리포트(후속 캠페인). 격자 밖 제거 스펙은 별도
   'ablation' 캠페인으로 사전등록 후 실행·기록(시도 수 +1).
4. **G6** Val(14일 워밍업 포함, 잠금 구간 미포함): Sharpe > 0, primary MDD ≤ 20%, stress Sharpe > 0(규칙 b),
   worst_case_mdd95 ≤ 20%, 기준 엔진 거래 목록 일치, Val 사용 원장 + 누적 Holm. Val 실행도 원장에 기록(같은 logic_hash →
   시도 수 불변; 재평가는 이전 결정보다 느슨해질 수 없음). 노출 인식 변형은 기본 OFF(위).
5. **등급**: grade 비용으로 G2·G4·G6 재확인 → 모두 통과 A, 아니면 B.
6. G6 통과 후보 → 견고성 묶음(Dev). 통과면 `StrategyRegistry` draft → candidate → validated (증거 첨부) +
   risk.yaml, 견고성 실패면 draft → candidate 까지만. G0~G6 실패 후보는 원장·리포트에만.
7. **G7(잠금)은 이 파이프라인이 절대 실행하지 않는다** — 사람 승인(`admin_cli approve-lockbox`) 후 별도 절차.
