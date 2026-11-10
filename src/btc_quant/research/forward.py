"""전진 인큐베이션 (PLAN §10b-5): Validated·Challenger 전략을 Forward 구간에서 매일 사후 전진 테스트.

    python -m btc_quant.research.forward run [--as-of YYYY-MM-DD] [--no-fetch] [--root DIR] [--no-watchlist]
    python -m btc_quant.research.forward watchlist       # 관찰 목록 검증·목록 (읽기 전용)

예약 실행 (docs/OPERATIONS.md §12): PC/VPS 의 cron 으로 매일 00:20 UTC 이후 1회 —
    20 0 * * *  cd ~/btc-quant && uv run python -m btc_quant.research.forward run >> logs/forward.log 2>&1
클라우드 세션 안에서는 세션이 살아 있는 동안만 예약 작업이 돈다(세션이 끝나면 멈춘다).

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
- **긴 결정 주기(15m/1h/4h)**: 14일은 4h 봉 84개뿐이라 지표가 덜 데워진다. 결정 주기 ≥ 15분인 스펙은
  워밍업 = max(G8.forward_warmup_days, ⌈페이퍼 `required_lookback_bars(spec)` × B / 1440⌉일) (상한
  `paper.bars.MAX_WARMUP_DAYS` = 120일)로 늘리고 그동안 status=warming_up 을 낸다(`spec_warmup_days`).
  예: 4h donchian_breakout(288) → 290봉 ≈ 49일. 1m/5m 스펙은 기존 그대로(설정 일수).

관찰 목록 (forward watchlist, 사용자 결정 2026-11-10)
- `config/forward_watchlist.yaml` (**사람 전용** — 연구 모드 guard 가 쓰기를 거부하고 MCP 에 편집 도구가 없다)에
  사람이 올린 전략(예: G6 에서 떨어진 Dev 생존 스펙)을 승격 없이 **관찰만** 한다. 레지스트리 상태를 바꾸지 않고,
  관문·리스크 한도·페이퍼/실거래와 무관하다. 원장 forward_results 에 state=`watchlist` 로 같은 (key, logic_hash,
  as_of) 멱등 규칙으로 기록된다. 항목의 logic_hash 가 스펙과 다르면 평가하지 않는다(조용한 스펙 변경 방지).
  Val 이 롤링되면(새 val_id, 사람 편집) 관찰 목록 전략은 새 Val 에서 정식 재평가될 수 있다 — 그것은 별도 절차.
- **워밍업 = 페이퍼와 같은 메모리 내 REST 워밍업** (`warmup="rest"`): Forward 시작 직전
  `paper.bars.window_minutes(required_lookback_bars(spec))` 분의 1분 캔들을 거래소 REST 로 받아 **메모리에서만**
  Forward 1분봉 앞에 붙이고(같은 `prepare_m1`) 지표 상태만 데운다. 이 캔들은 **잠금 구간(Forward 직전) 가격**이다 —
  페이퍼 서비스가 시작할 때 최근 캔들로 지표를 데우는 것과 같은 성격으로, 신호 계산에만 쓰이고 **저장하지 않으며
  (data/raw·원장 어디에도) 성과를 계산·기록·보고하지 않는다**. 성과 구간은 [Forward 시작 + 결정 봉 1개, as_of+1일):
  첫 진입 신호도 Forward 봉 하나가 닫힌 뒤에야 나온다. 엔진은 시작 인덱스 이전에 거래를 열지 않으며, 그래도
  이전 거래·일간 수익이 나오면 오류로 처리한다(`_assert_forward_only`). REST 워밍업을 받을 수 없으면(`--no-fetch`,
  네트워크 오류) 그 날 기록을 남기지 않고(재실행 때 다시 시도) 리포트에 warmup_unavailable 로만 표시한다.
- 레지스트리 전략(validated/challenger)은 기존 규칙(Forward 데이터만, 일수 워밍업) 그대로다.

다자산 유니버스 (PLAN D15): 스펙의 `universe`(예: BTC/XRP/ETH-KRW)마다 Forward 1분봉을 받아(수집도 자산별) 같은
엔진으로 실행하고 슬리브 평균 = 포트폴리오로 기록한다(캠페인·파이프라인과 같은 회계). B&H = 동일 비중. 원장 레코드에
`universe`·`per_asset`(자산별 거래 수·수익)이 붙는다. 기본 유니버스 [BTC-KRW] 는 이전과 같은 호출·같은 기록.
자산별 비용 모델(`get_sleeve_cost`, D17), 부분 사이징(`spec.sizing`, D16 — 레코드에 `sizing`)도 캠페인과 같다.
일간 수익 회계(PLAN D18)는 원천 캠페인 매니페스트 `daily_returns` 를 따른다(`campaign_manifest_accounting`):
'mtm' 캠페인(예: camp-000030)의 스펙은 Forward 에서도 평가 기준(MTM) 일간 수익으로 기록하고(`accounting: mtm`),
기대 구간의 원장 Dev 지표도 같은 회계다. 매니페스트가 없거나 D18 이전 캠페인은 'realized'(이전과 같은 기록).

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

from . import universe as U
from .campaign import (
    DEFAULT_COSTS,
    HALF_LIFE_DAYS,
    CampaignError,
    ResearchEnv,
    aux_context,
    config_root,
    get_cost,
    get_sleeve_cost,
    git_commit,
    manifest_accounting,
    prepare_m1,
    run_params,
)

DEFAULT_STATES = ("validated", "challenger")
WATCHLIST_STATE = "watchlist"
WATCHLIST_REL = Path("config") / "forward_watchlist.yaml"
WATCHLIST_REQUIRED = ("key", "logic_hash", "added_by", "added_at", "reason", "source_campaign")
OHLCV = ["open", "high", "low", "close", "volume"]
REST_WARMUP_NOTE = ("indicator warm-up reads lockbox-period REST candles (prices only) in memory, exactly like the "
                    "paper service; nothing is stored and no pre-Forward performance is computed or reported")
DEFAULT_EXCHANGES = ("coinone", "upbit")
DEFAULT_WARMUP_DAYS = 14
SYMBOL = "BTC-KRW"
TIMEFRAME = "1m"

# 저장소에 해당 시계열·구간이 없을 때 read_ohlcv 가 던지는 예외들
_NO_DATA = (FileNotFoundError, duckdb.IOException)

Fetcher = Callable[[str, pd.Timestamp, pd.Timestamp], pd.DataFrame]


# ============================================================== 수집
def rest_fetcher(exchange: str, start: pd.Timestamp, end: pd.Timestamp, symbol: str = SYMBOL) -> pd.DataFrame:
    """기존 REST 수집기로 [start, end) 1분봉 (기본 KRW-BTC; symbol = 유니버스 자산 BTC-KRW/XRP-KRW/ETH-KRW)."""
    market = U.market_code(symbol)
    if exchange == "coinone":
        from btc_quant.data import coinone
        return coinone.fetch_candles(market, 1, start, end, progress=False)
    if exchange in ("upbit", "bithumb"):
        from btc_quant.data import upbit
        return upbit.fetch_candles(exchange, market, 1, start.to_pydatetime(), end.to_pydatetime(),
                                   progress=False)
    raise ValueError(f"no REST fetcher for {exchange!r}")


def _call_fetcher(fetcher: Fetcher, exchange: str, start, end, symbol: str = SYMBOL) -> pd.DataFrame:
    """기본 심볼은 이전 시그니처 그대로 (exchange, start, end), 다른 자산은 symbol= 키워드를 붙인다."""
    if symbol == SYMBOL:
        return fetcher(exchange, start, end)
    return fetcher(exchange, start, end, symbol=symbol)


def _series_key(exchange: str, symbol: str) -> str:
    """수집·커버리지 키: 기본 심볼은 거래소 이름 그대로(이전 형식), 다른 자산은 '<거래소>:<심볼>'."""
    return exchange if symbol == SYMBOL else f"{exchange}:{symbol}"


def _stored_last(env: ResearchEnv, exchange: str, start, end, symbol: str = SYMBOL) -> tuple[pd.Timestamp | None, int]:
    """Forward 구간 [start, end) 안의 저장된 마지막 봉 시각과 행 수 (DataAccess 경유)."""
    try:
        df = env.data.read_range(start, end, exchange, symbol, TIMEFRAME)
    except _NO_DATA:
        return None, 0
    if df.empty:
        return None, 0
    return pd.Timestamp(df.index.max()), len(df)


def fetch_forward(env: ResearchEnv, exchanges, fwd_start: pd.Timestamp, window_end: pd.Timestamp,
                  fetcher: Fetcher | None = None, log: Callable[[str], None] = lambda s: None,
                  symbols=(SYMBOL,)) -> dict:
    """저장소에 없는 Forward 1분봉을 받아 추가. 요청 시작은 항상 >= fwd_start (잠금 구간 미접근).
    symbols: 추적 전략들의 유니버스 자산 합집합 (기본 BTC-KRW; 결과 키는 `_series_key`)."""
    from btc_quant.data.store import write_ohlcv

    fetcher = fetcher or rest_fetcher
    out = {}
    for sym in symbols:
        for ex in exchanges:
            key = _series_key(ex, sym)
            last, _ = _stored_last(env, ex, fwd_start, window_end, sym)
            start = max(fwd_start, last + pd.Timedelta(minutes=1)) if last is not None else fwd_start
            if start >= window_end:
                out[key] = {"fetched": 0, "note": "up to date"}
                continue
            try:
                df = _call_fetcher(fetcher, ex, start, window_end, sym)
            except Exception as e:   # noqa: BLE001 — 예약 작업: 거래소 하나의 실패로 전체를 멈추지 않는다
                out[key] = {"fetched": 0, "error": f"{type(e).__name__}: {e}"}
                log(f"fetch {key} failed: {e}")
                continue
            if df is None or df.empty:
                out[key] = {"fetched": 0, "start": start.isoformat()}
                continue
            idx = pd.DatetimeIndex(df.index)
            idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
            df = df.set_axis(idx)
            df = df[(df.index >= max(start, fwd_start)) & (df.index < window_end)]   # 이중 안전장치
            if not df.empty:
                write_ohlcv(df, ex, sym, TIMEFRAME, root=env.data.data_root)
            out[key] = {"fetched": len(df), "start": start.isoformat(), "end": window_end.isoformat()}
            log(f"fetch {key}: {len(df)} rows [{start}, {window_end})")
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
    warmup: str = "forward"          # "forward" (Forward 데이터만, 일수 워밍업) | "rest" (메모리 내 REST 워밍업)
    watch: dict | None = None        # 관찰 목록 메타데이터 (added_by, added_at, reason, source_campaign)
    accounting: str = "realized"     # 일간 수익 회계 (PLAN D18) — 원천 캠페인 매니페스트 `daily_returns`
    position_scale: float = 1.0      # 운용 크기 배수 (risk.yaml, G6 크기 조절 R1; 관찰 목록·사이드카 없음 = 1.0)


def campaign_manifest_accounting(env: ResearchEnv, campaign_id: str | None) -> str:
    """원천 캠페인의 일간 수익 회계 (매니페스트 `daily_returns`; 캠페인·매니페스트가 없거나 D18 이전 = 'realized').
    Forward 수익·Sharpe·기대 구간(원장 Dev 지표)이 같은 회계가 되도록 캠페인·파이프라인과 같은 값을 쓴다."""
    import json
    if not campaign_id:
        return "realized"
    path = env.campaign_dir(str(campaign_id)) / "manifest.json"
    if not path.exists():
        return "realized"
    try:
        return manifest_accounting(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return "realized"


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


def _sidecar_scale(env: ResearchEnv, state: str, sid: str, version: int) -> float:
    """레지스트리 리스크 사이드카의 position_scale (없으면 1.0) — 페이퍼와 같은 해석(`paper.service.load_position_scale`)."""
    from btc_quant.paper.service import load_position_scale
    try:
        return load_position_scale(env.registry.sidecar_path(state, sid, version))
    except (OSError, ValueError, KeyError):
        return 1.0


def resolve_strategies(env: ResearchEnv, states=DEFAULT_STATES) -> list[ForwardStrategy]:
    out = []
    for st in states:
        for x in env.registry.list(st):
            spec = env.registry.load(x["id"], x["version"])
            ctx = _context(env, x["id"], x["version"])
            out.append(ForwardStrategy(key_of(x["id"], x["version"]), x["id"], x["version"], st, spec,
                                       spec.logic_hash(), ctx["exchange"], ctx["cost"], ctx["campaign_id"],
                                       accounting=campaign_manifest_accounting(env, ctx["campaign_id"]),
                                       position_scale=_sidecar_scale(env, st, x["id"], x["version"])))
    return out


# ============================================================== 관찰 목록 (사람 전용 파일)
class WatchlistError(ValueError):
    pass


def watchlist_path(env: ResearchEnv) -> Path:
    """관찰 목록 파일은 연구 저장소(env.root)의 config/ 에 있다 (코드 저장소로 대체하지 않는다 — 임시 저장소 테스트가
    실제 관찰 목록을 집어오지 않게)."""
    return Path(env.root) / WATCHLIST_REL


def parse_watchlist_entry(e: dict, base: Path) -> tuple[dict, object]:
    """항목 하나 검증 → (메타데이터, StrategySpec). 문제가 있으면 WatchlistError."""
    from btc_quant.strategy.spec import StrategySpec

    if not isinstance(e, dict):
        raise WatchlistError(f"entry must be a mapping, got {type(e).__name__}")
    missing = [k for k in WATCHLIST_REQUIRED if not str(e.get(k) or "").strip()]
    if missing:
        raise WatchlistError(f"entry {e.get('key')!r}: missing {missing}")
    if ("spec" in e) == ("spec_path" in e):
        raise WatchlistError(f"entry {e['key']!r}: give exactly one of spec (inline) or spec_path")
    try:
        if "spec" in e:
            spec = StrategySpec.from_dict(e["spec"])
        else:
            sp = Path(e["spec_path"])
            spec = StrategySpec.from_yaml(sp if sp.is_absolute() else base / sp)
        spec.validate(REGISTRY)
    except WatchlistError:
        raise
    except Exception as ex:   # noqa: BLE001 — 파싱 오류는 항목 오류로
        raise WatchlistError(f"entry {e['key']!r}: bad spec ({type(ex).__name__}: {ex})") from ex
    key = key_of(spec.id, spec.version)
    if str(e["key"]) != key:
        raise WatchlistError(f"entry key {e['key']!r} != spec key {key!r}")
    if str(e["logic_hash"]) != spec.logic_hash():
        raise WatchlistError(f"entry {key!r}: logic_hash {e['logic_hash']} != spec logic_hash "
                             f"{spec.logic_hash()} (spec changed? a human must re-add it)")
    meta = {k: str(e[k]) for k in WATCHLIST_REQUIRED}
    for k in ("exchange", "cost", "source_run", "spec_path"):
        if e.get(k):
            meta[k] = str(e[k])
    return meta, spec


def load_watchlist(path: str | Path, base: str | Path | None = None) -> tuple[list[tuple[dict, object]], list[dict]]:
    """config/forward_watchlist.yaml → ([(meta, spec)], [오류]). 파일이 없으면 ([], [])."""
    import yaml

    path = Path(path)
    if not path.exists():
        return [], []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise WatchlistError(f"{path}: top level must be a mapping with 'entries'")
    raw = data.get("entries") or []
    if not isinstance(raw, list):
        raise WatchlistError(f"{path}: 'entries' must be a list")
    base = Path(base) if base is not None else path.parent.parent
    entries, errors, seen = [], [], set()
    for e in raw:
        try:
            meta, spec = parse_watchlist_entry(e, base)
            if meta["key"] in seen:
                raise WatchlistError(f"duplicate entry {meta['key']!r}")
            seen.add(meta["key"])
            entries.append((meta, spec))
        except WatchlistError as ex:
            errors.append({"key": (e or {}).get("key") if isinstance(e, dict) else None, "error": str(ex)})
    return entries, errors


def resolve_watchlist(env: ResearchEnv, path: str | Path | None = None,
                      skip_keys=()) -> tuple[list[ForwardStrategy], list[dict]]:
    """관찰 목록 → ForwardStrategy(state=watchlist, warmup=rest). 거래소·비용은 항목 값, 없으면 원천 캠페인."""
    path = Path(path) if path is not None else watchlist_path(env)
    entries, errors = load_watchlist(path, base=env.root)
    out = []
    for meta, spec in entries:
        key = meta["key"]
        if key in skip_keys:
            errors.append({"key": key, "error": "already tracked from the registry; watchlist entry ignored"})
            continue
        camp = env.ledger.get("campaigns", meta["source_campaign"]) or {}
        grid = camp.get("grid") or {}
        exchange = meta.get("exchange") or (grid.get("data") or {}).get("exchange") or "coinone"
        cost = meta.get("cost") or (grid.get("costs") or {}).get("primary") or DEFAULT_COSTS["primary"]
        out.append(ForwardStrategy(key, spec.id, spec.version, WATCHLIST_STATE, spec, spec.logic_hash(),
                                   exchange, cost, meta["source_campaign"], warmup="rest", watch=meta,
                                   accounting=campaign_manifest_accounting(env, meta["source_campaign"])))
    return out, errors


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


def forward_aux_context(env: ResearchEnv, spec, fwd_start: pd.Timestamp, window_end: pd.Timestamp,
                        warm_raw: Callable[[str], pd.DataFrame] | None = None, symbol: str = SYMBOL):
    """보조 데이터가 필요한 스펙의 ctx (필요 없으면 None).

    research/campaign.aux_context·페이퍼와 같은 구성: 보조 거래소 1분봉을 **Forward 구간만**
    (`read_range(fwd_start, window_end)` — 대상 시계열과 같은 창, 잠금 구간 미접근) 읽어
    regularize(clean_wicks) → 결정 주기 봉 `to_tf` → 블록이 대상 봉 index 에 reindex(ffill 없음).
    warm_raw(exchange) 를 주면(관찰 목록의 메모리 내 REST 워밍업) 그 캔들을 Forward 1분봉 앞에 붙인다.
    반환 (ctx, rows) 또는 데이터가 없으면 CampaignError."""
    keys = spec_aux_keys(spec)
    if not keys:
        return None, {}
    aux_m1, rows = {}, {}
    for a in keys:
        try:
            raw = env.data.read_range(fwd_start, window_end, a, symbol, TIMEFRAME)
        except _NO_DATA:
            raw = pd.DataFrame()
        if raw.empty:
            raise CampaignError(f"no {a} aux data in the forward segment")
        rows[a] = len(raw)
        if warm_raw is not None:
            raw = _join_warmup(warm_raw(a), raw, fwd_start)
        aux_m1[a] = prepare_m1(raw, a)
    return aux_context(aux_m1, spec.tf_min), rows


def _utc(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return df.set_axis(idx.rename("ts"))


def _join_warmup(warm: pd.DataFrame, fwd: pd.DataFrame, fwd_start: pd.Timestamp) -> pd.DataFrame:
    """메모리 내 워밍업 캔들(< fwd_start 만) + Forward 1분봉(>= fwd_start 만) — OHLCV 열만, 시간순."""
    fwd = _utc(fwd)[OHLCV]
    fwd = fwd[fwd.index >= fwd_start]
    if warm is None or warm.empty:
        return fwd
    warm = _utc(warm)[OHLCV].astype(float)
    warm = warm[warm.index < fwd_start]
    out = pd.concat([warm, fwd])
    return out[~out.index.duplicated(keep="last")].sort_index()


class WarmupUnavailable(RuntimeError):
    pass


class RestWarmup:
    """관찰 목록용 메모리 내 REST 워밍업 캔들 (페이퍼 서비스 워밍업과 같은 성격). 한 번의 실행 안에서만 캐시하고
    어디에도 저장하지 않는다. 요청 끝은 항상 Forward 시작(미포함)."""

    def __init__(self, fetcher: Fetcher | None):
        self.fetcher = fetcher
        self._cache: dict[tuple, pd.DataFrame] = {}
        self.calls: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []

    def get(self, exchange: str, start: pd.Timestamp, fwd_start: pd.Timestamp,
            symbol: str = SYMBOL) -> pd.DataFrame:
        if self.fetcher is None:
            raise WarmupUnavailable("REST warm-up disabled (--no-fetch)")
        k = (exchange, start, fwd_start) if symbol == SYMBOL else (exchange, start, fwd_start, symbol)
        if k not in self._cache:
            self.calls.append(k)
            try:
                df = _call_fetcher(self.fetcher, exchange, start, fwd_start, symbol)
            except Exception as e:   # noqa: BLE001 — 네트워크 오류 → 그 날 기록하지 않고 다음 실행에서 재시도
                raise WarmupUnavailable(f"warm-up fetch {exchange} failed: {type(e).__name__}: {e}") from e
            if df is None or df.empty:
                raise WarmupUnavailable(f"warm-up fetch {exchange}: no candles before the Forward start")
            df = _utc(df)
            self._cache[k] = df[(df.index >= start) & (df.index < fwd_start)][OHLCV].astype(float)
        return self._cache[k]


def spec_warmup_days(spec, base_days: int) -> int:
    """Forward 워밍업 일수. 결정 주기 ≤ 5분은 base_days 그대로(기존 동작). 15m/1h/4h 는 지표에 필요한 봉 수
    (`paper.bars.required_lookback_bars`, 120일 상한)를 일수로 환산해 base_days 와 큰 쪽."""
    tf = int(spec.tf_min)
    if tf <= 5:
        return int(base_days)
    from btc_quant.paper.bars import required_lookback_bars
    need = required_lookback_bars(spec, default_days=0.0)
    return max(int(base_days), int(math.ceil(need * tf / 1440.0)))


def rest_warmup_window(spec) -> tuple[int, int]:
    """(결정 주기 lookback 봉 수, 1분봉 워밍업 분) — 페이퍼 서비스와 같은 계산
    (`required_lookback_bars(spec)` 기본 3일·상한 120일, `window_minutes`)."""
    from btc_quant.paper.bars import required_lookback_bars, window_minutes
    lb = required_lookback_bars(spec)
    return lb, window_minutes(lb, spec.tf_min)


def _assert_forward_only(res, eval_start: pd.Timestamp) -> None:
    """성과는 eval_start 이후만 — 워밍업(잠금 구간) 가격 위의 거래·일간 수익이 섞이면 오류."""
    tr = res.trades
    if tr is not None and len(tr) and pd.Timestamp(pd.to_datetime(tr["entry_time"]).min()) < eval_start:
        raise RuntimeError("forward evaluation produced a trade before the evaluation start")
    d = res.daily_returns
    if len(d) and pd.Timestamp(d.index.min()) < eval_start.floor("D"):
        raise RuntimeError("forward evaluation produced daily returns before the evaluation start")


def _benchmark(m1: pd.DataFrame, days: pd.DatetimeIndex, eval_start: pd.Timestamp) -> pd.Series | None:
    """같은 Forward 평가 구간의 BTC 매수보유 일간 수익 (기준가 = eval_start 직전 Forward 봉 종가)."""
    if len(days) == 0:
        return None
    c = m1["close"]
    before = c[(c.index < eval_start)]
    after = c[c.index >= eval_start]
    if after.empty:
        return None
    return M.buy_hold_daily(after, days, first_price=float(before.iloc[-1]) if len(before) else None)


BENCH_KEYS = ("exposure_time", "exposure_days", "days_in_market", "bh_total_return", "bh_sharpe", "bh_max_dd",
              "bh_matched_total_return", "excess_return_vs_bh", "excess_return_vs_matched_bh")


def evaluate(env: ResearchEnv, fs: ForwardStrategy, fwd_start: pd.Timestamp, window_end: pd.Timestamp,
             warmup_days: int, band: float, warm: RestWarmup | None = None) -> dict:
    rest = fs.warmup == "rest"
    if rest:
        lb, wmin = rest_warmup_window(fs.spec)
        warm_start = fwd_start - pd.Timedelta(minutes=wmin)
        eval_start = fwd_start + pd.Timedelta(minutes=fs.spec.tf_min)     # Forward 봉 1개가 닫힌 뒤부터
        wdesc = {"mode": "rest_in_memory", "lookback_bars": lb, "warmup_minutes": wmin,
                 "warmup_start": warm_start.isoformat(), "note": REST_WARMUP_NOTE}
        warmup_days = 0
    else:
        warmup_days = spec_warmup_days(fs.spec, warmup_days)
        eval_start = fwd_start + pd.Timedelta(days=warmup_days)
        wdesc = None
    base = {"strategy_id": fs.id, "version": fs.version, "state": fs.state, "exchange": fs.exchange,
            "cost": fs.cost, "campaign_id": fs.campaign_id,
            "period": {"segment": "forward", "forward_start": fwd_start.isoformat(),
                       "eval_start": eval_start.isoformat(), "end": window_end.isoformat(),
                       "warmup_days": warmup_days}}
    if wdesc:
        base["period"]["warmup"] = wdesc
    if fs.accounting != "realized":
        base["accounting"] = fs.accounting          # D18 이전 캠페인(realized)은 이전과 같은 레코드
    if getattr(fs.spec, "sizing", None) is not None and not fs.spec.sizing.is_default:
        base["sizing"] = {"entry_fraction": float(fs.spec.sizing.entry_fraction)}
    if float(fs.position_scale) != 1.0:
        base["position_scale"] = float(fs.position_scale)   # 크기 조절 전략만 (1.0 = 이전과 같은 레코드)
    if fs.watch is not None:
        base["observation_only"] = True
        base["watchlist"] = {k: fs.watch[k] for k in ("added_by", "added_at", "reason", "source_campaign")}
    if window_end <= eval_start:
        return {**base, "status": "warming_up",
                "days_until_eval": math.ceil((eval_start - window_end) / pd.Timedelta(days=1))}
    syms = list(getattr(fs.spec, "universe", (SYMBOL,)) or (SYMBOL,))
    if len(syms) > 1 or syms[0] != SYMBOL:
        base["universe"] = syms
    w = (warm if warm is not None else RestWarmup(None)) if rest else None
    sleeves, raws, m1s, aux_rows_all = {}, {}, {}, {}
    cost = get_cost(fs.cost, config_root(env))
    for sym in syms:
        try:
            raw = env.data.read_range(fwd_start, window_end, fs.exchange, sym, TIMEFRAME)
            if raw.empty:
                raise CampaignError("no data")
        except (*_NO_DATA, CampaignError):
            return {**base, "status": "no_data", **({"note": f"no {sym} data"} if len(syms) > 1 else {})}
        warm_raw = None
        if rest:
            def warm_raw(ex: str, _sym=sym) -> pd.DataFrame:
                df = w.get(ex, warm_start, fwd_start, _sym)
                key = ex if _sym == SYMBOL else f"{ex}:{_sym}"
                wdesc.setdefault("warmup_rows", {})[key] = len(df)      # 진단용 행 수만 (가격·성과 없음)
                return df
        try:
            src = _join_warmup(warm_raw(fs.exchange), raw, fwd_start) if rest else raw
            m1 = prepare_m1(src, fs.exchange)
            if m1.index.max() < eval_start:
                return {**base, "status": "no_data", "note": "no bars after warm-up"}
            ctx, aux_rows = forward_aux_context(env, fs.spec, fwd_start, window_end, warm_raw, symbol=sym)
        except WarmupUnavailable as e:
            return {**base, "status": "warmup_unavailable", "note": str(e), "_persist": False}
        except CampaignError as e:
            return {**base, "status": "no_data", "note": str(e)}
        c_sym = get_sleeve_cost(fs.cost, sym, config_root(env))     # 자산별 모델 (D17; BTC-KRW = `cost` 와 같은 모델)
        res = fast.run_fast(fs.spec, m1, c_sym, eval_start, window_end, ctx=ctx, accounting=fs.accounting)
        _assert_forward_only(res, eval_start)
        sleeves[sym], raws[sym], m1s[sym] = res, raw, m1
        if aux_rows:
            aux_rows_all.update(aux_rows if sym == SYMBOL else {f"{k}:{sym}": v for k, v in aux_rows.items()})
    res = U.portfolio_result(sleeves)
    if len(syms) == 1:
        bh = _benchmark(m1s[syms[0]], res.daily_returns.index, eval_start)
    else:
        parts = {sym: _benchmark(m1s[sym], res.daily_returns.index, eval_start) for sym in syms}
        bh = None if any(v is None for v in parts.values()) else U.combine_daily(parts)
    met = M.summarize(res, HALF_LIFE_DAYS, benchmark=bh)
    raw0 = raws[syms[0]]
    out = {**base, "status": "ok", "n_days": met["n_days"], "n_trades": met["n_trades"],
           "net_return": met["total_return"], "sharpe": met["sharpe"], "mean_daily": met["mean_daily"],
           "max_dd": met["max_dd"], "expectancy": met["expectancy"], "win_rate": met["win_rate"],
           "data_rows": len(raw0) if len(syms) == 1 else int(sum(len(r) for r in raws.values())),
           "data_last_ts": pd.Timestamp(min(r.index.max() for r in raws.values())).isoformat()}
    out.update({k: met[k] for k in BENCH_KEYS if k in met})
    if float(fs.position_scale) != 1.0:      # 운용 크기 기준 수익·낙폭 (관문·기대 구간 비교는 위의 전액 기준 그대로)
        sd = res.daily_returns * float(fs.position_scale)
        out["net_return_scaled"] = float((1 + sd).prod() - 1)
        out["max_dd_scaled"] = float(M.max_drawdown(sd)[0])
    if len(syms) > 1:
        out["per_asset"] = {sym: {"n_trades": int(len(r.trades)),
                                  "net_return": float((1 + r.daily_returns).prod() - 1),
                                  "data_rows": len(raws[sym])} for sym, r in sleeves.items()}
    if aux_rows_all:
        out["aux_rows"] = aux_rows_all
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
                gate_cfg: dict | None = None, log: Callable[[str], None] = lambda s: None,
                watchlist: str | Path | bool | None = None, warmup_fetcher: Fetcher | None = None) -> dict:
    """watchlist: None = 연구 저장소의 config/forward_watchlist.yaml (없으면 비어 있음), False = 관찰 목록 끔,
    경로 = 그 파일. warmup_fetcher: 관찰 목록 메모리 내 REST 워밍업용 (기본: fetch=True 이면 fetcher 또는 REST,
    fetch=False 이면 없음 → 관찰 목록은 warmup_unavailable, 기록 없음)."""
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
    pver = run_params(env)                     # params_version + cost_versions (docs/PARAMS.md §4)
    tracked = resolve_strategies(env, states)
    watch, watch_errors = ([], []) if watchlist is False else resolve_watchlist(
        env, None if watchlist in (None, True) else watchlist, skip_keys={fs.key for fs in tracked})
    symbols = [SYMBOL] + sorted({s for fs in tracked + watch for s in getattr(fs.spec, "universe", ())} - {SYMBOL},
                                key=lambda x: (U.FIXED_ASSETS.index(x) if x in U.FIXED_ASSETS else 99, x))
    fetched = fetch_forward(env, exchanges, fwd_start, window_end, fetcher, log, symbols) if fetch else None
    for e in watch_errors:
        log(f"watchlist: {e['key']}: {e['error']}")
    if warmup_fetcher is None and fetch:
        warmup_fetcher = fetcher or rest_fetcher
    warm = RestWarmup(warmup_fetcher)
    results, new = [], 0
    for fs in tracked + watch:
        prior = [r for r in env.ledger.forward_results(fs.key, as_of_s) if r["logic_hash"] == fs.logic_hash]
        if prior:
            results.append(prior[0])
            continue
        rec = evaluate(env, fs, fwd_start, window_end, warmup, band, warm)
        if rec.pop("_persist", True) is False:          # 워밍업 불가: 기록하지 않음(다음 실행에서 재시도)
            results.append({"strategy_key": fs.key, "logic_hash": fs.logic_hash, "as_of": as_of_s, **rec})
            log(f"{fs.key}: {rec['status']} (not recorded)")
            continue
        rec["code_commit"] = git_commit(env.code_root)
        rec["params_version"] = pver.get("params_version")
        rec["cost_versions"] = pver.get("cost_versions")
        rid, created = env.ledger.record_forward_result(fs.key, fs.logic_hash, as_of_s, rec)
        new += int(created)
        results.append(env.ledger.get("forward_results", rid))
        log(f"{fs.key}: {rec['status']}")
    coverage = {_series_key(ex, sym): _coverage(env, ex, fwd_start, window_end, sym)
                for sym in symbols for ex in exchanges}
    summary = {"as_of": as_of_s, "forward_start": fwd_start.isoformat(), "window_end": window_end.isoformat(),
               "warmup_days": warmup, "states": list(states), "fetch": fetched, "coverage": coverage,
               "n_strategies": len(results), "n_new": new, "results": results,
               "watchlist_errors": watch_errors, "warmup_fetches": len(warm.calls)}
    env.reports_dir.mkdir(parents=True, exist_ok=True)
    path = env.reports_dir / f"forward_{as_of_s}.md"
    path.write_text(render(summary, env), encoding="utf-8")
    summary["report"] = str(path)
    return summary


def _coverage(env: ResearchEnv, ex: str, start, end, symbol: str = SYMBOL) -> dict:
    last, n = _stored_last(env, ex, start, end, symbol)
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


MIN_TRADES_NOTE, MIN_DAYS_NOTE = 30, 60     # 리포트 표본 경고 문턱 (정보용 — 어떤 판정에도 쓰지 않는다)


def render(summary: dict, env: ResearchEnv) -> str:
    all_rows = summary["results"]
    rows = [r for r in all_rows if r.get("state") != WATCHLIST_STATE]
    wrows = [r for r in all_rows if r.get("state") == WATCHLIST_STATE]
    L = [f"# Forward incubation — {summary['as_of']}\n",
         f"Forward segment from {summary['forward_start'][:10]}; evaluated window ends "
         f"{summary['window_end']} (exclusive). Warm-up (registry strategies): first {summary['warmup_days']} "
         "Forward days compute signals only (no Forward-external / lockbox data is used for warm-up). States: "
         + ", ".join(summary["states"]) + ".\n",
         "## Data coverage (1m, Forward segment)\n",
         "| exchange | rows | expected | last bar |", "|---|---|---|---|"]
    for ex, c in summary["coverage"].items():
        L.append(f"| {ex} | {c['rows']} | {c['expected_rows']} | {c['last_ts'] or '–'} |")
    L.append("\n## Strategies\n")
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
            if r.get("status") == "ok" and r.get("state") != WATCHLIST_STATE:
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
    if wrows or summary.get("watchlist_errors"):
        L += render_watchlist(summary, wrows, env)
    return "\n".join(L)


def _pct(x) -> str:
    return f"{100 * x:+.2f}%" if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) \
        else "–"


def render_watchlist(summary: dict, wrows: list[dict], env: ResearchEnv) -> list[str]:
    L = ["## Watchlist — OBSERVATION ONLY\n",
         "> **Observation only.** These strategies were placed on `config/forward_watchlist.yaml` by a human. "
         "They are **not promoted** (e.g. they failed G6), gates are unchanged, and watchlist status never implies "
         "promotion, risk sizing, or paper/live trading. Forward evidence only accumulates; a formal re-evaluation "
         "is possible only on a future rolled Val (new `val_id`, a human edit of config/splits.yaml).\n",
         "> Performance below is **Forward-only**: from the Forward start + 1 decision bar to the window end. "
         "Indicator state is warmed up in memory from exchange REST candles immediately before the Forward start "
         "(these are lockbox-period prices, used for signal state only — exactly like the paper service's "
         "warm-up; never stored, no pre-Forward trades or returns are computed or reported).\n",
         "| strategy | source | status | eval from | days | trades | net return | Sharpe | exposure (time / days) "
         "| B&H | exposure-matched B&H | vs matched B&H | Dev-expected trades |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    small = []
    for r in wrows:
        w = r.get("watchlist") or {}
        e = r.get("expected") or {}
        ok = r.get("status") == "ok"
        exp_tr = (f"{_f(e.get('expected_trades'), 1)} [{_f(e.get('trades_lo'), 1)}, {_f(e.get('trades_hi'), 1)}]"
                  if e else "–")
        expo = (f"{_pct(r.get('exposure_time'))} / {_pct(r.get('exposure_days'))}" if ok else "–")
        status = r["status"] + (f" ({r['note']})" if r.get("note") and not ok else "")
        L.append(f"| `{r['strategy_key']}` | {w.get('source_campaign', r.get('campaign_id') or '–')} | {status} | "
                 f"{(r.get('period') or {}).get('eval_start', '–')[:16]} | {_f(r.get('n_days'))} | "
                 f"{_f(r.get('n_trades'))} | {_pct(r.get('net_return')) if ok else '–'} | "
                 f"{_f(r.get('sharpe'), 2) if ok else '–'} | {expo} | "
                 f"{_pct(r.get('bh_total_return')) if ok else '–'} | "
                 f"{_pct(r.get('bh_matched_total_return')) if ok else '–'} | "
                 f"{_pct(r.get('excess_return_vs_matched_bh')) if ok else '–'} | {exp_tr} |")
        if ok and (int(r.get("n_trades") or 0) < MIN_TRADES_NOTE or int(r.get("n_days") or 0) < MIN_DAYS_NOTE):
            small.append(r)
    L.append("")
    if small or any(r.get("status") != "ok" for r in wrows):
        L.append(f"**Sample-size warning:** fewer than {MIN_TRADES_NOTE} Forward trades or {MIN_DAYS_NOTE} Forward "
                 "days — net return and Sharpe here are dominated by noise and say essentially nothing about the "
                 "edge yet. Read them only as an accumulating log; no decision is taken from this table.\n")
    for r in wrows:
        w = r.get("watchlist") or {}
        if w:
            L.append(f"- `{r['strategy_key']}`: added {w.get('added_at')} by {w.get('added_by')} — "
                     f"{w.get('reason')} (source {w.get('source_campaign')}; cost {r.get('cost')}, "
                     f"exchange {r.get('exchange')})")
    hist: dict[str, list] = {}
    for r in env.ledger.forward_results():
        if r.get("state") == WATCHLIST_STATE and r.get("status") == "ok" and r["as_of"] <= summary["as_of"]:
            hist.setdefault(r["strategy_key"], []).append(r)
    for k, hs in sorted(hist.items()):
        L.append(f"- `{k}`: {len(hs)} daily watchlist evaluations recorded so far (each is cumulative from the "
                 "same Forward evaluation start)")
    for e in summary.get("watchlist_errors") or []:
        L.append(f"- watchlist entry `{e.get('key')}` not evaluated: {e.get('error')}")
    L.append("")
    return L


# ============================================================== CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m btc_quant.research.forward",
                                 description="Forward incubation of validated/challenger strategies "
                                             "+ observation-only watchlist")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="fetch new Forward 1m data, evaluate, record, write report")
    r.add_argument("--as-of", default=None, help="UTC date YYYY-MM-DD (default: last completed UTC day)")
    r.add_argument("--no-fetch", action="store_true",
                   help="do not call exchange REST APIs (watchlist entries then report warmup_unavailable)")
    r.add_argument("--root", default=None, help="repository root (default: auto)")
    r.add_argument("--states", default=None, help="comma-separated registry states")
    r.add_argument("--watchlist", default=None, help="watchlist file (default: <root>/config/forward_watchlist.yaml)")
    r.add_argument("--no-watchlist", action="store_true", help="skip the observation-only watchlist")
    w = sub.add_parser("watchlist", help="validate and list config/forward_watchlist.yaml (read-only)")
    w.add_argument("--root", default=None, help="repository root (default: auto)")
    a = ap.parse_args(argv)
    if a.cmd == "watchlist":
        env = ResearchEnv.at(a.root)
        fss, errors = resolve_watchlist(env)
        for fs in fss:
            print(f"{fs.key}  logic_hash={fs.logic_hash}  {fs.spec.timeframe}  {fs.exchange}/{fs.cost}  "
                  f"added {fs.watch['added_at']} by {fs.watch['added_by']} — {fs.watch['reason']}")
        for e in errors:
            print(f"INVALID {e['key']}: {e['error']}")
        return 1 if errors else 0
    states = tuple(s.strip() for s in a.states.split(",")) if a.states else None
    wl = False if a.no_watchlist else a.watchlist
    out = run_forward(root=a.root, as_of=a.as_of, fetch=not a.no_fetch, states=states, log=print, watchlist=wl)
    print(f"forward {out['as_of']}: {out['n_strategies']} strategies ({out['n_new']} new records) → {out['report']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
