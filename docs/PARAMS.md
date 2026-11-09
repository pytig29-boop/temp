# 파라미터 거버넌스 — 한도·임계값을 누가, 무엇으로, 어떻게 바꾸는가

> 기준일 2026-11-09 · 코드 `src/btc_quant/params/` · 목록 `config/params.yaml` · 비용 모델 `config/costs.yaml`

## 0. 왜 필요한가

`coinone_stress` 비용(스프레드 5bp + 슬리피지 8bp/편도 ≈ 왕복 21bp)은 근거 없이 정한 값이었고,
실측 코인원 비용(왕복 약 2~3bp)의 약 8배였다. 그 결과 파이프라인은 회전율이 높은 전략을 **어떤 엣지로도**
검증할 수 없었다(`research/reports/pipeline_calibration.md`, PLAN D12). 숫자 하나가 연구 전체의 결론을
정하는데, 그 숫자를 누가 무엇을 근거로 바꿀 수 있는지가 정해져 있지 않았던 것이 문제다.

원칙: **모든 한도·임계값은 데이터나 계산이 제안한다. 다만 종류(등급)에 따라 제안의 근거와 적용 규칙이 다르다.**

## 1. 세 등급

| 등급 | 무엇 | 예 | 근거 | 적용 규칙 | 소유자 |
|---|---|---|---|---|---|
| ① 시장 측정치 | 시장이 정하는 사실 | 스프레드·슬리피지·stress 비용, 수수료, 지연, 데이터 품질 임계값(꼬리 판정 k/창, 수집기 무수신 초) | 실측 데이터(호가 스냅샷, 페이퍼 호가 체결) | **비대칭**: 더 보수적(엄격)인 변경은 자동 적용, 더 느슨한 변경은 사람 승인. 모든 버전 기록, 실행마다 버전 id 저장 | auto |
| ② 증거 기준 | "얼마나 확실해야 믿는가" | 유의수준, DSR/PBO 문턱, Holm α, 최소 거래 수, 관문 통과선, 견고성 기준 | **합성 보정만**(calibration_study: 귀무 시장 오탐률, 참 Sharpe 별 검정력)과 명시한 목표 | 실제 연구 결과로 조정 금지(골대 이동). 다음 캠페인부터만 적용. 특정 후보·캠페인을 근거로 한 제안은 거부. 사람 승인 | human |
| ③ 위험 선호 | 사람이 감내할 손실 | 최대 낙폭 허용(G6.max_mdd), 손실 한도 하한(§7), 자본 비중, 주문 금액 | 사람의 가치 판단 | AI 는 결과(정지 빈도·최악 손실·회복 기간)를 표로만 제시, 사람이 고른다 | human |

등급별 개수(2026-11-09): ① 24 · ② 61 · ③ 12 (총 97). ① 추가(같은 날, 데이터 점검 `research/reports/data_audit_20261109.md` 근거, 엄격 방향이라 자동 적용): 코인원 꼬리 정리 `quality.clean_wicks.coinone.k` 0.03→**0.01**·`.window` 15, 긴 공백 워밍업 `quality.gap_warmup.min_gap_minutes` 30·`quality.gap_warmup.bars` 0→**1** — 연구 `prepare_m1`·페이퍼 `M1History` 공통(`quality.wick_params`, `prepare_venue_m1`). `python -m btc_quant.params status` 로 확인.

### 왜 ①은 비대칭인가
비용을 올리는 실수는 좋은 전략을 놓치게 할 뿐이지만(기회 손실), 내리는 실수는 나쁜 전략을 실거래로 보낸다(실제 손실).
그래서 측정이 "더 비싸다"고 하면 즉시 반영하고, "더 싸다"고 하면 사람이 근거 리포트를 보고 승인한다.
비교 기준은 **왕복 총비용**(2 × (편도 가격 불리분 + 수수료)).

### 왜 ②는 실제 결과로 바꾸면 안 되는가
후보가 DSR 0.93 으로 떨어진 뒤 문턱을 0.90 으로 내리면, 그 문턱은 더 이상 오탐률을 통제하지 못한다(사후 선택).
기준의 성질(오탐률·검정력)은 정답을 아는 합성 시장에서만 잴 수 있으므로, 제안 근거는 합성 보정뿐이다.
승인된 변경도 **승인 이후 등록되는 캠페인부터** 적용된다: 이미 등록된 캠페인은 나중에 평가해도 등록 당시 기준을 쓴다.

## 2. 파일

| 경로 | 내용 | 누가 쓰나 |
|---|---|---|
| `config/params.yaml` | 목록: key, class, source(값의 실제 위치), value, rationale, evidence, version, last_updated, owner, history + `settings`(보정 방법론 값) | `btc_quant.params` (승인·보정) |
| `config/costs.yaml` | 비용 모델 6개(코인원·업비트 × base/conservative/stress)의 버전 이력. v1 = 기존 하드코딩 프리셋과 동일 | 보정(엄격 자동) / 승인(느슨) |
| `research/param_proposals/<id>.yaml` | 제안 (승인 전에는 효과 없음) | AI 도구 |
| `research/param_approvals/<id>.json` | 승인·거부 기록 | 사람 전용 명령 |
| `research/reports/params_calibration_<날짜>.md` | ① 보정 근거 리포트 | 보정 |
| `research/reports/criteria_<key>_<날짜>.md` | ② 합성 보정 근거 리포트 | 기준 도구 |

값의 원본은 `source` 가 가리키는 곳이다(`config/gates.yaml`, `config/splits.yaml`, `config/exchanges.yaml`,
코드 상수·기본 인자, `config/costs.yaml`, params.yaml `settings`). 레지스트리는 값을 복제하지 않고 기록만 하며,
원본이 레지스트리의 value 와 다르면 **drift 오류**로 검증이 실패한다(규칙을 거치지 않은 수정 탐지).
`config/gates.yaml` 의 모든 말단 항목은 목록에 있어야 한다(새 기준을 추가하면 params.yaml 에도 등급과 근거를 추가).

연구 모드 hook(`.claude/hooks/guard.py`)은 `config/params.yaml`, `config/costs.yaml`, `research/param_approvals/`
직접 쓰기와 `python -m btc_quant.params approve|reject`, 그리고 검증을 우회하는 쓰기 함수 호출
(`add_version(s)`, `record_change`, `proposals.approve`)을 차단한다. 제안 파일 쓰기는 허용한다.

## 3. 흐름

### ① 비용 보정
```
python -m btc_quant.params sample --venues coinone,upbit --seconds 1200   # REST 호가(양쪽 전체) 수집
python -m btc_quant.params calibrate [--dry-run]
```
- 입력: `data/raw/exchange=<ex>/symbol=BTC-KRW/orderbook/` 의 WS 수집기 파일(`date=*/HH-*.parquet`)·REST 샘플
  (`rest_*.parquet`, 같은 스키마)·구 요약 스냅샷(`snap_*.parquet`, 매수 쪽만), 그리고 페이퍼 호가 체결
  `runtime/paper/*/fills.jsonl` 의 `book_cost_bps`(코인원).
- 유효 편도 비용 = 실제 주문 금액(기본 30만 원 = 페이퍼 주문 금액)으로 호가를 걸어 올라간 VWAP 의 mid 대비 불리분
  (스프레드/2 + 충격, 수수료 제외). 매수·매도 모두.
- 도출: base = 중앙값, conservative = p90, stress = p99 × 1.5(안전계수 — 관측 못 한 급변·점검 직후·새벽 얇은 호가 여유).
  spread_bps = 같은 분위의 스프레드, slip_bps = 편도 비용 − 스프레드/2. 거래소 안에서 base ≤ conservative ≤ stress.
  수수료(fee_rate)는 보정하지 않는다(별도 ① 항목 `exchanges.*.taker_fee`).
- 표본 판정(시간 버킷 = UTC 1시간, 버킷 안 스냅샷 ≥ 60 이어야 '관측'):
  - **refused**: 관측 시간 < 2 또는 스냅샷 < 300 → 그 거래소는 변경 없음(모두 부족하면 명령 자체가 거부).
  - **final**: 관측 시간 ≥ 72, 서로 다른 KST 시각 ≥ 24, 일수 ≥ 7.
  - **provisional**: 그 사이. 분위수 대신 시간 버킷 블록 부트스트랩 95% **상한**을 쓰고(보수적), 엄격 변경도
    자동 적용하지 않고 제안에 넣는다(`settings.calibration.provisional_auto_apply_stricter`).
- 결정(모델마다): stricter → `config/costs.yaml` 새 버전(applied_by=auto) / looser → 제안 / equal(±0.05bp) → 없음.
  자동 적용분이 순서를 깨면 윗 역할도 함께 올린다(엄격 방향).
- 한계: 호가 비용은 "결정 시점 호가에 즉시 시장가" 비용이다. 결정→체결 지연 중 가격 이동(역선택)은
  견고성 묶음의 지연 검사와 페이퍼 G8 슬리피지 비교가 다룬다.

### ② 증거 기준 제안
```
python -m btc_quant.params propose-criteria gates.G5.min_dsr --values 0.9 0.975 \
    --motivation "DSR 문턱의 합성 오탐률/검정력 점검" --ref synthetic:calibration_study \
    --max-fpr 0.05 --min-power 0.7 --power-sharpe 2 --mode bounded --null-markets 20 --planted-markets 10 --jobs 4
```
- 한 번에 한 기준. 대상은 class ② 이고 값이 `config/gates.yaml` 에 있는 항목(견고성 기준 `robustness.*` 포함).
- **anti-goalpost**: motivation/references 에 원장 id(`camp-`/`run-`/`hyp-`/`val-`/`lbx-`/`fwd-`/`trn-NNN`),
  원장에 있는 spec_id·logic_hash, 레지스트리 전략 id, `research/campaigns|ledger|lessons`·`strategies/` 경로가 있으면
  거부(아무것도 실행하지 않음). references 는 `synthetic:`, `docs/`, `literature:`, `research/reports/pipeline_calibration` 만.
- 후보 값마다 **같은 seed** 의 합성 시장(calibration_study: 귀무 = 무작위 보행, 심은 엣지 = 목표 참 Sharpe)에서
  파이프라인을 돌려 오탐률(귀무 시장에서 validated ≥ 1)과 검정력(S* validated)을 Wilson 구간과 함께 표로 만든다.
- 목표를 만족하는 값 중 검정력이 가장 높은 값을 추천. 현재 값이면 제안 없음, 만족하는 값이 없으면 리포트만.
  `quick` 모드는 구조 점검용이라 제안을 쓰지 않는다.
- 제안은 `applies_from = 다음 캠페인`. 승인 시 원장 캠페인 수 + 1 이 `applies_from_campaign` 으로 고정되고,
  `gates.yaml` 값과 params.yaml 이력이 함께 갱신된다. `gate_config_for_campaign(campaign_id)` 가 캠페인 시점 기준을 재구성한다.

### ③ 위험 선호 선택지
```
python -m btc_quant.params options --returns val_daily.parquet --trades val_trades.parquet --capital 10000000 \
    --title "<전략> Val" --out research/reports/options_<전략>.md [--propose gates.risk.drawdown_floor]
```
- 검증된 전략의 Val/Dev 일간 수익(무거래일 0 포함)을 순환 블록 부트스트랩으로 1년 경로 5,000개 생성.
- 낙폭 한도 −8/−10/−12/−15/−20/−25% 마다: 1년 내 정지 확률, 연 기대 정지 횟수(정지 후 검토·재개 가정),
  첫 정지까지 일수, 정지 시 손실 p95(하루 갭 포함), 1년 최악 손실 p95/p99, 회복 기대 일수, 실제 회복 일수.
- 일일 손실 한도 −2~−5% 의 연 발동 일수, 자본 비중 10~100% 의 원화 최악 손실, 거래 MC MDD95 와 §7 공식값.
- `--propose` 를 주면 선택지를 제안으로 남기고, 사람은 `approve <id> --choice <한도>` 로 하나를 고른다.

### 승인 (사람 전용)
```
python -m btc_quant.params approve <id> [--choice <label>]     # 대화형 터미널에서 id 를 타이핑해야 진행
python -m btc_quant.params reject <id> --reason "..."
python -m btc_quant.params history [costs | <cost model> | <param key>]
```
- 비대화형(stdin 이 tty 아님)이면 거부. 제안 파일이 생성 후 바뀌었으면(내용 해시) 거부.
- 제안의 `from` 값이 지금 값과 다르면(그 사이 다른 변경) 낡은 제안으로 거부 → 다시 보정·제안.

## 4. 실행 기록에 params_version 남기기

```python
from btc_quant.params import params_snapshot, cost_model, gate_config_for_campaign
snap = params_snapshot()      # {"params_version": "79bc502df565", "cost_versions": {...}, "registry_version": 1, ...}
```
- `params_version` = 모든 관리 파라미터의 현재 값 + 비용 모델 현재 버전의 12자리 해시. 값이 하나라도 바뀌면 바뀐다.
- 저장 위치(연결 완료): 캠페인 `manifest.json` 의 `params`·`params_version`·`cost_versions`, 원장 `runs` 레코드의
  선택 필드 `params_version`·`cost_versions`(`Ledger.record_run(s)(..., params_version=, cost_versions=)`; 이전
  레코드에는 없음 — 추가 전용 형식, 시도·예산 판정 불변), 선별 `signal_screen.json` 의 `params`,
  `pipeline.json` 과 전진 인큐베이션 `forward_results` 레코드의 `params_version`·`cost_versions`.
  레지스트리 검증이 실패하면(drift 등) `params_version=None` + `params_error` 를 남기고 계속한다.
  같은 결과를 재현하려면 `cost_model(name, version=v)` 와 params.yaml history 로 당시 값을 복원한다.
- 비용 모델: `research/campaign.get_cost(name)` 는 `btc_quant.params.cost_model(name)` 의 얇은 래퍼(costs.yaml
  mtime 캐시), `paper/service.COSTS`/`load_costs()` 는 `all_cost_models()` (이름 동일). 예전 상수
  `campaign.UPBIT_STRESS` 는 없어졌고 `campaign.COST_MODELS` 는 호환용 지연 속성이다.
- 관문 설정: 파이프라인은 `gate_config_for_campaign(campaign_id)` (전향 적용, 넘겨받은 설정은 deepcopy),
  전진 인큐베이션은 `gate_config_for_campaign(None)`(현재 gates.yaml). 설정 루트는 연구 저장소에
  `config/gates.yaml` 이 있으면 그곳, 아니면 코드 저장소(`campaign.config_root`).
- 견고성 기준: `config/gates.yaml` 의 `robustness:` (params.yaml `robustness.*`, `latency_delays_min` 만 class ①).
  `robustness.run_suite(..., thresholds=)` 로 넘기며 파이프라인은 캠페인 시점 설정의 `robustness` 를 쓴다.
  `research/robustness.py` 상수는 같은 값의 대체값이다.

## 5. 현재 상태 (2026-11-09)

- 비용 모델: 코인원 3종은 **v2(잠정 B안, 왕복 2/4/8bp)** — 사용자 채팅 승인(2026-11-09), 기록
  `research/param_approvals/`. 업비트 3종은 v1. 첫 실측 보정 제안(실측값 그대로)은 B안으로 대체되어 기각.
  근거 `research/reports/params_calibration_20261109.md`. 72시간·24 KST시간대 표본 확보 후 재보정.
- 견고성 기준(`robustness.*`)은 `config/gates.yaml` `robustness:` 로 이관됐다(값·params_version 변화 없음).
  다른 class ② 기준과 같은 절차(propose-criteria → 사람 승인 → 다음 캠페인부터)로 바꾼다.
- `risk_prefs.capital_fraction` 은 미정(사람 결정 대기).
