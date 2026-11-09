# 블록 라이브러리

`import btc_quant.strategy.blocks` 시 아래 블록이 전역 `REGISTRY`에 등록된다.
계약: `fn(bars, ctx, **params) -> bool Series` (bars.index 정렬, NaN 없음, 워밍업=False).
True@t = 봉 t가 닫힌 시점(t+5분)까지의 정보로 조건 성립. 모든 블록은 미래 변조·절단 테스트(`tests/test_blocks.py`)로 인과성 검증.

표기: `ema(x,n)` = `ewm(span=n, adjust=False)` (인과 재귀), `r_k` = k봉 로그수익률, bp = 1e-4.
기본값은 함수 시그니처 값, 격자 "중앙값" = `grid[len//2]`.

| 블록 | 종류 | 수식 | 파라미터 (기본) | 격자 | live_ready |
|---|---|---|---|---|---|
| `ema_slope` | entry · 모멘텀 | `(ema_t/ema_{t-1}-1)·1e4 ≥ min_slope_bp`, ema=ema(close,span) | span=24, min_slope_bp=2 | span [12,24,48,96]; min_slope_bp [1,2,3,5] | ✓ |
| `roc_threshold` | entry · 모멘텀 | `(close_t/close_{t-L}-1)·1e4 ≥ thr_bp` | lookback=12, thr_bp=60 | lookback [3,6,12,24]; thr_bp [20,40,60,100] | ✓ |
| `ema_cross_up` | entry · 모멘텀 | `d=ema(fast)-ema(slow)`, `d_{t-1}≤0 ∧ d_t>0` | fast=8, slow=48 | fast [5,8,12]; slow [26,48,96] | ✓ |
| `donchian_breakout` | entry · 돌파 | `close_t > max(high_{t-L..t-1})` | lookback=48 | [24,48,96,144,288] | ✓ |
| `atr_channel_breakout` | entry · 돌파 | `close_t > ema_{t-1}(close,n) + mult·ATR_{t-1}`, ATR=ema(TR,n) | span=24, mult=3 | span [12,24,48]; mult [1.5,2,3,4,5] | ✓ |
| `range_compression_break` | entry · 돌파 | `w=(maxH_L-minL_L)/close`; `pctrank_288(w)_{t-1} ≤ squeeze_pct ∧ close_t > maxH_L,t-1` | lookback=24, squeeze_pct=0.2 | lookback [12,24,48]; squeeze_pct [0.1,0.2,0.3,0.4] | ✓ |
| `bollinger_z_revert` | entry · 평균회귀 | `z=(close-SMA_W)/STD_W ≤ -z` (현재 봉 포함) | window=48, z=2.5 | window [24,48,96]; z [1.5,2,2.5,3] | ✓ |
| `rsi_oversold_bounce` | entry · 평균회귀 | Wilder RSI: `RSI_{t-1}<level ∧ RSI_t≥level` (워밍업 3·period) | period=14, level=30 | period [7,14,21]; level [20,25,30,35] | ✓ |
| `binance_lead_impulse` | entry · 교차시장 | `rb=r_k(binance)·1e4 ≥ thr_bp ∧ rb - r_k(local)·1e4 ≥ thr_bp/2` | k=2, thr_bp=20 | k [1,2,3]; thr_bp [10,20,30,50] | ✗ (requires `binance`) |
| `random_entry` | entry · 귀무 | `splitmix64(ts_ns ^ splitmix64(seed)) / 2^64 < p` — 봉 시각만으로 결정 | p=0.02, seed=0 | p [0.005,0.01,0.02,0.05,0.1]; seed [0..4] | ✓ |
| `session_kst` | filter | 봉 종료 시각(t+5분)의 KST 시 ∈ SESSIONS[session] | session="active" | us_open [22,23,0]; night [22,23,0,1]; asia_open [9,10]; active [22,23,0,9]; all_but_quiet (3–8시 제외) | ✓ |
| `vol_regime` | filter | `rv=std(r_1, window)`; rv의 직전 2016봉(7일, 최소 288) 롤링 백분위 ≥ min_pct | window=24, min_pct=0.5 | window [12,24,48]; min_pct [0.2,0.33,0.5,0.67,0.8] | ✓ |
| `volume_z` | filter | `x=log1p(vol)`; `(x_t - mean(x_{t-W..t-1}))/std(…) ≥ min_z` | window=96, min_z=1 | window [48,96,288]; min_z [0,0.5,1,1.5,2] | ✓ |
| `trend_regime_1h` | filter | 1시간봉 종가 H > ema(H, span) — 판정 시각 t+5분 기준 **마지막 완료된** 시간봉 `floor(t+5분,h)-1h` 사용 | span=24 (시간) | [12,24,48,168] | ✓ |
| `binance_not_dumping` | filter | `r_k(binance)·1e4 > -max_drop_bp` | k=3, max_drop_bp=40 | k [1,3,6]; max_drop_bp [20,40,60,100] | ✗ (requires `binance`) |
| `ema_cross_down` | exit_signal | `d=ema(fast)-ema(slow)`, `d_{t-1}≥0 ∧ d_t<0` | fast=8, slow=48 | fast [5,8,12]; slow [26,48,96] | ✓ |

## 참고
- 진입 비율(격자 중앙값, 업비트 2026-08~11 5분봉 약 2.9만 개): 대부분 1–3%, `atr_channel_breakout` ~5%(상태형: 채널 위에 있는 동안 계속 True).
  필터: session(asia_open) 8%, vol_regime 50%, volume_z 17%, trend_regime_1h 45%.
- 상태형(조건이 지속되는 동안 True) 블록: `ema_slope`, `roc_threshold`, `atr_channel_breakout`, `bollinger_z_revert`, 모든 필터.
  사건형(교차 1봉만 True): `ema_cross_up/down`, `rsi_oversold_bounce`, `donchian_breakout`(대체로 짧은 연속).
- 바이낸스 aux는 `ctx.aux["binance"]`(5분봉 OHLCV)를 `bars.index`로 reindex해서 사용 — 누락 시각은 False.
  가격 단위(USDT vs KRW)가 달라 수익률로만 비교한다.
- 인덱스에 빈 봉(결측)이 있으면 롤링/shift는 "봉 개수" 기준이다(시간 기준 아님).
