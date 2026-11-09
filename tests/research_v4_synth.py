"""v4 통합 테스트용 합성 시장 (test_research_integration_*.py 가 import).

- `make_lagged_root`: 업비트가 가격을 먼저 움직이고 대상(코인원)이 정확히 1분 늦게 따라가는 시장.
  코인원 종가(t) = 업비트 종가(t−1) × (1 + 작은 잡음) → 업비트 1분 충격(`upbit_lead_impulse`)이 다음 1분의
  코인원 수익을 예측한다(1분 지연 = 엣지 전부 → 견고성 latency 검사에서 깨져야 정상).
분할(테스트용, 짧게): dev 2023-01-01~2023-09-01, val ~2023-11-01, lockbox ~2023-12-01, forward 이후.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from btc_quant.data.store import write_ohlcv

LAG_SPLITS = {
    "lockbox_id": "lbA", "lockbox_cap": 10, "val_id": "valA",
    "segments": {
        "dev": {"start": "2023-01-01", "end": "2023-09-01"},
        "val": {"start": "2023-09-01", "end": "2023-11-01"},
        "lockbox": {"start": "2023-11-01", "end": "2023-12-01"},
        "forward": {"start": "2023-12-01", "end": None},
    },
}
LAG_START, LAG_END = "2023-01-01", "2023-12-01"


def make_lagged_markets(seed: int = 3, sigma1: float = 0.0007) -> tuple[pd.DataFrame, pd.DataFrame]:
    idx = pd.date_range(LAG_START, LAG_END, freq="1min", tz="UTC", inclusive="left", name="ts")
    n = len(idx)
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, sigma1, n)
    up_c = 50_000_000.0 * np.exp(np.cumsum(r))
    up_o = np.concatenate([[up_c[0]], up_c[:-1]])
    wig = np.abs(rng.normal(0, sigma1 * 0.2, (2, n)))
    up = pd.DataFrame({"open": up_o, "high": np.maximum(up_o, up_c) * (1 + wig[0]),
                       "low": np.minimum(up_o, up_c) * (1 - wig[1]),
                       "close": up_c, "volume": rng.lognormal(1.0, 0.4, n)}, index=idx)
    co_c = np.concatenate([[up_c[0]], up_c[:-1]]) * 1.001 * np.exp(rng.normal(0, 0.00005, n))
    co_o = np.concatenate([[co_c[0]], co_c[:-1]])
    wig = np.abs(rng.normal(0, sigma1 * 0.2, (2, n)))
    co = pd.DataFrame({"open": co_o, "high": np.maximum(co_o, co_c) * (1 + wig[0]),
                       "low": np.minimum(co_o, co_c) * (1 - wig[1]),
                       "close": co_c, "volume": rng.lognormal(0.0, 0.4, n)}, index=idx)
    for df in (up, co):
        df["quote_volume"] = df["volume"] * df["close"]
    gap = (idx >= "2023-03-03 05:00") & (idx < "2023-03-03 05:20")     # 업비트만 점검 공백 (filled 경로)
    return co, up[~gap]


def make_lagged_root(tmp: Path, seed: int = 3) -> Path:
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "splits.yaml").write_text(yaml.safe_dump(LAG_SPLITS))
    co, up = make_lagged_markets(seed)
    write_ohlcv(co, "coinone", "BTC-KRW", "1m", root=tmp / "data" / "raw")
    write_ohlcv(up, "upbit", "BTC-KRW", "1m", root=tmp / "data" / "raw")
    return tmp


HYP = {"text": "Coinone follows Upbit with ~1 minute lag", "rationale": "synthetic lagged market",
       "success_criteria": ">=1 spec passes G0-G6"}


def aux_campaign_dict(name: str = "lag", **over) -> dict:
    d = {
        "name": name, "hypothesis": dict(HYP), "timeframe": "1m",
        "data": {"exchange": "coinone", "symbol": "BTC-KRW", "timeframe": "1m", "aux": ["upbit"]},
        "entry": {"block": "upbit_lead_impulse", "grid": {"k": [1], "thr_bp": [8, 10, 12]}},
        "filters": [[]],
        "exits": {"mode": "atr", "sl_atr": [3.0, 6.0], "tp_atr": [2.0], "max_hold_min": [3], "atr_bars": 14},
        "costs": {"stress": "coinone_base"},
        "budget": 20,
    }
    d.update(over)
    return d


def aux_screen_dict(name: str = "lagscreen", **over) -> dict:
    d = {
        "name": name, "hypothesis": dict(HYP),
        "data": {"exchange": "coinone", "symbol": "BTC-KRW", "timeframe": "1m", "aux": ["upbit"]},
        "bar_minutes": 1,
        "signals": [{"entry": {"block": "upbit_lead_impulse", "grid": {"k": [1], "thr_bp": [8, 10, 12]}}},
                    {"entry": {"block": "donchian_breakout", "params": {"lookback": 60}}}],
        "study": {"horizons": [1, 5, 15], "primary_horizons": [1, 5], "min_gap_min": 30, "n_null": 200,
                  "min_events": 300},
        "budget": 10, "family": "upbit_lead_impulse",
    }
    d.update(over)
    return d
