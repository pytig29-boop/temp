"""합성 점수 블록 (blocks/experimental/composite.py): 손계산 z, 동일 가중 불변성, 인과성(1m~4h), 결측 aux, 비퇴화."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btc_quant.strategy.blocks import REGISTRY, Context
from btc_quant.strategy.blocks.acceptance import acceptance_check, perturb_after, synthetic_market
from btc_quant.strategy.blocks.experimental import composite as CZ
from btc_quant.strategy.spec import BlockRef

BLOCKS = {"composite_z": CZ.PRESETS, "composite_z_upbit": CZ.UPBIT_PRESETS}


@pytest.fixture(scope="module")
def m5():
    return synthetic_market(30, seed=21, bar_minutes=5)


def _ctx(aux, bar=None):
    return Context(aux=dict(aux), bar=bar)


# ------------------------------------------------------------------ 등록·메타데이터
def test_registration_and_low_degrees_of_freedom():
    for name, presets in BLOCKS.items():
        d = REGISTRY.get(name)
        assert d.experimental and d.kind == "entry" and d.live_ready
        assert set(d.grid) == {"preset", "threshold"}                 # 파라미터 2개뿐 (가중치 없음)
        assert d.grid["preset"] == list(presets) and d.grid["threshold"] == CZ.THRESHOLDS
        assert len(d.grid["preset"]) * len(d.grid["threshold"]) <= 12
        assert d.max_tf_min is None                                    # 1m~4h 모두 허용
        assert d.lookback_bars({"preset": d.grid["preset"][0]}) <= 48 + CZ.NORM_WINDOW + 1
    assert REGISTRY.get("composite_z").requires == ()
    assert REGISTRY.get("composite_z_upbit").requires == ("upbit",)
    # aux 요구는 프리셋 성분과 일치: 기본 블록 프리셋은 aux 성분 없음, 업비트 블록 프리셋은 하나 이상
    for p, comps in CZ.PRESETS.items():
        assert all(CZ.COMPONENTS[c][2] == () for c, _ in comps), p
    for p, comps in CZ.UPBIT_PRESETS.items():
        assert any(CZ.COMPONENTS[c][2] == ("upbit",) for c, _ in comps), p
    assert all(s in (-1, 1) for comps in CZ.ALL_PRESETS.values() for _, s in comps)


@pytest.mark.parametrize("name", list(BLOCKS))
def test_acceptance_check_passes(name):
    rep = acceptance_check(name)
    assert rep.passed, str(rep)


# ------------------------------------------------------------------ 손계산
def test_trailing_z_hand_computed():
    x = pd.Series([1.0, 2.0, 3.0, 4.0, 10.0, 4.0, 4.0, 4.0, 4.0, 4.0])
    z = CZ.trailing_z(x, window=4, clip=99)
    # 처음 2개(min_periods = W/2 = 2 개의 *직전* 값 필요) → NaN
    assert z.iloc[:2].isna().all()
    # t=2: 직전 [1,2] → mean 1.5, std 0.7071 → (3-1.5)/0.7071
    assert z.iloc[2] == pytest.approx(1.5 / np.std([1, 2], ddof=1))
    # t=4: 직전 [1,2,3,4] (현재 봉 10 제외)
    assert z.iloc[4] == pytest.approx((10 - 2.5) / np.std([1, 2, 3, 4], ddof=1))
    # t=9: 직전 4개 [4,4,4,4] → std 0 → NaN (신호 없음)
    assert np.isnan(z.iloc[9])
    # 클립
    assert CZ.trailing_z(x, window=4, clip=3.0).iloc[4] == 3.0
    # 비유한 값은 결측으로
    y = pd.Series([1.0, 2.0, np.inf, 3.0, 4.0, 5.0])
    assert np.isnan(CZ.trailing_z(y, window=4).iloc[2])


def test_score_is_equal_weighted_mean_of_signed_component_z(m5):
    bars, aux = m5
    ctx = _ctx(aux)
    for preset, comps in CZ.ALL_PRESETS.items():
        manual = sum(s * CZ.trailing_z(CZ.COMPONENTS[c][0](bars, ctx)) for c, s in comps) / len(comps)
        got = CZ.composite_score(bars, ctx, preset)
        pd.testing.assert_series_equal(got, manual, check_names=False, atol=1e-12, rtol=0)
        assert got.notna().mean() > 0.9


def test_component_features_hand_computed():
    idx = pd.date_range("2026-01-01", periods=60, freq="5min", tz="UTC")
    c = pd.Series(np.linspace(100, 159, 60), index=idx)
    bars = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 2.0, "quote_volume": 2.0 * c})
    up = bars.assign(close=c * 1.01)
    ctx = Context(aux={"upbit": up})
    f = {k: v[0](bars, ctx) for k, v in CZ.COMPONENTS.items()}
    assert f["ret_6"].iloc[10] == pytest.approx(np.log(c.iloc[10] / c.iloc[4]))
    assert f["ret_48"].iloc[:48].isna().all() and f["ret_48"].iloc[48] == pytest.approx(np.log(c.iloc[48] / c.iloc[0]))
    w = c.iloc[11:59]
    assert f["boll_48"].iloc[58] == pytest.approx((c.iloc[58] - w.mean()) / w.std())
    hi, lo = c.iloc[11:59].max() + 1, c.iloc[11:59].min() - 1
    assert f["chanpos_48"].iloc[58] == pytest.approx((c.iloc[58] - lo) / (hi - lo))
    assert f["gap"].iloc[5] == pytest.approx(-np.log(1.01))
    assert f["lag_1"].iloc[5] == pytest.approx(0.0, abs=1e-12)      # 같은 비율로 움직이면 지연 없음
    assert (f["logvol"] == np.log1p(2.0)).all()


# ------------------------------------------------------------------ 동일 가중 불변성
def test_equal_weight_invariance(m5, monkeypatch):
    bars, aux = m5
    ctx = _ctx(aux)
    # (1) 성분 순서를 바꿔도 같은 점수 (가중치가 위치에 묶이지 않음)
    comps = CZ.ALL_PRESETS["reversion"]
    monkeypatch.setitem(CZ.ALL_PRESETS, "rev_perm", tuple(reversed(comps)))
    pd.testing.assert_series_equal(CZ.composite_score(bars, ctx, "reversion"),
                                   CZ.composite_score(bars, ctx, "rev_perm"), atol=1e-12, rtol=0)
    # (2) 각 성분의 원시 특징에 양의 아핀 변환 → z 불변 → 점수 불변 (척도가 다른 성분도 같은 몫)
    x = CZ.COMPONENTS["logvol"][0](bars, ctx)
    za, zb = CZ.trailing_z(x), CZ.trailing_z(1000.0 * x - 7.0)
    np.testing.assert_allclose(za.to_numpy(), zb.to_numpy(), atol=1e-9, equal_nan=True)
    # (3) 가격 단위(통화)를 바꿔도 점수 불변: 대상·업비트 가격 × 상수 (수익·볼린저·채널 위치·가격차 z 불변)
    px = ["open", "high", "low", "close"]
    b2 = bars.copy()
    b2[px] *= 1e-3
    a2 = {k: v.copy() for k, v in aux.items()}
    a2["upbit"][px] *= 7.0
    for preset in CZ.ALL_PRESETS:
        if preset == "rev_perm":
            continue
        np.testing.assert_allclose(CZ.composite_score(bars, ctx, preset).to_numpy(),
                                   CZ.composite_score(b2, _ctx(a2), preset).to_numpy(), atol=1e-8, equal_nan=True)
    # (4) 점수 × |P| = 부호 붙인 z 의 합: 성분마다 기여 계수가 정확히 1/|P|
    for preset, comps in CZ.PRESETS.items():
        zs = [s * CZ.component_z(bars, ctx, c) for c, s in comps]
        np.testing.assert_allclose((CZ.composite_score(bars, ctx, preset) * len(comps)).to_numpy(),
                                   sum(zs).to_numpy(), atol=1e-12, equal_nan=True)


def test_one_extreme_component_cannot_dominate(m5):
    bars, aux = m5
    b = bars.copy()
    t = b.index[len(b) // 2]
    b.loc[t, "volume"] *= 1e6                                         # 거래량 z 가 수십이 될 만한 급증
    z = CZ.component_z(b, Context(), "logvol")
    assert z.loc[t] == CZ.Z_CLIP
    sc = CZ.composite_score(b, Context(), "volume_momentum")
    assert sc.loc[t] <= (CZ.Z_CLIP + CZ.Z_CLIP) / 2 + 1e-12
    # 수익 성분이 음(−)이면 거래량 급증만으로는 문턱 2 를 넘지 못한다
    r = CZ.component_z(b, Context(), "ret_12").loc[t]
    assert sc.loc[t] == pytest.approx((r + CZ.Z_CLIP) / 2)


# ------------------------------------------------------------------ 인과성·결정 주기
def _resampled(minutes: int, days: int, seed: int):
    return synthetic_market(days, seed=seed, bar_minutes=minutes)


@pytest.mark.parametrize("minutes,days", [(1, 4), (5, 20), (15, 40), (60, 80), (240, 200)])
def test_causal_and_non_degenerate_all_presets_all_timeframes(minutes, days):
    bars, aux = _resampled(minutes, days, seed=minutes)
    ctx = _ctx(aux, pd.Timedelta(minutes=minutes))
    n = len(bars)
    rng = np.random.default_rng(minutes)
    for name, presets in BLOCKS.items():
        for preset in presets:
            ref = BlockRef(name, {"preset": preset, "threshold": 1.0})
            full = REGISTRY.evaluate(ref, bars, ctx)
            assert full.dtype == bool and full.index.equals(bars.index) and not full.isna().any()
            assert 0 < full.mean() < 0.6, (name, preset, minutes, full.mean())
            # 워밍업: 표준화 창 절반 + 특징 창 이전에는 신호 없음
            assert not full.iloc[: CZ.NORM_WINDOW // 2].any()
            # 미래 변조 (대상 + aux)
            cut = int(rng.integers(n // 2, n - 5))
            t = bars.index[cut]
            frames = perturb_after([bars, aux["binance"], aux["upbit"]], t, rng)
            out = REGISTRY.evaluate(ref, frames[0], _ctx({"binance": frames[1], "upbit": frames[2]}, ctx.bar))
            assert out.loc[:t].equals(full.loc[:t]), (name, preset, minutes)
            # 절단
            part = REGISTRY.evaluate(ref, bars.iloc[:cut], _ctx({k: v.iloc[:cut] for k, v in aux.items()}, ctx.bar))
            assert part.equals(full.iloc[:cut]), (name, preset, minutes)


def test_trailing_window_reproduces_last_value(m5):
    bars, aux = m5
    ctx = _ctx(aux)
    for name, presets in BLOCKS.items():
        d = REGISTRY.get(name)
        for preset in presets:
            lb = d.lookback_bars({"preset": preset})
            sc_full = CZ.composite_score(bars, ctx, preset)
            for e in (len(bars) - 1, len(bars) // 2 + 3):
                s = slice(e - lb + 1, e + 1)
                part = CZ.composite_score(bars.iloc[s], _ctx({k: v.iloc[s] for k, v in aux.items()}), preset)
                assert part.iloc[-1] == pytest.approx(sc_full.iloc[e], abs=1e-9), (preset, e)


# ------------------------------------------------------------------ 결측·오류
def test_missing_or_gappy_upbit(m5):
    bars, aux = m5
    with pytest.raises(KeyError, match="upbit"):
        REGISTRY.evaluate(BlockRef("composite_z_upbit", {"preset": "lead"}), bars, Context())
    REGISTRY.evaluate(BlockRef("composite_z", {"preset": "momentum"}), bars, Context())   # aux 불필요
    up = aux["upbit"].copy()
    hole = bars.index[len(bars) // 2: len(bars) // 2 + 10]
    up = up.drop(hole)                                                # 업비트 결측 봉
    ctx = Context(aux={"upbit": up})
    for preset in CZ.UPBIT_PRESETS:
        sc = CZ.composite_score(bars, ctx, preset)
        assert sc.loc[hole].isna().all()
        out = REGISTRY.evaluate(BlockRef("composite_z_upbit", {"preset": preset, "threshold": 0.5}), bars, ctx)
        assert not out.loc[hole].any() and out.any()


def test_flat_volume_disables_volume_presets(m5):
    bars, _ = m5
    b = bars.assign(volume=0.0)                                       # 체결 없는 거래소: 거래량 z 정의 불가
    for preset in ("reversion", "volume_momentum"):
        assert not CZ.composite_score(b, Context(), preset).notna().any()
        assert not REGISTRY.evaluate(BlockRef("composite_z", {"preset": preset, "threshold": 0.5}), b).any()
    assert REGISTRY.evaluate(BlockRef("composite_z", {"preset": "momentum", "threshold": 0.5}), b).any()


def test_unknown_or_wrong_block_preset_rejected(m5):
    bars, aux = m5
    with pytest.raises(ValueError, match="composite_z_upbit"):
        REGISTRY.get("composite_z").fn(bars, Context(aux=aux), preset="lead")
    with pytest.raises(ValueError, match="not in"):
        REGISTRY.get("composite_z_upbit").fn(bars, Context(aux=aux), preset="momentum")
    with pytest.raises(ValueError, match="unknown preset"):
        CZ.composite_score(bars, Context(), "nope")


def test_threshold_monotone(m5):
    bars, aux = m5
    ctx = _ctx(aux)
    for name, presets in BLOCKS.items():
        for preset in presets:
            rates = [REGISTRY.evaluate(BlockRef(name, {"preset": preset, "threshold": t}), bars, ctx).mean()
                     for t in CZ.THRESHOLDS]
            assert all(a >= b for a, b in zip(rates, rates[1:])) and rates[0] > rates[-1]
