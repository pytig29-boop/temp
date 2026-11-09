# btc-quant

BTC 현물 단기 매매 연구 시스템. 현재 단계: **Phase 0 — 비용 바닥 분석 + 데이터 수집기**.

## 현재 결정 사항 (2026-11-09)
- 첫 질문: "5분봉 기반 진입이 거래 비용을 이길 만큼 움직임이 있는가?" → `cost-floor` 분석으로 답한다.
- 거래 후보: 국내 KRW 마켓(업비트·빗썸, 수수료 비교 후 결정). 연구 데이터: 업비트 + 바이낸스 공개 데이터.
- 실행 환경: 클라우드 세션에서 개발 → 완성 후 VPS 이전.
- ML은 나중에. 규칙 기반부터 단순하게.

## 설치·실행
```bash
uv sync
uv run pytest -q

# 1) 데이터 수집 (1분봉 1년: 업비트 약 2,600회 요청, 수 분 소요)
uv run btc-quant fetch-candles --exchange upbit --market KRW-BTC --unit 1 --start 2025-11-01
uv run btc-quant fetch-binance --symbol BTCUSDT --start 2025-11 --end 2026-10
uv run btc-quant sample-orderbook --exchange upbit --market KRW-BTC --seconds 600

# 2) 품질 검사 & 비용 바닥 리포트 (research/reports/ 에 생성)
uv run btc-quant quality --exchange upbit --symbol BTC-KRW
uv run btc-quant cost-floor --exchange upbit --symbol BTC-KRW --spread-bps <측정값>

# 3) 바이낸스 → 업비트 선행 여부
uv run btc-quant lead-lag --a upbit:BTC-KRW --b binance:BTC-USDT
```

클라우드 환경에서는 네트워크 허용 도메인에 `api.upbit.com`, `api.bithumb.com`,
`data.binance.vision`, `api.binance.com` 을 추가해야 한다.

## 비용 바닥 분석이 계산하는 것
- 결정은 5분봉 종가, 진입은 다음 봉 시가. 경로는 1분봉 high/low, 같은 봉 TP·SL 동시 도달은 손절.
- 보유 시간별 `P(수익률 > 왕복비용)` — 무작위 진입 기준선.
- 대칭 TP/SL별 **손익분기에 필요한 승률 개선(%p)** — 전략이 넘어야 할 최소 예측력.
- KST 시간대별·변동성 국면별로 비용이 상대적으로 작아지는 구간.
- 수수료 값은 `config/exchanges.yaml` 기준이며 아직 공식 페이지로 검증 전(`verified: false`).

## 구조
```
src/btc_quant/
  data/      upbit.py(업비트·빗썸 v1), binance_vision.py, orderbook.py, quality.py, store.py(Parquet+DuckDB)
  analysis/  cost_floor.py, lead_lag.py
  cli.py
config/exchanges.yaml
research/reports/   분석 리포트 (커밋 대상)
data/               원천 데이터 (git 제외)
```
