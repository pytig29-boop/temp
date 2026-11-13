"""R4 트랙 E 벽시계 진입 블록 clock_entry_utc (docs/prompts/opus_r4_plan.md 단계 1.1)."""
import numpy as np
import pandas as pd
import pytest

from btc_quant.strategy.blocks import REGISTRY, Context
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

F = REGISTRY.get("clock_entry_utc").fn


def _bars(freq, periods, start="2024-01-01"):
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": np.linspace(1, 2, periods), "volume": 1.0},
                        index=idx)


def test_1h_2100_once_per_day_bar_end():
    b = _bars("1h", 24 * 10)
    s = F(b, Context(), hour=21, minute=0)
    assert s.sum() == 10
    ends = s[s].index + pd.Timedelta("1h")
    assert all(t.hour == 21 and t.minute == 0 for t in ends)
    assert all(t.hour == 20 for t in s[s].index)          # 봉 시작 20:00 → 종료 21:00


def test_5m_0005_once_per_day():
    b = _bars("5min", 288 * 5)
    s = F(b, Context(), hour=0, minute=5)
    assert s.sum() == 5
    assert all(t.hour == 0 and t.minute == 0 for t in s[s].index)


def test_missing_bars_clock_based():
    b = _bars("1h", 24 * 6)
    b = b.drop(b.index[[3, 4, 5, 30, 31]])                   # 결측 봉 (21시 봉은 남김)
    s = F(b, Context(bar_minutes=60) if "bar_minutes" in Context.__dataclass_fields__ else Context(), hour=21, minute=0)
    assert s.sum() == 6
    assert all(t.hour == 20 for t in s[s].index)


def test_4h_rejected_by_spec_validation():
    ex = ExitSpec(1.0, 1.0, 120)
    StrategySpec("a", BlockRef("clock_entry_utc", {"hour": 21, "minute": 0}), ex, timeframe="1h").validate(REGISTRY)
    with pytest.raises(ValueError, match="meaningless at a 240-minute"):
        StrategySpec("a", BlockRef("clock_entry_utc", {"hour": 21, "minute": 0}), ExitSpec(1.0, 1.0, 480),
                     timeframe="4h").validate(REGISTRY)


def test_future_perturbation_and_truncation_invariant():
    b = _bars("1h", 24 * 8)
    s = F(b, Context(), hour=21, minute=0)
    b2 = b.copy()
    b2.iloc[100:, b2.columns.get_loc("close")] *= 3.0
    s2 = F(b2, Context(), hour=21, minute=0)
    assert s.iloc[:100].equals(s2.iloc[:100])
    s3 = F(b.iloc[:100], Context(), hour=21, minute=0)
    assert s.iloc[:100].equals(s3)
