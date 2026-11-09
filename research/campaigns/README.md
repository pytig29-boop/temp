# 연구 캠페인 (PLAN §5) — `btc_quant.research`

캠페인 = **사전등록한 가설 하나 + 고정 격자·예산** 의 Dev 전수 실행 → 관문 G0~G6 → 리포트.
결과(통과든 실패든)는 원장(`research/ledger/*.jsonl`)과 리포트(`research/reports/campaign_<id>.md`)에 그대로 남는다.

## 실행

```bash
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
data: {exchange: coinone, symbol: BTC-KRW, timeframe: 1m, aux: []}   # 기본값; aux 는 아직 [] 만 허용
entry:
  block: donchian_breakout
  grid: {lookback: [48, 96, 144]}   # 생략한 파라미터는 블록 기본 격자(docs/BLOCKS.md)
filters:                            # 필터 '세트' 목록. [] = 필터 없음. 세트당 0~2개
  - []
  - [{block: vol_regime, grid: {window: [24], min_pct: [0.33, 0.5]}}]
exits: {sl_pct: [0.5, 0.8], tp_pct: [0.6, 1.0], max_hold_min: [60, 120]}  # max_hold 는 5의 배수
costs:                              # 선택 (아래가 기본값)
  primary: coinone_conservative     # 순위·관문 기준 (D3, D8)
  grade: upbit_conservative         # A/B 등급 재확인
  stress: coinone_stress            # G6 stress MDD
  grade_stress: upbit_stress        # 등급용 G6 stress (types.py 에 없어 research.campaign 에서 정의)
budget: 700                         # 조합 수 > budget 이면 원장에 아무것도 쓰지 않고 거부
```
조합 수 = 진입 격자 × Σ(필터 세트별 격자) × 청산 격자. 모든 조합은 `StrategySpec.validate`
(필터 ≤ 2, 파라미터 ≤ 8 — 청산 3개 포함)를 통과해야 한다. 같은 값 중복·알 수 없는 키는 거부.
**격자를 넓혀 다시 해보기는 새 캠페인**(새 원장 레코드, 시도 수 누적)이다.

## 무엇이 어디에 저장되나

`research/campaigns/<campaign_id>/` (campaign_id = 원장 id, 예: `camp-000001`)

| 파일 | 내용 | git |
|---|---|---|
| `template.yaml` | 실행한 템플릿 원문 | 커밋 |
| `manifest.json` | 가설·캠페인 id, 기간, 데이터 스냅샷 id, 코드 커밋, 스펙·실행 수, 실행 시간 | 커밋 |
| `summary_top50.csv` | primary 비용 상위 50개 스펙의 파라미터·지표 | 커밋 |
| `pipeline.json` | 관문 결과(상위 K 후보별 G0~G6 수치, 기여도, 등급, 레지스트리 전이) | 커밋 |
| `results.parquet` | 전체 스펙 × 평가 비용(primary, grade)의 `metrics.summarize` 결과 + 스펙 JSON | **커밋 안 함** |
| `returns_<primary>.parquet` | primary 비용 일간 수익 행렬(일자 × logic_hash) — PBO·실질 시도 수 입력 | **커밋 안 함** |
| `.gitignore` | `*.parquet` (러너가 생성) | 커밋 |

parquet 는 같은 데이터 스냅샷·코드 커밋이면 재실행으로 결정적으로 재현된다. 원장 runs 에는 모든 실행의
지표·스냅샷·커밋이 남으므로 parquet 가 없어도 감사 가능하다. 리포트는 `research/reports/campaign_<id>.md`.

## 절차와 관문 결정 (요약; 상세는 `src/btc_quant/research/pipeline.py`, `nulls.py` docstring)

1. 예산 확인 → 데이터 스냅샷 id(파일 해시) → **가설·캠페인 원장 등록** → Dev 데이터 로드
   (`DataAccess.read("dev")` → `regularize(clean_wicks(·))`) → (진입 파라미터 × 필터 파라미터)마다 신호 1회 →
   청산 격자 `simulate_many` → 스펙 × {primary, grade} 비용 `summarize` → 원장 runs 에 전부 기록.
2. primary 가중 Sharpe 순 상위 K(기본 5) 후보에 대해:
   - **G0** validate + live_ready + aux 없음 + 이 데이터에서 절단 인과성 점검.
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
4. **G6** Val(14일 워밍업 포함, 잠금 구간 미포함): Sharpe > 0, stress MDD ≤ 20%, worst_case_mdd95 ≤ 20%,
   기준 엔진 거래 목록 일치. Val 실행도 원장에 기록(같은 logic_hash → 시도 수 불변).
5. **등급**: grade 비용으로 G2·G4·G6 재확인 → 모두 통과 A, 아니면 B.
6. G6 통과 후보만 `StrategyRegistry` draft → candidate → validated (증거 첨부). 실패 후보는 원장·리포트에만.
7. **G7(잠금)은 이 파이프라인이 절대 실행하지 않는다** — 사람 승인(`admin_cli approve-lockbox`) 후 별도 절차.
