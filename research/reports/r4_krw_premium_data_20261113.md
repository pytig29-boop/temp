# R4 트랙 G — 김치 프리미엄 데이터 준비 (2026-11-13)

| 질문 | 답 | 근거 |
|---|---|---|
| 원/달러 일별 이력을 받았나 | **예** | FRED DEXKOUS (뉴욕 정오 고시, 영업일), 2020-12-01~2025-10-31, 1,284행(빈 값 54). 수집 `scripts/fetch_usdkrw.sh` → `data/external/fx/DEXKOUS.csv` (+ `SOURCE.txt` 받은 시각). data/ 는 git 제외라 파일은 스크립트로 다시 받는다 |
| 실시간 해외 BTC 달러 가격 | **예 (BTC)** | 이 환경에서 Coinbase `v2/prices/BTC-USD/spot`, Kraken `Ticker?pair=XBTUSD` 응답 확인(1회 호출). 실시간 원/달러 환율 원천은 미확인 — FRED 는 하루 늦은 고시 |
| 독립 사건 ≥ 300 | **아니오** | BTC 만 131건 (2021 28 / 2022 35 / 2023 15 / 2024 29 / 2025 24). XRP·ETH 는 해외 달러 1분봉 이력이 저장소에 없어 셀 수 없음 |

- 방법: `research/program/scripts/r4g_premium_events.py`. 4시간 봉 premium = log(코인원 BTC-KRW / (바이낸스 BTC-USDT × USD/KRW)),
  환율은 하루 밀어 사용(미래 정보 방지)·주말 직전 값. z = 직전 180봉 대비, z ≤ −2, 8시간 군집 제거. **수익은 계산하지 않음.**
- 원장: log_exploration camp-000042 (krw_premium 1건).
- **결론: 검증 불가로 닫는다** (사건 수 < 300, XRP·ETH 해외 이력 없음, 실시간 환율 원천 미확인). 이번 주기 선별 없음.
- 다시 볼 조건: XRP·ETH 해외 달러 이력(예: 바이낸스 XRP/ETH-USDT 1분봉) 확보 + 실시간 환율 원천 확보 → 3자산 합계 사건 수 재계산.
