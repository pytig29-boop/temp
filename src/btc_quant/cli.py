"""btc-quant CLI.

예)
  uv run btc-quant fetch-candles --exchange upbit --market KRW-BTC --unit 1 --start 2025-11-01 --end 2026-11-09
  uv run btc-quant fetch-binance --symbol BTCUSDT --start 2025-11 --end 2026-10
  uv run btc-quant sample-orderbook --exchange upbit --market KRW-BTC --seconds 600
  uv run btc-quant quality --exchange upbit --symbol BTC-KRW
  uv run btc-quant cost-floor --exchange upbit --symbol BTC-KRW --spread-bps 1.0
  uv run btc-quant lead-lag --a upbit:BTC-KRW --b binance:BTC-USDT
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .analysis import cost_floor, lead_lag
from .data import binance_vision, coinone, korbit, orderbook, quality, store, upbit

EXCHANGES = list(upbit.BASE_URLS) + ["korbit", "coinone"]

REPORTS = Path("research/reports")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _symbol(market: str) -> str:
    q, b = market.split("-")  # Upbit 표기 KRW-BTC → 내부 표준 BTC-KRW
    return f"{b}-{q}"


def cmd_fetch_candles(a):
    if a.exchange == "korbit":
        df = korbit.fetch_candles(a.market, a.unit, _dt(a.start), _dt(a.end))
    elif a.exchange == "coinone":
        df = coinone.fetch_candles(a.market, a.unit, _dt(a.start), _dt(a.end))
    else:
        df = upbit.fetch_candles(a.exchange, a.market, a.unit, _dt(a.start), _dt(a.end))
    files = store.write_ohlcv(df, a.exchange, _symbol(a.market), f"{a.unit}m")
    print(f"{len(df):,} candles → {len(files)} files")


def cmd_fetch_binance(a):
    sym = a.symbol.replace("USDT", "-USDT")
    for m in binance_vision.month_range(a.start, a.end):
        df = binance_vision.fetch_monthly(a.symbol, a.interval, m)
        store.write_ohlcv(df, "binance", sym, a.interval)
        print(f"{m}: {len(df):,} rows")


def cmd_sample_orderbook(a):
    df = orderbook.sample(a.exchange, a.market, a.seconds, a.interval)
    out = Path("data/raw") / f"exchange={a.exchange}" / f"symbol={_symbol(a.market)}" / "orderbook"
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / f"snap_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.parquet")
    print(df.describe().T[["mean", "50%", "max"]].to_markdown(floatfmt=".2f"))


def cmd_quality(a):
    df = store.read_ohlcv(a.exchange, a.symbol, a.timeframe, a.start, a.end)
    print(quality.check_ohlcv(df, freq=a.timeframe.replace("m", "min")).to_markdown())


def cmd_cost_floor(a):
    raw = store.read_ohlcv(a.exchange, a.symbol, "1m", a.start, a.end)
    q = quality.check_ohlcv(raw)
    m1 = quality.regularize(quality.clean_wicks(raw))
    scen = (cost_floor.zero_fee_scenarios(a.scenarios) if a.scenarios in ("coinone", "korbit")
            else cost_floor.default_scenarios(a.spread_bps, a.bithumb_spread_bps))
    title = f"{a.exchange} {a.symbol} ({m1.index[0]:%Y-%m-%d} ~ {m1.index[-1]:%Y-%m-%d})"
    md = cost_floor.build_report(m1, title, scen, quality_md=q.to_markdown())
    REPORTS.mkdir(parents=True, exist_ok=True)
    f = REPORTS / f"cost_floor_{a.exchange}_{a.scenarios}_{datetime.now(timezone.utc):%Y%m%d}.md"
    f.write_text(md, encoding="utf-8")
    print(f"report → {f}")


def cmd_lead_lag(a):
    (ea, sa), (eb, sb) = a.a.split(":"), a.b.split(":")
    ca = store.read_ohlcv(ea, sa, "1m", a.start, a.end)["close"]
    cb = store.read_ohlcv(eb, sb, "1m", a.start, a.end)["close"]
    print(lead_lag.lead_lag(ca, cb).to_markdown(floatfmt=".4f"))
    print()
    print(lead_lag.conditional_next_move(ca, cb).to_markdown(floatfmt=".3f"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="btc-quant")
    sp = p.add_subparsers(required=True)

    s = sp.add_parser("fetch-candles", help="Upbit/Bithumb 분봉 수집")
    s.add_argument("--exchange", choices=EXCHANGES, default="upbit")
    s.add_argument("--market", default="KRW-BTC")
    s.add_argument("--unit", type=int, default=1)
    s.add_argument("--start", required=True)
    s.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"))
    s.set_defaults(fn=cmd_fetch_candles)

    s = sp.add_parser("fetch-binance", help="data.binance.vision 월별 kline")
    s.add_argument("--symbol", default="BTCUSDT")
    s.add_argument("--interval", default="1m")
    s.add_argument("--start", required=True, help="YYYY-MM")
    s.add_argument("--end", required=True, help="YYYY-MM")
    s.set_defaults(fn=cmd_fetch_binance)

    s = sp.add_parser("sample-orderbook", help="호가 스냅샷으로 스프레드 측정")
    s.add_argument("--exchange", choices=EXCHANGES, default="upbit")
    s.add_argument("--market", default="KRW-BTC")
    s.add_argument("--seconds", type=int, default=600)
    s.add_argument("--interval", type=float, default=2.0)
    s.set_defaults(fn=cmd_sample_orderbook)

    for name, fn in (("quality", cmd_quality), ("cost-floor", cmd_cost_floor)):
        s = sp.add_parser(name)
        s.add_argument("--exchange", default="upbit")
        s.add_argument("--symbol", default="BTC-KRW")
        s.add_argument("--timeframe", default="1m")
        s.add_argument("--start")
        s.add_argument("--end")
        s.add_argument("--spread-bps", type=float, default=1.4)
        s.add_argument("--bithumb-spread-bps", type=float, default=3.3)
        s.add_argument("--scenarios", choices=["default", "coinone", "korbit"], default="default")
        s.set_defaults(fn=fn)

    s = sp.add_parser("lead-lag", help="두 시장 1분 수익률 선후행")
    s.add_argument("--a", default="upbit:BTC-KRW")
    s.add_argument("--b", default="binance:BTC-USDT")
    s.add_argument("--start")
    s.add_argument("--end")
    s.set_defaults(fn=cmd_lead_lag)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
