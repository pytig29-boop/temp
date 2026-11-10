"""페이퍼 호가 단위 = 거래소 가격대별 정책 (config/ticks.yaml, 코인원 공개 API range_units 캐시, class ①) —
정책표 해석·갱신(버전)·Dev 비교 함수, PaperTrader._tick 의 출처 선택. 백테스트는 데이터 추정 그대로."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btc_quant.data import ticks as TK
from btc_quant.paper.bars import M1History
from btc_quant.strategy.spec import BlockRef, ExecutionSpec, ExitSpec, StrategySpec

from test_paper_trader import make_trader, synth_raw

BANDS = [(0, 0.0001), (1, 0.001), (5, 0.005), (10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1),
         (5000, 5), (10000, 10), (50000, 50), (100000, 100), (500000, 500), (1000000, 1000), (5000000, 5000),
         (10000000, 10000)]


def payload(bands=BANDS):
    rows = [{"range_min": a, "next_range_min": (bands[i + 1][0] if i + 1 < len(bands) else 10 ** 10),
             "price_unit": u} for i, (a, u) in enumerate(bands)]
    return {"result": "success", "error_code": "0", "range_price_units": rows}


def test_repo_policy_matches_coinone_table():
    pol = TK.tick_policy("coinone")
    assert pol is not None and pol.version >= 1 and pol.exchange == "coinone"
    assert list(pol.bands) == [(float(a), float(u)) for a, u in BANDS]
    for px, t in ((152_000_000, 10_000), (9_999_999, 5_000), (4_900_000, 1_000), (1_484, 1), (999.9, 0.5), (500, 0.5),
                  (0.5, 0.0001)):
        assert pol.tick(px) == t, px
    assert np.isnan(pol.tick(0)) and np.isnan(pol.tick(float("nan")))
    np.testing.assert_array_equal(pol.ticks([152e6, 1484, 999.9]), [10_000, 1, 0.5])
    assert TK.tick_policy("upbit") is None and TK.tick_policy(None) is None
    from btc_quant.params import load_registry
    reg = load_registry()
    assert reg.resolve("ticks.coinone.KRW") == pol.version and reg.get("ticks.coinone.KRW").cls == 1


def test_parse_and_refresh_versions(tmp_path):
    assert TK.parse_range_units(payload()) == [(float(a), float(u)) for a, u in BANDS]
    bad = payload()
    bad["range_price_units"][3]["next_range_min"] = 49
    with pytest.raises(TK.TickPolicyError, match="contiguous"):
        TK.parse_range_units(bad)
    with pytest.raises(TK.TickPolicyError):
        TK.parse_range_units({"result": "error"})
    tables = {"BTC": BANDS, "XRP": BANDS, "ETH": BANDS}
    fetch = lambda s, q: TK.parse_range_units(payload(tables[s]))   # noqa: E731
    r1 = TK.refresh_coinone(tmp_path, fetch=fetch)
    assert r1["changed"] and r1["version"] == 1 and TK.tick_policy("coinone", root=tmp_path).version == 1
    assert TK.refresh_coinone(tmp_path, fetch=fetch)["changed"] is False
    new = [*BANDS[:-1], (10000000, 5000)]
    tables.update(BTC=new, XRP=new, ETH=new)
    r3 = TK.refresh_coinone(tmp_path, fetch=fetch)
    pol = TK.tick_policy("coinone", root=tmp_path)
    assert r3["changed"] and pol.version == 2 and pol.tick(2e8) == 5000
    doc = TK.load_ticks(tmp_path)
    assert doc["venues"]["coinone"]["KRW"]["history"][0]["version"] == 1
    tables["XRP"] = BANDS
    with pytest.raises(TK.TickPolicyError, match="differ by symbol"):
        TK.refresh_coinone(tmp_path, fetch=fetch)


def test_compare_inferred_vs_policy_reports_mismatch_runs():
    pol = TK.TickPolicy("coinone", "KRW", 1, tuple((float(a), float(u)) for a, u in BANDS))
    idx = pd.date_range("2024-01-01", periods=4 * 1440, freq="1min", tz="UTC", name="ts")
    rng = np.random.default_rng(0)
    px = 50_000_000 + np.cumsum(rng.normal(0, 3000, len(idx)))
    grid = np.where(idx < pd.Timestamp("2024-01-03", tz="UTC"), 1000.0, 10000.0)   # 앞 2일 1,000원 격자, 뒤 2일 10,000원
    close = np.round(px / grid) * grid
    m1 = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=idx)
    m1["filled"] = False
    out = TK.compare_inferred_vs_policy(m1, pol)
    assert out["n_days"] == 4 and out["n_match"] == 2 and out["n_mismatch"] == 2
    (run,) = out["mismatch_runs"]
    assert (run["start"], run["end"], run["n_days"], run["inferred"], run["policy"]) == (
        "2024-01-01", "2024-01-02", 2, 1000.0, 10000.0)


SPEC = StrategySpec("tk", BlockRef("random_entry", {"p": 0.5, "seed": 1}), ExitSpec(0.3, 0.2, 30),
                    execution=ExecutionSpec("limit", offset_bp=2.0))


@pytest.mark.parametrize("exchange,source,expect", [("coinone", "auto", "policy"), (None, "auto", "infer"),
                                                    ("coinone", "infer", "infer"), ("coinone", "policy", "policy")])
def test_paper_tick_source(tmp_path, exchange, source, expect):
    t0 = pd.Timestamp("2026-11-12 00:00", tz="UTC")
    raw = synth_raw(np.random.default_rng(1), t0 - pd.Timedelta(days=1), 1440, p0=150_000_000.0)
    hist = M1History(max_rows=10 ** 5, exchange=exchange)
    hist.seed(raw)
    tr = make_trader(SPEC, tmp_path, tick_source=source)
    ts = hist.last_ts
    tk = tr._tick(hist, ts)
    if expect == "policy":
        assert tk == 10_000.0                        # 코인원 1천만 원 이상 = 10,000원 (정책표)
    else:
        from btc_quant.backtest.signals import infer_ticks
        assert tk == float(infer_ticks(hist.frame((ts.value // 60_000_000_000 // 1440 - 1) * 1440,
                                                  ts.value // 60_000_000_000))[-1])
        assert tk != 10_000.0                        # 합성 데이터(1원 반올림 ≈ 연속 가격)는 정책과 다른 추정


def test_paper_tick_policy_required_but_missing(tmp_path):
    hist = M1History(max_rows=10, exchange="upbit")
    tr = make_trader(SPEC, tmp_path, tick_source="policy")
    with pytest.raises(ValueError, match="no tick policy"):
        tr._tick(hist, pd.Timestamp("2026-11-12", tz="UTC"))
    with pytest.raises(ValueError):
        make_trader(SPEC, tmp_path / "x", tick_source="guess")
