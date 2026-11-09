"""Execution study (research/execution_study.py): synthetic trade / book streams with a planted
Upbit -> Coinone lag validate bar building, signal timing, slippage, depth walk, catch-up time,
forward returns, the matched control and the Forward-only data guard."""
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest.types import CostModel
from btc_quant.live import ws_collector as wc
from btc_quant.paper.bars import MinuteBarBuilder
from btc_quant.research import execution_study as es

REPO = Path(__file__).resolve().parents[1]
T0 = pd.Timestamp("2026-11-10 00:00", tz="UTC")       # KST 09:00 -> session_kst all_but_quiet open
P = 100_000_000.0
JUMP_BP = 30.0
JUMP_S = 100 * 60 + 30                                  # Upbit jumps at 01:40:30 (inside minute 100)
N_MIN = 130
CO_HALF = 5_000.0                                       # Coinone spread 10,000 KRW (1 bp)
UP_HALF = 1_000.0
LEVEL_QTY = 0.001                                       # ~100k KRW per level -> 300k walks 3 levels
LEVEL_STEP = 1e-4
COST = CostModel("coinone_conservative", 0.0, 2.0, 1.0)


def up_mid(s: float) -> float:
    return P * (1 + JUMP_BP / 1e4) if s >= JUMP_S else P


def co_mid(s: float, lag: float) -> float:
    return P * (1 + JUMP_BP / 1e4) if s >= JUMP_S + lag else P


def _levels(best: float, sign: int) -> list[float]:
    return [best * (1 + sign * k * LEVEL_STEP) for k in range(15)]


def synth_rows(lag: float, n_min: int = N_MIN, start: pd.Timestamp = T0):
    """Collector-format rows: Upbit trades every 2 s at mid, Coinone trades at :05,:15,..,:55 hitting the bid,
    books every second (received 0.1 s after the exchange time). Coinone follows Upbit's jump after `lag` s."""
    t0 = start.value / 1e9
    rows = {("upbit", "trades"): [], ("coinone", "trades"): [], ("upbit", "orderbook"): [],
            ("coinone", "orderbook"): []}
    for s in range(n_min * 60):
        if s % 2 == 0:
            rows[("upbit", "trades")].append({"ts_exchange": int((t0 + s) * 1000), "ts_recv": t0 + s + 0.05,
                                              "price": up_mid(s), "qty": 0.01, "side": "buy",
                                              "trade_id": f"u{s}"})
        if s % 10 == 5:
            rows[("coinone", "trades")].append({"ts_exchange": int((t0 + s) * 1000), "ts_recv": t0 + s + 0.05,
                                                "price": co_mid(s, lag) - CO_HALF, "qty": 0.002, "side": "sell",
                                                "trade_id": f"c{s}"})
        for ex, mid, half in (("upbit", up_mid(s), UP_HALF), ("coinone", co_mid(s, lag), CO_HALF)):
            bids = [(p, LEVEL_QTY) for p in _levels(mid - half, -1)]
            asks = [(p, LEVEL_QTY) for p in _levels(mid + half, +1)]
            rows[(ex, "orderbook")].append(wc._book_row(int((t0 + s) * 1000), t0 + s + 0.1, bids, asks))
    return rows


@pytest.fixture
def make_root(tmp_path):
    def _make(lag: float, start: pd.Timestamp = T0):
        root = tmp_path / f"lag{lag:g}_{start:%H}"
        (root / "config").mkdir(parents=True)
        for f in ("splits.yaml", "costs.yaml"):
            shutil.copy(REPO / "config" / f, root / "config" / f)
        tpl = root / es.DEFAULT_TEMPLATE
        tpl.parent.mkdir(parents=True)
        shutil.copy(REPO / es.DEFAULT_TEMPLATE, tpl)
        for (ex, kind), rows in synth_rows(lag, start=start).items():
            wc.write_rows(root / "data" / "raw", ex, kind, rows)
        return root
    return _make


def run(root, **kw):
    return es.run_study(T0, T0 + pd.Timedelta(minutes=N_MIN), root=root, rest_warmup=False, gaps_path=None, **kw)


# ---------------------------------------------------------------- pieces
def test_build_bars_matches_builder_and_drops_late_trades():
    t0 = T0.value / 1e9
    rows = [(t0 + 5, t0 + 5.1, 100.0), (t0 + 65, t0 + 65.1, 101.0), (t0 + 70, t0 + 70.1, 103.0),
            (t0 + 100, t0 + 100.1, 99.0), (t0 + 119, t0 + 125.0, 50.0),      # late: minute 1 closed at 122 s
            (t0 + 130, t0 + 130.1, 102.0), (t0 + 200, t0 + 200.1, 104.0)]
    df = pd.DataFrame({"ts_exchange": pd.to_datetime([int(r[0] * 1000) for r in rows], unit="ms", utc=True),
                       "ts_recv": pd.to_datetime([int(r[1] * 1000) for r in rows], unit="ms", utc=True),
                       "price": [r[2] for r in rows], "qty": 1.0, "trade_id": [str(i) for i in range(len(rows))]})
    df = pd.concat([df, df.iloc[[1]]], ignore_index=True)        # duplicate delivery after a reconnect
    bb = es.build_bars(df.sort_values("ts_recv", kind="stable"))
    ref = MinuteBarBuilder(grace_seconds=2.0)
    out = []
    for r in rows:
        out += ref.on_time(r[1])
        ref.on_trade(int(r[0] * 1000), r[2], 1.0)
    out += ref.on_time(rows[-1][1])
    real = [b for b in out if not b.filled]
    assert list(bb.raw["open"]) == [b.open for b in real]
    assert list(bb.raw["close"]) == [b.close for b in real]
    m1 = bb.raw.loc[T0 + pd.Timedelta(minutes=1)]
    assert (m1.open, m1.high, m1.low, m1.close) == (101.0, 103.0, 99.0, 99.0)   # late 50.0 dropped
    assert bb.late == 1 and bb.duplicates == 1 and bb.ignored == 1
    assert bb.first_trade[T0 + pd.Timedelta(minutes=1)] == T0 + pd.Timedelta(seconds=65)


def test_walks_match_brute_force():
    rng = np.random.default_rng(1)
    px = np.sort(rng.uniform(100, 110, (20, 6)), axis=1)
    qty = rng.uniform(0.1, 2.0, (20, 6))
    notional = 400.0
    got = es.walk_buy(px, qty, notional)
    for i in range(20):
        left, q = notional, 0.0
        for p, s in zip(px[i], qty[i]):
            take = min(s, left / p)
            q += take
            left -= take * p
            if left <= 1e-12:
                break
        exp = notional / q if left <= 1e-9 else np.nan
        assert (np.isnan(exp) and np.isnan(got[i])) or got[i] == pytest.approx(exp, rel=1e-12)
    bpx = px[:, ::-1]
    bq = qty[:, ::-1]
    got_s = es.walk_sell(bpx, bq, 1.5)
    for i in range(20):
        left, proceeds = 1.5, 0.0
        for p, s in zip(bpx[i], bq[i]):
            take = min(s, left)
            proceeds += take * p
            left -= take
        exp = proceeds / 1.5 if left <= 1e-12 else np.nan
        assert got_s[i] == pytest.approx(exp, rel=1e-12)
    assert np.isnan(es.walk_buy(px[:1], qty[:1] * 1e-6, notional)[0])         # not enough depth


def test_book_asof_uses_receive_clock_and_gaps():
    df = pd.DataFrame({"ts": pd.to_datetime([0, 1000, 2000], unit="ms", utc=True),
                       "ts_recv": pd.to_datetime([500, 1500, 2500], unit="ms", utc=True),
                       "best_bid": [1.0, 2.0, 3.0], "best_ask": [1.5, 2.5, 3.5],
                       "bid_px": [[1.0]] * 3, "bid_qty": [[1.0]] * 3, "ask_px": [[1.5]] * 3, "ask_qty": [[1.0]] * 3})
    b = es.Book.from_frame(df, gaps=[(int(2.6e9), int(5e9))])
    q = np.array([0.4e9, 0.5e9, 1.49e9, 2.0e9, 3.0e9, 40e9]).astype(np.int64)
    assert list(b.idx_at(q, max_age_s=30)) == [-1, 0, 0, 1, -1, -1]


def test_ws_files_ignores_non_partition_and_pre_forward(tmp_path):
    base = tmp_path / "exchange=coinone" / "symbol=BTC-KRW" / "orderbook"
    for rel in ("rest_20261109T134956.parquet", "date=2026-11-08/23-0000.parquet",
                "date=2026-11-10/01-0000.parquet", "date=2026-11-10/01-0001.parquet.tmp",
                "date=2026-11-10/05-0000.parquet"):
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_bytes(b"")
    fwd = pd.Timestamp("2026-11-09", tz="UTC")
    files = es.ws_files(tmp_path, "coinone", "orderbook", pd.Timestamp("2026-11-08", tz="UTC"),
                        pd.Timestamp("2026-11-10 02:00", tz="UTC"), fwd)
    assert [f.relative_to(base).as_posix() for f in files] == ["date=2026-11-10/01-0000.parquet"]


def test_since_before_forward_is_refused(make_root):
    root = make_root(40)
    with pytest.raises(es.ExecutionStudyError, match="Forward"):
        es.run_study("2026-11-08T23:00", T0 + pd.Timedelta(minutes=N_MIN), root=root, rest_warmup=False)


# ---------------------------------------------------------------- planted lag, end to end
@pytest.mark.parametrize("lag", [40, 60, 120])
def test_planted_lag_end_to_end(make_root, lag):
    res = run(make_root(lag))
    t_ev = T0 + pd.Timedelta(minutes=101)               # decision = close of the impulse bar (minute 100)
    ev = res.events
    # lead fires on the impulse bar; if Coinone is still stale one bar later the gap block (whose window now
    # contains the first dislocated bar, so its std > 0) fires at t + 1 min
    exp = [t_ev] + ([t_ev + pd.Timedelta(minutes=1)] if lag > 90 else [])
    assert list(ev["t"]) == exp
    if lag > 90:
        assert ev["families"].iloc[1] == "gap"
    assert "lead" in ev["families"].iloc[0] and "gap" not in ev["families"].iloc[0]
    pc = res.per_config.set_index("config")
    assert pc.loc["upbit_lead_impulse(k=1,thr_bp=20) & session_kst(session=all_but_quiet)", "n_events"] == 1
    r = ev.iloc[0]
    assert bool(r["measurable"])
    # backtest open = first Coinone print in minute t (t+5 s, hits the pre-jump bid)
    assert r["a_open"] == P - CO_HALF and r["open_delay_s"] == pytest.approx(5.0)
    exp_ask = P + CO_HALF                                # Coinone has not moved at t+L (lag > 30 s + 2 s)
    for L in (0.5, 1, 2):
        assert r[f"ask_L{L:g}"] == exp_ask
        assert r[f"slip_top_bp_L{L:g}"] == pytest.approx((exp_ask / (P - CO_HALF) - 1) * 1e4)
        vw = es.walk_buy(np.array([_levels(exp_ask, 1)]), np.full((1, 15), LEVEL_QTY), es.DEFAULT_NOTIONAL)[0]
        assert r[f"slip_walk_bp_L{L:g}"] == pytest.approx((vw / (P - CO_HALF) - 1) * 1e4)
        assert r[f"slip_walk_bp_L{L:g}"] > r[f"slip_top_bp_L{L:g}"]
        assert r[f"closed_before_L{L:g}"] == pytest.approx(0.0)
    assert r["deficit_bp"] == pytest.approx(np.log(1 + JUMP_BP / 1e4) * 1e4, rel=1e-6)
    assert not r["already_caught_up"]
    # Coinone book moves at JUMP_S + lag; first 1 s grid point whose last received snapshot is post-jump
    exp_t = (JUMP_S + lag) - 101 * 60 + 1
    assert r["t50_s"] == exp_t and r["t90_s"] == exp_t
    assert r["upbit_ret_prev60_bp"] == pytest.approx(JUMP_BP, rel=1e-6)
    assert r["coinone_ret_prev60_bp"] == pytest.approx(0.0)
    # forward returns, h = 5: backtest open -> close of minute 105 (post-jump bid print)
    c5 = P * (1 + JUMP_BP / 1e4) - CO_HALF
    a = P - CO_HALF
    assert r["bt_gross_bp_h5"] == pytest.approx((c5 / a - 1) * 1e4)
    assert r["bt_net_bp_h5"] == pytest.approx(COST.net_return(COST.buy_fill(a), COST.sell_fill(c5)) * 1e4)
    e = es.walk_buy(np.array([_levels(exp_ask, 1)]), np.full((1, 15), LEVEL_QTY), es.DEFAULT_NOTIONAL)[0]
    bid5 = P * (1 + JUMP_BP / 1e4) - CO_HALF
    x = es.walk_sell(np.array([_levels(bid5, -1)]), np.full((1, 15), LEVEL_QTY), es.DEFAULT_NOTIONAL / e)[0]
    assert r["exec_book_net_bp_h5"] == pytest.approx((x / e - 1) * 1e4)
    assert r["exec_model_net_bp_h5"] == pytest.approx((COST.sell_fill(c5) / e - 1) * 1e4)
    # 1 s path: Coinone flat until the catch-up, then +JUMP
    S = res.ev_meas.path_s
    co = res.ev_meas.co_path[0]
    assert np.allclose(co[(S >= 0) & (S < exp_t)], 0.0)
    assert co[S == exp_t][0] == pytest.approx(JUMP_BP, rel=1e-6)
    # control: same calendar hour, >= 5 min from any trigger, measured the same way
    ctl = res.control
    assert len(ctl) > 10
    assert (pd.DatetimeIndex(ctl["t"]).floor("h") == t_ev.floor("h")).all()
    trig = pd.DatetimeIndex(res.events["t"])
    assert (np.abs((pd.DatetimeIndex(ctl["t"]) - trig[0]).total_seconds()) >= 300).all()
    flat = ctl[pd.DatetimeIndex(ctl["t"]) < T0 + pd.Timedelta(minutes=95)]
    assert np.allclose(flat["slip_top_bp_L1"], (exp_ask / (P - CO_HALF) - 1) * 1e4)   # flat market: 1 bp
    assert flat["deficit_bp"].abs().max() < 1e-6 and flat["already_caught_up"].all()
    md = es.render_markdown(res)
    assert "INSUFFICIENT_DATA" in md and "| upbit |" in md and "Re-run" in md


def test_lag_shorter_than_bar_gives_no_event(make_root):
    """Coinone catches up within the impulse minute (lag 20 s -> its last print of minute 100 is post-jump):
    the lead block must not fire."""
    res = run(make_root(20))
    assert res.events.empty or not (pd.DatetimeIndex(res.events["t"]) == T0 + pd.Timedelta(minutes=101)).any()
    assert res.verdict["status"] == "INSUFFICIENT_DATA"
    es.render_markdown(res)


def test_bootstrap_and_verdict_rules():
    m, lo, hi, n = es.boot_mean(np.r_[np.ones(50), np.nan])
    assert (m, lo, hi, n) == (1.0, 1.0, 1.0, 50)
    d = es.boot_diff(np.full(40, 3.0) + np.linspace(-1, 1, 40), np.zeros(40))
    assert d[0] == pytest.approx(3.0) and d[1] > 2.5
    params = {"primary_latency": 1.0, "cost": {"entry_side_bp": 2.0}, "horizons": [1, 3, 5]}
    ev = pd.DataFrame({"measurable": True, "slip_walk_bp_L1": np.linspace(7, 9, 40),
                       "exec_book_net_bp_h5": np.linspace(-6, -2, 40), "bt_net_bp_h5": 1.0})
    v = es.make_verdict(ev, ev, params, {"eligible_hours": 10.0})
    assert v["status"] == "NOT_EXECUTABLE" and v["extra_vs_model_bp"] == pytest.approx(6.0)
    v = es.make_verdict(ev.head(5), ev, params, {"eligible_hours": 10.0})
    assert v["status"] == "INSUFFICIENT_DATA"


def test_event_selection_matches_signal_study():
    """Same events (count, first/last) as research `signal_study.study_signal` on the same prepared bars."""
    from btc_quant.research.campaign import aux_context, prepare_m1
    from btc_quant.research.signal_study import ScreenTemplate, study_signal
    rng = np.random.default_rng(7)
    n = 3000
    idx = pd.date_range(T0, periods=n, freq="1min", name="ts")
    up = P * np.exp(np.cumsum(rng.normal(0, 8e-4, n)))
    co = np.r_[up[0], up[:-1]] * np.exp(rng.normal(0, 3e-4, n))           # Coinone lags Upbit by 1 bar
    def raw(c):
        o = np.r_[c[0], c[:-1]]
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.0002, "low": np.minimum(o, c) * 0.9998,
                             "close": c, "volume": 1.0}, index=idx)
    r_co = raw(co).drop(idx[rng.choice(n, 150, replace=False)])               # some no-trade minutes
    co_m1, up_m1 = prepare_m1(r_co, "coinone"), prepare_m1(raw(up), "upbit")
    tpl = ScreenTemplate.from_yaml(REPO / es.DEFAULT_TEMPLATE)
    sig = es.signal_events(co_m1, up_m1, tpl.configs(), tpl.min_gap_min, horizons=(1, 3, 5))
    ctx = aux_context({"upbit": up_m1}, 1)
    total = 0
    for (e, fs), (_, row) in zip(tpl.configs(), sig.per_config.iterrows()):
        st = study_signal(co_m1, e, fs, bar_minutes=1, horizons=[1, 3, 5], primary_horizons=[5], ctx=ctx,
                          n_null=20, min_gap_min=tpl.min_gap_min, min_events=1)
        mine = sig.events[sig.events["config"] == row["config"]]
        assert st["n_events"] == row["n_events"] == len(mine)
        assert st["n_raw_triggers"] == row["n_triggers"]
        if len(mine):
            assert pd.Timestamp(st["first_event"]) == mine["t"].min()
            assert pd.Timestamp(st["last_event"]) == mine["t"].max()
        total += len(mine)
    assert total > 20


def test_quiet_session_diagnostics_and_baseline(make_root):
    """Same planted 60 s lag but inside KST 03-08 (session_kst all_but_quiet closed): no pre-registered events,
    the filter-dropped diagnostic still finds the lead event, the baseline measures every eligible minute,
    and the verdict falls back to baseline dispersion and the Dev prior rate."""
    start = pd.Timestamp("2026-11-09 20:00", tz="UTC")          # KST 05:00
    root = make_root(60, start)
    res = es.run_study(start, start + pd.Timedelta(minutes=N_MIN), root=root, rest_warmup=False, gaps_path=None)
    assert res.events.empty and res.coverage["eligible_minutes"] == 0
    t_ev = start + pd.Timedelta(minutes=101)
    de = res.diag_events
    assert list(de["t"]) == [t_ev] and "lead" in de["families"].iloc[0]
    r = de.iloc[0]
    assert r["slip_top_bp_L1"] == pytest.approx(((P + CO_HALF) / (P - CO_HALF) - 1) * 1e4)
    assert r["t50_s"] == (JUMP_S + 60) - 101 * 60 + 1
    bl = res.baseline
    assert len(bl) == res.coverage["unfiltered_minutes"] > 50
    flat = bl[pd.DatetimeIndex(bl["t"]) < start + pd.Timedelta(minutes=95)]
    assert np.allclose(flat["slip_top_bp_L1"], ((P + CO_HALF) / (P - CO_HALF) - 1) * 1e4)
    v = res.verdict
    assert v["status"] == "INSUFFICIENT_DATA" and v["rate_source"] == "dev_prior"
    assert v["sd_source"] == "baseline" and v["n_baseline"] == bl["measurable"].sum()
    md = es.render_markdown(res)
    assert "Diagnostics outside the pre-registered selection" in md and "Unfiltered events:" in md
    no = es.run_study(start, start + pd.Timedelta(minutes=N_MIN), root=root, rest_warmup=False, gaps_path=None,
                      diagnostics=False)
    assert no.baseline is None and "Diagnostics outside" not in es.render_markdown(no)
