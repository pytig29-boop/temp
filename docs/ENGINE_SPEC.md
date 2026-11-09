# 백테스트 실행 의미론 (빠른 엔진·기준 엔진·페이퍼 엔진 공통 계약)

두 엔진은 같은 입력에 대해 **같은 거래 목록**을 내야 한다(차등 테스트로 강제).

## 입력
- `m1`: 1분봉 DataFrame, `quality.prepare_venue_m1(raw, exchange)` 결과 (= 거래소별 `clean_wicks`(코인원 k=0.01, 그 밖 0.03, `quality.wick_params`) → `regularize`(거래량 0 평탄 봉도 filled) → 30분 이상 filled 구간 직후 첫 실제 봉 filled(가격 유지, `gap_warmup_mask`); 연구 `campaign.prepare_m1` 과 페이퍼 `M1History(exchange=…)` 가 같은 함수·같은 값). 연속 1분 index(UTC), 컬럼 open/high/low/close/volume, `filled`(bool).
- `spec`: `StrategySpec`
- `cost`: `CostModel`
- `ctx`: `Context` (보조 데이터)
- `start`, `end`: 진입 허용 구간 [start, end). 열린 포지션은 end 이후 첫 1분봉 시가에 `end` 사유로 청산(데이터가 없으면 마지막 종가).

## 결정 주기 B (`StrategySpec.timeframe`)
- `"5m"`(기본) → B = 5, `"1m"` → B = 1. 아래 규칙은 모두 B로 일반화되어 있고, B = 5일 때 이전 규칙과 완전히 같다.
- 블록은 결정 주기 봉 `bars = to_tf(m1, B)`로 평가된다 (`to_5m = to_tf(·, 5)`).
  봉 index는 UTC epoch 기준 B분 정렬. B = 1이면 1분봉 그대로.
- `bars.complete[s]`: 봉을 이루는 1분봉 B개가 모두 실제(filled 아님). B = 1이면 `complete[s] = ~filled[s]`.
- `max_hold_min`은 B의 양의 배수 (1m: 임의의 양의 정수 분).

## 신호
- 결정 주기 봉 `bars = to_tf(m1, B)`. 봉 시작 s, 정보 확정 시각 s+B분.
- `entry_ok[s] = entry[s] & AND(filters[s]) & bars.complete[s]`
- `exit_sig[s]` = 신호 청산 블록 (없으면 항상 False)

## 상태 기계 (포지션 최대 1개, 롱 온리, 전액)
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
