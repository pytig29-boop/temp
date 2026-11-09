"""진입 · 귀무 블록: 무작위 진입 (기준선/우연 비교용)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import REGISTRY, Context

_GOLDEN = np.uint64(0x9E3779B97F4A7C15)


def _splitmix64(x: np.ndarray) -> np.ndarray:
    x = x + _GOLDEN
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def hash_uniform(index: pd.DatetimeIndex, seed: int) -> np.ndarray:
    """(시각, seed) → [0,1) 균등값. 배열 위치와 무관 → 결정적이며 인과적."""
    ns = np.asarray(index.as_unit("ns").asi8, dtype=np.int64).view(np.uint64)
    with np.errstate(over="ignore"):
        x = _splitmix64(ns ^ _splitmix64(np.full(len(ns), seed, dtype=np.uint64)))
    return (x >> np.uint64(11)).astype(np.float64) / float(1 << 53)


@REGISTRY.register(
    "entry",
    grid={"p": [0.005, 0.01, 0.02, 0.05, 0.1], "seed": [0, 1, 2, 3, 4]},
    description="무작위 진입: splitmix64(timestamp_ns ^ h(seed))/2^64 < p. 봉 시각만으로 결정 (귀무 기준선).",
)
def random_entry(bars: pd.DataFrame, ctx: Context, p: float = 0.02, seed: int = 0) -> pd.Series:
    return pd.Series(hash_uniform(bars.index, int(seed)) < p, index=bars.index)
