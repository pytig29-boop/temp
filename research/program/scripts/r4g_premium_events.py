"""R4 트랙 G 단계 3: 김치 프리미엄 z ≤ −2 독립 사건 수만 센다 (수익 미계산). 개발 구간만.

premium_t = log(코인원 BTC-KRW 종가 / (바이낸스 BTC-USDT 종가 × USD/KRW)), 4시간 봉.
USD/KRW = FRED DEXKOUS 영업일 값을 다음 날 00:00 UTC 부터 쓰도록 하루 밀고(뉴욕 정오 고시 → 미래 정보 방지) 주말·휴일은 직전 값 유지.
z_t = (premium_t − 직전 180봉 평균) / 직전 180봉 표준편차 (현재 봉 제외). 사건 = z ≤ −2, 8시간 안 연속 사건은 첫 번째만.
XRP·ETH 는 해외 달러 1분봉 이력이 저장소에 없어 셀 수 없다(바이낸스는 BTC-USDT 만).
"""
import json

import numpy as np
import pandas as pd

from btc_quant.registry.splits import DataAccess

da = DataAccess()
krw = da.read("dev", "coinone", "BTC-KRW", "1m")["close"]
usd = da.read("dev", "binance", "BTC-USDT", "1m")["close"]
krw = krw.where(krw > 0).resample("4h").last()
usd = usd.where(usd > 0).resample("4h").last()
fx = pd.read_csv("data/external/fx/DEXKOUS.csv", parse_dates=["observation_date"])
fx = fx.set_index("observation_date")["DEXKOUS"].replace(".", np.nan).astype(float).dropna()
fx.index = (fx.index + pd.Timedelta(days=1)).tz_localize("UTC")
fx4 = fx.reindex(krw.index.union(fx.index)).ffill().reindex(krw.index)
prem = np.log(krw / (usd.reindex(krw.index) * fx4)).dropna()
mu = prem.rolling(180, min_periods=180).mean().shift(1)
sd = prem.rolling(180, min_periods=180).std().shift(1)
z = ((prem - mu) / sd).dropna()
hits = z[z <= -2].index
kept, last = [], None
for t in hits:
    if last is None or (t - last) >= pd.Timedelta("8h"):
        kept.append(t)
    last = t
yrs = pd.Series(1, index=pd.DatetimeIndex(kept)).groupby(lambda t: t.year).sum().to_dict()
print(json.dumps({"bars": int(len(prem)), "z_bars": int(len(z)), "raw_hits": int(len(hits)),
                  "independent_events": len(kept), "by_year": {int(k): int(v) for k, v in yrs.items()},
                  "premium_coverage": [str(prem.index[0]), str(prem.index[-1])]}, indent=1))
