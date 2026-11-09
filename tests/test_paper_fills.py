"""페이퍼: 호가 체결 추정과 모델 체결 비교 (G8 슬리피지 입력)."""
import math

import pytest

from btc_quant.backtest.types import COINONE_CONSERVATIVE, CostModel
from btc_quant.paper.fills import compare_to_model, slippage_stats, walk_book

BOOK = {"ts": 1_794_225_900_000, "ts_recv": 1_794_225_900.5,
        "best_bid": 99_990.0, "best_ask": 100_010.0, "mid": 100_000.0,
        "bid_px": [99_990.0, 99_980.0, 99_900.0], "bid_qty": [1.0, 2.0, 10.0],
        "ask_px": [100_010.0, 100_020.0, 100_100.0], "ask_qty": [1.0, 2.0, 10.0]}


def test_walk_book_buy_notional():
    # 1개 + 1.5개 : 100,010 + 150,030 = 250,040 KRW
    f = walk_book(BOOK, "buy", notional=250_040.0, now=1_794_225_901.0)
    assert f.complete and f.levels == 2
    assert f.qty == pytest.approx(2.5)
    assert f.vwap == pytest.approx(250_040 / 2.5)
    assert f.cost_bps == pytest.approx((250_040 / 2.5 / 100_000 - 1) * 1e4)   # 1.6bp
    assert f.spread_bps == pytest.approx(2.0)
    assert f.book_age_s == pytest.approx(0.5) and f.book_ts == BOOK["ts"]


def test_walk_book_sell_qty_and_depth_shortfall():
    f = walk_book(BOOK, "sell", qty=2.0)
    assert f.complete and f.vwap == pytest.approx((99_990 + 99_980) / 2)
    assert f.cost_bps == pytest.approx((1 - f.vwap / 100_000) * 1e4)            # 1.5bp, + = 불리
    g = walk_book(BOOK, "sell", qty=20.0)
    assert not g.complete and g.qty == pytest.approx(13.0) and g.levels == 3
    with pytest.raises(ValueError):
        walk_book(BOOK, "hold", qty=1)


def test_compare_to_model_signs():
    cost = CostModel("c", 0.0, 2.0, 3.0)          # 편도 4bp
    f = walk_book(BOOK, "buy", notional=100_000.0)
    model = cost.buy_fill(100_000.0)                # 1분봉 시가 = 100,000
    c = compare_to_model(f, model, 100_000.0, cost)
    assert c["model_cost_bps"] == pytest.approx(4.0) and c["conservative_cost_bps"] == pytest.approx(4.0)
    assert c["book_cost_bps"] == pytest.approx(1.0)                  # 반 스프레드
    assert c["vs_model_bps"] == pytest.approx((100_010 / model - 1) * 1e4)   # 호가가 모델보다 유리 → 음수
    assert c["vs_model_bps"] < 0 and c["within_conservative"] is True
    s = walk_book(BOOK, "sell", qty=13.0)          # 깊게 → 불리
    cs = compare_to_model(s, cost.sell_fill(100_000.0), 100_000.0, cost)
    assert cs["book_cost_bps"] > 4.0 and cs["within_conservative"] is False and cs["vs_model_bps"] > 0
    none = compare_to_model(None, model, 100_000.0, cost)
    assert none["book_cost_bps"] is None and none["within_conservative"] is None


def test_slippage_stats_g8():
    recs = [{"side": "buy", "book_cost_bps": 1.0, "vs_model_bps": -3.0, "within_conservative": True},
            {"side": "sell", "book_cost_bps": 3.0, "vs_model_bps": -1.0, "within_conservative": True},
            {"side": "sell", "book_cost_bps": 5.0, "vs_model_bps": 1.0, "within_conservative": False},
            {"side": "buy", "book_cost_bps": None}]
    st = slippage_stats(recs)
    assert st["n"] == 3 and st["n_total"] == 4
    assert st["mean_cost_bps"] == pytest.approx(3.0) and st["median_cost_bps"] == pytest.approx(3.0)
    assert st["frac_within_conservative"] == pytest.approx(2 / 3)
    assert st["g8_slippage_ok"] is True                     # 평균 3bp ≤ 보수적 4bp
    assert st["by_side"]["sell"]["n"] == 2
    st2 = slippage_stats(recs, conservative=CostModel("tight", 0.0, 1.0, 1.0))   # 1.5bp
    assert st2["g8_slippage_ok"] is False
    assert slippage_stats([])["g8_slippage_ok"] is None
    assert COINONE_CONSERVATIVE.side_cost * 1e4 == pytest.approx(4.0)


def test_trader_records_book_fill(tmp_path):
    """트레이더가 진입 시 결정 시점 호가(book_open)에 실제 금액을 대입해 기록한다."""
    import json

    import pandas as pd

    from btc_quant.live.risk import RiskLimits, RiskManager
    from btc_quant.paper.bars import Bar, M1History
    from btc_quant.paper.trader import PaperTrader
    from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

    t0 = pd.Timestamp("2026-11-12 00:00", tz="UTC")
    hist = M1History()
    for i in range(-30, 0):
        hist.append(Bar(t0 + pd.Timedelta(minutes=i), 100_000, 100_000, 100_000, 100_000, 1, False))
    spec = StrategySpec("bk", BlockRef("random_entry", {"p": 1.0}), ExitSpec(5.0, 5.0, 5))
    risk = RiskManager(RiskLimits(-0.5, -0.5, -0.5, 100, stale_seconds=1e9), "bk", "B", kill_dir=None)
    cost = CostModel("c", 0.0, 2.0, 3.0)
    tr = PaperTrader(spec, cost, risk, "B", out_dir=tmp_path, initial_equity=250_040.0,
                     lookback_bars=20, clock=lambda: 1_794_225_901.0)
    tr.attach(hist, BOOK)                                  # 결정 시점 호가
    bar = Bar(t0, 100_000, 100_000, 100_000, 100_000, 1, False)
    for nb in hist.append(bar):
        ev = tr.on_bar(nb, hist, BOOK)
    assert ev[0]["type"] == "entry"
    fill = json.loads((tmp_path / "fills.jsonl").read_text().splitlines()[0])
    assert fill["side"] == "buy" and fill["notional"] == 250_040.0
    assert fill["book"]["qty"] == pytest.approx(2.5) and fill["book"]["levels"] == 2
    assert fill["book_cost_bps"] == pytest.approx(1.6)
    assert fill["model_fill"] == pytest.approx(100_000 * 1.0004)
    assert fill["vs_model_bps"] == pytest.approx((100_016 / 100_040 - 1) * 1e4)
    assert math.isfinite(fill["mid_vs_ref_bps"])
