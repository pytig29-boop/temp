"""교차거래소(업비트 aux) 블록 테스트 — 손계산 사례, 프리미엄 표류 불변성, 합성 선행-지연 시장에서의 메커니즘 확인.
합성 데이터는 시장 결과를 의미하지 않는다. 일반 계약·인과성 검사는 tests/test_blocks.py (전 블록 parametrize)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btc_quant.strategy.blocks import REGISTRY, Context
from btc_quant.strategy.blocks.acceptance import synthetic_market
from btc_quant.strategy.blocks.experimental.cross_venue import venue_gap_z
from btc_quant.strategy.spec import BlockRef

CROSS = ["upbit_lead_impulse", "venue_gap_revert", "venue_gap_closed", "upbit_not_dumping"]


def frame(close, idx) -> pd.DataFrame:
    c = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0, "quote_volume": c}, index=idx)


def idx(n: int, freq: str = "1min") -> pd.DatetimeIndex:
    return pd.date_range("2026-03-01", periods=n, freq=freq, tz="UTC", name="ts")


@pytest.fixture(scope="module", params=[1, 5], ids=["1m", "5m"])
def market(request):
    days = 15 if request.param == 1 else 60
    return synthetic_market(days, seed=21, bar_minutes=request.param)


def test_metadata():
    for n in CROSS:
        d = REGISTRY.get(n)
        assert d.requires == ("upbit",) and d.live_ready
        assert d.status == "experimental" and d.author and d.rationale
        assert all(3 <= len(v) <= 5 for v in d.grid.values())
    for n in ("binance_lead_impulse", "binance_not_dumping"):
        d = REGISTRY.get(n)
        assert d.requires == ("binance",) and not d.live_ready and d.status == "official"


def test_requires_upbit_aux():
    i = idx(10)
    bars = frame(np.full(10, 100.0), i)
    with pytest.raises(KeyError):
        REGISTRY.evaluate(BlockRef("venue_gap_revert", {}), bars, Context(aux={"binance": bars}))
    with pytest.raises(KeyError):
        REGISTRY.get("upbit_lead_impulse").fn(bars, Context(), k=1)


def test_upbit_lead_impulse_hand():
    i = idx(6)
    up = frame([100, 100, 100, 100.3, 100.3, 100.3], i)       # +30bp at bar 3
    lag = frame([50, 50, 50, 50, 50.15, 50.15], i)             # target follows one bar later
    same = frame([50, 50, 50, 50.15, 50.15, 50.15], i)         # target moved together
    f = REGISTRY.get("upbit_lead_impulse").fn
    out = f(lag, Context(aux={"upbit": up}), k=1, thr_bp=20)
    assert out.tolist() == [False, False, False, True, False, False]
    assert not f(same, Context(aux={"upbit": up}), k=1, thr_bp=20).any()
    out2 = f(lag, Context(aux={"upbit": up}), k=2, thr_bp=20)
    assert out2.tolist() == [False, False, False, True, False, False]   # bar 4: ru=30, rl=30 → no
    # 업비트 봉 결측 → False (NaN 전파 금지)
    out3 = f(lag, Context(aux={"upbit": up.drop(i[3])}), k=1, thr_bp=20)
    assert not out3.any() and out3.dtype == bool


def test_upbit_not_dumping_hand():
    i = idx(5)
    up = frame([100, 100, 99.5, 99.5, 99.5], i)                 # -50bp at bar 2
    out = REGISTRY.get("upbit_not_dumping").fn(frame(np.ones(5), i), Context(aux={"upbit": up}),
                                               k=1, max_drop_bp=40)
    assert out.tolist() == [False, True, False, True, True]    # bar 0: warm-up (no return) → False


def test_venue_gap_hand_and_premium_invariance():
    rng = np.random.default_rng(0)
    n = 400
    i = idx(n)
    up_c = 90e6 * np.exp(np.cumsum(rng.normal(0, 5e-4, n)))
    noise = rng.normal(0, 2e-4, n)
    tg_c = up_c * np.exp(noise)
    tg_c[300] *= np.exp(-30e-4)                                   # 30bp 이례적 할인
    up, tg = frame(up_c, i), frame(tg_c, i)
    ctx = Context(aux={"upbit": up})
    rev = REGISTRY.get("venue_gap_revert").fn(tg, ctx, window=120, z=3.0)
    assert rev.iloc[300] and rev.iloc[:300].sum() <= 3
    closed = REGISTRY.get("venue_gap_closed").fn(tg, ctx, window=120, exit_z=0.0)
    assert not closed.iloc[300]
    assert not (rev & closed).any()
    # 수동 계산과 일치
    g = np.log(tg_c) - np.log(up_c)
    w = g[300 - 120:300]
    z_hand = (g[300] - w.mean()) / w.std(ddof=1)
    assert venue_gap_z(tg, ctx, 120).iloc[300] == pytest.approx(z_hand)
    # 상수 프리미엄(가격 단위·수준 차) 불변 + 느린 표류는 직전 창 de-meaning 으로 대부분 제거
    z0 = venue_gap_z(tg, ctx, 120)
    z1 = venue_gap_z(frame(tg_c * 1.013, i), ctx, 120)
    pd.testing.assert_series_equal(z0, z1, atol=1e-6)
    # 워밍업: 처음 window/2 봉은 z 없음 → False
    assert not rev.iloc[:60].any()


def test_gap_signal_ignores_drifting_premium():
    """프리미엄이 크게 표류(+200bp, 선형)해도 de-meaning 덕분에 신호가 표류 방향으로 쏠리지 않는다."""
    rng = np.random.default_rng(1)
    n = 3000
    i = idx(n)
    up_c = 90e6 * np.exp(np.cumsum(rng.normal(0, 5e-4, n)))
    drift = np.linspace(0, -200e-4, n)                           # 대상이 점점 싸짐 (레벨 기준이면 항상 신호)
    tg_c = up_c * np.exp(drift + rng.normal(0, 3e-4, n))
    rev = REGISTRY.get("venue_gap_revert").fn(frame(tg_c, i), Context(aux={"upbit": frame(up_c, i)}),
                                             window=240, z=2.5)
    assert rev.mean() < 0.03


def _fwd(bars: pd.DataFrame, h: int = 1) -> pd.Series:
    """신호 봉 t 종가 → t+h 종가 로그수익률 (테스트 평가용 — 블록 내부에서는 금지)."""
    return np.log(bars["close"].shift(-h) / bars["close"])


@pytest.mark.parametrize("name,params", [("upbit_lead_impulse", {"k": 1, "thr_bp": 10}),
                                         ("venue_gap_revert", {"window": 240, "z": 2.0})])
def test_mechanism_on_lagged_synthetic(market, name, params):
    """대상이 업비트를 1봉 지연 추종하는 합성 시장에서는 신호 후 대상 다음 봉 수익이 양(+)이어야 한다."""
    bars, aux = market
    sig = REGISTRY.evaluate(BlockRef(name, params), bars, Context(aux=aux))
    fwd = _fwd(bars)
    assert sig.sum() > 50
    after = fwd[sig].mean()
    assert after > 0 and after > fwd.mean() + 2 * fwd.std() / np.sqrt(sig.sum())


def test_not_dumping_filters_out_downmoves(market):
    bars, aux = market
    ok = REGISTRY.evaluate(BlockRef("upbit_not_dumping", {"k": 1, "max_drop_bp": 20}), bars, Context(aux=aux))
    fwd = _fwd(bars)
    assert fwd[~ok].mean() < fwd[ok].mean()
