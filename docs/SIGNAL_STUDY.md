# 신호 선별 단계 (이벤트 스터디) · 검정력 계산기

PLAN §10b-1, §10b-3. 코드: `src/btc_quant/research/signal_study.py`, `src/btc_quant/research/power.py`.

## 0. 왜 필요한가
개발 구간 약 4.8년에서 DSR(G5) 통과에 필요한 연 Sharpe는 실질 시도 10회면 1.5~2.2, 200회면 2.0~3.3,
1,000회면 2.2~3.7이다(`power.power_table`, V = 1/T~4/T). 청산 격자(×80)가 시도 수를 폭증시키므로,
**청산을 고르기 전에** "진입 신호(+필터)가 미래 수익에 대한 정보를 갖는가, 얼마나 오래 가는가"를
신호 구성 하나당 **시도 1회**로 값싸게 판정한다. 통과한 신호만 전략화(원칙적 청산, §10b-2)로 넘긴다.

## 1. 이벤트 정의
- 결정 봉 크기 `bar_minutes` (D10: 5 또는 1; 스윙 연구 15·60·240). 결정 봉 s 가 닫히는 시각 t = s + bar_minutes 에 결정.
  15 이상은 엔진과 같은 `to_tf` 봉(UTC epoch 정렬, complete = 데이터 공백(연속 filled ≥ 30분) 없음, ENGINE_SPEC).
- 트리거 = `entry[s] & AND(filters[s]) & complete[s]` (엔진과 동일한 entry_ok).
- 진입 기준가 = 1분봉 t 의 **시가** (ENGINE_SPEC과 동일). 1분봉 t 가 없거나 filled 면 이벤트가 아니다.
- 지평 H 분 — 기본값은 결정 봉 길이별(`signal_study.HORIZON_PRESETS`, 템플릿 `study` 에서 덮어쓸 수 있음):

  | bar_minutes | horizons (분) | primary_horizons | min_gap_min |
  |---|---|---|---|
  | 1, 5 | 5·15·30·60·120·240 | 30·60·120 | 60 |
  | 15 | 15·30·60·120·240·480 | 60·120·240 | 60 |
  | 60 | 60·120·240·480·1440 | 240·480·1440 | 120 |
  | 240 | 240·480·1440·2880 | 480·1440·2880 | 480 |

  군집 제거 간격 = max(60분, 2·B): 상태형 진입이 연속 봉에서 같은 움직임을 거듭 세지 않게(남은 겹침은 HAC).
  매칭 귀무 층(KST 시 × 추적 변동성)은 그대로 유효하다 — 4h 결정 시각은 KST 01/05/09/13/17/21시 6개뿐이지만 신호와
  귀무가 같은 적격 집합(같은 결정 시각들)에서 뽑히므로 층 구성이 정확히 일치한다. 최대보유 제안·자리표시 청산은
  B 의 배수로 올림(1m/5m 는 기존처럼 5분 단위).
  - 수익 r_H = close(1분봉 t+H−1) / open_t − 1 (t+H 시각의 종가)
  - MFE_H = max(high[t … t+H−1]) / open_t − 1, MAE_H = min(low[…]) / open_t − 1
  - 비용 차감: (1+r)(1−c) − 1, c = 왕복 비용(`coinone_conservative` 10bp, `upbit_conservative` 18bp 등)
- 최장 지평 창이 데이터 끝을 넘는 결정, 층화용 추적 변동성이 아직 없는 결정은 신호·귀무 모두에서 제외.
- 기간: `screen` 은 Dev 구간 [start, end) 의 결정 시각만 쓴다. 데이터는 `DataAccess.read("dev", …)`
  로만 읽는다(Val·잠금 구간 접근 없음).

### 군집 제거 (de-clustering)
상태형 진입(여러 봉 연속 True)은 같은 움직임을 n 번 세어 표본 수를 부풀린다. 시간순으로 트리거를 훑어
**마지막으로 채택한 이벤트 + `min_gap_min`(기본 60분) 이전의 재트리거는 버린다** (채택 간격 ≥ 60분).
60분은 Phase 0에서 비용을 감당할 수 있는 최소 보유(1~2시간)의 하단이자 기본 주 지평의 중앙값이다.
H > 60 지평에서 남는 수익 창 겹침은 아래 HAC 표준오차가 처리한다. 보고: `n_raw_triggers`(원 트리거),
`n_events`(채택).

## 2. 통계량
- 지평별 평균·중앙값·적중률(r>0), 비용 차감 평균·적중률, MFE/MAE 평균·중앙·분위.
- **겹침 인식 표준오차(HAC)**: Var(평균) = n⁻²·Σ_i Σ_j w_ij e_i e_j (·n/(n−1)),
  w_ij = max(0, 1 − |t_i − t_j| / H). 달력 시간 Bartlett 커널이며, 대역폭 H에서 w 는 두 이벤트의 수익 창
  겹침 비율과 정확히 같다(가격 증분이 iid면 겹친 수익의 상관 = 겹침 비율). 삼각 커널은 양의 정부호라
  분산이 음수가 되지 않는다. 블록 부트스트랩 대신 쓰는 이유: 이벤트 시각이 불규칙해 '블록'을 정의하기
  어렵고, 겹침 구조를 직접 알고 있기 때문.

## 3. 매칭 귀무 (matched null)
질문은 "신호가 **같은 기회 집합의 무작위 시점**보다 나은가"이다.
- 적격 집합(pool) = 필터 통과 & complete & 진입 가능 & 지평 가용인 모든 결정 봉 (진입 신호 무관).
  → 필터 자체의 효과는 귀무에도 있으므로 **진입 신호의 추가 정보만** 검정한다.
- 층(stratum) = **KST 시(24) × 추적 변동성 3분위(3)** = 최대 72층. 추적 변동성 = 결정 시각까지 직전
  60개 1분 로그수익률의 표준편차, 3분위 경계 = 적격 집합 분포(층 구분용일 뿐 거래 신호가 아니므로 전 구간
  분위를 써도 미래 정보 누설이 판정에 들어가지 않는다).
- 귀무 1회 = 층 g 마다 신호 이벤트의 층 g 개수만큼 적격 집합의 층 g 에서 무작위 추출(층 구성·총 개수 정확히
  일치). 층 크기 ≥ 20×필요 개수면 복원추출(비복원과 사실상 동일), 아니면 비복원. B 회(기본 1,000) 반복.
- 이유: 시간대·변동성 국면은 평균 수익과 분산을 모두 바꾼다(Phase 0: KST 22~01시, 고변동 국면).
  "밤에만 뜨는 신호"가 밤의 드리프트를 정보로 오인하지 않게 한다(테스트
  `test_hour_stratification_removes_time_of_day_effect`).
- 귀무 추출은 군집 제거를 하지 않는다 → 귀무 평균의 분산이 약간 커져 보수적.
- `stratify` = `hour_vol`(기본) | `hour` | `none`.

### 검정통계량
z_H = (신호 평균 − 귀무 중심) / max(SE_HAC, 귀무 표준편차), p_H = 1 − Φ(z_H) (단측, 현물 롱 전용).
- 귀무 중심 = 귀무 평균들의 평균 → 시간대·국면·필터 구성에서 오는 기대수익을 제거.
- 분모의 max: 신호 이벤트는 같은 추세 구간에 몰리는 경향(양의 상관)이 있어 흩어진 귀무 추출의 분산만
  쓰면 반보수적이다. 신호 자신의 HAC 분산과 큰 쪽을 쓴다.
- 정규 꼬리를 쓰는 이유: B = 1,000이면 경험적 p 의 하한이 0.001이라 다중검정 문턱(α/m)을 표현할 수 없다.
  경험적 p(`p_null_emp`)와 백분위도 함께 보고한다(교차 점검용).

## 4. 정보 지평과 청산 제안
- **정보 지평** = z_H 가 최대인 H. 드리프트가 τ분 지속되면 초과수익은 τ까지 선형 증가 후 평평, 잡음은 √H
  로 커지므로 z 는 τ 근처에서 최대가 된다(테스트: 60분 심은 드리프트 → 60).
- **포화 지평** = 초과수익이 최대값의 90%에 처음 도달하는 H.
- 모양(`profile`): peaked / rising(최장 지평에서도 증가 — 더 긴 지평 확인 필요) / decaying(최단 지평 최대) / none.
- 제안(최적화가 아님, 고정값으로 쓰라는 뜻):
  - `max_hold_min` = 정보 지평(5분 배수로 올림)
  - 익절 = 정보 지평 안 MFE 의 중앙값, 손절 = |MAE| 의 75% 분위 (이벤트 75%가 닿지 않는 거리)
  - 같은 값을 결정 봉 ATR(14, TR 단순평균/종가) 배수로도 보고 → §10b-2 의 ATR 배수 손절·익절 패턴 중심값.

## 5. 다중검정과 통과 규칙 (사전등록)
1. 신호마다 **주 지평 k ≤ 3개를 사전등록**(기본 30·60·120분 — 비용을 감당할 수 있는 보유 범위). 6개 지평을
   모두 보고하지만 판정은 주 지평만.
2. 신호 p = min(1, k·min_{H∈주 지평} p_H) (지평 간 Bonferroni; 지평들은 양의 상관이라 보수적).
3. 선별 계열 = 이번 선별의 모든 신호 구성 + 원장에 있는 **같은 계열(family)의 이전 선별 시도**.
   여기에 **BH(FDR q = 0.10, 기본)** 또는 Holm(FWER) 적용.
   - BH 를 기본으로 한 이유: 선별은 1차 거름망이고 통과 신호는 이후 G0~G6(DSR 포함)를 다시 거치므로
     위양성 비용이 제한적이지만, 위음성(진짜 신호 탈락)은 되돌릴 수 없다. 더 엄격하게는 `method: holm`.
4. **통과 ⇔** (보정 후 기각) ∧ (n_events ≥ `min_events`, 기본 300 = G1 최소 거래 수) ∧
   (최적 주 지평에서 primary 비용 차감 평균 > 0).
   - 마지막 조건은 검정이 아니라 실용 조건: 정보가 있어도 비용을 못 넘으면 전략화 가치가 없다.
     등급 비용(업비트) 차감 값도 표에 보고(D8 A/B 등급 예비 판단용).
- 성질: 순수 잡음 신호 3개 선별을 30개 seed 로 반복하면 보정 후 유의한 seed 가 10% 이하(테스트).

## 6. 원장 기록
- `register_hypothesis` → `create_campaign(grid = 선별 템플릿, budget = 신호 구성 수, cost_models =
  [primary, grade], family)` 를 **실행 전에** 기록.
- **신호 구성 하나 = 시도 1회** (지평별이 아님). `runs` 에 primary 비용 이름으로 한 줄, metrics =
  `{kind: "signal_screen", p_signal, n_events, 지평별 요약, info_horizon, suggestion, decision, correction}`.
- 원장 스펙: 진입·필터는 실제 값, 청산은 자리표시(SL = TP = 999%, 최대보유 = 최장 지평) → logic_hash 는
  진입·필터(·결정 봉)로만 갈린다. StrategySpec `timeframe` = `tf_name(bar_minutes)` ("1m"/"5m"/"15m"/"1h"/"4h").
- family: 템플릿 `family`, 없으면 진입 블록 이름 → **같은 계열의 전략 캠페인 시도 수에 누적**되어 이후
  G5 의 n_family 를 (조금) 키운다. 선별 결과를 보고 고른 신호이므로 정직한 계산이다.
- 결과 파일: `research/campaigns/<campaign_id>/signal_screen.{json,csv,md}`, `template.yaml`.

## 7. 템플릿 형식
```yaml
name: momentum_screen
hypothesis: {text: "...", rationale: "...", success_criteria: "BH q=0.10 통과 & 순평균>0"}
data: {exchange: upbit, symbol: BTC-KRW, timeframe: 1m}
bar_minutes: 5                      # 1 또는 5 (D10) | 15 · 60 · 240 (스윙, 지평 기본값이 바뀜 — §1 표)
signals:                            # 진입 격자 × 필터 세트 (캠페인 템플릿과 같은 블록 문법, params 로 단일값 가능)
  - entry: {block: donchian_breakout, grid: {lookback: [48, 96]}}
    filters: [[], [{block: vol_regime, params: {window: 24, min_pct: 0.67}}]]
  - entry: {block: bollinger_z_revert, params: {window: 48, z: 2.5}}
study: {horizons: [5, 15, 30, 60, 120, 240], primary_horizons: [30, 60, 120], min_gap_min: 60,
        n_null: 1000, seed: 0, min_events: 300, stratify: hour_vol}
costs: {primary: coinone_conservative, grade: upbit_conservative}
correction: {method: bh, alpha: 0.10}
budget: 20                          # 신호 구성 수 상한 (초과 시 원장에 쓰기 전 거부)
family: breakout                    # 생략 시 진입 블록 이름
```
실행: `from btc_quant.research.signal_study import screen; res = screen("path.yaml"); print(res.markdown())`.
단일 신호: `study_signal(m1, BlockRef(...), (필터...), bar_minutes=5, cost_round_trip="upbit_conservative")`.

## 8. 전략 구성으로 이어지는 방식
1. **선별** (이 문서): 계열의 신호 후보 수십 개 → 통과 신호 + 정보 지평 + MFE/MAE 기반 ATR 배수.
2. **검정력 점검** (`power.budget_report`): 통과 신호로 만들 청산 캠페인의 격자 크기·계열 누적 시도 수로
   실질 시도 수를 추정하고 G5 통과 최소 연 Sharpe 를 확인. 신호의 비용 차감 평균·빈도로 얻을 수 있는
   Sharpe 가 그보다 낮으면 등록하지 않는다(격자 축소 또는 포기).
3. **전략화** (§10b-2): 진입·필터는 선별 통과 값으로 **고정**, 청산은 정보 지평 기반 max_hold + ATR 배수
   SL/TP 4~6 패턴만. 시도 수가 신호 1개 × 4~6 이라 DSR 요구치가 낮아진다.
4. G0~G6 는 기존과 동일(선별 통과는 관문 면제가 아니다).

## 9. 검정력 계산기 (power.py)
- `required_sharpe(n_days, n_trials, V, skew, kurt, min_dsr)` → DSR = min_dsr 인 연 Sharpe (brentq 로 PSR 식을 역산).
- `max_trials(sharpe_ann, n_days, …)` → 그 Sharpe 로 감당 가능한 최대 실질 시도 수.
- `estimate_effective_trials(n_grid, family)` → pipeline.campaign_stats 와 같은 식
  N_dsr = max(k_eff, (계열 기존 시도 + n_grid)·압축비). 압축비 = 저장된 `pipeline.json` 들의
  k_eff/스펙 수 중앙값(3개 이상일 때), 없으면 기본 0.05(청산 변형 ×80 이 신호 조합당 ~2 군집으로 묶인다는
  가정의 두 배 — 이력이 쌓이면 교체).
- V 기본값 = 1/n_days(진짜 SR 0인 시도들의 표본 SR 분산, 하한). 이력이 있으면 그 중앙값.
- CLI: `uv run python -m btc_quant.research.power --days 1764 --grid 1280 --family donchian_breakout --target 2.0`

| 실질 시도 N | V=1/T | V=2/T | V=4/T |
|---|---|---|---|
| 1 | 0.75 | 0.75 | 0.75 |
| 10 | 1.47 | 1.76 | 2.18 |
| 100 | 1.90 | 2.38 | 3.06 |
| 200 | 2.01 | 2.53 | 3.27 |
| 1000 | 2.23 | 2.85 | 3.72 |

(Dev 1,764일, 왜도 0, 첨도 3, DSR ≥ 0.95. 음의 왜도·두꺼운 꼬리면 더 높아진다.)

## 10. 한계
- 롱 전용 단측 검정. 하락 예측 신호는 '정보 없음'으로 나온다.
- 귀무 층화는 시간대·변동성만 맞춘다. 추세 국면 등 다른 교란은 필터로 명시해 같은 적격 집합에 넣어야 한다.
- 이벤트 스터디의 평균 수익은 실제 청산(SL/TP)의 손익과 다르다. 통과는 정보의 증거이지 수익성의 증거가 아니다.
- 1분 결정에서 블록의 봉 수 기반 파라미터(예: `BARS_PER_DAY`) 해석은 블록 쪽 1분 지원에 따른다.
