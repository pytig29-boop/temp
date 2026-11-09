"""Live executability study for the cross-venue 1m signal (Upbit -> Coinone), Forward data only.

    uv run python -m btc_quant.research.execution_study --since 2026-11-09T22:48 [--until ...]
        [--template research/campaign_templates/screens/c1_xvenue_lag_1m.yaml]
        [--latencies 0.5,1,2] [--notional 300000] [--no-rest-warmup] [--report PATH] [--events-csv PATH]

Question (research cycle 1, int-000001): the 1m Upbit->Coinone lead blocks beat the matched null by only
+1.3..2.9 bp at 5 min on Dev, and the backtest enters at the *next Coinone 1m open* (= the first Coinone
trade printed in minute t). Is that price executable, or is part of the edge a stale-print artefact?

Method (every step re-uses the research / paper code paths; nothing here changes them)
1. Data: the live WebSocket collector's Parquet files
   ``data/raw/exchange={coinone,upbit}/symbol=BTC-KRW/{trades,orderbook}/date=YYYY-MM-DD/HH-*.parquet``
   (schema: `btc_quant.live.ws_collector`). Only ``date=`` partitions on/after the Forward start
   (config/splits.yaml) are read; ``--since`` before the Forward start is refused. The lockbox / Val are never
   read. Optional warm-up candles for the rolling windows come from the public REST 1m endpoint (the same
   `paper.bars.fetch_warmup` the paper trader seeds with), clamped to start at or after the Forward start.
2. Bars: trades are replayed in receive order through `paper.bars.MinuteBarBuilder` (grace 2 s, late trades
   dropped exactly as live), then ``campaign.prepare_m1(raw, exchange)`` = per-venue clean_wicks ->
   regularize (no-trade minute = filled) -> long-gap warm-up filled (= paper `M1History`).
3. Signals: the template's (entry x filter) configs evaluated with ``signal_study.decision_bars(m1, 1)`` and
   ``campaign.aux_context({"upbit": m1_upbit}, 1)``; eligibility as ``signal_study.study_events``
   (filters & complete decision bar, entry minute t not filled, 60-bar RV known, horizons available) and
   greedy declustering with the template's ``min_gap_min``. Decision time t = decision-bar close.
4. Measurement at each event t (all prices KRW, returns in bp):
   * (a) backtest entry = Coinone m1 open of minute t (first trade received in minute t); its print delay.
   * (b) executable entry = Coinone best ask at t+L (L = latencies, book state as last *received* snapshot
     <= t+L, collector samples <= 1 snapshot/s) and the VWAP of walking the ask side for ``notional`` KRW.
     slippage = (b - a) / a. The cost model v2 charges 2 bp on the entry side
     (half spread 1 bp + slip 1 bp) - the comparison is against that.
   * Coinone mid / Upbit mid paths at 1 s resolution for s in [-60, 300] s, and the cross-venue *book* deficit
     D0 = median(log mid gap over [t-20, t-5] min) - log mid gap(t) (bp, > 0 = Coinone cheap vs Upbit).
     Catch-up time t50 / t90 = first s at which (gap(t+s) - gap(t)) >= 50 % / 90 % of D0 (censored at 300 s;
     undefined when D0 < ``min_deficit_bp`` - i.e. the book had already caught up at t: the signal fired on
     a stale *print*).
   * Forward returns to h in {1, 3, 5} min: backtest (open -> close of minute t+h-1, net of cost v2), entry at the
     executable VWAP with the model's exit (cost v2 sell side), and fully book-measured (bid walk at t+h*60+L).
5. Control: eligible non-event minutes in the same calendar hours (>= 5 min away from any raw trigger), same
   measurements -> event minus control separates the signal's effect from generic microstructure.
6. Uncertainty: iid bootstrap 95 % CIs of means (events are >= min_gap_min apart within a config).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from btc_quant.backtest.types import CostModel
from btc_quant.paper.bars import MinuteBarBuilder
from btc_quant.strategy.blocks import REGISTRY
from btc_quant.strategy.blocks.base import Registry
from btc_quant.strategy.spec import BlockRef

from .campaign import aux_context, get_cost, prepare_m1
from .signal_study import (
    VOL_WINDOW_MIN,
    ScreenTemplate,
    _label,
    _rolling_rv,
    decision_bars,
    decluster,
)

SYMBOL = "BTC-KRW"
DEFAULT_TEMPLATE = "research/campaign_templates/screens/c1_xvenue_lag_1m.yaml"
DEFAULT_LATENCIES = (0.5, 1.0, 2.0)
PRIMARY_LATENCY = 1.0
DEFAULT_NOTIONAL = 300_000.0
DEFAULT_HORIZONS = (1, 3, 5)
PATH_PRE_S = 60
PATH_POST_S = 300
BASELINE_WIN_MIN = (20, 5)          # D0 baseline: median mid-gap over [t-20, t-5] min
MIN_DEFICIT_BP = 1.0
MAX_BOOK_AGE_S = 30.0               # the collector reconnects after 30 s of silence -> older = no data
GRACE_S = 2.0                       # MinuteBarBuilder grace (paper default)
CONTROL_EXCLUDE_MIN = 5
MAX_CONTROL = 3000
MIN_EVENTS_FOR_VERDICT = 30
N_BOOT = 2000
NS = 1_000_000_000
NS_MIN = 60 * NS
PATH_REPORT_S = (-60, -30, -10, -5, 0, 1, 2, 5, 10, 20, 30, 60, 120, 180, 300)


class ExecutionStudyError(RuntimeError):
    pass


# ============================================================== splits guard
def forward_start(root: str | Path = ".") -> pd.Timestamp:
    cfg = yaml.safe_load((Path(root) / "config" / "splits.yaml").read_text(encoding="utf-8"))
    return pd.Timestamp(cfg["segments"]["forward"]["start"], tz="UTC")


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# ============================================================== loading
def ws_files(data_root: str | Path, exchange: str, kind: str, since: pd.Timestamp, until: pd.Timestamp,
             fwd_start: pd.Timestamp, symbol: str = SYMBOL) -> list[Path]:
    """Collector part files whose UTC hour overlaps [since - 1 h, until + 1 h]; only date= partitions on/after
    the Forward start (anything else under the directory, e.g. REST snapshots, is ignored)."""
    base = Path(data_root) / f"exchange={exchange}" / f"symbol={symbol}" / kind
    out = []
    for d in sorted(base.glob("date=*")):
        try:
            day = pd.Timestamp(d.name.split("=", 1)[1], tz="UTC")
        except ValueError:
            continue
        if day < fwd_start.floor("D"):
            continue
        for f in sorted(d.glob("[0-9][0-9]-*.parquet")):
            hour = day + pd.Timedelta(hours=int(f.name[:2]))
            if hour < fwd_start.floor("h"):
                continue
            if hour + pd.Timedelta(hours=2) <= since or hour - pd.Timedelta(hours=1) > until:
                continue
            out.append(f)
    return out


def load_ws(data_root, exchange: str, kind: str, since: pd.Timestamp, until: pd.Timestamp,
            fwd_start: pd.Timestamp) -> pd.DataFrame:
    files = ws_files(data_root, exchange, kind, since, until, fwd_start)
    frames = []
    for f in files:
        try:
            frames.append(pd.read_parquet(f))
        except Exception as e:   # noqa: BLE001 — a file being written concurrently; skip it
            print(f"warning: skip unreadable {f}: {e}", file=sys.stderr)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    tcol = "ts_exchange" if kind == "trades" else "ts"
    for c in (tcol, "ts_recv"):
        df[c] = pd.to_datetime(df[c], utc=True).dt.as_unit("ns")
    lo = max(since, fwd_start)
    df = df[(df["ts_recv"] >= lo) & (df["ts_recv"] < until) & (df[tcol] >= fwd_start)]
    return df.sort_values(["ts_recv", tcol], kind="stable").reset_index(drop=True)


def load_gaps(path: str | Path | None) -> dict[str, list[tuple[int, int]]]:
    """runtime/collector_gaps.jsonl -> {exchange: [(start_ns, end_ns)]}."""
    out: dict[str, list[tuple[int, int]]] = {}
    if not path or not Path(path).exists():
        return out
    for line in Path(path).read_text().splitlines():
        try:
            g = json.loads(line)
            out.setdefault(g["exchange"], []).append((int(g["start"] * NS), int(g["end"] * NS)))
        except (ValueError, KeyError):
            continue
    return out


# ============================================================== bars (paper semantics)
@dataclass
class BarBuild:
    raw: pd.DataFrame            # real bars only: open/high/low/close/volume, index ts (UTC minute start)
    first_trade: pd.Series       # minute -> exchange time of the bar-opening trade
    late: int = 0
    ignored: int = 0
    duplicates: int = 0
    n_trades: int = 0
    first_minute: pd.Timestamp | None = None
    last_minute: pd.Timestamp | None = None


def build_bars(trades: pd.DataFrame, grace: float = GRACE_S) -> BarBuild:
    """Replay trades in receive order through `MinuteBarBuilder` exactly as the paper service does
    (on_time(recv) before each trade, so trades arriving after their minute closed are dropped)."""
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                         index=pd.DatetimeIndex([], tz="UTC", name="ts"))
    if trades is None or trades.empty:
        return BarBuild(empty, pd.Series(dtype="datetime64[ns, UTC]"))
    t = trades
    dup = 0
    if "trade_id" in t:
        has = t["trade_id"].astype(str).str.len() > 0
        d = has & t.duplicated(subset=["trade_id"], keep="first")
        dup = int(d.sum())
        t = t[~d]
    ts_ms = (pd.to_datetime(t["ts_exchange"], utc=True).dt.as_unit("ns").astype("int64") // 1_000_000).to_numpy()
    recv_s = pd.to_datetime(t["ts_recv"], utc=True).dt.as_unit("ns").astype("int64").to_numpy() / 1e9
    px = t["price"].to_numpy(float)
    qty = t["qty"].to_numpy(float)
    b = MinuteBarBuilder(grace_seconds=grace)
    bars = []
    first: dict[int, int] = {}
    for i in range(len(t)):
        bars += b.on_time(recv_s[i])
        before = b.trades
        b.on_trade(int(ts_ms[i]), px[i], qty[i])
        if b.trades > before:
            first.setdefault(int(ts_ms[i]) // 60_000, int(ts_ms[i]))
    bars += b.on_time(recv_s[-1])
    real = [x for x in bars if not x.filled]
    if real:
        raw = pd.DataFrame({"open": [x.open for x in real], "high": [x.high for x in real],
                            "low": [x.low for x in real], "close": [x.close for x in real],
                            "volume": [x.volume for x in real]},
                           index=pd.DatetimeIndex([x.ts for x in real], name="ts"))
    else:
        raw = empty
    ft = pd.Series({pd.Timestamp(m * NS_MIN, tz="UTC"): pd.Timestamp(v * 1_000_000, tz="UTC")
                    for m, v in first.items()}, dtype="datetime64[ns, UTC]")
    return BarBuild(raw, ft, late=b.late_trades, ignored=b.ignored_trades, duplicates=dup, n_trades=b.trades,
                    first_minute=bars[0].ts if bars else None, last_minute=bars[-1].ts if bars else None)


def seed_and_prepare(bb: BarBuild, exchange: str, seed: pd.DataFrame | None,
                     fwd_start: pd.Timestamp) -> pd.DataFrame:
    """[REST seed (< first live minute, >= Forward start)] + live real bars -> prepare_m1(·, exchange)."""
    raw = bb.raw
    if seed is not None and not seed.empty and bb.first_minute is not None:
        s = seed[(seed.index >= fwd_start) & (seed.index < bb.first_minute)]
        raw = pd.concat([s[["open", "high", "low", "close", "volume"]].astype(float), raw])
    if raw.empty:
        raise ExecutionStudyError(f"no {exchange} bars")
    return prepare_m1(raw, exchange)


# ============================================================== order book
@dataclass
class Book:
    t: np.ndarray            # int64 ns (receive clock), ascending
    bid: np.ndarray
    ask: np.ndarray
    ask_px: np.ndarray       # (n, D) ascending, nan padded
    ask_qty: np.ndarray
    bid_px: np.ndarray       # (n, D) descending
    bid_qty: np.ndarray
    gaps: list = field(default_factory=list)   # [(start_ns, end_ns)] collector disconnects

    @property
    def mid(self) -> np.ndarray:
        return (self.bid + self.ask) / 2

    def __len__(self) -> int:
        return len(self.t)

    @classmethod
    def from_frame(cls, df: pd.DataFrame, clock: str = "ts_recv", gaps=None) -> Book:
        if df is None or df.empty:
            z = np.zeros(0)
            return cls(np.zeros(0, np.int64), z, z, np.zeros((0, 1)), np.zeros((0, 1)), np.zeros((0, 1)),
                       np.zeros((0, 1)), list(gaps or []))
        df = df.sort_values(clock, kind="stable")
        t = pd.to_datetime(df[clock], utc=True).dt.as_unit("ns").astype("int64").to_numpy()

        def mat(col):
            rows = [np.asarray(v, float) for v in df[col]]
            d = max((len(r) for r in rows), default=1)
            m = np.full((len(rows), d), np.nan)
            for i, r in enumerate(rows):
                m[i, :len(r)] = r
            return m
        return cls(t, df["best_bid"].to_numpy(float), df["best_ask"].to_numpy(float), mat("ask_px"),
                   mat("ask_qty"), mat("bid_px"), mat("bid_qty"), list(gaps or []))

    def idx_at(self, q_ns, max_age_s: float = MAX_BOOK_AGE_S) -> np.ndarray:
        """Index of the last snapshot with time <= q (the state a trader had received by q); -1 if none, older
        than max_age_s, or q inside a recorded collector gap."""
        q = np.asarray(q_ns, dtype=np.int64)
        if len(self.t) == 0:
            return np.full(q.shape, -1)
        i = np.searchsorted(self.t, q, side="right") - 1
        i0 = np.clip(i, 0, None)
        ok = (i >= 0) & ((q - self.t[i0]) <= int(max_age_s * NS))
        for a, b in self.gaps:
            ok &= ~((q >= a) & (q <= b))
        return np.where(ok, i, -1)

    def take(self, arr: np.ndarray, idx: np.ndarray) -> np.ndarray:
        idx = np.asarray(idx)
        out = np.full(idx.shape, np.nan)
        m = idx >= 0
        out[m] = arr[idx[m]]
        return out

    def age_s(self, q_ns, idx) -> np.ndarray:
        q = np.asarray(q_ns, dtype=np.int64)
        out = np.full(q.shape, np.nan)
        m = np.asarray(idx) >= 0
        out[m] = (q[m] - self.t[np.asarray(idx)[m]]) / NS
        return out


def walk_buy(px: np.ndarray, qty: np.ndarray, notional: float) -> np.ndarray:
    """VWAP of buying `notional` KRW by walking ask levels (rows). nan if the visible depth is insufficient."""
    px = np.atleast_2d(np.asarray(px, float))
    qty = np.atleast_2d(np.asarray(qty, float))
    val = np.nan_to_num(px * qty)
    cum = np.cumsum(val, axis=1)
    prev = cum - val
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.clip(np.where(val > 0, (notional - prev) / val, 0.0), 0.0, 1.0)
        q = np.nansum(np.nan_to_num(qty) * frac, axis=1)
        out = notional / q
    return np.where((cum[:, -1] >= notional - 1e-9) & (q > 0), out, np.nan)


def walk_sell(px: np.ndarray, qty: np.ndarray, q_target) -> np.ndarray:
    """VWAP of selling q_target BTC (per row) by walking bid levels. nan if depth insufficient."""
    px = np.atleast_2d(np.asarray(px, float))
    qty = np.nan_to_num(np.atleast_2d(np.asarray(qty, float)))
    qt = np.broadcast_to(np.asarray(q_target, float), (px.shape[0],))[:, None]
    cum = np.cumsum(qty, axis=1)
    prev = cum - qty
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.clip(np.where(qty > 0, (qt - prev) / qty, 0.0), 0.0, 1.0)
        proceeds = np.nansum(np.nan_to_num(px) * qty * frac, axis=1)
        out = proceeds / qt[:, 0]
    return np.where(cum[:, -1] >= qt[:, 0] - 1e-12, out, np.nan)


def _rows(book: Book, side: str, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    px, qty = (book.ask_px, book.ask_qty) if side == "ask" else (book.bid_px, book.bid_qty)
    d = px.shape[1] if px.ndim == 2 else 1
    P = np.full((len(idx), d), np.nan)
    Q = np.full((len(idx), d), np.nan)
    m = idx >= 0
    if m.any():
        P[m] = px[idx[m]]
        Q[m] = qty[idx[m]]
    return P, Q


# ============================================================== signals
def config_label(entry: BlockRef, filters) -> str:
    return _label(entry, filters)


def family_of(entry: BlockRef) -> str:
    return {"upbit_lead_impulse": "lead", "venue_gap_revert": "gap"}.get(entry.block, entry.block)


@dataclass
class SignalSet:
    events: pd.DataFrame        # rows: config, family, t (decision time), pos (m1 position of minute t)
    triggers: pd.DataFrame      # raw (pre-decluster) triggers: config, t
    pool_t: pd.DatetimeIndex    # eligible decision times (all configs)
    per_config: pd.DataFrame    # config, family, n_pool, n_triggers, n_events


def signal_events(co_m1: pd.DataFrame, up_m1: pd.DataFrame, configs, min_gap_min: float,
                  horizons=DEFAULT_HORIZONS, since: pd.Timestamp | None = None,
                  until: pd.Timestamp | None = None, registry: Registry = REGISTRY) -> SignalSet:
    """Signal events exactly as `signal_study.study_signal` / `study_events` select them (1m decision bars)."""
    bars = decision_bars(co_m1, 1)
    ctx = aux_context({"upbit": up_m1}, 1)
    filled = co_m1["filled"].to_numpy(bool)
    n1 = len(co_m1)
    hmax = max(horizons)
    t_dec = bars.index + pd.Timedelta(minutes=1)
    pos = co_m1.index.get_indexer(t_dec)
    p_safe = np.where(pos >= 0, pos, 0)
    rv = _rolling_rv(co_m1["close"].to_numpy(float), VOL_WINDOW_MIN)
    rv_at = np.where(p_safe >= 1, rv[np.maximum(p_safe - 1, 0)], np.nan)
    base_ok = (pos >= 0) & ~filled[p_safe] & (p_safe + hmax - 1 < n1) & np.isfinite(rv_at)
    if since is not None:
        base_ok &= np.asarray(t_dec >= since)
    if until is not None:
        base_ok &= np.asarray(t_dec < until)
    cache: dict = {}

    def ev(ref):
        k = json.dumps([ref.block, ref.params], sort_keys=True, default=str)
        if k not in cache:
            cache[k] = registry.evaluate(ref, bars, ctx).to_numpy(bool)
        return cache[k]

    rows, trig_rows, stats = [], [], []
    pool_all = None
    for entry, filters in configs:
        ok = base_ok & bars["complete"].to_numpy(bool)
        for f in filters:
            ok &= ev(f)
        pool_all = ok if pool_all is None else (pool_all & ok)
        trig = np.flatnonzero(ok & ev(entry))
        keep = decluster(t_dec[trig].as_unit("ns").asi8, min_gap_min)
        lab = config_label(entry, filters)
        for i in trig:
            trig_rows.append({"config": lab, "t": t_dec[i]})
        for i in trig[keep]:
            rows.append({"config": lab, "family": family_of(entry), "block": entry.block,
                         "params": dict(entry.params), "t": t_dec[i], "pos": int(pos[i])})
        stats.append({"config": lab, "family": family_of(entry), "n_pool": int(ok.sum()),
                      "n_triggers": int(trig.size), "n_events": int(keep.sum())})
    pool_all = base_ok if pool_all is None else pool_all
    ev_df = pd.DataFrame(rows, columns=["config", "family", "block", "params", "t", "pos"])
    tr_df = pd.DataFrame(trig_rows, columns=["config", "t"])
    return SignalSet(ev_df, tr_df, t_dec[pool_all], pd.DataFrame(stats))


def pool_events(events: pd.DataFrame) -> pd.DataFrame:
    """Union over configs, one row per decision time (configs / families joined)."""
    if events.empty:
        return pd.DataFrame(columns=["t", "configs", "families", "n_configs"])
    g = events.groupby("t", sort=True)
    return pd.DataFrame({"configs": g["config"].agg(lambda s: "; ".join(sorted(set(s)))),
                         "families": g["family"].agg(lambda s: ",".join(sorted(set(s)))),
                         "n_configs": g["config"].nunique()}).reset_index()


def control_times(sig: SignalSet, event_t: pd.DatetimeIndex, exclude_min: int = CONTROL_EXCLUDE_MIN,
                  max_n: int = MAX_CONTROL, seed: int = 0) -> pd.DatetimeIndex:
    """Eligible non-event decision minutes in the same calendar (UTC) hours as the events, at least
    `exclude_min` minutes from any raw trigger of any config."""
    pool = pd.DatetimeIndex(sig.pool_t)
    if len(pool) == 0 or len(event_t) == 0:
        return pd.DatetimeIndex([], tz="UTC")
    hours = pd.DatetimeIndex(event_t).floor("h").unique()
    m = np.asarray(pool.floor("h").isin(hours))
    trig = np.sort(pd.DatetimeIndex(sig.triggers["t"]).as_unit("ns").asi8) if len(sig.triggers) else np.zeros(0, np.int64)
    if trig.size:
        p = pool.as_unit("ns").asi8
        j = np.searchsorted(trig, p)
        d_next = np.abs(trig[np.clip(j, 0, trig.size - 1)] - p)
        d_prev = np.abs(p - trig[np.clip(j - 1, 0, trig.size - 1)])
        m &= np.minimum(d_next, d_prev) >= exclude_min * NS_MIN
    out = pool[m]
    if len(out) > max_n:
        rng = np.random.default_rng(seed)
        out = out[np.sort(rng.choice(len(out), max_n, replace=False))]
    return out


# ============================================================== measurement
@dataclass
class Measured:
    table: pd.DataFrame                  # one row per decision time
    path_s: np.ndarray                   # seconds grid
    co_path: np.ndarray                  # (n, S) Coinone mid return vs t (bp)
    up_path: np.ndarray                  # (n, S) Upbit mid return vs t (bp)
    closure: np.ndarray                  # (n, S) deficit closure fraction (gap(t+s)-gap(t))/D0


def measure(times, co_m1: pd.DataFrame, first_trade: pd.Series, co_book: Book, up_book: Book,
            cost: CostModel, latencies=DEFAULT_LATENCIES, notional: float = DEFAULT_NOTIONAL,
            horizons=DEFAULT_HORIZONS, primary_latency: float = PRIMARY_LATENCY,
            path_pre_s: int = PATH_PRE_S, path_post_s: int = PATH_POST_S,
            baseline_win_min=BASELINE_WIN_MIN, min_deficit_bp: float = MIN_DEFICIT_BP,
            max_book_age_s: float = MAX_BOOK_AGE_S) -> Measured:
    T = pd.DatetimeIndex(times)
    n = len(T)
    tn = T.as_unit("ns").asi8.astype(np.int64)
    pos = co_m1.index.get_indexer(T)
    o = co_m1["open"].to_numpy(float)
    c = co_m1["close"].to_numpy(float)
    filled = co_m1["filled"].to_numpy(bool)
    ok_pos = pos >= 1
    ps = np.where(ok_pos, pos, 1)
    a = np.where(ok_pos & ~filled[ps], o[ps], np.nan)
    prev_close = np.where(ok_pos, c[ps - 1], np.nan)
    ft = first_trade.reindex(T)
    ft_ns = ft.to_numpy(dtype="datetime64[ns]").astype(np.int64)
    open_delay = np.where(ft.isna().to_numpy(), np.nan, (ft_ns - tn) / NS)
    tab: dict[str, np.ndarray] = {"t": T, "a_open": a, "open_delay_s": open_delay,
                                  "prev_close": prev_close}

    def bp(x, ref):
        with np.errstate(divide="ignore", invalid="ignore"):
            return (np.asarray(x, float) / np.asarray(ref, float) - 1.0) * 1e4

    i0 = co_book.idx_at(tn, max_book_age_s)
    mid0 = co_book.take(co_book.mid, i0)
    tab["co_mid_t"] = mid0
    tab["stale_print_bp"] = bp(mid0, prev_close)       # Coinone book mid at t vs last print (bar close)
    tab["open_vs_mid_bp"] = bp(a, mid0)
    tab["co_spread_bp_t"] = bp(co_book.take(co_book.ask, i0), co_book.take(co_book.bid, i0))
    entry_vwap = {}
    for L in latencies:
        q = tn + int(L * NS)
        i = co_book.idx_at(q, max_book_age_s)
        ask = co_book.take(co_book.ask, i)
        mid = co_book.take(co_book.mid, i)
        P, Q = _rows(co_book, "ask", i)
        vw = np.where(i >= 0, walk_buy(P, Q, notional), np.nan) if n else np.zeros(0)
        entry_vwap[L] = vw
        tab[f"book_age_s_L{L:g}"] = co_book.age_s(q, i)
        tab[f"ask_L{L:g}"] = ask
        tab[f"half_spread_bp_L{L:g}"] = bp(ask, mid)
        tab[f"slip_top_bp_L{L:g}"] = bp(ask, a)
        tab[f"slip_walk_bp_L{L:g}"] = bp(vw, a)
        tab[f"impact_bp_L{L:g}"] = bp(vw, ask)

    # 1 s paths and cross-venue book deficit
    S = np.arange(-int(path_pre_s), int(path_post_s) + 1)
    Q = tn[:, None] + S[None, :].astype(np.int64) * NS
    ci = co_book.idx_at(Q.ravel(), max_book_age_s).reshape(Q.shape)
    ui = up_book.idx_at(Q.ravel(), max_book_age_s).reshape(Q.shape)
    cm = co_book.take(co_book.mid, ci.ravel()).reshape(Q.shape) if n else np.zeros((0, len(S)))
    um = up_book.take(up_book.mid, ui.ravel()).reshape(Q.shape) if n else np.zeros((0, len(S)))
    z = int(np.flatnonzero(S == 0)[0])
    co_path = bp(cm, cm[:, [z]]) if n else cm
    up_path = bp(um, um[:, [z]]) if n else um
    with np.errstate(divide="ignore", invalid="ignore"):
        gap = np.log(cm) - np.log(um)
    b0, b1 = baseline_win_min
    Bs = np.arange(-int(b0) * 60, -int(b1) * 60 + 1, 1)
    QB = tn[:, None] + Bs[None, :].astype(np.int64) * NS
    cb = co_book.take(co_book.mid, co_book.idx_at(QB.ravel(), max_book_age_s)).reshape(QB.shape) if n else np.zeros((0, len(Bs)))
    ub = up_book.take(up_book.mid, up_book.idx_at(QB.ravel(), max_book_age_s)).reshape(QB.shape) if n else np.zeros((0, len(Bs)))
    with np.errstate(divide="ignore", invalid="ignore"):
        gb = np.log(cb) - np.log(ub)
    enough = (np.isfinite(gb).sum(axis=1) >= 0.5 * len(Bs)) if n else np.zeros(0, bool)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        base = np.where(enough, np.nanmedian(gb, axis=1) if n else np.zeros(0), np.nan)
    g0 = gap[:, z] if n else np.zeros(0)
    D0 = (base - g0) * 1e4
    with np.errstate(divide="ignore", invalid="ignore"):
        closure = (gap - g0[:, None]) * 1e4 / D0[:, None]
    post = S >= 0
    has_def = np.isfinite(D0) & (D0 >= min_deficit_bp)
    tab["upbit_mid_t"] = um[:, z] if n else np.zeros(0)
    tab["deficit_bp"] = D0
    tab["already_caught_up"] = np.where(np.isfinite(D0), D0 < min_deficit_bp, np.nan)
    tab["upbit_ret_prev60_bp"] = -up_path[:, 0] / (1 + up_path[:, 0] / 1e4) if n else np.zeros(0)
    tab["coinone_ret_prev60_bp"] = -co_path[:, 0] / (1 + co_path[:, 0] / 1e4) if n else np.zeros(0)
    for frac, name in ((0.5, "t50_s"), (0.9, "t90_s")):
        hit = (closure[:, post] >= frac) if n else np.zeros((0, post.sum()), bool)
        first = np.where(hit.any(axis=1), np.argmax(hit, axis=1).astype(float), np.nan) if n else np.zeros(0)
        cens = has_def & ~hit.any(axis=1) if n else np.zeros(0, bool)
        tab[name] = np.where(has_def, first, np.nan)
        tab[name + "_censored"] = cens
    for L in latencies:     # share of the deficit already closed when our order lands (exact t+L as-of)
        q = tn + int(L * NS)
        cL = co_book.take(co_book.mid, co_book.idx_at(q, max_book_age_s))
        uL = up_book.take(up_book.mid, up_book.idx_at(q, max_book_age_s))
        with np.errstate(divide="ignore", invalid="ignore"):
            fr = ((np.log(cL) - np.log(uL)) - g0) * 1e4 / D0
        tab[f"closed_before_L{L:g}"] = np.where(has_def, fr, np.nan)

    # forward returns
    ev = entry_vwap.get(primary_latency, next(iter(entry_vwap.values())) if entry_vwap else np.full(n, np.nan))
    for h in horizons:
        ex_i = ps + h - 1
        valid = ok_pos & (ex_i < len(c))
        c_exit = np.where(valid, c[np.clip(ex_i, 0, len(c) - 1)], np.nan)
        tab[f"bt_gross_bp_h{h}"] = bp(c_exit, a)
        tab[f"bt_net_bp_h{h}"] = (cost.net_return(cost.buy_fill(a), cost.sell_fill(c_exit))) * 1e4
        tab[f"exec_model_net_bp_h{h}"] = cost.net_return(ev, cost.sell_fill(c_exit)) * 1e4
        qx = tn + int((h * 60 + primary_latency) * NS)
        ix = co_book.idx_at(qx, max_book_age_s)
        Pb, Qb = _rows(co_book, "bid", ix)
        with np.errstate(divide="ignore", invalid="ignore"):
            qty = notional / ev
        xs = np.where(ix >= 0, walk_sell(Pb, Qb, np.nan_to_num(qty, nan=0.0)) if n else np.zeros(0), np.nan)
        xs = np.where(np.isfinite(qty), xs, np.nan)
        tab[f"exec_book_net_bp_h{h}"] = cost.net_return(ev, xs) * 1e4
        mid_e = co_book.take(co_book.mid, co_book.idx_at(tn + int(primary_latency * NS), max_book_age_s))
        tab[f"exec_mid_gross_bp_h{h}"] = bp(co_book.take(co_book.mid, ix), mid_e)
    tab["measurable"] = np.isfinite(a) & np.isfinite(tab[f"slip_walk_bp_L{primary_latency:g}"])
    return Measured(pd.DataFrame(tab), S, co_path, up_path, closure)


# ============================================================== statistics
def boot_mean(x, n_boot: int = N_BOOT, seed: int = 0) -> tuple[float, float, float, int]:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return (math.nan, math.nan, math.nan, 0)
    if x.size == 1:
        return (float(x[0]), math.nan, math.nan, 1)
    rng = np.random.default_rng(seed)
    bs = x[rng.integers(0, x.size, (n_boot, x.size))].mean(axis=1)
    return (float(x.mean()), float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975)), int(x.size))


def boot_diff(x, y, n_boot: int = N_BOOT, seed: int = 0) -> tuple[float, float, float]:
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    x, y = x[np.isfinite(x)], y[np.isfinite(y)]
    if x.size < 2 or y.size < 2:
        return (math.nan, math.nan, math.nan)
    rng = np.random.default_rng(seed)
    bx = x[rng.integers(0, x.size, (n_boot, x.size))].mean(axis=1)
    by = y[rng.integers(0, y.size, (n_boot, y.size))].mean(axis=1)
    d = bx - by
    return (float(x.mean() - y.mean()), float(np.quantile(d, 0.025)), float(np.quantile(d, 0.975)))


def n_needed(sd: float, half_width: float, z: float = 1.96) -> float:
    if not np.isfinite(sd) or sd <= 0:
        return math.nan
    return math.ceil((z * sd / half_width) ** 2)


# ============================================================== driver
@dataclass
class StudyResult:
    params: dict
    coverage: dict
    per_config: pd.DataFrame
    events: pd.DataFrame          # pooled, measured
    control: pd.DataFrame         # measured
    ev_meas: Measured
    ctl_meas: Measured
    verdict: dict


def run_study(since, until=None, root: str | Path = ".", data_root: str | Path | None = None,
              template: str | Path = DEFAULT_TEMPLATE, latencies=DEFAULT_LATENCIES,
              notional: float = DEFAULT_NOTIONAL, horizons=DEFAULT_HORIZONS,
              primary_latency: float = PRIMARY_LATENCY, rest_warmup: bool = True,
              warmup_minutes: int = 300, gaps_path: str | Path | None = "runtime/collector_gaps.jsonl",
              seed_fetch=None, max_control: int = MAX_CONTROL, seed: int = 0,
              registry: Registry = REGISTRY) -> StudyResult:
    root = Path(root)
    data_root = Path(data_root) if data_root is not None else root / "data" / "raw"
    fwd = forward_start(root)
    since = _utc(since)
    if since < fwd:
        raise ExecutionStudyError(f"--since {since} is before the Forward start {fwd}: refusing "
                                  "(this study uses Forward data only)")
    until = _utc(until) if until is not None else pd.Timestamp.now(tz="UTC").ceil("min")
    if primary_latency not in latencies:
        latencies = tuple(sorted(set(latencies) | {primary_latency}))
    tpl = ScreenTemplate.from_yaml(template if Path(template).is_absolute() else root / template,
                                   registry=registry)
    configs = tpl.configs()
    cost = get_cost(tpl.costs["primary"], root=root)
    gaps = load_gaps(root / gaps_path if gaps_path and not Path(gaps_path).is_absolute() else gaps_path)
    load_from = max(fwd, since - pd.Timedelta(minutes=warmup_minutes))

    builds, m1, books, cov = {}, {}, {}, {"forward_start": str(fwd)}
    for ex in ("coinone", "upbit"):
        tr = load_ws(data_root, ex, "trades", load_from, until, fwd)
        ob = load_ws(data_root, ex, "orderbook", load_from, until, fwd)
        bb = build_bars(tr)
        if bb.raw.empty:
            raise ExecutionStudyError(f"no live {ex} trades in [{load_from}, {until})")
        seed_df = None
        if rest_warmup and bb.first_minute is not None:
            start = max(fwd, bb.first_minute - pd.Timedelta(minutes=warmup_minutes))
            n_min = int((bb.first_minute - start) / pd.Timedelta(minutes=1))
            if n_min > 0:
                try:
                    if seed_fetch is not None:
                        seed_df = seed_fetch(ex, start, bb.first_minute)
                    else:
                        from btc_quant.paper.bars import fetch_warmup
                        seed_df = fetch_warmup(bb.first_minute, n_min, exchange=ex)
                except Exception as e:    # noqa: BLE001
                    print(f"warning: REST warm-up for {ex} failed ({e}); continuing without", file=sys.stderr)
        builds[ex] = bb
        m1[ex] = seed_and_prepare(bb, ex, seed_df, fwd)
        books[ex] = Book.from_frame(ob, gaps=gaps.get(ex, []))
        offs = ((tr["ts_recv"] - tr["ts_exchange"]).dt.total_seconds() if not tr.empty else pd.Series(dtype=float))
        bt = np.diff(books[ex].t) / NS if len(books[ex]) > 1 else np.zeros(0)
        n_seed = 0 if seed_df is None else int(((seed_df.index >= fwd) & (seed_df.index < bb.first_minute)).sum())
        live = m1[ex].loc[bb.first_minute:bb.last_minute] if bb.first_minute is not None else m1[ex].iloc[:0]
        cov[ex] = {"trades": len(tr), "trades_used": bb.n_trades, "late_trades": bb.late,
                   "ignored_trades": bb.ignored, "duplicate_trades": bb.duplicates,
                   "live_first_minute": str(bb.first_minute), "live_last_minute": str(bb.last_minute),
                   "live_minutes": len(live), "live_real_bars": int((~live["filled"]).sum()),
                   "rest_seed_bars": n_seed, "book_snapshots": len(books[ex]),
                   "book_interval_median_s": float(np.median(bt)) if bt.size else math.nan,
                   "book_interval_p95_s": float(np.quantile(bt, 0.95)) if bt.size else math.nan,
                   "recv_minus_exchange_median_s": float(offs.median()) if len(offs) else math.nan,
                   "collector_gaps": len(gaps.get(ex, []))}

    # live window: events need live bars on both venues and book coverage
    live_start = max(builds["coinone"].first_minute, builds["upbit"].first_minute)
    ev_since = max(since, live_start)
    sig = signal_events(m1["coinone"], m1["upbit"], configs, tpl.min_gap_min, horizons,
                        since=ev_since, until=until, registry=registry)
    pooled = pool_events(sig.events)
    ev_t = pd.DatetimeIndex(pooled["t"]) if len(pooled) else pd.DatetimeIndex([], tz="UTC")
    ctl_t = control_times(sig, ev_t, max_n=max_control, seed=seed)
    kw = dict(latencies=latencies, notional=notional, horizons=horizons, primary_latency=primary_latency)
    em = measure(ev_t, m1["coinone"], builds["coinone"].first_trade, books["coinone"], books["upbit"], cost, **kw)
    cm = measure(ctl_t, m1["coinone"], builds["coinone"].first_trade, books["coinone"], books["upbit"], cost, **kw)
    events = pd.concat([pooled.reset_index(drop=True), em.table.drop(columns=["t"])], axis=1) if len(pooled) else em.table
    cov["event_window"] = [str(ev_since), str(until)]
    cov["eligible_minutes"] = len(sig.pool_t)
    cov["eligible_hours"] = len(sig.pool_t) / 60.0
    params = {"since": str(since), "until": str(until), "template": str(template), "latencies": list(latencies),
              "primary_latency": primary_latency, "notional_krw": notional, "horizons": list(horizons),
              "cost": {"name": cost.name, "spread_bps": cost.spread_bps, "slip_bps": cost.slip_bps,
                       "fee_rate": cost.fee_rate, "entry_side_bp": cost.side_cost * 1e4,
                       "round_trip_bp": -cost.net_return(cost.buy_fill(1.0), cost.sell_fill(1.0)) * 1e4},
              "min_gap_min": tpl.min_gap_min, "rest_warmup": rest_warmup, "grace_s": GRACE_S,
              "min_deficit_bp": MIN_DEFICIT_BP, "baseline_win_min": list(BASELINE_WIN_MIN),
              "n_configs": len(configs)}
    verdict = make_verdict(events, cm.table, params, cov)
    return StudyResult(params, cov, sig.per_config, events, cm.table, em, cm, verdict)


# ============================================================== verdict
BT_EXCESS_BP = (1.3, 2.9)       # Dev matched-null excess at 5 min, camp-000001 (fact-000001)


def make_verdict(ev: pd.DataFrame, ctl: pd.DataFrame, params: dict, cov: dict) -> dict:
    L = params["primary_latency"]
    side_bp = params["cost"]["entry_side_bp"]
    col = f"slip_walk_bp_L{L:g}"
    m = ev["measurable"].astype(bool) if "measurable" in ev and len(ev) else pd.Series(dtype=bool)
    e = ev[m] if len(ev) else ev
    n = len(e)
    slip = boot_mean(e[col]) if n else (math.nan,) * 3 + (0,)
    slip_c = boot_mean(ctl[col]) if len(ctl) else (math.nan,) * 3 + (0,)
    extra = slip[0] - side_bp if np.isfinite(slip[0]) else math.nan
    h = max(params["horizons"])
    net_book = boot_mean(e[f"exec_book_net_bp_h{h}"]) if n else (math.nan,) * 3 + (0,)
    net_bt = boot_mean(e[f"bt_net_bp_h{h}"]) if n else (math.nan,) * 3 + (0,)
    sd = float(np.nanstd(e[col], ddof=1)) if n > 2 else math.nan
    sd_r = float(np.nanstd(e[f"exec_book_net_bp_h{h}"], ddof=1)) if n > 2 else math.nan
    rate = n / cov["eligible_hours"] if cov.get("eligible_hours") else math.nan
    need_slip = n_needed(sd, 0.5)
    need_ret = math.ceil(((1.96 + 0.84) * sd_r / 2.0) ** 2) if np.isfinite(sd_r) and sd_r > 0 else math.nan
    if n < MIN_EVENTS_FOR_VERDICT:
        status = "INSUFFICIENT_DATA"
    elif np.isfinite(net_book[1]) and net_book[1] > 0:
        status = "EXECUTABLE"
    elif np.isfinite(slip[1]) and slip[1] - side_bp > BT_EXCESS_BP[1]:
        status = "NOT_EXECUTABLE"          # extra entry shortfall alone exceeds the whole Dev excess
    elif np.isfinite(extra) and extra > BT_EXCESS_BP[0]:
        status = "LIKELY_NOT_EXECUTABLE"
    else:
        status = "UNDETERMINED"
    return {"status": status, "n_events": n, "slip_mean_ci": slip[:3], "slip_control_mean_ci": slip_c[:3],
            "extra_vs_model_bp": extra, "entry_side_model_bp": side_bp, "net_book_h": h,
            "net_book_mean_ci": net_book[:3], "net_bt_mean_ci": net_bt[:3], "events_per_eligible_hour": rate,
            "n_needed_slip_pm0.5bp": need_slip, "n_needed_detect_2bp_net": need_ret,
            "hours_needed_slip": need_slip / rate if rate and np.isfinite(need_slip) else math.nan,
            "hours_needed_net": need_ret / rate if rate and np.isfinite(need_ret) else math.nan}


# ============================================================== report
def _f(x, nd=2) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    return "–" if not np.isfinite(x) else f"{x:.{nd}f}"


def _ci(t) -> str:
    m, lo, hi = t[:3]
    if not np.isfinite(m):
        return "–"
    if not np.isfinite(lo):
        return f"{m:.2f}"
    return f"{m:.2f} [{lo:.2f}, {hi:.2f}]"


def _q(x, qs=(0.1, 0.5, 0.9)) -> str:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return "–"
    return " / ".join(f"{np.quantile(x, q):.1f}" for q in qs)


def _metric_rows(ev: pd.DataFrame, ctl: pd.DataFrame, metrics) -> list[str]:
    out = ["| metric | events: mean [95% CI] (n) | p10 / p50 / p90 | control: mean [95% CI] (n) | "
           "event − control [95% CI] |", "|---|---|---|---|---|"]
    for col, name in metrics:
        if col not in ev and col not in ctl:
            continue
        a = ev[col] if col in ev else pd.Series(dtype=float)
        b = ctl[col] if col in ctl else pd.Series(dtype=float)
        ea, cb = boot_mean(a), boot_mean(b)
        d = boot_diff(a, b)
        out.append(f"| {name} | {_ci(ea)} ({ea[3]}) | {_q(a)} | {_ci(cb)} ({cb[3]}) | {_ci(d)} |")
    return out


def render_markdown(res: StudyResult) -> str:
    p, cov, v = res.params, res.coverage, res.verdict
    L = p["primary_latency"]
    ev = res.events[res.events["measurable"].astype(bool)] if len(res.events) else res.events
    ctl = res.control[res.control["measurable"].astype(bool)] if len(res.control) else res.control
    lines = ["# Execution study — Upbit→Coinone 1m cross-venue signal (live WS data)", "",
             f"Generated {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC by "
             "`python -m btc_quant.research.execution_study`. Forward segment only "
             f"(Forward start {cov['forward_start']}); lockbox / Val not read. Not a ledger trial "
             "(measurement of execution, no new hypothesis test).", "",
             f"**Verdict: {v['status']}** — {verdict_text(v, p)}", "",
             "## Data", "",
             f"- Window: since {p['since']} until {p['until']}; events evaluated in {cov['event_window'][0]} .. "
             f"{cov['event_window'][1]}; eligible decision minutes (session filter etc.) {cov['eligible_minutes']} "
             f"(= {cov['eligible_hours']:.1f} h).",
             f"- Template `{p['template']}` ({p['n_configs']} configs), declustering {p['min_gap_min']:g} min, "
             f"cost `{p['cost']['name']}` v-current: spread {p['cost']['spread_bps']:g} bp + slip "
             f"{p['cost']['slip_bps']:g} bp/side → entry-side charge {p['cost']['entry_side_bp']:.1f} bp, round trip "
             f"{p['cost']['round_trip_bp']:.1f} bp.",
             f"- Latencies L = {', '.join(f'{x:g}' for x in p['latencies'])} s (primary {L:g} s); notional "
             f"{p['notional_krw']:,.0f} KRW; bars via MinuteBarBuilder (grace {p['grace_s']:g} s), REST warm-up "
             f"{'on' if p['rest_warmup'] else 'off'}.", "",
             "| venue | trades (used / late / dup) | live minutes (real bars) | REST seed bars | book snapshots "
             "(median / p95 interval s) | recv−exchange clock median s | collector gaps |",
             "|---|---|---|---|---|---|---|"]
    for ex in ("coinone", "upbit"):
        c = cov[ex]
        lines.append(f"| {ex} | {c['trades']} ({c['trades_used']} / {c['late_trades']} / {c['duplicate_trades']}) | "
                     f"{c['live_minutes']} ({c['live_real_bars']}) | {c['rest_seed_bars']} | {c['book_snapshots']} "
                     f"({_f(c['book_interval_median_s'])} / {_f(c['book_interval_p95_s'])}) | "
                     f"{_f(c['recv_minus_exchange_median_s'], 3)} | {c['collector_gaps']} |")
    lines += ["", "## Signal events (per config, live)", "",
              "| config | eligible min | raw triggers | events (declustered) |", "|---|---|---|---|"]
    for _, r in res.per_config.iterrows():
        lines.append(f"| `{r['config']}` | {r['n_pool']} | {r['n_triggers']} | {r['n_events']} |")
    n_lead = int(ev["families"].str.contains("lead").sum()) if len(ev) and "families" in ev else 0
    n_gap = int(ev["families"].str.contains("gap").sum()) if len(ev) and "families" in ev else 0
    lines += ["", f"Pooled (union over configs, one row per decision minute): **{len(res.events)} events**, "
              f"{len(ev)} measurable (book present at t+{L:g} s and minute-t open printed); lead family {n_lead}, "
              f"gap family {n_gap}. Control: {len(res.control)} matched minutes ({len(ctl)} measurable).", ""]

    lat = p["latencies"]
    m_entry = [("open_delay_s", "first Coinone print in minute t, s after t"),
               ("stale_print_bp", "Coinone mid(t) vs last print (bar close), bp"),
               ("open_vs_mid_bp", "backtest open (a) vs Coinone mid(t), bp"),
               ("co_spread_bp_t", "Coinone spread at t, bp")]
    for x in lat:
        m_entry += [(f"slip_top_bp_L{x:g}", f"best ask @t+{x:g}s − a, bp"),
                    (f"slip_walk_bp_L{x:g}", f"ask-walk VWAP {p['notional_krw']/1e3:,.0f}k @t+{x:g}s − a, bp")]
    m_entry += [(f"half_spread_bp_L{L:g}", f"half spread @t+{L:g}s, bp"),
                (f"impact_bp_L{L:g}", f"walk VWAP − best ask @t+{L:g}s, bp"),
                (f"book_age_s_L{L:g}", f"age of the snapshot used @t+{L:g}s, s")]
    lines += ["## Entry: backtest open (a) vs executable (b)", "",
              f"Cost model entry-side charge = {p['cost']['entry_side_bp']:.1f} bp (half spread "
              f"{p['cost']['spread_bps']/2:g} + slip {p['cost']['slip_bps']:g}). Slippage (b − a) above that is "
              "shortfall the backtest does not charge.", ""] + _metric_rows(ev, ctl, m_entry)

    m_cu = [("deficit_bp", "book deficit D0 (Coinone cheap vs Upbit, vs [t−20,t−5] min median), bp"),
            ("already_caught_up", "share with D0 < 1 bp (book already caught up at t)"),
            ("t50_s", "catch-up 50 % time, s (uncensored)"), ("t90_s", "catch-up 90 % time, s"),
            ("t50_s_censored", "share not 50 % caught up by 300 s")]
    for x in lat:
        m_cu.append((f"closed_before_L{x:g}", f"deficit share closed by t+{round(x)}s"))
    m_cu += [("upbit_ret_prev60_bp", "Upbit mid return t−60s→t, bp"),
             ("coinone_ret_prev60_bp", "Coinone mid return t−60s→t, bp")]
    lines += ["", "## Catch-up (Coinone book after the signal)", ""] + _metric_rows(ev, ctl, m_cu)
    lines += ["", "Mean mid paths (bp vs the mid at t; s relative to the decision time t):", "",
              "| s | Coinone mid (events) | Upbit mid (events) | deficit closure (events, median) | Coinone mid (control) |",
              "|---|---|---|---|---|"]
    S = res.ev_meas.path_s
    em_mask = res.events["measurable"].to_numpy(bool) if len(res.events) else np.zeros(0, bool)
    cm_mask = res.control["measurable"].to_numpy(bool) if len(res.control) else np.zeros(0, bool)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for s in PATH_REPORT_S:
            j = np.flatnonzero(S == s)
            if j.size == 0:
                continue
            j = int(j[0])
            ce = res.ev_meas.co_path[em_mask, j] if em_mask.any() else np.array([np.nan])
            ue = res.ev_meas.up_path[em_mask, j] if em_mask.any() else np.array([np.nan])
            cl = res.ev_meas.closure[em_mask, j] if em_mask.any() else np.array([np.nan])
            cc = res.ctl_meas.co_path[cm_mask, j] if cm_mask.any() else np.array([np.nan])
            lines.append(f"| {s} | {_f(np.nanmean(ce))} | {_f(np.nanmean(ue))} | {_f(np.nanmedian(cl))} | "
                         f"{_f(np.nanmean(cc))} |")

    m_ret = []
    for h in p["horizons"]:
        m_ret += [(f"bt_gross_bp_h{h}", f"h={h}m backtest gross (open→close), bp"),
                  (f"bt_net_bp_h{h}", f"h={h}m backtest net of cost v2, bp"),
                  (f"exec_model_net_bp_h{h}", f"h={h}m executable entry (walk @t+{L:g}s) + model exit, bp"),
                  (f"exec_book_net_bp_h{h}", f"h={h}m executable both sides (ask walk → bid walk), bp"),
                  (f"exec_mid_gross_bp_h{h}", f"h={h}m Coinone mid→mid (no cost), bp")]
    lines += ["", "## Forward returns", ""] + _metric_rows(ev, ctl, m_ret)

    lines += ["", "## Verdict and what more data is needed", "",
              f"- Status **{v['status']}** with n = {v['n_events']} measurable pooled events "
              f"(verdict needs ≥ {MIN_EVENTS_FOR_VERDICT}).",
              f"- Entry shortfall vs backtest open at L = {L:g} s: {_ci(v['slip_mean_ci'])} bp (control "
              f"{_ci(v['slip_control_mean_ci'])} bp). The model already charges {v['entry_side_model_bp']:.1f} bp, so "
              f"the uncharged extra is {_f(v['extra_vs_model_bp'])} bp vs the Dev excess of "
              f"{BT_EXCESS_BP[0]}–{BT_EXCESS_BP[1]} bp.",
              f"- Fully executable net at {v['net_book_h']} min: {_ci(v['net_book_mean_ci'])} bp vs backtest-style "
              f"net {_ci(v['net_bt_mean_ci'])} bp on the same events.",
              f"- Event rate {_f(v['events_per_eligible_hour'])} pooled events per eligible hour. For a ±0.5 bp CI on "
              f"the entry shortfall: n ≈ {_f(v['n_needed_slip_pm0.5bp'], 0)} (≈ {_f(v['hours_needed_slip'], 0)} "
              f"eligible hours). To detect a +2 bp executable net edge (80 % power): n ≈ "
              f"{_f(v['n_needed_detect_2bp_net'], 0)} (≈ {_f(v['hours_needed_net'], 0)} eligible hours).",
              "", "## Caveats", "",
              "- Book snapshots are sampled ≤ 1/s by the collector (receive clock); sub-second latencies (0.5 s) are "
              "therefore resolved only to the last snapshot received, typically 0–1 s old (see snapshot age row). "
              "The paper trader additionally waits the 2 s bar-close grace, so its real latency is ≈ 2 s + L.",
              "- Top-15 depth only; a walk that runs out of visible depth is reported as missing (never happened if "
              "the n matches).",
              "- Events within a config are ≥ the declustering gap apart, but pooled events from different configs "
              "can be close in time; bootstrap CIs are iid and therefore somewhat optimistic.",
              "- Short live window: one regime / time of day; Dev excess numbers come from 2021–2025.", ""]
    if len(res.events):
        cols = ["t", "families", "a_open", f"slip_walk_bp_L{L:g}", "deficit_bp", "t50_s", "open_delay_s",
                f"bt_net_bp_h{max(p['horizons'])}", f"exec_book_net_bp_h{max(p['horizons'])}"]
        lines += ["## Events (first 60)", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for _, r in res.events.head(60).iterrows():
            lines.append("| " + " | ".join(
                (f"{r[c]:%m-%d %H:%M}" if c == "t" else (str(r[c]) if c == "families" else _f(r[c], 1)))
                for c in cols) + " |")
    lines += ["", "## Re-run", "", "```", "uv run python -m btc_quant.research.execution_study --since "
              f"{p['since'][:16].replace(' ', 'T')}   # --until, --latencies, --notional, --no-rest-warmup, "
              "--events-csv", "```", ""]
    return "\n".join(lines)


def verdict_text(v: dict, p: dict) -> str:
    s = v["status"]
    slip = _ci(v["slip_mean_ci"])
    if s == "INSUFFICIENT_DATA":
        return (f"only {v['n_events']} measurable live events (< {MIN_EVENTS_FOR_VERDICT}); preliminary entry "
                f"shortfall vs the backtest open {slip} bp against the model's {v['entry_side_model_bp']:.1f} bp, "
                f"executable {v['net_book_h']}-min net {_ci(v['net_book_mean_ci'])} bp. Re-run on more hours.")
    if s == "EXECUTABLE":
        return f"executable net at {v['net_book_h']} min is positive with its 95 % CI above 0 ({_ci(v['net_book_mean_ci'])} bp)."
    if s == "NOT_EXECUTABLE":
        return (f"the entry shortfall vs the backtest open ({slip} bp) exceeds the model's "
                f"{v['entry_side_model_bp']:.1f} bp by more than the whole Dev excess even at its CI lower bound.")
    if s == "LIKELY_NOT_EXECUTABLE":
        return (f"mean uncharged entry shortfall {_f(v['extra_vs_model_bp'])} bp exceeds the low end of the Dev "
                f"excess ({BT_EXCESS_BP[0]} bp); executable net {_ci(v['net_book_mean_ci'])} bp.")
    return (f"entry shortfall {slip} bp and executable net {_ci(v['net_book_mean_ci'])} bp do not separate the "
            "cases yet.")


# ============================================================== CLI
def _parse_floats(s: str) -> tuple[float, ...]:
    return tuple(float(x) for x in s.split(",") if x.strip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m btc_quant.research.execution_study", description=__doc__.split("\n")[0])
    ap.add_argument("--since", required=True, help="UTC start of live data to use (>= Forward start)")
    ap.add_argument("--until", default=None, help="UTC end (default: now)")
    ap.add_argument("--root", default=".")
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--template", default=DEFAULT_TEMPLATE)
    ap.add_argument("--latencies", default=",".join(f"{x:g}" for x in DEFAULT_LATENCIES))
    ap.add_argument("--primary-latency", type=float, default=PRIMARY_LATENCY)
    ap.add_argument("--notional", type=float, default=DEFAULT_NOTIONAL)
    ap.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS))
    ap.add_argument("--no-rest-warmup", action="store_true")
    ap.add_argument("--warmup-minutes", type=int, default=300)
    ap.add_argument("--report", default=None, help="default research/reports/execution_study_<YYYYMMDD>.md")
    ap.add_argument("--events-csv", default=None)
    ap.add_argument("--json", default=None, help="write the summary (coverage, per-config, verdict) as JSON")
    a = ap.parse_args(argv)
    res = run_study(a.since, a.until, root=a.root, data_root=a.data_root, template=a.template,
                    latencies=_parse_floats(a.latencies), notional=a.notional,
                    horizons=tuple(int(x) for x in _parse_floats(a.horizons)),
                    primary_latency=a.primary_latency, rest_warmup=not a.no_rest_warmup,
                    warmup_minutes=a.warmup_minutes)
    md = render_markdown(res)
    out = Path(a.report) if a.report else (Path(a.root) / "research" / "reports"
                                            / f"execution_study_{pd.Timestamp(res.params['until']):%Y%m%d}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    if a.events_csv:
        pd.concat([res.events.assign(set="event"), res.control.assign(set="control")]).to_csv(a.events_csv, index=False)
    if a.json:
        Path(a.json).write_text(json.dumps({"params": res.params, "coverage": res.coverage,
                                            "per_config": res.per_config.to_dict("records"),
                                            "verdict": res.verdict}, indent=1, default=str))
    v = res.verdict
    print(f"{v['status']}: n={v['n_events']} slip(L={res.params['primary_latency']:g})={_ci(v['slip_mean_ci'])} bp "
          f"net_book={_ci(v['net_book_mean_ci'])} bp → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
