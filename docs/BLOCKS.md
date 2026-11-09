# 블록 라이브러리

`import btc_quant.strategy.blocks` 시 아래 블록이 전역 `REGISTRY`에 등록된다 (공식 모듈 + `blocks/experimental/` 자동 import).
계약: `fn(bars, ctx, **params) -> bool Series` (bars.index 정렬, NaN 없음, 워밍업=False).
True@t = 봉 t가 닫힌 시점(t+봉길이)까지의 정보로 조건 성립. 모든 블록은 미래 변조·절단 테스트(`tests/test_blocks.py`,
5분봉과 1분봉 모두)로 인과성 검증.

**봉 길이 (D10: 5분 또는 1분)**: 격자·룩백은 모두 **봉 개수** 단위다 (1분봉에서 `lookback=12` = 12분).
벽시계 시간에 묶인 블록만 봉 길이를 `bar_delta(bars.index, ctx)` 로 구해 환산한다 — 우선순위 `ctx.bar` > index 인접 간격 최빈값
(앞쪽 1만 개) > 5분. 해당 블록: `session_kst`(판정 시각 = t+봉길이), `trend_regime_1h`(마지막 완료 시간봉), `vol_regime`(7일 창 =
5분 2016봉 / 1분 10080봉, 최소 1일), `range_compression_break`(1일 백분위 창 = 288 / 1440봉). 5분봉에서는 기존과 동일.

표기: `ema(x,n)` = `ewm(span=n, adjust=False)` (인과 재귀), `r_k` = k봉 로그수익률, bp = 1e-4.
기본값은 함수 시그니처 값, 격자 "중앙값" = `grid[len//2]`.

상태: **official** = 사람이 검토·병합, **experimental** = AI 작성·자동 수용 검사 통과 (아래 "실험 블록" 절, PLAN D11).

| 블록 | 종류 | 수식 | 파라미터 (기본) | 격자 | live_ready | 상태 |
|---|---|---|---|---|---|---|
| `ema_slope` | entry · 모멘텀 | `(ema_t/ema_{t-1}-1)·1e4 ≥ min_slope_bp`, ema=ema(close,span) | span=24, min_slope_bp=2 | span [12,24,48,96]; min_slope_bp [1,2,3,5] | ✓ | official |
| `roc_threshold` | entry · 모멘텀 | `(close_t/close_{t-L}-1)·1e4 ≥ thr_bp` | lookback=12, thr_bp=60 | lookback [3,6,12,24]; thr_bp [20,40,60,100] | ✓ | official |
| `ema_cross_up` | entry · 모멘텀 | `d=ema(fast)-ema(slow)`, `d_{t-1}≤0 ∧ d_t>0` | fast=8, slow=48 | fast [5,8,12]; slow [26,48,96] | ✓ | official |
| `donchian_breakout` | entry · 돌파 | `close_t > max(high_{t-L..t-1})` | lookback=48 | [24,48,96,144,288] | ✓ | official |
| `atr_channel_breakout` | entry · 돌파 | `close_t > ema_{t-1}(close,n) + mult·ATR_{t-1}`, ATR=ema(TR,n) | span=24, mult=3 | span [12,24,48]; mult [1.5,2,3,4,5] | ✓ | official |
| `range_compression_break` | entry · 돌파 | `w=(maxH_L-minL_L)/close`; `pctrank_1일(w)_{t-1} ≤ squeeze_pct ∧ close_t > maxH_L,t-1` | lookback=24, squeeze_pct=0.2 | lookback [12,24,48]; squeeze_pct [0.1,0.2,0.3,0.4] | ✓ | official |
| `bollinger_z_revert` | entry · 평균회귀 | `z=(close-SMA_W)/STD_W ≤ -z` (현재 봉 포함) | window=48, z=2.5 | window [24,48,96]; z [1.5,2,2.5,3] | ✓ | official |
| `rsi_oversold_bounce` | entry · 평균회귀 | Wilder RSI: `RSI_{t-1}<level ∧ RSI_t≥level` (워밍업 3·period) | period=14, level=30 | period [7,14,21]; level [20,25,30,35] | ✓ | official |
| `binance_lead_impulse` | entry · 교차시장 | `rb=r_k(binance)·1e4 ≥ thr_bp ∧ rb - r_k(local)·1e4 ≥ thr_bp/2` | k=2, thr_bp=20 | k [1,2,3]; thr_bp [10,20,30,50] | ✗ (requires `binance`) | official |
| `random_entry` | entry · 귀무 | `splitmix64(ts_ns ^ splitmix64(seed)) / 2^64 < p` — 봉 시각만으로 결정 | p=0.02, seed=0 | p [0.005,0.01,0.02,0.05,0.1]; seed [0..4] | ✓ | official |
| `session_kst` | filter | 봉 종료 시각(t+봉길이)의 KST 시 ∈ SESSIONS[session] | session="active" | us_open [22,23,0]; night [22,23,0,1]; asia_open [9,10]; active [22,23,0,9]; all_but_quiet (3–8시 제외) | ✓ | official |
| `vol_regime` | filter | `rv=std(r_1, window)`; rv의 직전 7일(5분 2016봉/1분 10080봉, 최소 1일) 롤링 백분위 ≥ min_pct | window=24, min_pct=0.5 | window [12,24,48]; min_pct [0.2,0.33,0.5,0.67,0.8] | ✓ | official |
| `volume_z` | filter | `x=log1p(vol)`; `(x_t - mean(x_{t-W..t-1}))/std(…) ≥ min_z` | window=96, min_z=1 | window [48,96,288]; min_z [0,0.5,1,1.5,2] | ✓ | official |
| `trend_regime_1h` | filter | 1시간봉 종가 H > ema(H, span) — 판정 시각 t+봉길이 기준 **마지막 완료된** 시간봉 `floor(t+봉길이,h)-1h` 사용 | span=24 (시간) | [12,24,48,168] | ✓ | official |
| `binance_not_dumping` | filter | `r_k(binance)·1e4 > -max_drop_bp` | k=3, max_drop_bp=40 | k [1,3,6]; max_drop_bp [20,40,60,100] | ✗ (requires `binance`) | official |
| `ema_cross_down` | exit_signal | `d=ema(fast)-ema(slow)`, `d_{t-1}≥0 ∧ d_t<0` | fast=8, slow=48 | fast [5,8,12]; slow [26,48,96] | ✓ | official |
| `upbit_lead_impulse` | entry · 교차거래소 | `ru=r_k(upbit)·1e4 ≥ thr_bp ∧ ru - r_k(target)·1e4 ≥ thr_bp/2` | k=1, thr_bp=10 | k [1,2,3]; thr_bp [5,10,20,40] | ✓ (requires `upbit`) | experimental |
| `venue_gap_revert` | entry · 교차거래소 | `g=log close - log upbit_close`; `z=(g_t-mean(g_{t-W..t-1}))/std(…)` (최소 W/2 개) `≤ -z` | window=240, z=2.5 | window [60,120,240,480]; z [1.5,2,2.5,3] | ✓ (requires `upbit`) | experimental |
| `upbit_not_dumping` | filter | `r_k(upbit)·1e4 > -max_drop_bp` | k=3, max_drop_bp=40 | k [1,3,6]; max_drop_bp [20,40,60,100] | ✓ (requires `upbit`) | experimental |
| `venue_gap_closed` | exit_signal | `venue_gap_revert` 와 같은 z `≥ exit_z` | window=240, exit_z=0 | window [60,120,240,480]; exit_z [-0.5,0,0.5,1] | ✓ (requires `upbit`) | experimental |

## 참고
- 진입 비율(격자 중앙값, 업비트 2026-08~11 5분봉 약 2.9만 개): 대부분 1–3%, `atr_channel_breakout` ~5%(상태형: 채널 위에 있는 동안 계속 True).
  필터: session(asia_open) 8%, vol_regime 50%, volume_z 17%, trend_regime_1h 45%.
- 상태형(조건이 지속되는 동안 True) 블록: `ema_slope`, `roc_threshold`, `atr_channel_breakout`, `bollinger_z_revert`, 모든 필터.
  사건형(교차 1봉만 True): `ema_cross_up/down`, `rsi_oversold_bounce`, `donchian_breakout`(대체로 짧은 연속).
- 바이낸스 aux는 `ctx.aux["binance"]`(5분봉 OHLCV)를 `bars.index`로 reindex해서 사용 — 누락 시각은 False.
  가격 단위(USDT vs KRW)가 달라 수익률로만 비교한다.
- 인덱스에 빈 봉(결측)이 있으면 롤링/shift는 "봉 개수" 기준이다(시간 기준 아님).
- 교차거래소(업비트) aux는 `ctx.aux["upbit"]` = 업비트 KRW-BTC OHLCV, **대상 거래소 bars 와 같은 봉 길이·index**
  (campaign/paper 가 정렬해서 넣는다). 업비트 WebSocket 은 실시간 수신 가능 → live_ready=True. 바이낸스 블록은 그대로 live_ready=False.

## 교차거래소 블록 메커니즘 (PLAN §10b-6, 검증 전 가설)
- 근거: Phase 0 에서 바이낸스가 업비트를 약 1분 선행(상관 0.16). 코인원(점유율 ~2%)은 업비트(국내 최대 KRW 유동성)를
  뒤따를 것으로 예상 → 1분 결정(D10)에서 업비트의 직전 움직임/가격차가 코인원의 다음 봉 수익을 설명할 수 있다.
- `upbit_lead_impulse`: 업비트가 k봉 동안 thr_bp 이상 올랐는데 대상은 그 절반 이상 덜 올랐다 → 따라잡기 기대.
- `venue_gap_revert` / `venue_gap_closed`: 로그 가격차의 **직전 창 기준** z (프리미엄 표류 제거). 대상이 이례적으로
  싸면 진입, 가격차가 평균 근처(exit_z)로 돌아오면 청산. 현물 롱 전용이라 "대상이 비쌀 때"는 쓰지 않는다.
- `upbit_not_dumping`: 업비트 급락 중 롱 보류 (`binance_not_dumping` 의 실시간 가능 대체).
- 주의: 종가 = 마지막 체결가. 체결이 드문 코인원 종가는 stale 할 수 있어, 가격차 신호 일부는 "실제로 그 가격에 살 수 없는"
  가짜일 수 있다 → 페이퍼/호가 데이터로 체결 가능성 확인 필요. 거래소 간 시계(타임스탬프) 오차도 1분봉에서는 영향이 크다.

## 실험 블록 (PLAN D11, "B안")
| 단계 | 누가 | 내용 |
|---|---|---|
| 작성 | AI 가능 | `src/btc_quant/strategy/blocks/experimental/<module>.py` 에 `REGISTRY.register_experimental(kind, grid, description, author=, created="YYYY-MM-DD", rationale=, requires=, live_ready=, lookback_bars=)` 로 등록. 패키지가 자동 import (`_` 로 시작하는 모듈 제외). 이 문서 표에 상태 `experimental` 인 행 추가. |
| 자동 수용 | 기계 | `acceptance_check(name)` (`blocks/acceptance.py`) 통과. `tests/test_blocks_experimental.py` 가 모든 실험 블록에 대해 강제 → 통과하면 사람 없이 캠페인에 사용 가능. |
| 사용 | 캠페인 | `Registry.check` 기본 허용. 잠금처럼 공식만 허용할 곳은 `check(ref, kind, allow_experimental=False)`. `REGISTRY.all()` 은 기본 공식만 반환(`include_experimental=True` 로 포함). |
| 잠금 전 | 사람 | `REGISTRY.spec_uses_experimental(spec)` 가 비어 있지 않은 후보는 잠금 요청 전 사람 검토 필수. |
| 승격 | 사람 (코드 변경) | 함수를 공식 모듈로 옮기고 `register_experimental` → `register` 로 바꾸며 이 표의 상태를 `official` 로 변경. 런타임 승격 없음: `REGISTRY.promote(name)` 은 항상 `PromotionError`(절차 안내 포함)를 던진다. 의미 변경 = 새 이름. |

`acceptance_check` 검사 항목 (합성 시장: 대상 + 바이낸스 + 업비트, 대상이 업비트를 정확히 1봉 지연; 5분봉 60일, 1분봉 15일):
`metadata`(kind/status/설명, 격자 1–2 파라미터 × 3–5 값, requires ⊆ {binance, upbit}, binance → live_ready=False, 실험 블록 출처),
`signature`(격자 키 = 기본값 있는 파라미터), `output_types`(기본/중앙/처음/끝 + 축별 전체 격자, 5분·1분: bool Series, index 일치, NaN 없음),
`purity`(입력 불변·결정적), `causality_future`(t 이후 대상·aux 변조 → t까지 불변; 5분 3 파라미터×4 지점, 1분 2 지점),
`causality_trunc`(절단 재계산 동일; 5분 3, 1분 2 지점), `front_window`(`lookback_bars` 선언 시 후행 창 마지막 값 = 전체 이력 값),
`non_degenerate`(5분 중앙 격자 신호 비율: filter 1–99%, entry/exit 0.1–60%), `non_degenerate_1m`(처음/중앙/끝 중 하나 이상 0<비율<1),
`docs`(이 표에 행 존재, 실험 블록은 행에 `experimental`). 통과 = 논리·인과성 이상 없음이지 엣지가 있다는 뜻이 아니다.
