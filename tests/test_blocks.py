"""블록 라이브러리 테스트 — 합성 데이터로 로직·인과성만 검증 (시장 결과를 의미하지 않음)."""
from __future__ import annotations

import inspect
import zlib

import numpy as np
import pandas as pd
import pytest

from btc_quant.strategy.blocks import REGISTRY, Context
from btc_quant.strategy.features import to_5m
from btc_quant.strategy.spec import BlockRef

ALL = REGISTRY.all()
IDS = [d.name for d in ALL]
OHLCV = ["open", "high", "low", "close", "volume", "quote_volume"]


def _m1_from_logret(r: np.ndarray, idx: pd.DatetimeIndex, p0: float, vol: np.ndarray, rng) -> pd.DataFrame:
    close = p0 * np.exp(np.cumsum(r))
    open_ = np.r_[p0, close[:-1]]
    wick = np.abs(rng.normal(0, 0.0004, (2, len(r))))
    high = np.maximum(open_, close) * (1 + wick[0])
    low = np.minimum(open_, close) * (1 - wick[1])
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": vol, "quote_volume": vol * close}, index=idx)


def synth(days: int = 60, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(로컬 KRW 5분봉, 바이낸스 USDT 5분봉). 바이낸스가 정확히 1개 5분봉(5분) 선행."""
    rng = np.random.default_rng(seed)
    n = days * 1440
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC", name="ts")
    # 변동성 국면(느린 로그-AR) + 시간대 계절성
    regime = np.exp(np.convolve(rng.normal(0, 0.05, n), np.ones(720) / np.sqrt(720), mode="same"))
    hour_kst = (idx.hour + 9) % 24
    season = np.where(np.isin(hour_kst, [22, 23, 0, 9]), 1.5, 1.0)
    sig = 0.0011 * regime * season
    rb = rng.normal(0, 1, n) * sig                                     # 바이낸스 1분 수익률
    rl = 0.7 * np.r_[np.zeros(5), rb[:-5]] + rng.normal(0, 1, n) * sig * 0.7  # 5분 지연 추종
    vb = np.exp(rng.normal(3, 0.5, n)) * season * regime
    vl = np.exp(rng.normal(0, 0.6, n)) * season * regime
    bn = to_5m(_m1_from_logret(rb, idx, 60_000.0, vb, rng))
    lc = to_5m(_m1_from_logret(rl, idx, 90_000_000.0, vl, rng))
    return lc[OHLCV], bn[OHLCV].reindex(lc.index)


@pytest.fixture(scope="module")
def data60():
    return synth(60, seed=1)


@pytest.fixture(scope="module")
def data_small():
    return synth(10, seed=2)


def mid_params(d) -> dict:
    return {k: v[len(v) // 2] for k, v in d.grid.items()}


def run(d, bars, bn, **params) -> pd.Series:
    return d.fn(bars, Context(aux={"binance": bn}), **params)


def test_registry_contents():
    names = set(IDS)
    expected = {"ema_slope", "roc_threshold", "ema_cross_up", "donchian_breakout", "atr_channel_breakout",
                "range_compression_break", "bollinger_z_revert", "rsi_oversold_bounce", "binance_lead_impulse",
                "random_entry", "session_kst", "vol_regime", "volume_z", "trend_regime_1h",
                "binance_not_dumping", "ema_cross_down"}
    assert expected <= names
    for d in ALL:
        assert d.description and 1 <= len(d.grid) <= 2
        assert all(3 <= len(v) <= 5 for v in d.grid.values())
        assert d.live_ready == ("binance" not in d.requires)


@pytest.mark.parametrize("d", ALL, ids=IDS)
def test_output_shape(d, data_small):
    bars, bn = data_small
    out = run(d, bars, bn, **mid_params(d))
    assert isinstance(out, pd.Series) and out.dtype == bool
    assert out.index.equals(bars.index)
    assert not out.isna().any()
    # 기본값으로도 동작
    assert run(d, bars, bn).index.equals(bars.index)


@pytest.mark.parametrize("d", ALL, ids=IDS)
def test_causality_future_noise(d, data60):
    """t 이후 봉(및 바이낸스)을 변조해도 t까지의 출력은 동일해야 한다."""
    bars, bn = data60
    rng = np.random.default_rng(zlib.crc32(d.name.encode()))
    params_list = [mid_params(d), {k: v[0] for k, v in d.grid.items()}, {k: v[-1] for k, v in d.grid.items()}]
    n = len(bars)
    for params in params_list:
        base = run(d, bars, bn, **params)
        for cut in rng.integers(n // 10, n - 10, size=4):
            t = bars.index[cut]
            b2, n2 = bars.copy(), bn.copy()
            after = b2.index > t
            for f in (b2, n2):
                k = int(after.sum())
                mult = np.exp(rng.normal(0, 0.02, (k, 1)) + rng.normal(0, 0.003, (k, 4)))
                px = f.loc[after, ["open", "high", "low", "close"]].to_numpy() * mult
                f.loc[after, ["open", "high", "low", "close"]] = px
                f.loc[after, "high"] = px.max(axis=1)
                f.loc[after, "low"] = px.min(axis=1)
                f.loc[after, ["volume", "quote_volume"]] *= np.exp(rng.normal(0, 1.0, (k, 1)))
            out = run(d, b2, n2, **params)
            pd.testing.assert_series_equal(out.loc[:t], base.loc[:t], check_names=False,
                                           obj=f"{d.name}{params} cut={t}")


@pytest.mark.parametrize("d", ALL, ids=IDS)
def test_causality_truncation(d, data60):
    """잘라낸 데이터로 계산해도 겹치는 구간 결과가 같아야 한다 (미래 봉 존재 여부와 무관)."""
    bars, bn = data60
    params = mid_params(d)
    base = run(d, bars, bn, **params)
    for cut in (len(bars) // 3, len(bars) // 2 + 7, len(bars) - 1):
        out = run(d, bars.iloc[:cut], bn.iloc[:cut], **params)
        pd.testing.assert_series_equal(out, base.iloc[:cut], check_names=False)


@pytest.mark.parametrize("d", ALL, ids=IDS)
def test_non_degenerate(d, data60):
    bars, bn = data60
    rate = run(d, bars, bn, **mid_params(d)).mean()
    if d.kind == "filter":
        assert 0.01 < rate < 0.99, rate
    else:
        assert 0.001 < rate < 0.60, rate


@pytest.mark.parametrize("d", ALL, ids=IDS)
def test_grid_params_accepted(d, data_small):
    bars, bn = data_small
    sig = inspect.signature(d.fn)
    for k in d.grid:
        assert k in sig.parameters
        assert sig.parameters[k].default is not inspect.Parameter.empty
    ref = BlockRef(d.name, mid_params(d))
    REGISTRY.check(ref, d.kind)
    for k, values in d.grid.items():
        for v in values:
            p = {**mid_params(d), k: v}
            REGISTRY.check(BlockRef(d.name, p), d.kind)
            out = REGISTRY.evaluate(BlockRef(d.name, p), bars, Context(aux={"binance": bn}))
            assert out.dtype == bool and len(out) == len(bars)


def test_random_entry_deterministic_and_position_free(data_small):
    bars, _ = data_small
    d = REGISTRY.get("random_entry")
    a = d.fn(bars, Context(), p=0.05, seed=3)
    b = d.fn(bars.iloc[100:], Context(), p=0.05, seed=3)
    pd.testing.assert_series_equal(a.iloc[100:], b)
    c = d.fn(bars, Context(), p=0.05, seed=4)
    assert (a != c).any()
    assert abs(a.mean() - 0.05) < 0.01


def test_trend_regime_uses_completed_hour_only(data_small):
    bars, _ = data_small
    d = REGISTRY.get("trend_regime_1h")
    out = d.fn(bars, Context(), span=12)
    # 같은 '마지막 완료 시간'을 공유하는 봉들(hh:55 ~ 다음 hh:50)은 같은 값
    key = (bars.index + pd.Timedelta("5min")).floor("h")
    assert (out.groupby(key).nunique() == 1).all()


def test_session_kst_hours():
    idx = pd.date_range("2026-01-01 12:00", periods=24 * 12, freq="5min", tz="UTC")  # KST 21:00~
    bars = pd.DataFrame({"close": 1.0}, index=idx)
    out = REGISTRY.get("session_kst").fn(bars, Context(), session="us_open")
    # 봉 12:55 UTC(=21:55 KST) 는 22:00 KST에 닫힘 → us_open
    assert not out.loc["2026-01-01 12:50"] and out.loc["2026-01-01 12:55"]
    assert out.loc["2026-01-01 15:50"] and not out.loc["2026-01-01 15:55"]  # 00:55 KST 시작봉은 01시 결정
