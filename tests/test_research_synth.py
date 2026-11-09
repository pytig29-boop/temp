"""연구 계층 테스트용 합성 시장 + 임시 저장소 (다른 test_research_* 파일이 import 한다).

- planted: donchian_breakout(lookback=48) 돌파 직후 12개 5분봉(1시간) 동안 양의 드리프트가 실제로 존재.
- random walk: 드리프트 없음.
분할(테스트용): dev 2023-01-01~2024-01-01, val ~2024-03-01, lockbox ~2024-04-01, forward 이후.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from numba import njit

from btc_quant.data.store import write_ohlcv

SPLITS = {
    "lockbox_id": "lbT", "lockbox_cap": 10,
    "segments": {
        "dev": {"start": "2023-01-01", "end": "2024-01-01"},
        "val": {"start": "2024-01-01", "end": "2024-03-01"},
        "lockbox": {"start": "2024-03-01", "end": "2024-04-01"},
        "forward": {"start": "2024-04-01", "end": None},
    },
}
DATA_START, DATA_END = "2023-01-01", "2024-04-01"


@njit(cache=True)
def _gen(n5, sigma5, mu, lookback, hold, seed):
    np.random.seed(seed)
    n1 = n5 * 5
    o = np.empty(n1)
    h = np.empty(n1)
    lo = np.empty(n1)
    c = np.empty(n1)
    hi5 = np.empty(n5)
    cl5 = np.empty(n5)
    price = 50_000_000.0
    s1 = sigma5 / np.sqrt(5.0)
    drift_left = 0
    for t in range(n5):
        d = mu / 5.0 if drift_left > 0 else 0.0
        if drift_left > 0:
            drift_left -= 1
        bh = -1.0
        for j in range(5):
            k = t * 5 + j
            op = price
            price = price * np.exp(d + s1 * np.random.randn())
            o[k] = op
            c[k] = price
            h[k] = max(op, price) * (1.0 + abs(np.random.randn()) * s1 * 0.3)
            lo[k] = min(op, price) * (1.0 - abs(np.random.randn()) * s1 * 0.3)
            if h[k] > bh:
                bh = h[k]
        hi5[t] = bh
        cl5[t] = price
        if mu != 0.0 and t >= lookback:
            m = -1.0
            for q in range(t - lookback, t):
                if hi5[q] > m:
                    m = hi5[q]
            if cl5[t] > m and drift_left == 0:   # 돌파 확정(봉 t 종가) → 다음 hold 봉 동안 드리프트
                drift_left = hold
    return o, h, lo, c


def make_market(planted: bool, seed: int = 7, mu: float = 0.00015, sigma5: float = 0.0012) -> pd.DataFrame:
    idx = pd.date_range(DATA_START, DATA_END, freq="1min", tz="UTC", inclusive="left", name="ts")
    n5 = len(idx) // 5
    o, h, lo, c = _gen(n5, sigma5, mu if planted else 0.0, 48, 12, seed)
    rng = np.random.default_rng(seed)
    vol = rng.lognormal(0.0, 0.5, len(idx))
    df = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": vol,
                       "quote_volume": vol * c}, index=idx)
    # 약간의 결측(점검 공백) — regularize 의 filled 경로도 지나가게
    drop = (idx >= "2023-06-10 03:00") & (idx < "2023-06-10 03:40")
    return df[~drop]


def make_root(tmp: Path, planted: bool, seed: int = 7) -> Path:
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "splits.yaml").write_text(yaml.safe_dump(SPLITS))
    write_ohlcv(make_market(planted, seed), "coinone", "BTC-KRW", "1m", root=tmp / "data" / "raw")
    return tmp


def template_dict(name: str = "synth", budget: int = 1000, **over) -> dict:
    d = {
        "name": name,
        "hypothesis": {"text": "Donchian breakouts continue for ~1h on BTC-KRW",
                       "rationale": "synthetic test market", "success_criteria": ">=1 spec passes G0-G6"},
        "data": {"exchange": "coinone", "symbol": "BTC-KRW", "timeframe": "1m", "aux": []},
        "entry": {"block": "donchian_breakout", "grid": {"lookback": [24, 48, 96]}},
        "filters": [[], [{"block": "session_kst", "grid": {"session": ["all_but_quiet"]}}]],
        "exits": {"sl_pct": [0.6, 1.0], "tp_pct": [0.8, 1.2], "max_hold_min": [60]},
        "budget": budget,
    }
    d.update(over)
    return d


def test_planted_market_shape(tmp_path):
    df = make_market(True, seed=1)
    assert df.index.is_monotonic_increasing and (df["high"] >= df[["open", "close"]].max(axis=1)).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()
