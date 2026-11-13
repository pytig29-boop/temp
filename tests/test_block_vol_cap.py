"""R4 트랙 F 실험 필터 vol_cap: vol_regime 과 같은 rv·순위, 반대쪽."""
import numpy as np
import pandas as pd

from btc_quant.strategy.blocks import REGISTRY, Context


def test_vol_cap_mirrors_vol_regime():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-01", periods=2000, freq="4h", tz="UTC")
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)) * (1 + (np.arange(len(idx)) // 200) % 3)))
    b = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx)
    cap = REGISTRY.get("vol_cap").fn(b, Context(), window=48, max_pct=0.67)
    reg = REGISTRY.get("vol_regime").fn(b, Context(), window=48, min_pct=0.67)
    both = cap & reg
    # 같은 백분위 rank 에서 rank == 0.67 일 때만 둘 다 True 가능
    assert both.mean() < 0.02
    valid = cap | reg
    assert valid.iloc[400:].mean() > 0.97
    assert 0.3 < cap.iloc[400:].mean() < 0.95
