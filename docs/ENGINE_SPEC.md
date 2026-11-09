# 백테스트 실행 의미론 (빠른 엔진·기준 엔진·페이퍼 엔진 공통 계약)

두 엔진은 같은 입력에 대해 **같은 거래 목록**을 내야 한다(차등 테스트로 강제).

## 입력
- `m1`: 1분봉 DataFrame, `quality.regularize(quality.clean_wicks(raw))` 결과. 연속 1분 index(UTC), 컬럼 open/high/low/close/volume, `filled`(bool).
- `spec`: `StrategySpec`
- `cost`: `CostModel`
- `ctx`: `Context` (보조 데이터)
- `start`, `end`: 진입 허용 구간 [start, end). 열린 포지션은 end 이후 첫 1분봉 시가에 `end` 사유로 청산(데이터가 없으면 마지막 종가).

## 신호
- 5분봉 `bars = to_5m(m1)`. 봉 시작 s, 정보 확정 시각 s+5분.
- `entry_ok[s] = entry[s] & AND(filters[s]) & bars.complete[s]`
- `exit_sig[s]` = 신호 청산 블록 (없으면 항상 False)

## 상태 기계 (포지션 최대 1개, 롱 온리, 전액)
1분봉 i를 시간순으로 처리. 결정 시점은 5분 경계(분 % 5 == 0)인 1분봉 i의 **시가 직전** = 직전 5분봉 s = t_i − 5분의 종가 확정 시점.

### 진입
- 조건: 무포지션, `entry_ok[t_i − 5m]`, `t_i >= start`, `t_i < end`, 쿨다운 해제.
- 체결: 1분봉 i의 시가 `o_i` → `entry_fill = cost.buy_fill(o_i)`. 진입 기준가 `ref = o_i`.
- 레벨: `sl = ref × (1 − SL%)`, `tp = ref × (1 + TP%)`, 만기 `t_exp = t_i + max_hold_min`.

### 보유 중 1분봉 k (진입 봉 i 포함) — 우선순위 SL > TP > 신호 > 시간
1. **만기**: `t_k >= t_exp` 이면 1분봉 k 시가에 청산 (`time`) — 이 봉에서 다른 검사보다 먼저 (k 시가 시점이므로).
2. **신호 청산**: t_k가 5분 경계이고 `exit_sig[t_k − 5m]` 이고 k > i 이면 k 시가에 청산 (`signal`). 단 1번과 동시면 `time`.
3. **SL 갭**: k > i 이고 `o_k <= sl` → 기준가 `o_k` (`sl`)
4. **SL 터치**: `l_k <= sl` → 기준가 `sl` (`sl`)
5. **TP**: `h_k >= tp + cost.tick` → 기준가 `tp` (`tp`). (갭 상승이어도 `tp`로 보수적 처리)
6. 같은 봉에서 3·4와 5가 동시 → SL.
- 시가 청산(1·2)은 해당 봉의 SL/TP 검사 **전에** 일어난다(봉 k 시가에 이미 나감).
- 청산 체결: `exit_fill = cost.sell_fill(기준가)`.
- 수익: `gross_ret = 기준가/ref − 1`, `net_ret = cost.net_return(entry_fill, exit_fill)`.
- `hold_min` = 청산 봉 시각 − 진입 봉 시각 (분). 봉 내 터치 청산은 해당 봉 시각 + 1분으로 계산.

### 쿨다운
청산 봉 시각 `t_x`(봉 내 청산이면 `t_x + 1분`) 이후 **다음 5분 경계 결정 시점은 건너뛰고** 그 다음부터 진입 가능.
(= 청산 후 최소 1개의 완전한 5분봉을 관찰한 뒤 진입)

### 데이터 공백
`filled` 봉은 평탄 봉(OHLC = 직전 종가)이므로 그대로 처리. 공백 후 첫 실제 봉의 갭은 SL 갭 규칙으로 반영된다.
진입은 `bars.complete[s]`가 True일 때만.

## 출력
- `trades`: `TRADE_COLUMNS` 순서, entry_time 오름차순.
- `daily_returns`: UTC 일자별 (1 + net_ret)의 곱 − 1, [start, end) 모든 날 포함(무거래일 0).

## 비용
`CostModel`: 편도 가격 불리분 = 스프레드/2 + 슬리피지, 수수료는 양방향 비율 차감.
