"""Limit-order (maker) fill model calibration from the live WebSocket data (Forward segment only).

    uv run python -m btc_quant.research.fill_model [--since ISO] [--until ISO] [--exchange coinone]
        [--offsets 0,2,5,10,20] [--tifs 1,5,15] [--latency 1] [--notional 300000] [--report PATH]

Question: the backtest fills a resting limit only when the 1m candle trades THROUGH the limit
(`backtest.types.LimitFillModel`: buy fills in minute k iff low_k <= L - max(m*tick, L*d/1e4), price = L).
Is that rule conservative against what a real order at the back of the queue would have experienced, and how
adverse are maker fills? Re-run as more WS data accumulates (the report states the sample size and status).

Method (no ledger trial: a measurement of execution, no strategy hypothesis is tested)
1. Data: collector Parquet ``data/raw/exchange=<ex>/symbol=BTC-KRW/{trades,orderbook}/date=*/HH-*.parquet``
   via `execution_study.load_ws` (Forward-start guard; lockbox / Val never read). Bars: trades replayed through the
   paper `MinuteBarBuilder` (`execution_study.build_bars`), then `quality.prepare_venue_m1` = what the backtest and
   the paper trader see (wick cleaning, filled minutes).
2. Hypothetical orders: at every live minute i (decision minute) with a real bar and a book snapshot, for each side
   (buy below / sell above) and offset delta (bp) a limit at P = snap(o_i * (1 -/+ delta)) on the venue tick grid
   (tick inferred from trade prices; buy rounds down, sell up), placed at tau = first trade of minute i + latency
   (the engine's reference price is o_i, unknown before that trade). Also at mean-reversion signal minutes
   (`bollinger_z_revert(window=20, z=2)` on 1m bars, signal on bar i-1 -> buy at i).
3. Realistic fill (conservative queue, `backtest.queue.simulate_order`): we join the back of the queue at P with the
   displayed size A ahead of us (snapshot at tau); filled when trades at P against our side exceed A + our size, or
   at the first trade through P. No credit for cancellations ahead. An optimistic-queue variant (cancellations
   credited) and the touch rule (any trade at P) bracket it.
4. Candle rules on the prepared 1m bars over minutes i .. i+TIF-1: touch (low <= P), 1 tick through, and
   max(1 tick, d bp) through for d in a grid. "Precision" = P(queue fill no later than the end of the candle-rule
   fill minute | candle-rule fill) — the candle rule is safe when this is ~1 (it never books a fill reality would
   not have given, nor earlier). "Recall" = P(candle fill | queue fill) (how many real fills the rule leaves out).
5. Adverse selection: markouts (bp, + = good for the maker) of the Coinone mid at fill + 1/5/15 min vs P for queue
   fills; for candle fills from the end of the fill minute (what the backtest sees); for real fills the candle rule
   misses (their sign says whether omitting them biases the backtest); and the unconditional mid drift.
6. Uncertainty: block bootstrap over 30-min blocks of placement time (orders overlap in time) -> 95% CIs.
7. Recommendation (class 1 = market measurement): the smallest through depth d whose precision lower bound is >=
   0.95 with fill_prob = 1; otherwise the largest d with fill_prob = the precision lower bound. Data status
   (provisional / final) uses the cost-calibration thresholds (`params.calibrate`: 72 h, 24 KST hours, 7 days).
   There is no params-registry entry for the fill model yet, so the result is a proposal; the stricter-only
   auto-apply rule would apply once `LimitFillModel` values are registered as class-1 parameters.
8. Paper-path check: `ReferenceEngine` replayed over the live window twice for a toy maker strategy (candle rule vs
   `QueueFillOracle` = the paper hook) -> trade counts and net returns.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from btc_quant.backtest.queue import (
    BookArrays,
    QueueFillOracle,
    TradeTape,
    simulate_order,
    snap_price,
)
from btc_quant.backtest.reference import ReferenceEngine
from btc_quant.backtest.signals import infer_ticks
from btc_quant.backtest.types import LIMIT_FILL_DEFAULT, CostModel, LimitFillModel
from btc_quant.data.quality import prepare_venue_m1
from btc_quant.strategy.blocks import REGISTRY
from btc_quant.strategy.blocks.base import Context
from btc_quant.strategy.features import to_tf
from btc_quant.strategy.spec import BlockRef, ExecutionSpec

from .execution_study import (
    Book,
    ExecutionStudyError,
    _utc,
    build_bars,
    forward_start,
    load_gaps,
    load_ws,
)

NS = 1_000_000_000
NS_MIN = 60 * NS
DEFAULT_OFFSETS_BP = (0.0, 2.0, 5.0, 10.0, 20.0)
DEFAULT_TIFS = (1, 5, 15)
THROUGH_BP_GRID = (1.0, 1.5, 2.0, 3.0, 5.0)     # rule "d bp" = max(1 tick, d bp) through
MARKOUT_MIN = (1, 5, 15)
LATENCY_S = 1.0
NOTIONAL = 300_000.0
BLOCK_MIN = 30
N_BOOT = 2000
TARGET_PRECISION = 0.95
SIGNAL = BlockRef("bollinger_z_revert", {"window": 20, "z": 2.0})
TICK_CANDIDATES = (100_000.0, 50_000.0, 10_000.0, 5_000.0, 1_000.0, 500.0, 100.0, 50.0, 10.0, 5.0, 1.0)


def rules(grid=THROUGH_BP_GRID) -> list[tuple[str, int, float | None]]:
    """(name, m ticks, d bp): margin = max(m × tick, d bp); touch = no margin."""
    return [("touch", 0, None), ("1tick", 1, 0.0), ("2tick", 2, 0.0)] + [(f"{d:g}bp", 1, float(d)) for d in grid]


def rule_names(grid=THROUGH_BP_GRID) -> list[str]:
    return [r[0] for r in rules(grid)]


def infer_tick(prices: np.ndarray, share: float = 0.999) -> float:
    """Largest candidate tick that divides >= `share` of the trade prices."""
    p = np.asarray(prices, float)
    p = p[np.isfinite(p)]
    for t in TICK_CANDIDATES:
        if p.size and np.mean(np.abs(p / t - np.round(p / t)) < 1e-9) >= share:
            return t
    return 0.0


def _thr(price: float, tick: float, m: int, d_bp: float | None) -> float:
    """Candle-rule trade-through margin: touch (d None) = 0, else max(m × tick, d bp) (= LimitFillModel.threshold)."""
    if d_bp is None:
        return 0.0
    return max(m * tick, price * d_bp * 1e-4)


# ============================================================== order generation + measurement
@dataclass
class Window:
    m1: pd.DataFrame                 # prepared 1m bars (backtest view)
    first_trade_ns: dict             # epoch minute -> first trade exchange ns
    trades: TradeTape
    book: BookArrays
    tick: float
    coverage: dict = field(default_factory=dict)


def placement_minutes(w: Window, max_tif: int) -> pd.DatetimeIndex:
    """Live minutes with a real bar (not filled), a first trade, a book snapshot at tau and max_tif minutes of data."""
    m1 = w.m1
    real = m1.index[~m1["filled"].to_numpy(bool)]
    last = m1.index[-1] - pd.Timedelta(minutes=max_tif)
    out = []
    for t in real:
        if t > last:
            break
        m = int(t.value // NS_MIN)
        if m in w.first_trade_ns:
            out.append(t)
    idx = pd.DatetimeIndex(out)
    if len(idx):
        tau = np.array([w.first_trade_ns[int(t.value // NS_MIN)] for t in idx], np.int64)
        ok = w.book.idx_at(tau) >= 0
        idx = idx[ok]
    return idx


def signal_minutes(m1: pd.DataFrame, ref: BlockRef = SIGNAL) -> pd.DatetimeIndex:
    """Decision minutes i whose bar i-1 fires the mean-reversion block (1m bars, complete)."""
    bars = to_tf(m1, 1)
    sig = REGISTRY.evaluate(ref, bars, Context()).to_numpy(bool) & bars["complete"].to_numpy(bool)
    return pd.DatetimeIndex(bars.index[sig] + pd.Timedelta(minutes=1))


def measure_orders(w: Window, minutes: pd.DatetimeIndex, sides=("buy", "sell"), offsets=DEFAULT_OFFSETS_BP,
                   max_tif: int = max(DEFAULT_TIFS), latency_s: float = LATENCY_S, notional: float = NOTIONAL,
                   grid=THROUGH_BP_GRID, markouts=MARKOUT_MIN) -> pd.DataFrame:
    m1 = w.m1
    pos = {t: i for i, t in enumerate(m1.index)}
    lo = m1["low"].to_numpy(float)
    hi = m1["high"].to_numpy(float)
    filled = m1["filled"].to_numpy(bool)
    mid = w.book.mid
    rl = rules(grid)
    names = [r[0] for r in rl]
    rows = []
    for t in minutes:
        i = pos.get(t)
        if i is None:
            continue
        m = int(t.value // NS_MIN)
        tau = int(w.first_trade_ns[m] + latency_s * NS)
        t0_ns = m * NS_MIN
        end_ns = t0_ns + max_tif * NS_MIN
        o = float(m1["open"].iat[i])
        jb = int(w.book.idx_at(np.array([tau]))[0])
        mid_tau = float(mid[jb]) if jb >= 0 else math.nan
        k_hi = min(len(m1), i + max_tif)
        for side in sides:
            sgn = 1.0 if side == "buy" else -1.0
            for off in offsets:
                raw = o * (1 - sgn * off * 1e-4)
                P = snap_price(raw, w.tick, side)
                qty = notional / P
                f_ns, how, status = simulate_order(side, P, qty, tau, end_ns, w.trades, w.book)
                fo_ns, _, _ = simulate_order(side, P, qty, tau, end_ns, w.trades, w.book, optimistic=True)
                r = {"t": t, "tau_ns": tau, "side": side, "offset_bp": off, "price": P, "ref": o,
                     "mid_tau": mid_tau, "status": status, "q_fill_ns": f_ns, "q_how": how,
                     "qo_fill_ns": fo_ns, "block": m // BLOCK_MIN}
                for name, mt, d in rl:
                    thr = _thr(P, w.tick, mt, d)
                    kk = -1
                    for k in range(i, k_hi):
                        if filled[k]:
                            continue
                        if (lo[k] <= P - thr) if side == "buy" else (hi[k] >= P + thr):
                            kk = k - i
                            break
                    r[f"k_{name}"] = kk
                # markouts: queue fill (from fill time), and unconditional mid drift from tau
                for h in markouts:
                    if f_ns >= 0:
                        j = int(w.book.idx_at(np.array([f_ns + h * NS_MIN]))[0])
                        r[f"mo_q_{h}"] = sgn * (mid[j] / P - 1) * 1e4 if j >= 0 else math.nan
                    else:
                        r[f"mo_q_{h}"] = math.nan
                    j = int(w.book.idx_at(np.array([tau + h * NS_MIN]))[0])
                    r[f"drift_{h}"] = sgn * (mid[j] / mid_tau - 1) * 1e4 if (j >= 0 and not math.isnan(mid_tau)) \
                        else math.nan
                    for name in names:
                        kk = r[f"k_{name}"]
                        if kk >= 0:
                            q = (m + kk + 1) * NS_MIN + h * NS_MIN
                            j = int(w.book.idx_at(np.array([q]))[0])
                            r[f"mo_{name}_{h}"] = sgn * (mid[j] / P - 1) * 1e4 if j >= 0 else math.nan
                        else:
                            r[f"mo_{name}_{h}"] = math.nan
                rows.append(r)
    return pd.DataFrame(rows)


def expand_tifs(orders: pd.DataFrame, tifs=DEFAULT_TIFS, grid=THROUGH_BP_GRID) -> pd.DataFrame:
    """One row per (order, TIF) with fill indicators for the queue sims and each candle rule."""
    out = []
    for T in tifs:
        d = orders.copy()
        d["tif"] = T
        t0 = pd.DatetimeIndex(d["t"]).as_unit("ns").asi8
        tend = t0 + T * NS_MIN
        d["q_fill"] = (d["q_fill_ns"] >= 0) & (d["q_fill_ns"] < tend)
        d["qo_fill"] = (d["qo_fill_ns"] >= 0) & (d["qo_fill_ns"] < tend)
        for name in rule_names(grid):
            k = d[f"k_{name}"].to_numpy()
            fill = (k >= 0) & (k < T)
            d[f"c_{name}"] = fill
            # timed: the real fill happened no later than the end of the candle-rule fill minute
            d[f"ct_{name}"] = fill & (d["q_fill_ns"].to_numpy() >= 0) & \
                (d["q_fill_ns"].to_numpy() < t0 + (k + 1) * NS_MIN)
        out.append(d)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# ============================================================== statistics
def block_ratio(num, den, blocks, n_boot: int = N_BOOT, seed: int = 0) -> tuple[float, float, float, int]:
    """sum(num)/sum(den) with a block bootstrap CI (blocks = cluster ids). den is a 0/1 mask."""
    num = np.asarray(num, float)
    den = np.asarray(den, float)
    ok = np.isfinite(num) & (den > 0)
    num, den, blocks = num[ok], den[ok], np.asarray(blocks)[ok]
    n = int(den.sum())
    if n == 0:
        return (math.nan, math.nan, math.nan, 0)
    est = float(num.sum() / den.sum())
    ub, inv = np.unique(blocks, return_inverse=True)
    if len(ub) < 2:
        return (est, math.nan, math.nan, n)
    sn = np.bincount(inv, weights=num, minlength=len(ub))
    sd = np.bincount(inv, weights=den, minlength=len(ub))
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(ub), (n_boot, len(ub)))
    with np.errstate(invalid="ignore", divide="ignore"):
        bs = sn[pick].sum(axis=1) / sd[pick].sum(axis=1)
    bs = bs[np.isfinite(bs)]
    return (est, float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975)), n)


def prop(d: pd.DataFrame, col: str, given: str | None = None, seed: int = 0):
    """P(col | given) with block CI."""
    g = d[given].to_numpy(bool) if given else np.ones(len(d), bool)
    return block_ratio(d[col].to_numpy(bool).astype(float) * g, g.astype(float), d["block"].to_numpy(), seed=seed)


def mean_ci(d: pd.DataFrame, col: str, mask=None, seed: int = 0):
    x = d[col].to_numpy(float)
    m = np.ones(len(d), bool) if mask is None else np.array(mask, bool)
    m &= np.isfinite(x)
    return block_ratio(np.where(m, x, 0.0), m.astype(float), d["block"].to_numpy(), seed=seed)


def recommend(pairs: pd.DataFrame, grid=THROUGH_BP_GRID, target: float = TARGET_PRECISION,
              current: LimitFillModel = LIMIT_FILL_DEFAULT) -> dict:
    """Least strict rule (in the order 1 tick, 2 ticks, max(1 tick, d bp) ascending) whose timed precision lower CI
    >= target, with fill_prob 1; otherwise the strictest rule with fill_prob = floor_0.05(its lower bound).
    Direction vs the current default (through_ticks, through_bp, fill_prob): stricter / looser / equal / mixed."""
    rows = []
    for name, mt, d in rules(grid)[1:]:
        est, lo, hi, n = prop(pairs, f"ct_{name}", f"c_{name}")
        rows.append({"rule": name, "m": mt, "d_bp": d, "precision": est, "lo": lo, "hi": hi, "n": n})
    tab = pd.DataFrame(rows)
    ok = tab[tab["lo"] >= target]
    if len(ok):
        r = ok.iloc[0]
        rec = {"through_ticks": int(r["m"]), "through_bp": float(r["d_bp"]), "fill_prob": 1.0, "basis": r["rule"],
               "precision_lo": float(r["lo"])}
    else:
        valid = tab[np.isfinite(tab["lo"])]
        if len(valid):
            r = valid.iloc[-1]
            p = max(0.05, math.floor(float(r["lo"]) * 20) / 20)
            rec = {"through_ticks": int(r["m"]), "through_bp": float(r["d_bp"]), "fill_prob": p, "basis": r["rule"],
                   "precision_lo": float(r["lo"])}
        else:
            rec = {"through_ticks": int(current.through_ticks), "through_bp": float(current.through_bp),
                   "fill_prob": float(current.fill_prob), "basis": "no data", "precision_lo": math.nan}
    stricter = (rec["through_ticks"] > current.through_ticks or rec["through_bp"] > current.through_bp
                or rec["fill_prob"] < current.fill_prob)
    looser = (rec["through_ticks"] < current.through_ticks or rec["through_bp"] < current.through_bp
              or rec["fill_prob"] > current.fill_prob)
    rec["direction"] = ("mixed" if stricter and looser else "stricter" if stricter else
                        "looser" if looser else "equal")
    rec["table"] = tab
    return rec


# ============================================================== paper-path replay (engine + oracle)
def replay(w: Window, offset_bp: float = 5.0, tif_min: int = 5, tp_bp: float = 10.0, sl_bp: float = 30.0,
           hold_min: int = 30, entry_minutes: pd.DatetimeIndex | None = None, latency_s: float = LATENCY_S,
           fill: LimitFillModel = LIMIT_FILL_DEFAULT) -> dict:
    """Toy maker strategy on the live window (1m decisions): buy limit offset_bp below o_i for tif_min minutes,
    TP limit at +tp_bp, SL -sl_bp market, max hold. Entry at every live minute (grid-like) or at entry_minutes.
    Run twice: candle rule (backtest) vs QueueFillOracle (paper hook). Cost: zero spread/slip (maker), Coinone 0 fee,
    stops/time exits at the 1m open with zero cost (comparison of fill logic only)."""
    zero = CostModel("zero", 0.0, 0.0, 0.0, 0.0)
    ex = ExecutionSpec("limit", offset_bp=offset_bp, tif_bars=tif_min, take_profit="limit")
    ticks = infer_ticks(w.m1)          # = run_reference_signals (backtest); paper passes the venue tick
    out = {}
    for label in ("candle", "queue"):
        eng = ReferenceEngine(sl_bp / 100.0, tp_bp / 100.0, hold_min, zero, timeframe_min=1, execution=ex,
                              fill=fill)
        if label == "queue":
            eng.fill_oracle = QueueFillOracle.from_arrays(w.trades, w.book, latency_s=latency_s)
        want = set(entry_minutes) if entry_minutes is not None else None
        m1 = w.m1
        for ts, r, tk in zip(m1.index, m1.itertuples(index=False), ticks):
            eng.on_decision(ts, want is None or ts in want, False)
            eng.on_bar_1m(ts, r.open, r.high, r.low, r.close, bool(r.filled), r.volume, tk)
        eng.finish(m1.index[-1], float(m1["close"].iloc[-1]))
        t = eng.trades_frame()
        out[label] = t
    a, b = out["candle"], out["queue"]
    same = len(set(a["entry_time"]) & set(b["entry_time"])) if len(a) and len(b) else 0
    return {"candle": a, "queue": b, "same_entries": same,
            "params": {"offset_bp": offset_bp, "tif_min": tif_min, "tp_bp": tp_bp, "sl_bp": sl_bp,
                       "hold_min": hold_min}}


# ============================================================== driver
@dataclass
class FillStudy:
    params: dict
    coverage: dict
    orders: pd.DataFrame
    pairs: pd.DataFrame
    sig_pairs: pd.DataFrame
    rec: dict
    replays: dict
    status: str


def load_window(since=None, until=None, root: str | Path = ".", data_root=None, exchange: str = "coinone",
                gaps_path: str | Path | None = "runtime/collector_gaps.jsonl") -> Window:
    root = Path(root)
    data_root = Path(data_root) if data_root is not None else root / "data" / "raw"
    fwd = forward_start(root)
    since = _utc(since) if since is not None else fwd
    if since < fwd:
        raise ExecutionStudyError(f"--since {since} is before the Forward start {fwd}: refusing (Forward data only)")
    until = _utc(until) if until is not None else pd.Timestamp.now(tz="UTC").ceil("min")
    tr = load_ws(data_root, exchange, "trades", since, until, fwd)
    ob = load_ws(data_root, exchange, "orderbook", since, until, fwd)
    if tr.empty or ob.empty:
        raise ExecutionStudyError(f"no live {exchange} trades/orderbook in [{since}, {until})")
    gp = None if gaps_path is None else (root / gaps_path if not Path(gaps_path).is_absolute() else Path(gaps_path))
    gaps = load_gaps(gp).get(exchange, [])
    bb = build_bars(tr)
    m1 = prepare_venue_m1(bb.raw, exchange)
    book = BookArrays.from_book(Book.from_frame(ob, gaps=gaps))
    tape = TradeTape.from_frame(tr)
    tick = infer_tick(tape.price)
    ft = {int(k.value // NS_MIN): int(v.value) for k, v in bb.first_trade.items()}
    # side sanity: taker sells should print at/below the prevailing best bid
    jb = book.idx_at(tape.t)
    okb = jb >= 0
    bid = np.where(okb, book.bid_px[np.clip(jb, 0, None), 0], np.nan)
    ask = np.where(okb, book.ask_px[np.clip(jb, 0, None), 0], np.nan)
    s = tape.taker_sell & okb
    b = ~tape.taker_sell & okb
    hours = sorted({int(x) // 3600 for x in (tape.t // NS)})
    kst = sorted({(h + 9) % 24 for h in hours})
    cov = {"forward_start": str(fwd), "since": str(since), "until": str(until), "exchange": exchange,
           "trades": len(tape.t), "book_snapshots": len(book.t), "live_minutes": len(m1),
           "live_real_minutes": int((~m1["filled"]).sum()),
           "first": str(m1.index[0]) if len(m1) else None, "last": str(m1.index[-1]) if len(m1) else None,
           "hours_observed": len(hours), "kst_hours": len(kst),
           "days": len({h // 24 for h in hours}), "tick": tick,
           "median_price": float(np.median(tape.price)) if len(tape.price) else math.nan,
           "taker_sell_at_or_below_bid": float(np.mean(tape.price[s] <= bid[s] + 1e-6)) if s.any() else math.nan,
           "taker_buy_at_or_above_ask": float(np.mean(tape.price[b] >= ask[b] - 1e-6)) if b.any() else math.nan,
           "collector_gaps": len(gaps)}
    return Window(m1, ft, tape, book, tick, cov)


def data_status(cov: dict, root=".") -> str:
    try:
        from btc_quant.params.calibrate import load_settings
        st = load_settings(root)
    except Exception:  # noqa: BLE001
        st = {"min_hours_final": 72, "min_kst_hours_final": 24, "min_days_final": 7, "min_hours_provisional": 2}
    h = cov["hours_observed"]
    if h < st.get("min_hours_provisional", 2):
        return "insufficient"
    if h >= st["min_hours_final"] and cov["kst_hours"] >= st["min_kst_hours_final"] and cov["days"] >= st["min_days_final"]:
        return "final"
    return "provisional"


def run_fill_study(since=None, until=None, root: str | Path = ".", data_root=None, exchange: str = "coinone",
                   offsets=DEFAULT_OFFSETS_BP, tifs=DEFAULT_TIFS, latency_s: float = LATENCY_S,
                   notional: float = NOTIONAL, grid=THROUGH_BP_GRID, replay_runs: bool = True,
                   window: Window | None = None) -> FillStudy:
    w = window if window is not None else load_window(since, until, root, data_root, exchange)
    tifs = tuple(sorted(int(t) for t in tifs))
    mins = placement_minutes(w, max(tifs))
    orders = measure_orders(w, mins, offsets=offsets, max_tif=max(tifs), latency_s=latency_s, notional=notional,
                            grid=grid)
    sig_t = signal_minutes(w.m1).intersection(mins)
    sig_orders = measure_orders(w, sig_t, sides=("buy",), offsets=offsets, max_tif=max(tifs), latency_s=latency_s,
                                notional=notional, grid=grid)
    pairs = expand_tifs(orders, tifs, grid) if len(orders) else pd.DataFrame()
    sig_pairs = expand_tifs(sig_orders, tifs, grid) if len(sig_orders) else pd.DataFrame()
    cov = dict(w.coverage)
    cov["placements"] = len(mins)
    cov["signal_placements"] = len(sig_t)
    rec = recommend(pairs[pairs["status"] != "cross"], grid) if len(pairs) else \
        {"through_ticks": LIMIT_FILL_DEFAULT.through_ticks, "through_bp": LIMIT_FILL_DEFAULT.through_bp,
         "fill_prob": LIMIT_FILL_DEFAULT.fill_prob, "basis": "no data", "precision_lo": math.nan,
         "direction": "equal", "table": pd.DataFrame()}
    reps = {}
    if replay_runs and len(w.m1) > 30:
        reps["grid_5bp"] = replay(w, offset_bp=5.0, tif_min=5, tp_bp=10.0, sl_bp=30.0, hold_min=30, latency_s=latency_s)
        if len(sig_t):
            reps["mr_signal_5bp"] = replay(w, offset_bp=5.0, tif_min=5, tp_bp=10.0, sl_bp=30.0, hold_min=30,
                                           entry_minutes=sig_t, latency_s=latency_s)
    params = {"offsets_bp": list(offsets), "tifs_min": list(tifs), "latency_s": latency_s, "notional_krw": notional,
              "through_bp_grid": list(grid), "markouts_min": list(MARKOUT_MIN), "block_min": BLOCK_MIN,
              "signal": f"{SIGNAL.block}({', '.join(f'{k}={v}' for k, v in SIGNAL.params.items())}) on 1m bars",
              "target_precision": TARGET_PRECISION,
              "current_model": {"through_ticks": LIMIT_FILL_DEFAULT.through_ticks,
                                "tick_from_data": LIMIT_FILL_DEFAULT.tick_from_data,
                                "through_bp": LIMIT_FILL_DEFAULT.through_bp,
                                "fill_prob": LIMIT_FILL_DEFAULT.fill_prob,
                                "min_volume": LIMIT_FILL_DEFAULT.min_volume}}
    return FillStudy(params, cov, orders, pairs, sig_pairs, rec, reps, data_status(cov, root))


# ============================================================== report
def _f(x, nd=2) -> str:
    return "–" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:.{nd}f}"


def _ci(t, nd=2, pct=False) -> str:
    est, lo, hi, n = t
    if n == 0 or not math.isfinite(est):
        return "– (0)"
    k = 100.0 if pct else 1.0
    if math.isfinite(lo):
        return f"{est * k:.{nd}f} [{lo * k:.{nd}f}, {hi * k:.{nd}f}] ({n})"
    return f"{est * k:.{nd}f} ({n})"


def _cip(t) -> str:
    """Proportion as 'x.x % [lo, hi] (n = N)'."""
    est, lo, hi, n = t
    if n == 0 or not math.isfinite(est):
        return "– (n = 0)"
    ci = f" [{lo * 100:.1f}, {hi * 100:.1f}]" if math.isfinite(lo) else ""
    return f"{est * 100:.1f} %{ci} (n = {n})"


def key_findings(res: FillStudy, rule: str = "1tick", h: int = 5) -> str:
    """Auto-generated summary of the main numbers (engine default rule)."""
    pm = res.pairs[res.pairs["status"] != "cross"]
    T = max(res.params["tifs_min"])
    d = pm[pm["tif"] == T]
    mk = d["q_fill"].to_numpy(bool)
    cm = d[f"c_{rule}"].to_numpy(bool)
    tp = prop(pm, "ct_touch", "c_touch")
    rp = prop(pm, f"ct_{rule}", f"c_{rule}")
    rr = prop(pm, f"c_{rule}", "q_fill")
    q1, q5 = mean_ci(d, "mo_q_1", mk), mean_ci(d, f"mo_q_{h}", mk)
    c1, c5 = mean_ci(d, f"mo_{rule}_1", cm), mean_ci(d, f"mo_{rule}_{h}", cm)
    miss = mean_ci(d, f"mo_q_{h}", mk & ~cm)
    dr = mean_ci(d, f"drift_{h}")
    out = ["**Key findings (preliminary):**\n",
           (f"- Touch rule is optimistic: only {_cip(tp)} of touch-rule fills had really filled by the end "
            "of that minute (queue ahead of us)."),
           (f"- `{rule}` trade-through (engine default): precision {_cip(rp)}, recall {_cip(rr)} "
            "of real (conservative-queue) fills."),
           (f"- Adverse selection (TIF {T}): real maker fills mark out {_f(q1[0])} bp at 1 min and {_f(q5[0])} bp at "
            f"{h} min vs unconditional drift {_f(dr[0])} bp; the candle rule's fills as the backtest sees them "
            f"{_f(c1[0])} / {_f(c5[0])} bp (more adverse = conservative if lower)."),
           (f"- Real fills the `{rule}` rule leaves out mark out {_ci(miss)} bp at {h} min (positive = omitting them "
            "is conservative: they are touch-and-bounce fills, the maker's best ones).\n")]
    return "\n".join(out)


def render_markdown(res: FillStudy) -> str:
    c, p, rec = res.coverage, res.params, res.rec
    pairs = res.pairs
    names = rule_names(p["through_bp_grid"])
    L = []
    L.append("# Limit-order fill model — candle trade-through rule vs queue simulation (live WS data)\n")
    L.append(f"Generated {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC by `python -m btc_quant.research.fill_model`. "
             f"Forward segment only (Forward start {c['forward_start']}); lockbox / Val not read. Not a ledger trial "
             "(execution measurement; no strategy hypothesis tested).\n")
    L.append(f"**Data status: {res.status.upper()}** — {c['hours_observed']} UTC hours observed "
             f"({c['kst_hours']} distinct KST hours, {c['days']} day(s)); final needs ≥ 72 h, ≥ 24 KST hours, ≥ 7 days "
             "(cost-calibration thresholds). Every number below is preliminary and comes from one short regime.\n")
    cur = p["current_model"]
    L.append(f"**Recommendation (class ①, proposal only):** `LimitFillModel(through_ticks={rec['through_ticks']}, "
             f"through_bp={rec['through_bp']:g}, fill_prob={rec['fill_prob']:g}, tick_from_data=True)` — basis "
             f"`{rec['basis']}`, timed precision lower bound "
             f"{_f(rec['precision_lo'] * 100 if isinstance(rec['precision_lo'], float) else math.nan, 1)} %; "
             f"current default `LIMIT_FILL_DEFAULT` through_ticks={cur['through_ticks']}, through_bp={cur['through_bp']:g}, "
             f"fill_prob={cur['fill_prob']:g} → **{rec['direction']}**. "
             "No params-registry entry exists for the fill model, so nothing is auto-applied.\n")
    if len(pairs):
        L.append(key_findings(res))
    L.append("## Data\n")
    L.append(f"- Exchange {c['exchange']}; window {c['first']} .. {c['last']} ({c['live_minutes']} live minutes, "
             f"{c['live_real_minutes']} with trades); {c['trades']:,} trades, {c['book_snapshots']:,} book snapshots "
             f"(top 15 levels, ≤ 1/s); collector gaps {c['collector_gaps']}.")
    L.append(f"- Tick inferred from trade prices: **{c['tick']:,.0f} KRW** (≈ {c['tick'] / c['median_price'] * 1e4:.2f} bp "
             f"at the median price {c['median_price']:,.0f}).")
    L.append(f"- Side sanity: taker-sell prints at/below the prevailing best bid {_f(c['taker_sell_at_or_below_bid'] * 100, 1)} %, "
             f"taker-buy at/above the best ask {_f(c['taker_buy_at_or_above_ask'] * 100, 1)} % (book snapshot ≤ 1 s old; "
             "values < 100 % are mostly the 1 s snapshot lag).")
    L.append(f"- Hypothetical orders: {c['placements']} placement minutes × 2 sides × {len(p['offsets_bp'])} offsets "
             f"({', '.join(f'{x:g}' for x in p['offsets_bp'])} bp from the minute open, snapped to the tick) × TIF "
             f"{', '.join(str(x) for x in p['tifs_min'])} min; latency {p['latency_s']:g} s after the first trade of the minute; "
             f"order size {p['notional_krw']:,.0f} KRW. Mean-reversion subset: {c['signal_placements']} buy placements "
             f"at `{p['signal']}` signal minutes.")
    if len(res.orders):
        st = res.orders["status"].value_counts().to_dict()
        L.append(f"- Queue position at placement: {', '.join(f'{k} {v}' for k, v in sorted(st.items()))} "
                 "(cross = marketable at placement, excluded from the precision statistics; deep/none = level not "
                 "visible yet — queue set when it first appears).")
    L.append("- CIs: 95 % block bootstrap over 30-min blocks of placement time (orders overlap; iid CIs would be too narrow).\n")

    if not len(pairs):
        L.append("No measurable orders.\n")
        return "\n".join(L)
    pm = pairs[pairs["status"] != "cross"]

    L.append("## Fill probability by side, offset and TIF (%)\n")
    hdr = ["side", "offset bp", "TIF", "n", "touch", "1 tick", "2 ticks", *[f"{d:g} bp" for d in p["through_bp_grid"]],
           "queue (cons.)", "queue (opt.)"]
    L.append("| " + " | ".join(hdr) + " |")
    L.append("|" + "---|" * len(hdr))
    for (side, off, T), d in pm.groupby(["side", "offset_bp", "tif"]):
        cells = [side, f"{off:g}", str(T), str(len(d))]
        cells += [_f(d[f"c_{nm}"].mean() * 100, 1) for nm in names]
        cells += [_f(d["q_fill"].mean() * 100, 1), _f(d["qo_fill"].mean() * 100, 1)]
        L.append("| " + " | ".join(cells) + " |")
    L.append("")

    L.append("## Is the candle rule conservative? (all sides, offsets, TIFs pooled; non-crossing orders)\n")
    L.append("Precision = P(conservative-queue fill no later than the end of the candle-rule fill minute | candle fill) — "
             "should be ≈ 100 % for a safe backtest rule. Untimed = queue fill anywhere in the TIF. Recall = P(candle "
             "fill | queue fill): the share of real fills the rule books (the rest are left out).\n")
    L.append("| rule | candle fills | precision timed % | precision untimed % | recall % | fill rate % (candle / queue) |")
    L.append("|---|---|---|---|---|---|")
    qrate = pm["q_fill"].mean() * 100
    for nm in names:
        L.append(f"| {nm} | {int(pm[f'c_{nm}'].sum())} | {_ci(prop(pm, f'ct_{nm}', f'c_{nm}'), 1, True)} | "
                 f"{_ci(prop(pm.assign(_cq=pm[f'c_{nm}'] & pm['q_fill']), '_cq', f'c_{nm}'), 1, True)} | "
                 f"{_ci(prop(pm, f'c_{nm}', 'q_fill'), 1, True)} | {_f(pm[f'c_{nm}'].mean() * 100, 1)} / {_f(qrate, 1)} |")
    L.append("")
    L.append("By TIF (engine default rule `1tick`): " + "; ".join(
        f"TIF {T}: precision {_ci(prop(d, 'ct_1tick', 'c_1tick'), 1, True)}, "
        f"recall {_ci(prop(d, 'c_1tick', 'q_fill'), 1, True)}"
        for T, d in pm.groupby("tif")) + ".\n")
    hw = pm[pm["q_fill"]]
    if len(hw):
        hc = hw["q_how"].value_counts(normalize=True).to_dict()
        L.append("How the conservative queue fills happened: " + ", ".join(f"{k} {v * 100:.1f} %" for k, v in hc.items())
                 + " (through = a print beyond our price; queue = displayed size ahead of us traded at our price).\n")

    L.append("## Adverse selection — markouts after fills (bp, + = good for the maker; TIF 15)\n")
    d15 = pm[pm["tif"] == max(p["tifs_min"])]
    mk = d15["q_fill"].to_numpy(bool)
    L.append("| subset | " + " | ".join(f"{h} min" for h in p["markouts_min"]) + " |")
    L.append("|---|" + "---|" * len(p["markouts_min"]))
    L.append("| queue fills (from real fill time) | " + " | ".join(
        _ci(mean_ci(d15, f"mo_q_{h}", mk)) for h in p["markouts_min"]) + " |")
    for nm in ("1tick", "1bp", "2bp"):
        if f"c_{nm}" not in d15:
            continue
        cm = d15[f"c_{nm}"].to_numpy(bool)
        L.append(f"| candle `{nm}` fills (from end of fill minute — backtest view) | " + " | ".join(
            _ci(mean_ci(d15, f"mo_{nm}_{h}", cm)) for h in p["markouts_min"]) + " |")
    for nm in ("1tick", "1bp"):
        miss = mk & ~d15[f"c_{nm}"].to_numpy(bool)
        L.append(f"| real fills the `{nm}` rule leaves out (from real fill time) | " + " | ".join(
            _ci(mean_ci(d15, f"mo_q_{h}", miss)) for h in p["markouts_min"]) + " |")
    L.append("| all placements: mid drift from τ (no fill) | " + " | ".join(
        _ci(mean_ci(d15, f"drift_{h}")) for h in p["markouts_min"]) + " |")
    L.append("")
    L.append("Reading: a maker fill is adverse when its markout is below the unconditional drift. If the fills the "
             "candle rule leaves out have *negative* markouts, omitting them flatters the backtest (it skips losing "
             "fills); if positive, omitting them is conservative.\n")

    L.append("## Mean-reversion signal times (buy limits only)\n")
    sp = res.sig_pairs
    if len(sp):
        sp = sp[sp["status"] != "cross"]
        L.append("| offset bp | TIF | n | touch % | 1 tick % | queue (cons.) % | precision `1tick` % | "
                 "queue-fill markout 5 min bp |")
        L.append("|---|---|---|---|---|---|---|---|")
        for (off, T), d in sp.groupby(["offset_bp", "tif"]):
            L.append(f"| {off:g} | {T} | {len(d)} | {_f(d['c_touch'].mean() * 100, 1)} | "
                     f"{_f(d['c_1tick'].mean() * 100, 1)} | {_f(d['q_fill'].mean() * 100, 1)} | "
                     f"{_ci(prop(d, 'ct_1tick', 'c_1tick'), 1, True)} | "
                     f"{_ci(mean_ci(d, 'mo_q_5', d['q_fill'].to_numpy(bool)))} |")
        L.append("")
    else:
        L.append("No signal minutes in the window.\n")

    L.append("## Recommendation detail\n")
    tab = rec.get("table")
    if tab is not None and len(tab):
        L.append("| rule | through depth | timed precision % [95 % CI] (n) |")
        L.append("|---|---|---|")
        for r in tab.itertuples():
            L.append(f"| {r.rule} | max({r.m} tick, {r.d_bp:g} bp) | {_ci((r.precision, r.lo, r.hi, r.n), 1, True)} |")
        L.append("")
    L.append(f"Rule: the least strict margin whose lower bound is ≥ {p['target_precision'] * 100:.0f} % → fill_prob 1; "
             "otherwise the strictest with fill_prob = its lower bound (floored to 0.05). The engine reads the tick from "
             "the 1m data per UTC day (`signals.infer_ticks`; Coinone BTC 1,000 → 10,000 KRW on 2024-09-29/30), so "
             "`through_ticks` is exact in every period; a bp-only margin (tick_from_data=False) must stay ≥ 1 tick "
             "(≥ 1.25 bp for Coinone BTC since 2024-09-30, min price 80.6 M KRW; XRP/ETH ticks are larger in bp) or it "
             "admits touch fills.\n")

    if res.replays:
        L.append("## Paper-path replay (ReferenceEngine: candle rule vs QueueFillOracle)\n")
        L.append("Toy 1m maker strategy over the live window: buy limit 5 bp below the minute open, TIF 5 min, TP limit "
                 "+10 bp, SL −30 bp (market), hold ≤ 30 min, zero cost (fill logic only). `queue` = the same engine "
                 "with `fill_oracle = QueueFillOracle` — the hook the paper trader uses.\n")
        L.append("| entries | rule | trades | mean net bp | win % | TP exits | same entry minutes |")
        L.append("|---|---|---|---|---|---|---|")
        for key, rp in res.replays.items():
            for lab in ("candle", "queue"):
                t = rp[lab]
                n = len(t)
                L.append(f"| {key} | {lab} | {n} | {_f(t['net_ret'].mean() * 1e4 if n else math.nan)} | "
                         f"{_f((t['net_ret'] > 0).mean() * 100 if n else math.nan, 1)} | "
                         f"{int((t['exit_reason'] == 'tp').sum()) if n else 0} | {rp['same_entries']} |")
        L.append("")

    L.append("## Limitations\n")
    L.append("- A few hours of one regime; hour-of-day, volatility and weekend effects are not sampled. CIs are block "
             "bootstrap over 30-min blocks — with few blocks they are themselves unstable.")
    L.append("- Book snapshots ≤ 1/s and top-15 levels: queue ahead is read from the last snapshot before τ (orders "
             "that joined in that ≤ 1 s are not counted ahead of us — slightly optimistic); levels deeper than 15 are "
             "unknown until visible. Hidden / iceberg liquidity and self-trade rules are not modelled.")
    L.append("- Trades are stamped with exchange time, books with receive time (median offset ≈ 0.1 s).")
    L.append("- Coinone volume includes bot-like micro prints (pitfall P-D1); they consume queue as any trade does.")
    L.append("- The candle comparison uses the prepared bars the backtest sees (wick cleaning can only remove "
             "extreme lows/highs → fewer candle fills, conservative).")
    L.append("- Our own order would add to displayed liquidity and could change others' behaviour; ignored.\n")
    L.append("## Re-run\n")
    L.append("```\nuv run python -m btc_quant.research.fill_model --report research/reports/fill_model_<date>.md\n```\n")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--root", default=".")
    ap.add_argument("--data-root")
    ap.add_argument("--exchange", default="coinone")
    ap.add_argument("--offsets", default=",".join(f"{x:g}" for x in DEFAULT_OFFSETS_BP))
    ap.add_argument("--tifs", default=",".join(str(x) for x in DEFAULT_TIFS))
    ap.add_argument("--latency", type=float, default=LATENCY_S)
    ap.add_argument("--notional", type=float, default=NOTIONAL)
    ap.add_argument("--no-replay", action="store_true")
    ap.add_argument("--report")
    ap.add_argument("--orders-csv")
    a = ap.parse_args(argv)
    try:
        res = run_fill_study(a.since, a.until, a.root, a.data_root, a.exchange,
                             tuple(float(x) for x in a.offsets.split(",")), tuple(int(x) for x in a.tifs.split(",")),
                             a.latency, a.notional, replay_runs=not a.no_replay)
    except ExecutionStudyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    md = render_markdown(res)
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(md, encoding="utf-8")
        print(f"wrote {a.report}")
    else:
        print(md)
    if a.orders_csv:
        res.orders.to_csv(a.orders_csv, index=False)
    r = res.rec
    print(f"status {res.status}; recommended through_bp={r['through_bp']:g} fill_prob={r['fill_prob']:g} "
          f"({r['direction']} vs current)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
