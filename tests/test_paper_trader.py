"""페이퍼 트레이더: 결정적 재생 == run_reference, 후행 창 신호 == 전체 이력 신호, 재시작 복원, 리스크 게이트."""
import json

import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest.reference import run_reference
from btc_quant.backtest.signals import build_signals
from btc_quant.backtest.types import CostModel
from btc_quant.data import quality
from btc_quant.live.risk import RiskLimits, RiskManager
from btc_quant.paper.bars import (M1History, MinuteBarBuilder, required_lookback_bars, ts_minute,
                                  window_minutes)
from btc_quant.paper.trader import PaperTrader, evaluate_signals
from btc_quant.strategy.blocks import REGISTRY
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

from test_paper_bars import synth_trades

T0 = pd.Timestamp("2026-11-12 00:00", tz="UTC")
COST = CostModel("t", 0.0005, 2.0, 1.0, 0.0)
LOOSE = RiskLimits(daily_loss=-0.99, weekly_loss=-0.99, max_drawdown=-0.99,
                   max_trades_per_day=10 ** 6, stale_seconds=1e12, price_deviation_threshold=10.0)
COLS = ["entry_time", "entry_price", "exit_time", "exit_price", "exit_reason", "gross_ret",
        "net_ret", "hold_min"]


def synth_raw(rng, start, n, p0=100_000_000.0):
    """REST 캔들 흉내(실제 봉만, 가끔 누락)."""
    idx = pd.date_range(start, periods=n, freq="1min", tz="UTC", name="ts")
    lr = rng.normal(0, 8e-4, n)
    c = p0 * np.exp(np.cumsum(lr))
    o = np.r_[p0, c[:-1]]
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 4e-4, n)))
    l = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 4e-4, n)))
    raw = pd.DataFrame({"open": o.round(), "high": h.round(), "low": l.round(), "close": c.round(),
                        "volume": rng.uniform(0, 3, n)}, index=idx)
    keep = rng.random(n) > 0.01
    return raw[keep]


def book_at(price, spread=2000.0, step=1000.0, qty=0.05, levels=15, ts_recv=0.0):
    return {"ts": 0, "ts_recv": ts_recv, "best_bid": price - spread / 2, "best_ask": price + spread / 2,
            "mid": price, "spread_bps": spread / price * 1e4,
            "bid_px": [price - spread / 2 - i * step for i in range(levels)], "bid_qty": [qty] * levels,
            "ask_px": [price + spread / 2 + i * step for i in range(levels)], "ask_qty": [qty] * levels}


def make_trader(spec, tmp_path, limits=LOOSE, kill_dir=None, cost=COST, **kw):
    risk = RiskManager(limits, spec.id, "B", kill_dir=kill_dir)
    return PaperTrader(spec, cost, risk, "B", out_dir=tmp_path / spec.id, clock=lambda: 0.0, **kw)


def run_live(trader, hist, trades, start_minute, end_s, builder=None):
    """체결 스트림 → 봉 → 트레이더 (실시간 경로 그대로). 호가는 직전 체결가 주변 합성."""
    b = builder or MinuteBarBuilder(2.0, start_minute=start_minute)
    last_px = hist.last_close
    events = []

    def drain(now):
        nonlocal events
        for bar in b.on_time(now):
            for nb in hist.append(bar):
                events += trader.on_bar(nb, hist, book_at(last_px))
    for ts_ms, p, q in trades:
        drain(ts_ms / 1000)
        b.on_trade(ts_ms, p, q)
        last_px = p
        trader.on_price(ts_ms, p, book_at(p))
    drain(end_s)
    return b, events


def read_jsonl(p):
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def paper_trades(trader) -> pd.DataFrame:
    rows = read_jsonl(trader.path("trades.jsonl"))
    df = pd.DataFrame(rows, columns=COLS + ["live"])
    for c in ("entry_time", "exit_time"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def assert_same_trades(paper: pd.DataFrame, ref: pd.DataFrame):
    ref = ref[ref["exit_reason"] != "end"].reset_index(drop=True)
    assert len(paper) == len(ref), (len(paper), len(ref))
    for c in ("entry_time", "exit_time"):
        assert (paper[c].dt.as_unit("ns").to_numpy() == pd.to_datetime(ref[c]).dt.as_unit("ns").to_numpy()).all(), c
    assert list(paper["exit_reason"]) == list(ref["exit_reason"])
    for c in ("entry_price", "exit_price", "gross_ret", "net_ret"):
        np.testing.assert_array_equal(paper[c].to_numpy(float), ref[c].to_numpy(float), err_msg=c)
    np.testing.assert_array_equal(paper["hold_min"].to_numpy(int), ref["hold_min"].to_numpy(int))


SPECS = [
    StrategySpec("p_rand", BlockRef("random_entry", {"p": 0.5, "seed": 3}),
                 ExitSpec(0.1, 0.1, 15, BlockRef("ema_cross_down", {"fast": 5, "slow": 26}))),
    StrategySpec("p_donch", BlockRef("donchian_breakout", {"lookback": 12}),
                 ExitSpec(0.15, 0.2, 30, None), (BlockRef("vol_regime", {"window": 12, "min_pct": 0.2}),)),
    StrategySpec("p_ema", BlockRef("ema_slope", {"span": 12, "min_slope_bp": 0.5}),
                 ExitSpec(0.2, 0.15, 25, BlockRef("ema_cross_down", {"fast": 8, "slow": 48})),
                 (BlockRef("trend_regime_1h", {"span": 12}),)),
    StrategySpec("p_rsi", BlockRef("rsi_oversold_bounce", {"period": 7, "level": 35}),
                 ExitSpec(0.1, 0.1, 10, None), (BlockRef("volume_z", {"window": 48, "min_z": 0.0}),)),
]


def _setup(seed, live_minutes=2 * 1440, seed_days=10):
    rng = np.random.default_rng(seed)
    raw_seed = synth_raw(rng, T0 - pd.Timedelta(days=seed_days), seed_days * 1440)
    raw_seed = raw_seed[raw_seed.index < T0]
    trades = synth_trades(rng, T0, live_minutes, p0=float(raw_seed["close"].iloc[-1]))
    return raw_seed, trades


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.id)
def test_replay_identical_to_run_reference(spec, tmp_path):
    raw_seed, trades = _setup(11)
    hist = M1History(max_rows=10 ** 6)
    hist.seed(raw_seed)
    tr = make_trader(spec, tmp_path)
    tr.attach(hist, book_at(hist.last_close))
    end_s = (T0 + pd.Timedelta(minutes=2 * 1440 + 1)).timestamp()
    run_live(tr, hist, trades, ts_minute(T0), end_s)
    m1 = hist.frame()
    ref = run_reference(spec, m1, COST, T0, m1.index[-1] + pd.Timedelta("1min")).trades
    paper = paper_trades(tr)
    assert len(paper) >= 5
    assert_same_trades(paper, ref)
    # 결정 기록: 5분 경계마다 1건, 신호 값 포함
    dec = read_jsonl(tr.path("decisions.jsonl"))
    assert len(dec) == 2 * 288 + 1 and all(pd.Timestamp(d["ts"]).minute % 5 == 0 for d in dec)
    assert {"entry", "filters", "complete", "exit_sig", "risk_ok", "entry_ok_gated"} <= set(dec[0])
    fills = read_jsonl(tr.path("fills.jsonl"))
    assert len(fills) == 2 * len(paper) + (1 if tr.position is not None else 0)
    assert all(f["book_cost_bps"] is not None for f in fills)


def test_trailing_window_equals_full_history():
    """블록은 인과적 → lookback 이 충분하면 후행 창의 마지막 봉 값 == 전체 이력 평가 값."""
    rng = np.random.default_rng(5)
    n = 26 * 1440
    raw = synth_raw(rng, T0 - pd.Timedelta(minutes=n), n)
    m1 = quality.regularize(quality.clean_wicks(raw))
    hist = M1History(max_rows=10 ** 6)
    hist.seed(raw)
    cases = []
    for d in REGISTRY.all():
        if not d.live_ready or d.name.startswith("_"):
            continue
        grid = {k: v for k, v in d.grid.items()}
        if d.name == "trend_regime_1h":
            grid["span"] = [12, 24]
        for j in range(2):
            params = {k: v[(j * 7 + i) % len(v)] for i, (k, v) in enumerate(grid.items())}
            ref = BlockRef(d.name, params)
            if d.kind == "entry":
                spec = StrategySpec("x", ref, ExitSpec(1, 1, 60))
            elif d.kind == "filter":
                spec = StrategySpec("x", BlockRef("random_entry", {"p": 0.5}), ExitSpec(1, 1, 60), (ref,))
            else:
                spec = StrategySpec("x", BlockRef("random_entry", {"p": 0.5}), ExitSpec(1, 1, 60, ref))
            cases.append(spec)
    assert len(cases) >= 26
    rng2 = np.random.default_rng(0)
    for spec in cases:
        lb = required_lookback_bars(spec)
        assert window_minutes(lb) < n - 3000, spec
        bars, entry_ok, exit_sig = build_signals(spec, m1)
        full_entry = REGISTRY.evaluate(spec.entry, bars)
        full_f = [REGISTRY.evaluate(f, bars) for f in spec.filters]
        lo = window_minutes(lb) // 5 + 12
        picks = rng2.choice(np.arange(lo, len(bars)), size=25, replace=False)
        for i in picks:
            s = bars.index[i]
            t = s + pd.Timedelta("5min")
            got = evaluate_signals(spec, hist.window(t - pd.Timedelta("1min"), window_minutes(lb)), s)
            assert got["entry"] == bool(full_entry.iloc[i]), (spec, s)
            assert got["filters"] == [bool(f.iloc[i]) for f in full_f], (spec, s)
            assert got["entry_ok"] == bool(entry_ok.iloc[i]), (spec, s)
            assert got["exit_sig"] == bool(exit_sig.iloc[i]), (spec, s)


def test_short_window_breaks_parity_negative_control():
    """lookback 이 부족하면 (vol_regime 7일 순위 창) 실제로 값이 달라진다 — 위 테스트가 의미 있음을 확인."""
    rng = np.random.default_rng(9)
    n = 10 * 1440
    raw = synth_raw(rng, T0 - pd.Timedelta(minutes=n), n)
    m1 = quality.regularize(quality.clean_wicks(raw))
    hist = M1History(max_rows=10 ** 6)
    hist.seed(raw)
    spec = StrategySpec("x", BlockRef("random_entry", {"p": 1.0}), ExitSpec(1, 1, 60),
                        (BlockRef("vol_regime", {"window": 24, "min_pct": 0.5}),))
    bars, entry_ok, _ = build_signals(spec, m1)
    diff = 0
    for i in range(len(bars) - 300, len(bars)):
        s = bars.index[i]
        got = evaluate_signals(spec, hist.window(s + pd.Timedelta("4min"), 2 * 1440), s)
        diff += got["entry_ok"] != bool(entry_ok.iloc[i])
    assert diff > 0


def test_restart_restore_and_catch_up(tmp_path):
    spec = SPECS[0]
    raw_seed, trades = _setup(23)
    end_min = 2 * 1440
    cut = T0 + pd.Timedelta(minutes=700)          # 여기서 프로세스 종료
    back = T0 + pd.Timedelta(minutes=760)         # 이 시각까지 다운 (60분 공백 → 재시작 시 재생)
    t_cut, t_back = cut.value // 10 ** 6, back.value // 10 ** 6

    hist = M1History(max_rows=10 ** 6)
    hist.seed(raw_seed)
    tr = make_trader(spec, tmp_path)
    tr.attach(hist)
    b, _ = run_live(tr, hist, [x for x in trades if x[0] < t_cut], ts_minute(T0), cut.timestamp() + 2)
    saved_last = tr.last_ts
    assert saved_last == cut - pd.Timedelta("1min")
    pos_before = tr.position
    del tr

    # 다운타임 동안의 봉은 재시작 시 REST 워밍업으로 받는다 (여기서는 같은 체결로 만든 이력)
    for bar in b.on_time(back.timestamp() + 0.0):
        pass
    hist2 = M1History(max_rows=10 ** 6)
    full_raw = pd.concat([raw_seed, _raw(trades, t_back)])
    hist2.seed(full_raw)
    risk2 = RiskManager.load(tmp_path / spec.id / "risk.json", LOOSE, kill_dir=None)
    tr2 = PaperTrader(spec, COST, risk2, "B", out_dir=tmp_path / spec.id, clock=lambda: 0.0)
    assert tr2.load_state()
    assert tr2.last_ts == saved_last and (tr2.position is None) == (pos_before is None)
    tr2.attach(hist2)                             # 공백 재생 (live=False)
    assert tr2.last_ts == back - pd.Timedelta("1min")
    run_live(tr2, hist2, [x for x in trades if x[0] >= t_back], ts_minute(back),
             (T0 + pd.Timedelta(minutes=end_min + 1)).timestamp())
    m1 = hist2.frame()
    ref = run_reference(spec, m1, COST, T0, m1.index[-1] + pd.Timedelta("1min")).trades
    paper = paper_trades(tr2)
    assert_same_trades(paper, ref)
    assert (~paper["live"]).sum() >= 1 or len(ref[(ref.entry_time >= cut) & (ref.entry_time < back)]) == 0
    dec = read_jsonl(tr2.path("decisions.jsonl"))
    assert len(dec) == 2 * 288 + 1                # 재생 결정 포함, 중복 없음


def _raw(trades, before_ms):
    from test_paper_bars import raw_from_trades
    return raw_from_trades([x for x in trades if x[0] < before_ms - (before_ms % 60_000)])


def test_logic_hash_mismatch_starts_fresh(tmp_path):
    spec = SPECS[0]
    tr = make_trader(spec, tmp_path)
    tr.save_state()
    other = StrategySpec(spec.id, spec.entry, ExitSpec(0.2, 0.1, 15, spec.exit.signal_exit))
    tr2 = make_trader(other, tmp_path)
    assert tr2.load_state() is False


# ------------------------------------------------------------------------------- 리스크 게이트

def _flat_live(tr, hist, start, minutes, price=100_000_000.0, jitter=0.0, rng=None):
    """매 분 체결이 있는 단순 스트림."""
    trades = []
    t0 = start.value // 10 ** 6
    p = price
    for m in range(minutes):
        if rng is not None:
            p *= float(np.exp(rng.normal(0, jitter)))
        trades.append((t0 + m * 60_000 + 500, round(p), 0.01))
        trades.append((t0 + m * 60_000 + 30_000, round(p), 0.01))
    return run_live(tr, hist, trades, ts_minute(start), (start + pd.Timedelta(minutes=minutes + 1)).timestamp())


def _seeded(rng_seed=1, days=4):
    rng = np.random.default_rng(rng_seed)
    raw = synth_raw(rng, T0 - pd.Timedelta(days=days), days * 1440)
    h = M1History(max_rows=10 ** 6)
    h.seed(raw[raw.index < T0])
    return h


ALWAYS = StrategySpec("always", BlockRef("random_entry", {"p": 1.0}), ExitSpec(5.0, 5.0, 10))


def test_kill_switch_gates_entries(tmp_path):
    kd = tmp_path / "rt"
    kd.mkdir()
    (kd / "KILL").write_text("test")
    hist = _seeded()
    tr = make_trader(ALWAYS, tmp_path, kill_dir=kd)
    tr.attach(hist)
    _flat_live(tr, hist, T0, 60)
    dec = read_jsonl(tr.path("decisions.jsonl"))
    assert dec and all(d["entry_ok"] and not d["entry_ok_gated"] for d in dec)
    assert all(d["risk_reason"].startswith("kill_switch") for d in dec)
    assert tr.n_trades == 0 and tr.position is None
    (kd / "KILL").unlink()
    _flat_live(tr, hist, T0 + pd.Timedelta(minutes=60), 30)
    assert tr.n_trades >= 1


def test_flatten_switch_forces_exit_next_open(tmp_path):
    kd = tmp_path / "rt"
    kd.mkdir()
    hist = _seeded()
    spec = StrategySpec("hold", BlockRef("random_entry", {"p": 1.0}), ExitSpec(5.0, 5.0, 600))
    tr = make_trader(spec, tmp_path, kill_dir=kd)
    tr.attach(hist)
    _flat_live(tr, hist, T0, 7)
    assert tr.position is not None
    (kd / "FLATTEN").write_text("x")
    _flat_live(tr, hist, T0 + pd.Timedelta(minutes=7), 5)
    trades = read_jsonl(tr.path("trades.jsonl"))
    assert [t["exit_reason"] for t in trades] == ["flatten"]
    # FLATTEN 감지는 7분봉 마감 → 8분봉 시가 청산
    assert pd.Timestamp(trades[0]["exit_time"]) == T0 + pd.Timedelta(minutes=8)
    assert tr.position is None and tr.n_trades == 1


def test_trade_limit_and_stale_data(tmp_path):
    hist = _seeded()
    limits = RiskLimits(-0.99, -0.99, -0.99, max_trades_per_day=2, stale_seconds=120)
    spec = StrategySpec("fast", BlockRef("random_entry", {"p": 1.0}), ExitSpec(5.0, 5.0, 5))
    tr = make_trader(spec, tmp_path, limits=limits)
    tr.attach(hist)
    _flat_live(tr, hist, T0, 120)
    trades = read_jsonl(tr.path("trades.jsonl"))
    assert len(trades) == 2                                 # 일일 거래 한도
    risk = read_jsonl(tr.path("risk.jsonl"))
    assert any(r.get("code") == "trade_limit" for r in risk)

    hist = _seeded()
    tr = make_trader(StrategySpec("stale", spec.entry, spec.exit), tmp_path,
                     limits=RiskLimits(-0.99, -0.99, -0.99, 100, stale_seconds=120))
    tr.attach(hist)
    b = MinuteBarBuilder(2.0, start_minute=ts_minute(T0))
    # 체결 없이 시간만 흐름 → filled 봉, 2분 이상 시세 없음 → 진입 금지
    for bar in b.on_time((T0 + pd.Timedelta(minutes=16)).timestamp()):
        for nb in hist.append(bar):
            tr.on_bar(nb, hist, None)
    dec = read_jsonl(tr.path("decisions.jsonl"))
    late = [d for d in dec if pd.Timestamp(d["ts"]) >= T0 + pd.Timedelta(minutes=5)]
    assert late and all(not d["risk_ok"] and d["risk_reason"].startswith("stale_data") for d in late)
    assert tr.n_trades == 0 and tr.position is None


def test_drawdown_halt_flattens(tmp_path):
    hist = _seeded()
    limits = RiskLimits(-0.99, -0.99, max_drawdown=-0.002, max_trades_per_day=100, stale_seconds=1e9)
    spec = StrategySpec("dd", BlockRef("random_entry", {"p": 1.0}), ExitSpec(50.0, 50.0, 600))
    tr = make_trader(spec, tmp_path, limits=limits)
    tr.attach(hist)
    t0 = T0.value // 10 ** 6
    trades = [(t0 + m * 60_000 + 500, 100_000_000.0 * (1 - 0.001 * max(0, m - 6)), 0.01) for m in range(20)]
    run_live(tr, hist, trades, ts_minute(T0), (T0 + pd.Timedelta(minutes=21)).timestamp())
    tl = read_jsonl(tr.path("trades.jsonl"))
    assert tl and tl[0]["exit_reason"] == "flatten"
    assert tr.risk.halt_level(pd.Timestamp.now(tz="UTC").to_pydatetime()).name in ("HUMAN",)
    assert tr.n_trades == 1                                  # HUMAN 정지 후 재진입 없음
