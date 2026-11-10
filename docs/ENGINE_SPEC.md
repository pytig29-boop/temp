# 백테스트 실행 의미론 (빠른 엔진·기준 엔진·페이퍼 엔진 공통 계약)

두 엔진은 같은 입력에 대해 **같은 거래 목록**을 내야 한다(차등 테스트로 강제).

## 입력
- `m1`: 1분봉 DataFrame, `quality.prepare_venue_m1(raw, exchange)` 결과 (= 거래소별 `clean_wicks`(코인원 k=0.01, 그 밖 0.03, `quality.wick_params`) → `regularize`(거래량 0 평탄 봉도 filled) → 30분 이상 filled 구간 직후 첫 실제 봉 filled(가격 유지, `gap_warmup_mask`); 연구 `campaign.prepare_m1` 과 페이퍼 `M1History(exchange=…)` 가 같은 함수·같은 값). 연속 1분 index(UTC), 컬럼 open/high/low/close/volume, `filled`(bool).
- `spec`: `StrategySpec`
- `cost`: `CostModel`
- `ctx`: `Context` (보조 데이터)
- `start`, `end`: 진입 허용 구간 [start, end). 열린 포지션은 end 이후 첫 1분봉 시가에 `end` 사유로 청산(데이터가 없으면 마지막 종가).

## 결정 주기 B (`StrategySpec.timeframe`)
- `"5m"`(기본) → B = 5, `"1m"` → B = 1, 스윙 연구용(PLAN §10 대안, 2026-11-09 추가) `"15m"` → 15, `"1h"` → 60,
  `"4h"` → 240. 아래 규칙은 모두 B로 일반화되어 있고, B = 5일 때 이전 규칙과 완전히 같다.
  이름은 항상 `spec.TIMEFRAMES` 키(`tf_name(B)`) — `"60m"` 같은 이름은 거부된다(다른 logic_hash 방지).
- 블록은 결정 주기 봉 `bars = to_tf(m1, B)`로 평가된다 (`to_5m = to_tf(·, 5)`).
  봉 index는 UTC epoch 기준 B분 정렬(모든 B가 1440의 약수 → UTC 자정 정렬과 같음). B = 1이면 1분봉 그대로.
  - **4h 봉 = 00/04/08/12/16/20 UTC = KST 09/13/17/21/01/05시.** KST 자정(15:00 UTC) 정렬은 쓰지 않는다:
    (1) 결정 경계(epoch 분 % B)·쿨다운 올림·빠른 엔진 결정 배열이 모두 UTC epoch 정수 연산이라 하나의 정의로 유지되고,
    (2) 한국 거래소 일봉 경계(업비트·코인원 일봉 09:00 KST = 00:00 UTC)와 이미 일치하며,
    (3) 일간 수익 집계(`daily_returns`)도 UTC 일자다. 시간대 효과는 `session_kst`(≤ 1h) 같은 블록의 몫.
  - 입력 1분봉 index 는 UTC 여야 한다(모든 경로가 UTC). `to_tf`의 리샘플 원점(첫날 자정)과 epoch 정렬이 같아지는 조건.
- `bars.complete[s]`:
  - B ≤ 5: 봉을 이루는 1분봉 B개가 모두 실제(filled 아님). B = 1이면 `complete[s] = ~filled[s]`.
  - **B ≥ 15**: 1분봉 B개가 모두 있고(부분 봉 아님), 봉 안 어느 분에서도 '그 분에서 끝나는 연속 filled 길이'
    (이전 봉에서 이어진 것 포함, `features.filled_run_length`)가 30분(`OUTAGE_MIN` = `quality.GAP_WARMUP_MIN_GAP`)
    미만. 즉 **데이터 공백만 배제하고 거래량 0인 조용한 분은 허용**한다. 이유: 코인원 Dev 1분봉의 7.6%가 filled 라
    '전부 실제' 규칙이면 4h 봉의 25%(2025년 3%), 1h 봉의 42%(2025년 14%)만 complete — 진입이 고활동 구간으로 편향되고
    표본이 사라진다. 새 규칙: 15m/1h/4h 모두 99.5% 이상 complete. filled 평탄 봉은 직전 종가라 봉 OHLC 를 바꾸지 않는다.
    후행 창(페이퍼)에서도 결정 봉 값은 전체 이력과 같다(창 길이 ≫ 30분).
- `max_hold_min`은 B의 양의 배수 (1m: 임의의 양의 정수 분; 4h: 240의 배수).
- 결정 경계 판정은 두 엔진 모두 **UTC epoch 분 % B == 0** (`signals.boundary_mask`, `reference.is_boundary`).
  B ≤ 60 이면 '시각의 분 % B' 와 같고, B = 240 이면 4시간 경계(01:00 등 정시는 경계가 아님).
- 쿨다운 올림 `ceilB` 도 epoch 정수 연산(B = 1·5 는 기존 `Timestamp.ceil` 경로 그대로).

## 신호
- 결정 주기 봉 `bars = to_tf(m1, B)`. 봉 시작 s, 정보 확정 시각 s+B분.
- `entry_ok[s] = entry[s] & AND(filters[s]) & bars.complete[s]`
- `exit_sig[s]` = 신호 청산 블록 (없으면 항상 False)

## 상태 기계 (포지션 최대 1개, 롱 온리, 전액 — 부분 사이징·분할 매수 사다리는 아래 "부분 사이징·분할 매수" 절)
1분봉 i를 시간순으로 처리. 결정 시점은 B분 경계(epoch 분 % B == 0)인 1분봉 i의 **시가 직전** = 직전 봉 s = t_i − B분의 종가 확정 시점.
(B = 1: 모든 1분봉이 결정 시점이고, 결정은 직전 1분봉 t_i − 1분의 신호를 쓴다.)

### 진입
- 조건: 무포지션, `entry_ok[t_i − B]`, `t_i >= start`, `t_i < end`, 쿨다운 해제, 1분봉 i가 filled 아님,
  (ATR 모드) 결정 시점 ATR 비율이 유한한 양수.
- 체결: 1분봉 i의 시가 `o_i` → `entry_fill = cost.buy_fill(o_i)`. 진입 기준가 `ref = o_i`.
- 레벨 (진입 시 한 번 계산, 보유 중 고정):
  - 고정 % 모드: `sl = ref × (1 − SL/100)`, `tp = ref × (1 + TP/100)`
  - ATR 모드: `sl = ref × (1 − k_sl × a)`, `tp = ref × (1 + k_tp × a)`, `a = ATR_n(t_i − B) / o_i` (아래 "ATR 청산")
  - 만기 `t_exp = t_i + max_hold_min`.

### ATR 청산 (`ExitSpec.stop_loss_atr = k_sl`, `take_profit_atr = k_tp`, `atr_bars = n`, 기본 14)
- 결정 주기 봉 행 순서 j(= `to_tf` 결과의 행; complete 여부와 무관하게 모든 봉 포함, filled 평탄 봉도 그대로)에 대해
  `TR_j = max(H_j − L_j, |H_j − C_{j−1}|, |L_j − C_{j−1}|)` (j ≥ 1; TR_0 없음).
- `ATR_n(j) = (TR_{j−n+1} + … + TR_j) / n` — 단순 평균, **왼쪽부터 순차 합산**(어느 후행 창에서 다시 계산해도
  비트 단위 동일). TR이 n개 미만인 j(< n)는 정의되지 않음.
- 인과성: 결정 시점 t_i에 쓰는 값은 봉 `s = t_i − B`(결정 봉, 포함)까지의 봉으로만 계산된다. 진입 후 봉은 레벨에 영향 없음.
- 비율 `a = ATR_n(s) / o_i` (진입 기준가 대비 비율). 연산 순서: `a = atr / o`, `d = k × a`, `level = ref × (1 ∓ d)`.
- `a`가 정의되지 않았거나(워밍업·결정 봉 없음) 유한한 양수가 아니면(ATR = 0 포함) 그 결정에서는 **진입하지 않는다**
  (진입 신호 False와 동일 — 쿨다운 등 상태 변화 없음).
- 그 밖의 청산 규칙(우선순위·갭·틱·hold_min·쿨다운)은 고정 % 모드와 같다.

### 보유 중 1분봉 k (진입 봉 i 포함) — 우선순위 SL > TP > 신호 > 시간
1. **만기**: `t_k >= t_exp` 이면 1분봉 k 시가에 청산 (`time`) — 이 봉에서 다른 검사보다 먼저 (k 시가 시점이므로).
2. **신호 청산**: t_k가 B분 경계이고 `exit_sig[t_k − B]` 이고 k > i 이면 k 시가에 청산 (`signal`). 단 1번과 동시면 `time`.
3. **SL 갭**: k > i 이고 `o_k <= sl` → 기준가 `o_k` (`sl`)
4. **SL 터치**: `l_k <= sl` → 기준가 `sl` (`sl`)
5. **TP**: `h_k >= tp + cost.tick` → 기준가 `tp` (`tp`). (갭 상승이어도 `tp`로 보수적 처리)
6. 같은 봉에서 3·4와 5가 동시 → SL.
- 시가 청산(1·2)은 해당 봉의 SL/TP 검사 **전에** 일어난다(봉 k 시가에 이미 나감).
- 청산 체결: `exit_fill = cost.sell_fill(기준가)`.
- 수익: `gross_ret = 기준가/ref − 1`, `net_ret = cost.net_return(entry_fill, exit_fill)`.
- `hold_min` = 청산 봉 시각 − 진입 봉 시각 (분). 봉 내 터치 청산은 해당 봉 시각 + 1분으로 계산.

### 쿨다운
청산 봉 시각 `t_x`(봉 내 청산이면 `t_x + 1분`) 이후 **다음 B분 경계 결정 시점은 건너뛰고** 그 다음부터 진입 가능.
(= 청산 후 최소 1개의 완전한 결정 주기 봉을 관찰한 뒤 진입)
- B = 1: 봉 내 청산(봉 k) → t_eff = t_k + 1분 → 결정 t_k + 1분(봉 k 기반)은 건너뛰고 t_k + 2분부터 진입 가능.
  시가 청산(봉 k 시가) → 결정 t_k는 건너뛰고 t_k + 1분(청산 후 전체를 관찰한 봉 k 기반)부터 진입 가능.

### 데이터 공백
`filled` 봉은 평탄 봉(OHLC = 직전 종가)이므로 그대로 처리. 공백 후 첫 실제 봉의 갭은 SL 갭 규칙으로 반영된다.
진입은 `bars.complete[s]`가 True일 때만. B = 1이면 "완전한 봉" = filled가 아닌 1분봉이므로,
진입에는 결정 봉 t_i − 1분과 진입 봉 t_i 둘 다 실제 봉이어야 한다.

### 긴 결정 주기(15m/1h/4h)에서의 해석
- 진입은 결정 봉이 닫힌 직후의 1분봉 시가(예: 4h 봉 00:00–04:00 → 04:00 시가), 보유 중 청산 감시는 그대로 1분 단위.
- 신호 청산은 B분 경계에서만(4h 봉마다 한 번), 만기는 B의 배수라 항상 경계에서 일어난다.
- 쿨다운: 봉 내 청산(예: 05:10 손절, t_eff 05:11) → 다음 경계 08:00 은 건너뛰고 12:00 부터 진입 가능
  (= 청산 후 4h 봉 하나를 완전히 관찰). 시가 청산(08:00 time/signal) → 08:00 건너뛰고 12:00 부터.
- ATR 은 결정 주기 봉(4h 봉)의 TR 단순 평균 — `atr_bars = 14` 는 56시간.
- 차등 테스트: `tests/test_engine_long_tf.py` (1m/5m/15m/1h/4h × %/ATR, 250 사례, 비트 단위 일치).

## 출력
- `trades`: `TRADE_COLUMNS` 순서, entry_time 오름차순.
- `daily_returns`: UTC 일자별 (1 + net_ret)의 곱 − 1, [start, end) 모든 날 포함(무거래일 0).

## 비용
`CostModel`: 편도 가격 불리분 = 스프레드/2 + 슬리피지, 수수료는 양방향 비율 차감.

## 확정된 세부 해석 (두 엔진 구현 기준, 2026-11-09)
1. **쿨다운**: 다음 진입 가능 결정 시점 = `ceilB(t_eff) + B분` (t_eff 이후 첫 B분 경계는 건너뜀; ceil은 UTC epoch 기준). 시가 청산(time/signal)이 경계 t에서 일어나면 t는 건너뛰고 t+B분부터 진입 가능.
2. **SL 갭**도 봉 내 청산으로 취급 → t_eff·hold_min은 봉 시각 + 1분.
3. **filled 1분봉에서는 진입하지 않는다** (시가가 합성값이므로). 5분봉이 complete여도 동일.
4. `end` 이후 첫 봉에서 time/signal 조건이면 해당 사유, 아니면 `end`. 가격은 그 봉 시가. `end` 이후 데이터가 없으면 마지막 종가, hold_min +1분.
5. `daily_returns` index는 [start, end)의 UTC 일자. `end` 사유 청산이 범위 밖 날짜면 마지막 날에 귀속.
6. 신호 청산은 결정 주기 봉 complete를 요구하지 않는다 (진입만 요구).
7. 빠른 엔진은 `CostModel`의 체결·수익 계산식을 연산 순서까지 그대로 복제한다 (비트 단위 일치). 수식 변경 시 양쪽 동시 수정.

## 확정된 세부 해석 — 결정 주기·ATR 청산 (2026-11-09 추가)
8. **하위 호환**: `timeframe` 기본 "5m", 고정 % 청산은 기존과 동일. `to_dict`/`logic_hash`는 기본값 필드
   (`timeframe = "5m"`, ATR 필드 미사용, `atr_bars = 14`)를 생략하므로 기존 스펙의 해시·YAML은 바이트 단위로 같다.
9. **청산 모드는 정확히 하나**: 고정 %(`stop_loss_pct`, `take_profit_pct`) 또는 ATR(`stop_loss_atr`, `take_profit_atr`, 선택 `atr_bars`).
   섞으면 검증 실패. 고정 % 모드에서 `atr_bars ≠ 14`도 검증 실패.
10. **파라미터 수(MAX_PARAMS)**: 청산은 손절·익절·최대보유 = 3 (두 모드 공통). ATR 모드에서 `atr_bars`가 기본값 14가
    아니면 선택된 파라미터로 보고 +1 (기본값 14는 고정 상수로 취급해 세지 않고, 직렬화에서도 생략).
11. **ATR 값 전달**: 기준 엔진은 `on_decision(t, entry_ok, exit_sig, atr)`로 결정 봉 t − B의 ATR(절대값)을 받고 진입 시
    `a = atr / o_i`를 계산한다. 빠른 엔진은 `prepare`에서 결정 행별 `a`를 ATR 창마다 한 번 계산해 두고
    (`Prepared.atr_frac[n]`) 배수 격자만 스윕한다. 두 엔진의 연산 순서는 같다(비트 단위 일치).
12. **ATR과 공백**: 결정 주기 봉은 `to_tf` 결과의 행 순서로 이어 붙인다(완전히 빈 봉은 없음 — `regularize` 후 입력 기준).
    filled 평탄 봉도 TR 계산에 그대로 포함된다(B = 1이면 그 봉의 TR = 0). 공백 직후의 갭은 다음 실제 봉의 TR에 반영된다.

## 지정가 집행 (`StrategySpec.execution`, 2026-11-10 추가)

"싸게 조금 사고 비싸게 조금 판다"(그리드·수동적 평균회귀)를 메이커 주문으로 시험하기 위한 규칙. 기본값
(`entry: market`, `take_profit: market`)은 위 의미론 그대로이고 직렬화·`logic_hash` 에서 생략된다(기존 해시 불변,
빠른 엔진은 기존 `_core` 경로 그대로).

### 스펙
```yaml
execution:            # 생략 = 시장가 (기존)
  entry: limit        # market | limit
  offset_bp: 5        # 또는 offset_atr: 0.5 (정확히 하나)
  tif_bars: 3         # 지정가 유지 결정 주기 봉 수 (기본 1)
  take_profit: limit  # market (기존 TP 규칙) | limit (익절가 매도 지정가). 손절은 항상 시장가
```
- 파라미터 수: 지정가 진입이면 오프셋 +1, `tif_bars ≠ 1` 이면 +1 (MAX_PARAMS 8 안). 익절 방식은 모드라 세지 않는다.
- 체결 보수성 파라미터는 스펙이 아니라 `backtest.types.LimitFillModel`(시장 측정치 class ①, 비용 모델과 같은
  위치): `run_fast/run_reference(..., fill=)`, `simulate_*(..., fill=)`, 기본 `LIMIT_FILL_DEFAULT`.

### 진입 지정가 (entry = limit)
- 주문: 시장가 진입이 일어났을 바로 그 결정(같은 조건: 무포지션·entry_ok·[start, end)·쿨다운·봉 i 실제·ATR 유효)에서,
  1분봉 i 시가에 **매수 지정가** `L = snap_down(o_i × (1 − d))`, `d = offset_bp/1e4` 또는 `offset_atr × ATR(s)/o_i`
  (ATR 창 = `exit.atr_bars`, 고정 % 청산이면 14). 기준가가 시장가 진입과 같은 o_i 다(시장가는 o_i 에 체결된다고
  가정하므로 대칭; 페이퍼/실거래는 그 분 첫 체결 후 주문 — 아래 대기열 재현의 τ).
- 유지: 1분봉 i, i+1, …, `t_i + tif_bars × B` 직전까지. 만료 시각 이상의 첫 1분봉 시가 전에 취소되고, 그 봉이
  결정 경계면 같은 봉에서 새 주문이 가능하다(미체결 만료에는 쿨다운 없음). 주문이 걸려 있는 동안의 결정은 무시.
  기간 종료(`end`) 이후 1분봉에서는 취소(체결 없음).
- 체결(1분봉 규칙, 아래): 1분봉 k 에서 처음 성립하면 `entry_time = t_k`, 기준가 = 체결가 = **L** (가격 개선 없음;
  시가가 L 아래로 갭이어도 L), 비용 = 수수료만(`CostModel.fee_rate`; 스프레드·슬리피지 없음).
- 레벨: `sl = L(1 − d_sl)`, `tp = L(1 + d_tp)` (ATR 모드 `d = k × ATR(s)/o_i` — 결정 시점 값), 만기 `t_k + max_hold_min`.
- **체결 봉 k 의 보수적 처리**: 봉 안에서 체결이 언제였는지 모르므로 익절은 하지 않고(고가가 체결 전일 수 있음)
  손절만 본다 — `o_k ≤ sl` 이면 시가 `o_k` 로(갭), 아니면 `l_k ≤ sl` 이면 `sl` 로. 다음 봉부터는 위 보유 규칙 그대로
  (시가 청산은 k 보다 뒤 봉에서만).
- `hold_min` = 청산 t_eff − t_k. 쿨다운은 청산 기준 그대로.

### 익절 지정가 (take_profit = limit)
- 진입 체결 시 `T = snap_up(tp)` 에 매도 지정가. 체결 = 1분봉 규칙(매도), 체결가 = T, 비용 = 수수료만, 사유 `tp`.
  시장가 진입이면 진입 봉부터, 지정가 진입이면 체결 다음 봉부터 판정. 우선순위는 SL > TP 그대로.
- `take_profit: market` = 기존(`h ≥ tp + cost.tick` → `tp` 에 비용 반영 체결).

### 1분봉 체결 규칙 (`LimitFillModel`) — 보수적 관통 규칙
- 매수 지정가 L 은 1분봉 k 에서 `low_k ≤ L − thr` 일 때만 체결, 매도 지정가 T 는 `high_k ≥ T + thr`.
  `thr = max(through_ticks × tick, P × through_bp × 1e-4)` — **터치(low = L)는 체결로 보지 않는다**: 같은 가격의
  대기열 앞쪽이 먼저 체결되므로 보장이 없다. 가격이 L 아래에서 거래됐다면 가격-시간 우선상 L 의 매수 대기열은 모두
  소진된 것이므로 체결이 보장된다.
- `tick`: `tick_from_data=True`(기본)면 **1분봉 데이터에서 UTC 일자별로 추정한 호가 단위**
  (`signals.infer_ticks`: 그날 실제 봉 종가의 99 % 이상이 배수인 가장 큰 1·5×10^k, 가격의 0.01 bp 미만 단위는 불가,
  실제 봉 없는 날은 직전 값). 코인원 BTC 는 2024-09-29/30 에 1,000 → 10,000원으로 바뀌었다(현재 ≈ 1 bp).
  주문 시각의 tick 을 쓴다(진입 = 주문 봉 i, 익절 = 진입 체결 봉). 추정 tick 이 없으면(0) 진입 지정가는 걸지
  않고(진입 신호 False 와 동일), 익절 지정가는 체결되지 않는다. `tick_from_data=False` 면 고정 `tick`(0 = 연속 가격)
  — 이때 bp 조건이 1 tick 미만이면 실제 격자 주문 기준으로 터치 체결이 섞여 낙관적이 된다.
- 지정가는 tick 격자로 맞춘다(매수 내림, 매도 올림; tick 0 이면 그대로).
- filled(합성) 1분봉에서는 체결되지 않는다. `min_volume > 0` 이면 그 봉 거래량 ≥ min_volume 일 때만.
- `fill_prob < 1`: 주문마다 결정적 해시 `u = hash_u01(주문 시각 epoch 분, 2·seed + side)` (side 0 매수 / 1 매도;
  익절은 진입 체결 분) 가 `u < fill_prob` 인 주문만 체결될 수 있다(재현 가능한 추가 할인). 불합격 주문은 수명 동안
  체결되지 않는다.
- 연산 순서(두 엔진 동일, 비트 단위): `px = o × (1 − d)`; `px = floor(px / tick) × tick`; `a = m × tick`;
  `b = px × through_bp × 1e-4`; `trig = px − (a if a > b else b)`. 익절: `T = ceil(tp / tick) × tick`, `T + max(…)`.
- 현재 값(2026-11-10, 잠정 — `research/reports/fill_model_20261110.md`): `through_ticks = 1`, `through_bp = 0`,
  `fill_prob = 1`, `tick_from_data = True`. 실시간 대기열 재현 대비 1 tick 관통의 정밀도(1분봉 규칙 체결 중 실제로
  같은 분 끝까지 체결된 비율) ≈ 99.5 % (95 % 하한 ≈ 99 %, 약 7시간 자료), 터치 규칙은 ≈ 74 % (낙관적).

### 대기열 재현 (`backtest.queue`) — 보정·페이퍼 공용
- 주문 시각 τ (진입 = 그 분 첫 체결 + 지연, 익절 = 진입 체결 + 지연). τ 에 받은 마지막 호가 스냅샷에서 우리 가격의
  표시 수량 A = 앞선 대기량(우리는 맨 뒤). 반대편 최우선과 맞닿으면 cross(τ 에 체결), 최우선보다 좋은 가격이면 A = 0,
  보이는 범위 밖(15단계보다 깊음)이면 처음 보일 때의 표시 수량.
- 체결: 우리 가격보다 나쁜 쪽 체결이 하나라도 나오면(관통) 체결; 우리 가격에서 우리 쪽을 치는 테이커 수량 누적이
  A + 우리 수량 이상이면 체결. 취소로 인한 전진은 인정하지 않는다(낙관 변형 `optimistic=True` 는 비교용).
- `simulate_order`(배열, numba) 와 `QueueSim`(상태 기계) 는 같은 결과(테스트).

### 페이퍼 훅
- `ReferenceEngine.from_exit(..., execution=spec.execution, fill=...)`; 매 1분봉 `on_bar_1m(ts, o, h, l, c, filled,
  volume, tick)` — `tick` 은 거래소 호가 단위(실시간). 걸린 진입 주문은 `engine.order`(`RestingOrder`: id, kind, side,
  price, placed, expires), 익절 주문은 `engine.position.tp_order`, 주문 기록 `engine.orders_log`(placed/filled/expired).
- `engine.fill_oracle = QueueFillOracle(latency_s=…, notional=…)` 을 꽂으면 1분봉 규칙 대신 대기열 재현으로 체결을
  판정한다. 페이퍼는 수신하는 체결마다 `oracle.add_trade(t_ns, price, qty, taker_side)`, 호가 스냅샷마다
  `oracle.add_book(t_ns, book_dict)` 를 시간순으로 넣고, 1분봉이 닫히면 평소처럼 `on_bar_1m` 을 부른다(엔진이 주문마다
  오라클에 "1분봉 ts 동안 체결됐나"를 묻는다). 체결가 = 지정가, 비용 = 수수료만.
- 페이퍼 트레이더(`paper/trader.py`, 2026-11-10 반영): (1) 엔진 생성에 `execution=spec.execution`,
  `fill=LIMIT_FILL_DEFAULT`; (2) 서비스 `limit_fill="queue"`(CLI `--limit-fill queue`)면 `QueueFillOracle` 을 꽂고 수신
  체결·호가를 `add_trade/add_book` 으로 넣는다(기본 "bar" = 1분봉 관통 규칙 = 백테스트와 같은 판정);
  (3) `on_bar_1m(..., bar.volume, tick)` — tick = **거래소 가격대별 호가 단위 정책**(2026-11-10~: `data.ticks`, 캐시
  `config/ticks.yaml` = 코인원 공개 API `range_units`)에서 그 봉 시가의 단위. 정책표가 없는 거래소(1분봉 이력 exchange 없음)나
  `PaperTrader(tick_source="infer")` 면 그 UTC 일자(+ 직전 일) 실시간 1분봉 종가에서 `infer_ticks`(백테스트와 같은 추정).
  백테스트는 데이터 추정 유지(과거 표가 달랐다 — research/reports/ticks_policy_check_20261110.md);
  (4) `_on_fill` 은 지정가 체결에서 호가 대입 비교를 건너뛰고 `maker: true` 로 기록; (5) 상태(STATE_VERSION 2, 1 도 읽음)에
  `engine.order`·`position.tp_order`·`position.maker`; (6) 리스크 강제 청산·사람 정지 시 걸린 진입 지정가 취소
  (`fills.jsonl` `order_cancel`).

### 연구 파이프라인과의 관계
- `fast.prepare_spec` 은 `Prepared.execution` 에 스펙의 집행을 담고 `simulate_prepared / simulate_exit /
  simulate_many` 는 인자 `execution=` 이 없으면 그것을 따른다. `prepare(...)` 를 직접 쓰는 연구 경로(캠페인
  `SignalCache`, G2 귀무 `nulls.RandomEntryNull`, 파이프라인 Dev 재실행)는 집행을 **명시적으로**
  `simulate_*(execution=…, fill=LIMIT_FILL_DEFAULT)` 로 넘긴다(2026-11-10 반영 — 캠페인 템플릿 `execution` 격자,
  G2 귀무 = 무작위 진입 시각에 같은 지정가 규칙, G3 인접 = 오프셋·tif 격자 한 칸 포함, 기여도·견고성·G6 = 스펙 집행
  그대로). 집행을 지원하지 않는 템플릿(신호 선별)은 `execution` 키를 거부한다(조용히 시장가로 평가하지 않음).
  견고성 지연 시나리오: 지정가는 지연된 결정 시점 시가 기준으로 다시 걸고(ATR 오프셋 배열도 지연 행으로 이동),
  사후 재가격되는 청산(SL/TP/만기)은 시장가로 처리(지정가 익절도 지연 시 테이커 비용 — 보수).
- 차등 테스트: `tests/test_engine_limit.py` (1m/5m/15m/1h × %/ATR × bp/ATR 오프셋 × 시장가/지정가 익절 × 고정/추정
  tick × fill_prob/min_volume, 240 사례, 비트 단위 일치) + 시나리오.

## 다자산 유니버스 (PLAN D15, 2026-11-10) — 엔진 밖의 회계
엔진 계약(위 전부)은 **자산 하나**에 대한 것이고 바뀌지 않는다. 다자산 스펙(`StrategySpec.universe`, 예: BTC/XRP/ETH-KRW)은
자산마다 그 자산의 `m1`(같은 `prepare_venue_m1`)·같은 `spec`·같은 `cost` 로 엔진을 따로 실행한다(슬리브, 자산별 독립 포지션).
결합은 엔진 밖(`research.universe`)에서: 포트폴리오 `daily_returns` = 슬리브 `daily_returns`(같은 [start, end) 일자)의 단순 평균,
`trades` = 슬리브 거래를 합친 목록(+ `symbol` 열, 진입 시각 순). 차등 테스트·미래참조 테스트는 자산마다 그대로 적용되고,
G6 의 기준 엔진 대조는 모든 자산에서 일치해야 한다. 기본 유니버스 [BTC-KRW] 는 엔진 호출·결과가 이전과 같다.

## 부분 사이징·분할 매수 사다리 (`StrategySpec.sizing`, `StrategySpec.ladder`, 2026-11-10, PLAN D16)

사용자 요청(2026-11-10): "떨어지면 일부 사고, 더 떨어지면 또 일부 산다" — 진입마다 자본 일부만 쓰고, 하락 단계마다
분할 매수. 기본값(`sizing` 없음 = 전액, `ladder` 없음 = 단일 진입)은 위 의미론·직렬화·`logic_hash`·거래 표·일간 수익이
**비트 단위로 그대로**다(빠른 엔진은 기존 `_core`/`_core_x` 경로, 거래 표에 새 열 없음). 차등 테스트
`tests/test_engine_ladder.py` (320 무작위 사례: 1m/5m/15m/1h/4h × %/ATR 청산 × 시장가/지정가 첫 진입 × 시장가/지정가 익절 ×
트리거/지정가 분할 × per_tranche/average × bp/ATR 간격 × 고정/추정 tick × fill_prob/min_volume × 부분 사이징 + 다자산
슬리브 12 사례, 비트 단위 일치) + 시나리오.

### 스펙
```yaml
sizing:                # 생략 = 전액 (기존)
  entry_fraction: 0.5  # 0 < f ≤ 1: 진입마다 슬리브 자산의 f, 나머지는 현금(수익 0)
ladder:                # 생략 = 단일 진입 (기존). sizing 과 함께 쓸 수 없다
  tranches: 3          # N ∈ [2, 5], 분할 크기 w = max_exposure / N (동일·고정, 비중 맞춤 없음)
  step_bp: 150         # 또는 step_atr: 1.0 (정확히 하나) — 단 간격 d
  order: market        # market (시장가 트리거) | limit (지정가)
  exit: average        # per_tranche | average
  max_exposure: 1.0    # 0 < x ≤ 1 (생략 = 1.0; class ③ 상한 risk_prefs.max_exposure_per_sleeve 이하)
```
- 파라미터 수(MAX_PARAMS 8): 사다리 +2(분할 수·간격). 주문·청산 방식은 모드(고정 선택이면 0, 캠페인 격자로 고르면 +1),
  `max_exposure` 는 위험 선호(0), `entry_fraction` 은 스펙 n_params 0(캠페인에서 격자로 고르면 +1). 예: 진입 2 + 필터 1 +
  청산 3 + 사다리 2 = 8 (상한에 닿음), 지정가 진입(오프셋 +1)까지 더하면 9 → 거부.
- 손절은 필수(청산 스펙의 SL > 0 — 없으면 스펙 무효). 익절폭·손절폭은 `exit` 의 % 또는 ATR 배수, 만기 = `max_hold_min`,
  신호 청산 블록도 그대로.

### 부분 사이징 (`entry_fraction` = f, 사다리 없음)
엔진 경로(진입·청산 시각·가격·사유)는 전액과 **같고**, 거래 행에 `weight = f`, `ladder = 거래 번호`, `tranche = 0` 이
붙는다. 거래마다 슬리브 손익 = f × net_ret → 일간 수익 = Π(1 + f·r) − 1 (아래 회계). 거래 단위 손익은 f 에 정확히 비례.

### 사다리 상태 기계 (결정·진입 조건·쿨다운·데이터 공백 규칙은 위와 같다)
- **첫 분할(j = 0)**: 기존 진입 그대로(시장가 = 결정 봉 다음 1분봉 시가 o_i, 지정가 = `execution` 지정가 규칙). 체결 1분봉 k0,
  기준가 P1, 그 시각 = 사다리 시작. 사다리 번호 = 지금까지 연 사다리 수(0, 1, …).
- 결정 시 값 (진입 시 한 번, 사다리 동안 고정): `d_sl`, `d_tp` = 고정 % /100 또는 ATR 배수 × a (a = ATR(s)/o_i, 결정 시점 —
  지정가 첫 진입이면 주문 결정 시점의 a), `d = step_bp/1e4` 또는 `step_atr × a`. ATR 창 = `exit.atr_bars`(고정 % 청산이면 14).
  ATR 간격 사다리는 ATR 이 필요하다(정의되지 않으면 진입하지 않음 — ATR 청산과 같은 규칙).
- **단 가격** (j = 1..N−1): `raw_j = P1 × (1 − j × d)` (연산 순서 그대로). 사다리 호가 단위 `tick` = 첫 체결 봉 k0 의 주문 tick
  (`LimitFillModel.order_tick`; 모든 사다리 주문 — 분할 매수·분할 익절·평균 익절 — 이 같은 tick).
  - `order: limit`: 매수 지정가 `L_j = snap_down(raw_j)`, 체결 판정 `low ≤ L_j − thr(L_j)`, 체결가 L_j(가격 개선 없음),
    비용 = 수수료만. filled 봉 불가, `min_volume`, `fill_prob` 해시 `u = hash_u01(epoch_min(k0)·8 + j, 2·seed + 0)`.
    지정가 체결 규칙 = 위 "1분봉 체결 규칙" 그대로.
  - `order: market` (시장가 트리거 — 보수적 관통 규칙): 트리거 `trig_j = raw_j − thr(raw_j)`. 1분봉 k 에서
    `o_k ≤ raw_j` 이면 **시가 o_k** 에 체결(갭: 이미 아래에서 시작 — 트리거가 첫 체결에 발동), 아니면 `low_k ≤ trig_j` 이면
    **raw_j** 에 체결(단을 1 tick(관통 폭) 이상 지나 거래됐을 때만; 터치는 불가). 즉 체결가 = min(시가, 단 가격),
    비용 = 시장가(`CostModel.buy_fill`, 스프레드·슬리피지·수수료). filled 봉 불가, fill_prob·min_volume 은 적용 안 함(테이커).
  - tick 을 모르면(추정 0) 분할 주문은 체결되지 않는다(보수적).
  - 분할 주문은 첫 분할 체결 시 모두 걸리고(재장전 없음 — 한 사다리에서 단마다 최대 1회), 사다리가 끝나면 취소된다.
  - **활성**: 시장가 첫 진입이면 k0 봉부터(시가 진입 후 봉 안의 하락은 진입 뒤에 일어난 것), 지정가 첫 진입이면 k0 다음 봉부터
    (봉 안 순서를 모름 — 같은 봉의 관통은 무시).
- **공동 손절 (필수)**: `S = (P1 × (1 − (N−1) × d)) × (1 − d_sl)` — **마지막 단 가격 기준**(평균단가 기준이 아님: 체결과
  무관하게 사다리 시작 시 고정 → 최대 손실이 진입 시 확정된다. 평균단가 기준은 분할이 체결될수록 손절이 내려가 손실 한도가
  움직인다). 모든 보유 분할을 시장가로: `o_k ≤ S` 이면 시가 o_k(갭), 아니면 `low_k ≤ S` 이면 S.
- **익절**
  - `per_tranche` (그리드): 분할마다 `tp_j = ref_j × (1 + d_tp)` (익절 지정가면 `snap_up`, 해시 `epoch_min(체결 봉)·8 + j`),
    그 분할만 청산(시장가 익절 = `high ≥ tp_j + cost.tick` → tp_j 에 비용 반영 / 지정가 = 관통 규칙, 수수료만).
    다른 분할은 계속 보유 — 하락 후 반등하면 아래 단부터 차례로 왕복(그리드). 청산된 단은 다시 걸리지 않는다.
  - `average`: 평균단가 `A = n / Σ_j (1/ref_j)` (분할 명목이 같으므로 조화평균, 체결 순서대로 합산), 익절가 `A × (1 + d_tp)`
    (지정가면 `snap_up`, 해시 `epoch_min(마지막 체결 봉)·8 + 7`, 분할 체결마다 다시 계산·재주문) — 도달하면 **전량** 청산.
- **만기**: 첫 분할 체결 + `max_hold_min` (= 가장 오래된 분할) 이상의 첫 1분봉 시가에 전량(`time`). 신호 청산·기간 종료(`end`)도
  전량 시가 청산. 데이터 끝이면 마지막 종가(`end`).
- **사다리 종료**: 보유 분할이 0 이 되는 순간(전량 청산 또는 per_tranche 익절로 마지막 분할 청산). 걸린 분할 주문 취소,
  쿨다운 = 마지막 청산의 t_eff 기준(위 규칙 그대로). **완전히 청산되고 쿨다운이 지나기 전에는 새 사다리를 열지 않는다.**

### 1분봉 k 처리 순서 (사다리 모드; 보수적 — 손절이 익절보다 먼저, 봉 안 체결 분할은 그 봉에서 익절 없음)
1. 시가 청산 (k > k0): 만기 > 신호 > 기간 종료 → 전량 o_k (t_eff = t_k), 사다리 종료.
2. 첫 분할(사다리 없을 때): 시장가 진입 또는 지정가 주문/체결 (기존 규칙).
3. 분할 체결: 걸린 단을 j 오름차순(가격 내림차순)으로 판정·체결 — 한 봉에서 여러 단이 체결될 수 있다.
4. 공동 손절: `o_k ≤ S` → 전량 o_k, 아니면 `low_k ≤ S` → 전량 S (이 봉에서 막 체결된 분할 포함 — 단을 지나 손절까지 내려간
   '떨어지는 칼' 봉은 체결 후 손절로 보수 처리). t_eff = t_k + 1분.
5. 익절 (4 가 아니면): per_tranche — 체결 봉 < k 인 분할(또는 시가에 체결된 첫 분할)만, j 오름차순; average — 이 봉에 봉 안
   체결(3, 또는 지정가 첫 분할의 체결 봉)이 없을 때만.
6. 보유 0 → 사다리 종료(쿨다운).
기간 종료(`end`) 이후 1분봉에서는 분할이 체결되지 않는다(1 에서 전량 청산).

### 거래 표·회계
- 거래 행 = 분할 하나. 열 `TRADE_COLUMNS` + `weight`(분할 크기 = 명목 / 사다리 시작 시 슬리브 자산), `ladder`(사다리 번호),
  `tranche`(단 j) — `backtest.sizing.SIZED_COLUMNS`. 행 순서 = (ladder, tranche). net_ret·gross_ret 은 분할 명목 대비,
  hold_min = 그 분할의 t_eff − 그 분할 체결 시각.
- **일간 수익 (실현 기준, 슬리브 자산 대비)** — `sizing.equity_returns` (두 엔진 공용 함수):
  ```
  E = 1
  사다리 g (시간순; 사다리는 겹치지 않는다):  E_g = E
      g 의 분할을 (청산 분, 단 번호) 순으로:  c = w · r · (E_g / E);   E ← E · (1 + c)
  일간 수익(d) = Π_{청산일 d 의 행} (1 + c) − 1   (기간 밖 청산은 마지막 날, 기존 규칙)
  ```
  c = 그 분할의 실현 손익(E_g·w·r) / 실현 직전 자산. 현금은 수익 0. weight = 1·거래마다 사다리 하나면 c = r(비트 단위) →
  기존 일간 수익과 같다. 부분 사이징만이면 c = f·r.
  **실현 기준을 쓰는 이유(설계 결정, 사람 확인 요청)**: 기존 모든 전략의 일간 수익·관문이 '청산일 귀속' 규약이고, 이를
  일반화해야 기본값에서 결과·해시·과거 캠페인 비교가 그대로다. 보유 중 평가손실은 일간 수익에 바로 보이지 않으므로(손절이
  최대 손실을 묶지만) **평가 기준(MTM) 일간 수익**을 따로 계산해 꼬리 위험에 보고한다: `sizing.mtm_daily_returns` = 보유
  분할을 매 UTC 일 종가에 청산했다고 본 자산(`CostModel.sell_fill` + 수수료)의 일간 변화(실현 손익은 청산일에) —
  견고성 'tail' 검사·리포트의 MTM 최악 일/주·CVaR·MDD.
  **관문 회계 (PLAN D18, 사용자 승인 2026-11-10)**: camp-000025 부터 등록되는 캠페인은 MTM 일간 수익이 관문 입력이다
  (`gates.yaml evaluation.daily_returns: mtm`, 등록 시점 값을 매니페스트 `daily_returns` 에 고정). 빠른 엔진
  `fast.mtm_daily_matrix` / `daily_returns_matrix(…, accounting="mtm", cost=)` / `to_result|run_fast(…, accounting="mtm")`
  (`BacktestResult.daily_returns` = MTM, `daily_realized` = 실현)는 `sizing.mtm_daily_returns` 와 같은 식(시험: 1e-13).
  평가 = 진입일 ~ 청산 전날 종가, 청산일은 실현; 보유가 없는 날의 자산·기간 총수익은 실현 기준과 같다.
  몬테카를로 MDD 는 포지션 손익과 함께 포지션 중간 최악 평가 저점(`sizing.position_mtm_troughs`)을 재표본한다.
- 독립 포지션 수 `n_positions` = 사다리 수(기본 = 거래 수): G1 표본, G2 귀무 거래 수 맞춤, 몬테카를로 MDD 의 재표본 단위
  (포지션 손익 = Σ_g w·r, `sizing.position_returns`).
- 노출: `exposure_time` = Σ weight × 보유 분 / 구간 분(자본 가중 평균 보유 비중), `max_inventory` = 최대 보유 비중(Σ 보유
  분할 weight), `max_inventory_minutes` = 그 비중에 연속으로 머문 최장 시간 (`sizing.inventory_profile`).

### 빠른 엔진·기준 엔진
- 빠른 엔진: `fast._core_l`(numba) — 사다리 전용 루프(첫 진입 시장가/지정가, 분할 트리거/지정가, 익절 시장가/지정가 모두),
  `simulate*(…, sizing=, ladder=)`(생략 = `Prepared.sizing/ladder`, `prepare_spec` 이 스펙에서 채움), `TradeArrays.weight/
  ladder/tranche`(None = 기본), `TradeArrays.n_positions`. 출력은 (ladder, tranche) 로 정렬.
- 기준 엔진: `ReferenceEngine(…, sizing=, ladder=)`; 열린 사다리 `eng.lad`(LadderState: held/pending/stop/expiry/avg_tp),
  `eng.in_position`. 분할 체결 FillEvent(reason "entry"(j=0)/"scale_in", tranche, weight), 분할 청산 FillEvent(tranche,
  trade=거래 행). 연산 순서는 빠른 엔진과 같다(비트 단위).
- 페이퍼(`paper/trader.py`): 같은 엔진 API — 여러 분할 주문이 동시에 걸리고(`lad.pending`), 지정가 분할·익절은 `fill_oracle`
  (대기열 재현)로도 판정된다(오라클은 주문 id 별 상태). 분할 명목 = 사다리 시작 실현 자산 × w, 실현 자산 += 시작 자산 × w ×
  net_ret, 평가자산 = 실현 + 보유 분할 평가손익. 상태 STATE_VERSION 3. 리스크 강제 청산 = 전량 + 분할 주문 취소.
  class ③ 노출 상한(`risk_prefs.max_exposure_per_sleeve`)을 넘는 스펙은 거부.
- 다자산 슬리브: 사다리·사이징은 슬리브 안의 규칙(슬리브 자산 기준) — 포트폴리오는 기존처럼 슬리브 일간 수익의 평균.
  max_exposure 1.0 = 모든 분할 체결 시 그 자산 슬리브 100% (3자산이면 총자본의 1/3).

### 관문과 크기
관문은 모두 **실제 사이징의** 포트폴리오 일간 수익으로 판정한다(`summarize(result)` — 결과의 daily_returns 가 이미 크기 반영).
- 낙폭 관문(G6 MDD·MC MDD95, §7 한도)은 **크기에 따라 달라지는 것이 의도**다 — entry_fraction 0.5 면 낙폭도 약 절반.
  (MDD 기준은 실제로 운용할 크기에서의 위험이므로.)
- 엣지 관문은 크기에 거의 불변이라 크기를 줄여 통과할 수 없다: G2 = 같은 사이징·같은 사다리 구조의 귀무 대비 백분위,
  G5 = Sharpe 기반 DSR·PBO. G1 = 독립 포지션 수(분할 행으로 표본을 부풀리지 않음). 테스트
  `tests/test_research_ladder.py::test_shrinking_entry_fraction_cannot_trivially_pass_g2_g5`.

### 지연 견고성 (research.robustness)
첫 진입 결정·신호 청산은 d 분 지연(ATR 간격은 지연 진입 행 기준으로 다시 계산), SL/TP/만기 청산은 d 분 뒤 시가로 사후 재가격.
걸려 있는 분할 주문(트리거·지정가)의 체결 시각은 지연하지 않는다(주문은 이미 거래소에 있다; 트리거 체결 지연은 손절과 같이
보수적이지 않을 수 있음 — 한계로 기록).
