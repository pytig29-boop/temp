"""블록 라이브러리 테스트 — 합성 데이터로 로직·인과성만 검증 (시장 결과를 의미하지 않음)."""
from __future__ import annotations

import inspect
import zlib

import numpy as np
import pandas as pd
import pytest

from btc_quant.strategy.blocks import REGISTRY, Context
from btc_quant.strategy.blocks.acceptance import perturb_after, synthetic_market
from btc_quant.strategy.spec import BlockRef

ALL = REGISTRY.all(include_experimental=True)   # 공식 + 실험 블록 모두 같은 계약 검사
IDS = [d.name for d in ALL]


def synth(days: int = 60, seed: int = 0, bar_minutes: int = 5) -> tuple[pd.DataFrame, dict]:
    """(대상 KRW 봉, {"binance", "upbit"}). 업비트는 대상보다 정확히 1봉 선행, 바이낸스는 1봉+1분 선행.
    생성기는 acceptance_check 와 공유 (blocks/acceptance.py)."""
    return synthetic_market(days, seed, bar_minutes)


@pytest.fixture(scope="module")
def data60():
    return synth(60, seed=1)


@pytest.fixture(scope="module")
def data_small():
    return synth(10, seed=2)


def mid_params(d) -> dict:
    return {k: v[len(v) // 2] for k, v in d.grid.items()}


@pytest.fixture(scope="module")
def data_1m():
    return synth(15, seed=3, bar_minutes=1)


def run(d, bars, aux, **params) -> pd.Series:
    return d.fn(bars, Context(aux=aux), **params)


def cut_aux(aux: dict, sl) -> dict:
    return {k: v.iloc[sl] for k, v in aux.items()}


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
        assert set(d.requires) <= {"binance", "upbit"}
        assert d.status in ("official", "experimental")
    # 기존 16개는 공식, 기본 all() 은 공식만
    assert all(REGISTRY.get(n).status == "official" for n in expected)
    assert {d.name for d in REGISTRY.all()} == {d.name for d in ALL if d.status == "official"}


@pytest.mark.parametrize("d", ALL, ids=IDS)
def test_output_shape(d, data_small):
    bars, aux = data_small
    out = run(d, bars, aux, **mid_params(d))
    assert isinstance(out, pd.Series) and out.dtype == bool
    assert out.index.equals(bars.index)
    assert not out.isna().any()
    # 기본값으로도 동작
    assert run(d, bars, aux).index.equals(bars.index)


@pytest.mark.parametrize("d", ALL, ids=IDS)
def test_causality_future_noise(d, data60):
    """t 이후 봉(및 바이낸스)을 변조해도 t까지의 출력은 동일해야 한다."""
    bars, aux = data60
    rng = np.random.default_rng(zlib.crc32(d.name.encode()))
    params_list = [mid_params(d), {k: v[0] for k, v in d.grid.items()}, {k: v[-1] for k, v in d.grid.items()}]
    n = len(bars)
    for params in params_list:
        base = run(d, bars, aux, **params)
        for cut in rng.integers(n // 10, n - 10, size=4):
            t = bars.index[cut]
            keys = list(aux)
            frames = perturb_after([bars, *[aux[x] for x in keys]], t, rng)
            out = run(d, frames[0], dict(zip(keys, frames[1:])), **params)
            pd.testing.assert_series_equal(out.loc[:t], base.loc[:t], check_names=False,
                                           obj=f"{d.name}{params} cut={t}")


@pytest.mark.parametrize("d", ALL, ids=IDS)
def test_causality_truncation(d, data60):
    """잘라낸 데이터로 계산해도 겹치는 구간 결과가 같아야 한다 (미래 봉 존재 여부와 무관)."""
    bars, aux = data60
    params = mid_params(d)
    base = run(d, bars, aux, **params)
    for cut in (len(bars) // 3, len(bars) // 2 + 7, len(bars) - 1):
        out = run(d, bars.iloc[:cut], cut_aux(aux, slice(None, cut)), **params)
        pd.testing.assert_series_equal(out, base.iloc[:cut], check_names=False)


@pytest.mark.parametrize("d", ALL, ids=IDS)
def test_non_degenerate(d, data60):
    bars, aux = data60
    rate = run(d, bars, aux, **mid_params(d)).mean()
    if d.kind == "filter":
        assert 0.01 < rate < 0.99, rate
    else:
        assert 0.001 < rate < 0.60, rate


@pytest.mark.parametrize("d", ALL, ids=IDS)
def test_grid_params_accepted(d, data_small):
    bars, aux = data_small
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
            out = REGISTRY.evaluate(BlockRef(d.name, p), bars, Context(aux=aux))
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


# ------------------------------------------------------------------ 1분봉 (D10)
@pytest.mark.parametrize("d", ALL, ids=IDS)
def test_1m_bars_contract_and_causality(d, data_1m):
    """모든 블록이 1분봉에서도 계약(bool, index, NaN 없음)·인과성(미래 변조, 절단)을 지킨다."""
    bars, aux = data_1m
    assert (bars.index[1] - bars.index[0]) == pd.Timedelta("1min")
    params = mid_params(d)
    base = run(d, bars, aux, **params)
    assert base.dtype == bool and base.index.equals(bars.index) and not base.isna().any()
    rng = np.random.default_rng(zlib.crc32(d.name.encode()) + 1)
    n = len(bars)
    keys = list(aux)
    for cut in (n // 2 + 11, n - 50):
        t = bars.index[cut]
        frames = perturb_after([bars, *[aux[x] for x in keys]], t, rng)
        out = run(d, frames[0], dict(zip(keys, frames[1:])), **params)
        pd.testing.assert_series_equal(out.loc[:t], base.loc[:t], check_names=False)
        tr = run(d, bars.iloc[:cut], cut_aux(aux, slice(None, cut)), **params)
        pd.testing.assert_series_equal(tr, base.iloc[:cut], check_names=False)
    rates = [run(d, bars, aux, **p).mean() for p in
             (params, {k: v[0] for k, v in d.grid.items()}, {k: v[-1] for k, v in d.grid.items()})]
    assert any(0 < r < 1 for r in rates), rates


def test_wall_clock_blocks_scale_with_bar_size(data_1m):
    """1분봉: 세션 판정 시각 = t+1분, 1시간 추세는 마지막 완료 시간봉, 7일 창은 1분봉 수로 환산."""
    from btc_quant.strategy.blocks.momentum import bar_delta, bars_per
    bars, _ = data_1m
    assert bar_delta(bars.index) == pd.Timedelta("1min")
    assert bars_per(bars.index, "1D") == 1440 and bars_per(bars.index, "7D") == 10080
    assert bar_delta(bars.index, Context(bar=pd.Timedelta("5min"))) == pd.Timedelta("5min")
    idx = pd.date_range("2026-01-01 12:50", periods=20, freq="1min", tz="UTC")   # KST 21:50~
    b = pd.DataFrame({"close": 1.0}, index=idx)
    out = REGISTRY.get("session_kst").fn(b, Context(), session="us_open")
    # 봉 12:59 UTC 는 13:00 UTC(=22:00 KST)에 닫힘 → us_open, 12:58 은 아님
    assert not out.loc["2026-01-01 12:58"] and out.loc["2026-01-01 12:59"]
    tr = REGISTRY.get("trend_regime_1h").fn(bars, Context(), span=12)
    key = (bars.index + pd.Timedelta("1min")).floor("h")
    assert (tr.groupby(key).nunique() == 1).all()


def test_bar_delta_robust_to_gaps():
    from btc_quant.strategy.blocks.momentum import bar_delta
    idx = pd.date_range("2026-01-01", periods=500, freq="5min", tz="UTC").delete([10, 11, 50, 200])
    assert bar_delta(idx) == pd.Timedelta("5min")
    assert bar_delta(idx[:1]) == pd.Timedelta("5min")
