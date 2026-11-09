"""전진 인큐베이션 (PLAN §10b-5): Validated·Challenger 전략을 Forward 구간에서 매일 사후 전진 테스트.

    python -m btc_quant.research.forward run [--as-of YYYY-MM-DD] [--no-fetch] [--root DIR]

예약 작업이 Claude 없이 매일 돌리도록 만든 저비용·멱등 절차:
1. (기본) 코인원·업비트 REST 로 Forward 시작 이후 1분봉 중 저장소에 아직 없는 부분만 받아
   data/raw 에 추가 (gates.yaml G8.forward_exchanges). 수집 요청 시작은 항상 Forward 시작 이후 —
   잠금 구간 데이터를 받거나 읽지 않는다. 거래소 하나가 실패해도 나머지는 계속한다.
2. 레지스트리의 G8.forward_states(기본 validated, challenger) 전략마다 Forward 데이터만
   DataAccess.read_range(forward_start, as_of+1일) 로 읽어 빠른 엔진 실행.
3. 결과를 원장 forward_results 에 (strategy_key, logic_hash, as_of) 당 1줄로 기록 — 이미 있으면
   다시 계산하지 않는다(멱등). 리포트 research/reports/forward_<as_of>.md 는 원장 기록만으로 다시
   만들어지므로 재실행해도 같은 내용이다.

워밍업 결정 (문서화된 선택)
- 지표 워밍업에 Forward 이전 데이터를 쓰지 않는다: 직전 구간이 잠금 구간이기 때문(잠금 구간이
  롤링돼 Forward 직전이 dev/val 이 되더라도 같은 규칙을 유지해 결과의 정의가 바뀌지 않게 한다).
- 대신 Forward 시작 후 G8.forward_warmup_days(기본 14일 = 파이프라인 G6 Val 워밍업과 동일) 동안은
  신호 계산만 하고 거래·평가를 하지 않는다. 평가 구간 = [forward_start + warmup, as_of + 1일).
  그 전에는 status=warming_up 레코드만 남긴다.
- 보조 데이터 블록(업비트 등, `requires`)은 보조 거래소 1분봉도 **같은 Forward 창**에서만 읽어 캠페인·페이퍼와
  같은 방식(regularize(clean_wicks) → to_tf, 대상 봉에 reindex, ffill 없음)으로 ctx.aux 를 만든다.
- 7일 이상 창을 쓰는 블록(vol_regime 순위 등)은 워밍업 직후 백테스트보다 짧은 이력을 본다
  (G6 Val 과 같은 한계). 일정 기간이 지나면 사라진다.

기대 구간 (vs backtest)
- 원장의 Dev 실행(같은 logic_hash, 전략 캠페인의 primary 비용) 지표 mean_daily·std_daily·
  trades_per_day 로 n일 누적 수익의 정규근사 구간 n·μ ± z·σ·√n (z: G8.pnl_band 양측, 기본 90%),
  거래 수는 포아송 근사 λ = n·tpd ± z·√λ. 전진 수익이 구간 밖이면 within_band=false.
  (G8 의 실측 페이퍼 조건과는 별개 — 이것은 사후 전진 테스트이다.)
"""
from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd
from scipy import stats

from btc_quant.backtest import fast
from btc_quant.evaluation import metrics as M
from btc_quant.params import gate_config_for_campaign
from btc_quant.registry.strategies import key_of
from btc_quant.strategy.blocks import REGISTRY

from .campaign import (
    DEFAULT_COSTS,
    HALF_LIFE_DAYS,
    CampaignError,
    ResearchEnv,
    aux_context,
    config_root,
    get_cost,
    git_commit,
    prepare_m1,
    run_params,
)

DEFAULT_STATES = ("validated", "challenger")
DEFAULT_EXCHANGES = ("coinone", "upbit")
DEFAULT_WARMUP_DAYS = 14
SYMBOL = "BTC-KRW"
TIMEFRAME = "1m"

# 저장소에 해당 시계열·구간이 없을 때 read_ohlcv 가 던지는 예외들
_NO_DATA = (FileNotFoundError, duckdb.IOException)

Fetcher = Callable[[str, pd.Timestamp, pd.Timestamp], pd.DataFrame]


# ============================================================== 수집
def rest_fetcher(exchange: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """기존 REST 수집기로 [start, end) 1분봉 (KRW-BTC)."""
    if exchange == "coinone":
        from btc_quant.data import coinone
        return coinone.fetch_candles("KRW-BTC", 1, start, end, progress=False)
    if exchange in ("upbit", "bithumb"):
        from btc_quant.data import upbit
        return upbit.fetch_candles(exchange, "KRW-BTC", 1, start.to_pydatetime(), end.to_pydatetime(),
                                   progress=False)
    raise ValueError(f"no REST fetcher for {exchange!r}")


def _stored_last(env: ResearchEnv, exchange: str, start, end) -> tuple[pd.Timestamp | None, int]:
    """Forward 구간 [start, end) 안의 저장된 마지막 봉 시각과 행 수 (DataAccess 경유)."""
    try:
        df = env.data.read_range(start, end, exchange, SYMBOL, TIMEFRAME)
    except _NO_DATA:
        return None, 0
    if df.empty:
        return None, 0
    return pd.Timestamp(df.index.max()), len(df)


def fetch_forward(env: ResearchEnv, exchanges, fwd_start: pd.Timestamp, window_end: pd.Timestamp,
                  fetcher: Fetcher | None = None, log: Callable[[str], None] = lambda s: None) -> dict:
    """저장소에 없는 Forward 1분봉을 받아 추가. 요청 시작은 항상 >= fwd_start (잠금 구간 미접근)."""
    from btc_quant.data.store import write_ohlcv

    fetcher = fetcher or rest_fetcher
    out = {}
    for ex in exchanges:
        last, _ = _stored_last(env, ex, fwd_start, window_end)
        start = max(fwd_start, last + pd.Timedelta(minutes=1)) if last is not None else fwd_start
        if start >= window_end:
            out[ex] = {"fetched": 0, "note": "up to date"}
            continue
        try:
            df = fetcher(ex, start, window_end)
        except Exception as e:   # noqa: BLE001 — 예약 작업: 거래소 하나의 실패로 전체를 멈추지 않는다
            out[ex] = {"fetched": 0, "error": f"{type(e).__name__}: {e}"}
            log(f"fetch {ex} failed: {e}")
            continue
        if df is None or df.empty:
            out[ex] = {"fetched": 0, "start": start.isoformat()}
            continue
        idx = pd.DatetimeIndex(df.index)
        idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        df = df.set_axis(idx)
        df = df[(df.index >= max(start, fwd_start)) & (df.index < window_end)]   # 이중 안전장치
        if not df.empty:
            write_ohlcv(df, ex, SYMBOL, TIMEFRAME, root=env.data.data_root)
        out[ex] = {"fetched": len(df), "start": start.isoformat(), "end": window_end.isoformat()}
        log(f"fetch {ex}: {len(df)} rows [{start}, {window_end})")
    return out


# ============================================================== 전략
@dataclass
class ForwardStrategy:
    key: str
    id: str
    version: int
    state: str
    spec: object
    logic_hash: str
    exchange: str
    cost: str
    campaign_id: str | None


def _context(env: ResearchEnv, sid: str, version: int) -> dict:
    """전략을 만든 캠페인(원장 전이 증거의 campaign_id) → 거래소·primary 비용."""
    camp_id = None
    for t in env.ledger.transitions(sid):
        if int(t.get("version", -1)) == int(version):
            camp_id = (t.get("evidence") or {}).get("campaign_id") or camp_id
    exchange, cost = "coinone", DEFAULT_COSTS["primary"]
    if camp_id:
        camp = env.ledger.get("campaigns", camp_id) or {}
        grid = camp.get("grid") or {}
        exchange = (grid.get("data") or {}).get("exchange", exchange)
        cost = (grid.get("costs") or {}).get("primary", cost)
    return {"campaign_id": camp_id, "exchange": exchange, "cost": cost}


def resolve_strategies(env: ResearchEnv, states=DEFAULT_STATES) -> list[ForwardStrategy]:
    out = []
    for st in states:
        for x in env.registry.list(st):
            spec = env.registry.load(x["id"], x["version"])
            ctx = _context(env, x["id"], x["version"])
            out.append(ForwardStrategy(key_of(x["id"], x["version"]), x["id"], x["version"], st, spec,
                                       spec.logic_hash(), ctx["exchange"], ctx["cost"], ctx["campaign_id"]))
    return out


# ============================================================== 기대 구간
def expectation_band(env: ResearchEnv, logic_hash: str, cost: str, n_days: int, band: float) -> dict | None:
    """원장 Dev 실행 지표로 n일 누적 수익·거래 수 구간 (정규·포아송 근사)."""
    runs = [r for r in env.ledger.records("runs")
            if r.get("logic_hash") == logic_hash and r.get("cost_name") == cost
            and (r.get("period") or {}).get("segment") == "dev"]
    if not runs or n_days <= 0:
        return None
    m = runs[-1].get("metrics") or {}
    mu, sd, tpd = m.get("mean_daily"), m.get("std_daily"), m.get("trades_per_day")
    if mu is None or sd is None:
        return None
    z = float(stats.norm.ppf(0.5 + band / 2))
    lam = float(tpd or 0.0) * n_days
    return {"source": f"dev run {runs[-1]['id']} ({cost})", "band": band, "n_days": n_days,
            "expected_return": mu * n_days,
            "return_lo": mu * n_days - z * sd * math.sqrt(n_days),
            "return_hi": mu * n_days + z * sd * math.sqrt(n_days),
            "expected_trades": lam, "trades_lo": max(0.0, lam - z * math.sqrt(lam)),
            "trades_hi": lam + z * math.sqrt(lam), "dev_sharpe": m.get("sharpe")}


# ============================================================== 평가
def spec_aux_keys(spec, registry=REGISTRY) -> list[str]:
    """스펙 블록(진입·필터·신호 청산)이 요구하는 보조 데이터 키 (예: ["upbit"])."""
    refs = [spec.entry, *spec.filters]
    if spec.exit.signal_exit:
        refs.append(spec.exit.signal_exit)
    keys: list[str] = []
    for r in refs:
        for k in registry.get(r.block).requires:
            if k not in keys:
                keys.append(k)
    return keys


def forward_aux_context(env: ResearchEnv, spec, fwd_start: pd.Timestamp, window_end: pd.Timestamp):
    """보조 데이터가 필요한 스펙의 ctx (필요 없으면 None).

    research/campaign.aux_context·페이퍼와 같은 구성: 보조 거래소 1분봉을 **Forward 구간만**
    (`read_range(fwd_start, window_end)` — 대상 시계열과 같은 창, 잠금 구간 미접근) 읽어
    regularize(clean_wicks) → 결정 주기 봉 `to_tf` → 블록이 대상 봉 index 에 reindex(ffill 없음).
    반환 (ctx, rows) 또는 데이터가 없으면 CampaignError."""
    keys = spec_aux_keys(spec)
    if not keys:
        return None, {}
    aux_m1, rows = {}, {}
    for a in keys:
        try:
            raw = env.data.read_range(fwd_start, window_end, a, SYMBOL, TIMEFRAME)
        except _NO_DATA:
            raw = pd.DataFrame()
        if raw.empty:
            raise CampaignError(f"no {a} aux data in the forward segment")
        aux_m1[a] = prepare_m1(raw, a)
        rows[a] = len(raw)
    return aux_context(aux_m1, spec.tf_min), rows


def evaluate(env: ResearchEnv, fs: ForwardStrategy, fwd_start: pd.Timestamp, window_end: pd.Timestamp,
             warmup_days: int, band: float) -> dict:
    eval_start = fwd_start + pd.Timedelta(days=warmup_days)
    base = {"strategy_id": fs.id, "version": fs.version, "state": fs.state, "exchange": fs.exchange,
            "cost": fs.cost, "campaign_id": fs.campaign_id,
            "period": {"segment": "forward", "forward_start": fwd_start.isoformat(),
                       "eval_start": eval_start.isoformat(), "end": window_end.isoformat(),
                       "warmup_days": warmup_days}}
    if window_end <= eval_start:
        return {**base, "status": "warming_up",
                "days_until_eval": math.ceil((eval_start - window_end) / pd.Timedelta(days=1))}
    try:
        raw = env.data.read_range(fwd_start, window_end, fs.exchange, SYMBOL, TIMEFRAME)
        m1 = prepare_m1(raw, fs.exchange)
    except (*_NO_DATA, CampaignError):
        return {**base, "status": "no_data"}
    if m1.index.max() < eval_start:
        return {**base, "status": "no_data", "note": "no bars after warm-up"}
    try:
        ctx, aux_rows = forward_aux_context(env, fs.spec, fwd_start, window_end)
    except CampaignError as e:
        return {**base, "status": "no_data", "note": str(e)}
    res = fast.run_fast(fs.spec, m1, get_cost(fs.cost, config_root(env)), eval_start, window_end, ctx=ctx)
    met = M.summarize(res, HALF_LIFE_DAYS)
    out = {**base, "status": "ok", "n_days": met["n_days"], "n_trades": met["n_trades"],
           "net_return": met["total_return"], "sharpe": met["sharpe"], "mean_daily": met["mean_daily"],
           "max_dd": met["max_dd"], "expectancy": met["expectancy"], "win_rate": met["win_rate"],
           "data_rows": len(raw), "data_last_ts": pd.Timestamp(raw.index.max()).isoformat()}
    if aux_rows:
        out["aux_rows"] = aux_rows
    exp = expectation_band(env, fs.logic_hash, fs.cost, met["n_days"], band)
    out["expected"] = exp
    out["within_band"] = (None if exp is None
                          else bool(exp["return_lo"] <= met["total_return"] <= exp["return_hi"]))
    return out


# ============================================================== 실행
def _as_of(as_of, now: pd.Timestamp) -> pd.Timestamp:
    if as_of is None:
        return now.floor("D") - pd.Timedelta(days=1)       # 마지막 완료 UTC 일
    t = pd.Timestamp(as_of)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.floor("D")


def run_forward(root: str | Path | None = None, env: ResearchEnv | None = None, as_of=None,
                fetch: bool = True, fetcher: Fetcher | None = None, now=None, states=None,
                gate_cfg: dict | None = None, log: Callable[[str], None] = lambda s: None) -> dict:
    env = env or ResearchEnv.at(root)
    cfg = gate_cfg if gate_cfg is not None else gate_config_for_campaign(None, root=config_root(env))
    g8 = cfg.get("G8") or {}
    states = tuple(states or g8.get("forward_states") or DEFAULT_STATES)
    exchanges = tuple(g8.get("forward_exchanges") or DEFAULT_EXCHANGES)
    warmup = int(g8.get("forward_warmup_days", DEFAULT_WARMUP_DAYS))
    band = float(g8.get("pnl_band", 0.90))
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    day = _as_of(as_of, now)
    as_of_s = day.date().isoformat()
    fwd_start, fwd_end = env.data.segment_bounds("forward", now=now)
    if day + pd.Timedelta(days=1) > now:
        raise ValueError(f"as-of {as_of_s} is not a completed UTC day (now {now})")
    window_end = min(day + pd.Timedelta(days=1), fwd_end)     # Forward 에 끝이 지정된 경우(롤링 후) 그 끝까지
    if window_end <= fwd_start:
        raise ValueError(f"as-of {as_of_s} is before the forward segment start {fwd_start.date()}")
    fetched = fetch_forward(env, exchanges, fwd_start, window_end, fetcher, log) if fetch else None
    pver = run_params(env)                     # params_version + cost_versions (docs/PARAMS.md §4)
    results, new = [], 0
    for fs in resolve_strategies(env, states):
        prior = [r for r in env.ledger.forward_results(fs.key, as_of_s) if r["logic_hash"] == fs.logic_hash]
        if prior:
            results.append(prior[0])
            continue
        rec = evaluate(env, fs, fwd_start, window_end, warmup, band)
        rec["code_commit"] = git_commit(env.code_root)
        rec["params_version"] = pver.get("params_version")
        rec["cost_versions"] = pver.get("cost_versions")
        rid, created = env.ledger.record_forward_result(fs.key, fs.logic_hash, as_of_s, rec)
        new += int(created)
        results.append(env.ledger.get("forward_results", rid))
        log(f"{fs.key}: {rec['status']}")
    coverage = {ex: _coverage(env, ex, fwd_start, window_end) for ex in exchanges}
    summary = {"as_of": as_of_s, "forward_start": fwd_start.isoformat(), "window_end": window_end.isoformat(),
               "warmup_days": warmup, "states": list(states), "fetch": fetched, "coverage": coverage,
               "n_strategies": len(results), "n_new": new, "results": results}
    env.reports_dir.mkdir(parents=True, exist_ok=True)
    path = env.reports_dir / f"forward_{as_of_s}.md"
    path.write_text(render(summary, env), encoding="utf-8")
    summary["report"] = str(path)
    return summary


def _coverage(env: ResearchEnv, ex: str, start, end) -> dict:
    last, n = _stored_last(env, ex, start, end)
    expected = int((end - start) / pd.Timedelta(minutes=1))
    return {"rows": n, "last_ts": last.isoformat() if last is not None else None,
            "expected_rows": expected}


# ============================================================== 리포트
def _f(x, nd=4) -> str:
    if x is None:
        return "–"
    if isinstance(x, bool):
        return "yes" if x else "NO"
    if isinstance(x, float):
        return f"{x:.{nd}f}" if math.isfinite(x) else "–"
    return str(x)


def render(summary: dict, env: ResearchEnv) -> str:
    L = [f"# Forward incubation — {summary['as_of']}\n",
         f"Forward segment from {summary['forward_start'][:10]}; evaluated window ends "
         f"{summary['window_end']} (exclusive). Warm-up: first {summary['warmup_days']} Forward days compute "
         "signals only (no Forward-external / lockbox data is used for warm-up). States: "
         + ", ".join(summary["states"]) + ".\n",
         "## Data coverage (1m, Forward segment)\n",
         "| exchange | rows | expected | last bar |", "|---|---|---|---|"]
    for ex, c in summary["coverage"].items():
        L.append(f"| {ex} | {c['rows']} | {c['expected_rows']} | {c['last_ts'] or '–'} |")
    L.append("\n## Strategies\n")
    rows = summary["results"]
    if not rows:
        L.append("_No validated/challenger strategies in the registry._\n")
    else:
        L.append("| strategy | state | status | days | trades | net return | Sharpe | expected (band) | "
                 "in band | trades expected |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            e = r.get("expected") or {}
            band = (f"{_f(e.get('expected_return'))} [{_f(e.get('return_lo'))}, {_f(e.get('return_hi'))}]"
                    if e else "–")
            tr = (f"{_f(e.get('expected_trades'), 1)} [{_f(e.get('trades_lo'), 1)}, {_f(e.get('trades_hi'), 1)}]"
                  if e else "–")
            status = r["status"] + (f" ({r['days_until_eval']}d left)" if r.get("days_until_eval") else "")
            L.append(f"| `{r['strategy_key']}` | {r.get('state')} | {status} | {_f(r.get('n_days'))} | "
                     f"{_f(r.get('n_trades'))} | {_f(r.get('net_return'))} | {_f(r.get('sharpe'), 2)} | {band} | "
                     f"{_f(r.get('within_band'))} | {tr} |")
        L.append("")
        out_band = [r["strategy_key"] for r in rows if r.get("within_band") is False]
        if out_band:
            L.append("**Outside the backtest band:** " + ", ".join(f"`{k}`" for k in out_band)
                     + " — flag for review (no automatic state change).\n")
        hist = {}
        for r in env.ledger.forward_results():
            if r.get("status") == "ok":
                hist.setdefault(r["strategy_key"], []).append(r)
        if hist:
            L.append("## History (ok days recorded so far)\n")
            for k, hs in sorted(hist.items()):
                hs = [h for h in hs if h["as_of"] <= summary["as_of"]]
                if hs:
                    n_in = sum(1 for h in hs if h.get("within_band"))
                    L.append(f"- `{k}`: {len(hs)} daily evaluations, within band {n_in}/{len(hs)}")
            L.append("")
    L.append("Expected band: Dev ledger metrics (mean/std of daily return, trades/day) of the same logic hash "
             "and primary cost, normal approximation over the evaluated days. This is a post-hoc forward test; "
             "G8 (paper trading) is separate.\n")
    return "\n".join(L)


# ============================================================== CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m btc_quant.research.forward",
                                 description="Forward incubation of validated/challenger strategies")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="fetch new Forward 1m data, evaluate, record, write report")
    r.add_argument("--as-of", default=None, help="UTC date YYYY-MM-DD (default: last completed UTC day)")
    r.add_argument("--no-fetch", action="store_true", help="do not call exchange REST APIs")
    r.add_argument("--root", default=None, help="repository root (default: auto)")
    r.add_argument("--states", default=None, help="comma-separated registry states")
    a = ap.parse_args(argv)
    states = tuple(s.strip() for s in a.states.split(",")) if a.states else None
    out = run_forward(root=a.root, as_of=a.as_of, fetch=not a.no_fetch, states=states, log=print)
    print(f"forward {out['as_of']}: {out['n_strategies']} strategies ({out['n_new']} new records) → {out['report']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
