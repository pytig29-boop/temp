"""대기열 재현(backtest/queue.py)·체결 모델 보정(research/fill_model.py)·페이퍼 훅(QueueFillOracle).

합성 호가·체결 스트림(수집기 스키마)으로 검증한다: 대기열 규칙 단위 사례, 배열 스캔(_scan) == 상태 기계(QueueSim),
엔진 + 오라클, 보정 파이프라인 끝까지(Forward 가드 포함)."""
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest.queue import (
    QueueFillOracle,
    QueueSim,
    displayed_at,
    simulate_order,
    snap_price,
)
from btc_quant.backtest.reference import ReferenceEngine
from btc_quant.backtest.types import CostModel, LimitFillModel
from btc_quant.live import ws_collector as wc
from btc_quant.research import fill_model as fm
from btc_quant.research.execution_study import ExecutionStudyError
from btc_quant.strategy.spec import ExecutionSpec

REPO = Path(__file__).resolve().parents[1]
T0 = pd.Timestamp("2026-11-10 00:00", tz="UTC")
TICK = 10_000.0
P0 = 100_000_000.0
NS = 1_000_000_000


def book(bids, asks):
    bp, bq = zip(*bids) if bids else ((), ())
    ap, aq = zip(*asks) if asks else ((), ())
    return {"bid_px": list(bp), "bid_qty": list(bq), "ask_px": list(ap), "ask_qty": list(aq)}


B = book([(100.0, 1.0), (99.0, 2.0), (97.0, 3.0)], [(101.0, 1.0), (102.0, 1.0)])


# ================================================================ 대기열 단위 규칙
@pytest.mark.parametrize("side,price,expect", [
    ("buy", 101.0, (0.0, "cross")), ("buy", 102.0, (0.0, "cross")), ("buy", 100.5, (0.0, "inside")),
    ("buy", 100.0, (1.0, "level")), ("buy", 99.0, (2.0, "level")), ("buy", 98.0, (0.0, "gap")),
    ("buy", 97.0, (3.0, "level")), ("buy", 96.0, (math.nan, "deep")),
    ("sell", 100.0, (0.0, "cross")), ("sell", 100.5, (0.0, "inside")), ("sell", 101.0, (1.0, "level")),
    ("sell", 103.0, (math.nan, "deep")),
])
def test_displayed_at(side, price, expect):
    a, st = displayed_at(side, price, B["bid_px"], B["bid_qty"], B["ask_px"], B["ask_qty"])
    assert st == expect[1]
    assert (math.isnan(a) and math.isnan(expect[0])) or a == expect[0]


def test_queue_back_of_line_and_through():
    s = QueueSim("buy", 99.0, 0.5, placed_ns=10)
    s.start(B)
    assert s.status == "level" and s.need == 2.5
    assert not s.on_trade(5, 99.0, 10.0, "sell")            # 주문 전 체결은 무시
    assert not s.on_trade(11, 99.0, 1.0, "buy")             # 매수 테이커는 우리(매수 대기열)를 치지 않음
    assert not s.on_trade(12, 99.0, 2.0, "sell")            # 앞선 2.0 소진, 우리 0.5 남음
    assert s.on_trade(13, 99.0, 0.5, "sell") and s.how == "queue" and s.filled_ns == 13
    t = QueueSim("buy", 99.0, 0.5, placed_ns=10)
    t.start(B)
    assert t.on_trade(11, 98.0, 0.001, "sell") and t.how == "through"
    # 취소는 인정하지 않음 (보수) / 낙관 변형은 인정
    c = QueueSim("buy", 99.0, 0.5, placed_ns=10)
    c.start(B)
    c.on_book(11, book([(100.0, 1.0), (99.0, 0.1)], [(101.0, 1.0)]))
    assert c.need == 2.5
    o = QueueSim("buy", 99.0, 0.5, placed_ns=10, optimistic=True)
    o.start(B)
    o.on_book(11, book([(100.0, 1.0), (99.0, 0.1)], [(101.0, 1.0)]))
    assert o.need == pytest.approx(0.6)
    # 매도 대칭
    s2 = QueueSim("sell", 102.0, 0.5, placed_ns=0)
    s2.start(B)
    assert s2.need == 1.5 and not s2.on_trade(1, 102.0, 1.0, "buy") and s2.on_trade(2, 102.5, 0.1, "buy")


def test_queue_deep_level_and_cross():
    d = QueueSim("buy", 95.0, 0.1, placed_ns=0)
    d.start(B)
    assert d.status == "deep" and math.isnan(d.need)
    assert not d.on_trade(1, 95.0, 5.0, "sell")             # 앞선 대기량 모름 → 세지 않음
    d.on_book(2, book([(96.0, 1.0), (95.0, 0.7)], [(97.0, 1.0)]))
    assert d.need == pytest.approx(0.8)
    assert d.on_trade(3, 95.0, 0.8, "sell")
    x = QueueSim("buy", 101.0, 0.1, placed_ns=7)
    x.start(B)
    assert x.filled and x.how == "cross" and x.filled_ns == 7


def test_snap_price():
    assert snap_price(100_004_999.0, TICK, "buy") == 100_000_000.0
    assert snap_price(100_000_001.0, TICK, "sell") == 100_010_000.0
    assert snap_price(123.4, 0.0, "buy") == 123.4


# ================================================================ 합성 시장 (수집기 스키마)
def synth_market(n_min=150, seed=0, start=T0):
    """초당 호가 15단계, 중간가 tick 격자 랜덤워크, 초당 포아송 체결(최우선 호가), 가격 이동 시 쓸어담는 체결."""
    rng = np.random.default_rng(seed)
    t0 = start.value / 1e9
    lvl = 0
    trades, books = [], []
    tid = 0
    for s in range(n_min * 60):
        move = rng.choice([-1, 0, 0, 0, 0, 1])
        bid = P0 + lvl * TICK
        if move:
            # 가격 이동: 기존 최우선 호가를 쓸고 다음 가격에서 체결
            side = "sell" if move < 0 else "buy"
            px = bid if move < 0 else bid + TICK
            for k in (0, 1):
                p = px + (-k * TICK if move < 0 else k * TICK)
                trades.append({"ts_exchange": int((t0 + s + 0.2 + 0.1 * k) * 1000), "ts_recv": t0 + s + 0.3 + 0.1 * k,
                               "price": p, "qty": float(rng.exponential(0.3)), "side": side,
                               "trade_id": f"t{tid}"})
                tid += 1
            lvl += move
            bid = P0 + lvl * TICK
        for _ in range(rng.poisson(1.5)):
            side = "sell" if rng.random() < 0.5 else "buy"
            trades.append({"ts_exchange": int((t0 + s + rng.uniform(0.4, 0.95)) * 1000),
                           "ts_recv": t0 + s + 0.99, "price": bid if side == "sell" else bid + TICK,
                           "qty": float(rng.exponential(0.05)), "side": side, "trade_id": f"t{tid}"})
            tid += 1
        bids = [(bid - k * TICK, float(rng.exponential(0.4))) for k in range(15)]
        asks = [(bid + TICK + k * TICK, float(rng.exponential(0.4))) for k in range(15)]
        books.append(wc._book_row(int((t0 + s) * 1000), t0 + s + 0.05, bids, asks))
    trades.sort(key=lambda r: r["ts_exchange"])
    return trades, books


@pytest.fixture(scope="module")
def market_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("fm")
    (root / "config").mkdir()
    for f in ("splits.yaml", "costs.yaml", "params.yaml"):
        if (REPO / "config" / f).exists():
            shutil.copy(REPO / "config" / f, root / "config" / f)
    trades, books = synth_market()
    wc.write_rows(root / "data" / "raw", "coinone", "trades", trades)
    wc.write_rows(root / "data" / "raw", "coinone", "orderbook", books)
    return root


@pytest.fixture(scope="module")
def window(market_root):
    return fm.load_window(T0, T0 + pd.Timedelta(minutes=150), root=market_root, gaps_path=None)


def test_scan_equals_queue_sim(window):
    """배열 스캔(보정용) == 상태 기계(페이퍼 오라클) — 무작위 주문 200개, 보수·낙관 둘 다."""
    rng = np.random.default_rng(5)
    w = window
    tr, bk = w.trades, w.book
    n = 0
    for _ in range(200):
        side = "buy" if rng.random() < 0.5 else "sell"
        t = int(bk.t[int(rng.integers(10, len(bk.t) - 400))]) + int(rng.integers(0, NS))
        mid = float(bk.mid[int(bk.idx_at(np.array([t]))[0])])
        px = snap_price(mid * (1 + (-1 if side == "buy" else 1) * rng.uniform(0, 8) * 1e-4), TICK, side)
        end = t + int(rng.integers(1, 10)) * 60 * NS
        for opt in (False, True):
            f, how, st = simulate_order(side, px, 0.003, t, end, tr, bk, optimistic=opt)
            sim = QueueSim(side, px, 0.003, t, optimistic=opt)
            j = int(bk.idx_at(np.array([t]))[0])
            sim.start(bk.snapshot(j) if j >= 0 else None)
            ev = [(int(x), 0, i) for i, x in enumerate(bk.t) if t <= x < end] + \
                 [(int(x), 1, i) for i, x in enumerate(tr.t) if t <= x < end]
            for tt, kind, i in sorted(ev):
                if sim.filled:
                    break
                if kind == 0:
                    sim.on_book(tt, bk.snapshot(i))
                else:
                    sim.on_trade(tt, float(tr.price[i]), float(tr.qty[i]), "sell" if tr.taker_sell[i] else "buy")
            assert st == sim.status
            assert (f if f >= 0 else None) == sim.filled_ns, (side, px, t, opt)
            assert how == sim.how
            n += int(f >= 0)
    assert n > 50


def test_load_window_and_tick(window):
    assert window.tick == TICK
    assert window.coverage["trades"] > 1000 and window.coverage["book_snapshots"] > 8000
    assert window.coverage["taker_sell_at_or_below_bid"] > 0.7    # 스냅샷 지연(≤ 1 s) 때문에 100 % 아님


def test_forward_guard(market_root):
    with pytest.raises(ExecutionStudyError, match="Forward"):
        fm.load_window(pd.Timestamp("2025-01-01", tz="UTC"), T0, root=market_root, gaps_path=None)


def test_fill_study_end_to_end(market_root, window, tmp_path):
    res = fm.run_fill_study(root=market_root, window=window, offsets=(0.0, 2.0, 5.0), tifs=(1, 5))
    pm = res.pairs[res.pairs["status"] != "cross"]
    assert len(pm) > 500
    # 단조성: 터치 ≥ 1 tick ≥ 2 tick ≥ 큰 bp; 낙관 대기열 ≥ 보수 대기열; 터치 recall = 1
    r = {k: pm[f"c_{k}"].mean() for k in fm.rule_names()}
    assert r["touch"] >= r["1tick"] >= r["2tick"] >= r["5bp"]
    assert (pm["qo_fill"] >= pm["q_fill"]).all()
    assert fm.prop(pm, "c_touch", "q_fill")[0] == 1.0
    # 가격-시간 우선: 관통(1 tick)은 실제 체결을 함의 (같은 분 끝까지) — 지연 1 s 안의 관통만 예외
    assert fm.prop(pm, "ct_1tick", "c_1tick")[0] > 0.97
    assert res.rec["through_ticks"] >= 1 and 0 < res.rec["fill_prob"] <= 1
    assert res.status in ("insufficient", "provisional")
    md = fm.render_markdown(res)
    for h in ("Fill probability", "Is the candle rule conservative", "Adverse selection", "Recommendation",
              "Paper-path replay", "Limitations"):
        assert h in md
    assert "grid_5bp" in res.replays and len(res.replays["grid_5bp"]["candle"]) > 0
    out = tmp_path / "r.md"
    assert fm.main(["--root", str(market_root), "--since", str(T0), "--until",
                    str(T0 + pd.Timedelta(minutes=150)), "--offsets", "2,5", "--tifs", "1,5", "--no-replay",
                    "--report", str(out)]) == 0
    assert out.read_text().startswith("# Limit-order fill model")


def test_block_ratio_and_infer_tick():
    est, lo, hi, n = fm.block_ratio(np.array([1, 0, 1, 1.0]), np.ones(4), np.array([0, 0, 1, 1]))
    assert est == 0.75 and n == 4 and lo <= est <= hi
    assert math.isnan(fm.block_ratio(np.zeros(3), np.zeros(3), np.zeros(3))[0])
    assert fm.infer_tick(np.array([101_880_000.0, 101_890_000.0])) == 10_000.0
    assert fm.infer_tick(np.array([38_034_000.0, 38_035_000.0])) == 1_000.0


# ================================================================ 페이퍼 훅: 엔진 + 오라클
def test_engine_with_queue_oracle(window):
    """오라클을 꽂은 기준 엔진은 대기열 재현으로 체결하고, 같은 주문의 1분봉 규칙 결과와 다를 수 있다.
    오라클 체결 → 진입 체결가 = 지정가, 익절 지정가도 오라클로 판정."""
    w = window
    ex = ExecutionSpec("limit", offset_bp=2.0, tif_bars=5, take_profit="limit")
    zero = CostModel("zero", 0.0, 0.0, 0.0, 0.0)
    from btc_quant.backtest.signals import infer_ticks
    ticks = infer_ticks(w.m1)
    res = {}
    for lab in ("candle", "queue"):
        eng = ReferenceEngine(0.3, 0.05, 30, zero, timeframe_min=1, execution=ex, fill=LimitFillModel())
        if lab == "queue":
            eng.fill_oracle = QueueFillOracle.from_arrays(w.trades, w.book, latency_s=1.0)
        for ts, r, tk in zip(w.m1.index, w.m1.itertuples(index=False), ticks):
            eng.on_decision(ts, True, False)
            eng.on_bar_1m(ts, r.open, r.high, r.low, r.close, bool(r.filled), r.volume, tk)
        res[lab] = eng.trades_frame()
        assert len(res[lab]) > 5
        t = res[lab]
        # 진입 체결가는 격자 위 지정가 (maker, 비용 없음)
        assert np.allclose(t.entry_price / TICK, np.round(t.entry_price / TICK))
        assert (t.exit_reason == "tp").any()
    # 1분봉 규칙(1 tick 관통)은 대기열 재현보다 진입이 적거나 같고, 공통 진입이 있다
    assert len(set(res["candle"].entry_time) & set(res["queue"].entry_time)) > 0
