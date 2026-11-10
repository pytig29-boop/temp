"""자산 유니버스 — 고정 3자산 풀링 평가 (PLAN D15, 사용자 결정 2026-11-10).

결정
- 거래 대상: 코인원 KRW 시장의 **BTC-KRW, XRP-KRW, ETH-KRW** (24h 거래대금 상위 3개 비스테이블코인, 고정 목록).
  업비트는 자산마다 같은 심볼의 참조·보조 거래소.
- 전략 스펙 하나를 선언된 유니버스의 **모든 자산에 똑같이** 적용하고 **하나의 풀링 전략**으로 평가한다.
  파라미터는 자산 수만큼 늘지 않는다(추가 자유도 없음, 원장 시도 1회) — 거래·증거만 약 3배.
- 자본: 자산마다 **같은 고정 슬리브**(1/N), 자산별 독립 포지션(최대 1개, 롱 온리 — ENGINE_SPEC 그대로).
  포트폴리오 일간 수익 = 슬리브 일간 수익의 **단순 평균**(= 매일 1/N 로 재조정되는 동일 비중).
  모든 관문·지표는 이 포트폴리오 일간 수익과 **풀링한 거래 목록**(G1 거래 수 = 자산 합계)으로 판정한다.
  자산별 결과는 보고만 한다(관문 아님).
- B&H 기준 = 같은 회계의 동일 비중 N자산 매수보유(자산별 `buy_hold_daily` 의 일간 평균).
- 기본 유니버스 = [BTC-KRW] → 스펙 dict·logic_hash·캠페인·관문 동작이 이전과 **비트 단위로 같다**
  (유니버스는 기본값이 아닐 때만 직렬화된다).

이 모듈은 순수 함수만 둔다 (I/O 없음). 사용처: research.campaign / pipeline / robustness / signal_study /
forward, paper.service.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from btc_quant.backtest.types import TRADE_COLUMNS, BacktestResult, CostModel
from btc_quant.strategy.spec import DEFAULT_UNIVERSE

DEFAULT_SYMBOL = DEFAULT_UNIVERSE[0]
# D15 고정 자산 (순서 = 정식 순서; 유니버스는 이 순서로 정렬해 같은 집합 = 같은 해시)
FIXED_ASSETS: tuple[str, ...] = ("BTC-KRW", "XRP-KRW", "ETH-KRW")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,10}-KRW$")
# 캠페인 결과표·원장 per_asset 에 남기는 자산별 지표 (정보 전용 — 관문 아님)
PER_ASSET_KEYS = ("n_trades", "n_positions", "recent_n_trades", "sharpe", "sharpe_w", "total_return", "max_dd", "expectancy",
                  "win_rate", "trades_per_day")


class UniverseError(ValueError):
    pass


def normalize_universe(u, allowed: tuple[str, ...] | None = FIXED_ASSETS) -> tuple[str, ...]:
    """유니버스 선언 → 정식 튜플 (FIXED_ASSETS 순서, 그 밖은 사전순). None → DEFAULT_UNIVERSE.

    allowed=None 이면 심볼 형식만 검사(테스트·직접 호출용). 빈 목록·중복·모르는 심볼은 UniverseError."""
    if u is None:
        return DEFAULT_UNIVERSE
    if isinstance(u, str):
        u = [u]
    if not isinstance(u, (list, tuple)) or not u:
        raise UniverseError("universe must be a non-empty list of symbols (e.g. [BTC-KRW, XRP-KRW, ETH-KRW])")
    syms = [str(s).strip().upper() for s in u]
    if len(set(syms)) != len(syms):
        raise UniverseError(f"universe has duplicate symbols: {syms}")
    bad = [s for s in syms if not SYMBOL_RE.match(s)]
    if bad:
        raise UniverseError(f"universe symbols must look like XXX-KRW: {bad}")
    if allowed is not None:
        unknown = [s for s in syms if s not in allowed]
        if unknown:
            raise UniverseError(f"universe symbols {unknown} not in the fixed asset list {list(allowed)} "
                                "(PLAN D15: BTC-KRW, XRP-KRW, ETH-KRW)")
    order = {s: i for i, s in enumerate(FIXED_ASSETS)}
    return tuple(sorted(syms, key=lambda s: (order.get(s, len(order)), s)))


def is_default(u) -> bool:
    return tuple(u) == DEFAULT_UNIVERSE


def asset_name(symbol: str) -> str:
    """BTC-KRW → BTC."""
    return str(symbol).split("-")[0]


def market_code(symbol: str) -> str:
    """내부 표기 BTC-KRW → 거래소 REST/WS 표기 KRW-BTC."""
    a, q = str(symbol).split("-")
    return f"{q}-{a}"


# ============================================================== 슬리브 회계
def combine_matrix(mats: list[np.ndarray]) -> np.ndarray:
    """슬리브별 [n_combo, n_days] 일간 수익 → 포트폴리오(단순 평균). 슬리브 1개면 그대로(비트 단위 동일)."""
    if len(mats) == 1:
        return mats[0]
    acc = np.zeros_like(np.asarray(mats[0], dtype=float))
    for m in mats:
        acc = acc + np.asarray(m, dtype=float)
    return acc / len(mats)


def combine_daily(series: dict[str, pd.Series] | list[pd.Series]) -> pd.Series:
    """슬리브 일간 수익(같은 일자 index) → 포트폴리오 일간 수익(단순 평균). 1개면 그대로."""
    ss = list(series.values()) if isinstance(series, dict) else list(series)
    if len(ss) == 1:
        return ss[0]
    idx = ss[0].index
    for s in ss[1:]:
        if not s.index.equals(idx):
            raise UniverseError("sleeve daily returns have different day indexes")
    out = pd.Series(combine_matrix([s.to_numpy(float) for s in ss]), index=idx, name=ss[0].name)
    return out


@dataclass
class PortfolioResult:
    """N 슬리브 결합 결과 — `BacktestResult` 와 같은 필드(관문·지표 함수가 그대로 읽는다) + 슬리브별 결과.

    trades: 슬리브 거래를 합친 목록(`symbol` 열 추가, 진입 시각 순). daily_returns: 슬리브 일간 수익의 평균."""
    spec_id: str
    logic_hash: str
    cost: CostModel
    start: pd.Timestamp
    end: pd.Timestamp
    trades: pd.DataFrame
    daily_returns: pd.Series
    sleeves: dict[str, BacktestResult] = field(default_factory=dict)
    accounting: str = "realized"                 # 관문 일간 수익 회계 (PLAN D18; 슬리브와 같음)
    daily_realized: pd.Series | None = None      # accounting='mtm' 이면 실현 기준 포트폴리오 일간 수익 (보고용)

    @property
    def n_sleeves(self) -> int:
        return len(self.sleeves)

    @property
    def symbols(self) -> list[str]:
        return list(self.sleeves)


def pooled_trades(sleeves: dict[str, BacktestResult]) -> pd.DataFrame:
    frames = []
    for sym, r in sleeves.items():
        t = r.trades.copy()
        t["symbol"] = sym
        frames.append(t)
    if not frames:
        return pd.DataFrame(columns=[*TRADE_COLUMNS, "symbol"])
    out = pd.concat(frames, ignore_index=True)
    if len(out):
        out = out.sort_values(["entry_time", "symbol"], kind="stable").reset_index(drop=True)
    return out


def portfolio_result(sleeves: dict[str, BacktestResult]) -> BacktestResult | PortfolioResult:
    """슬리브 결과 → 포트폴리오 결과. 슬리브 1개(기본 유니버스)면 그 결과 객체를 그대로 돌려준다."""
    if not sleeves:
        raise UniverseError("no sleeves")
    if len(sleeves) == 1:
        return next(iter(sleeves.values()))
    first = next(iter(sleeves.values()))
    daily = combine_daily({k: v.daily_returns for k, v in sleeves.items()})
    acct = getattr(first, "accounting", "realized")
    if any(getattr(v, "accounting", "realized") != acct for v in sleeves.values()):
        raise UniverseError("sleeves use different daily-return accounting")
    real = (combine_daily({k: v.daily_realized for k, v in sleeves.items()})
            if acct != "realized" and all(v.daily_realized is not None for v in sleeves.values()) else None)
    return PortfolioResult(first.spec_id, first.logic_hash, first.cost, first.start, first.end,
                           pooled_trades(sleeves), daily, dict(sleeves), acct, real)


def n_sleeves(result) -> int:
    return int(getattr(result, "n_sleeves", 1) or 1)


def portfolio_trade_returns(result) -> np.ndarray:
    """몬테카를로 MDD 입력: 포지션당 **포트폴리오** 손익 = 슬리브 포지션 손익 × (1/N).
    포지션 손익 = 거래 순수익(기본) 또는 사다리별 Σ weight × net_ret (부분 사이징·사다리, `sizing.frame_position_returns`
    — 같은 사다리 분할들은 함께 움직이므로 한 단위로 재표본한다). 다자산이면 슬리브별로 묶는다."""
    from btc_quant.backtest.sizing import frame_position_returns
    sl = getattr(result, "sleeves", None) or {}
    if len(sl) > 1:
        parts = [frame_position_returns(r.trades) for r in sl.values()]
        r = np.concatenate(parts) if parts else np.zeros(0)
    else:
        r = frame_position_returns(result.trades) if len(result.trades) else np.zeros(0)
    n = n_sleeves(result)
    return r if n == 1 else r / n


def portfolio_trade_troughs(result, closes) -> np.ndarray:
    """몬테카를로 MTM 낙폭 입력 (PLAN D18): `portfolio_trade_returns` 와 같은 순서·같은 1/N 배율의 포지션별 보유 중
    최악 평가 손익 (`sizing.position_mtm_troughs`, 슬리브 비용 모델로 청산 평가). closes: {심볼: 1분 close} 또는
    단일 자산이면 close Series."""
    from btc_quant.backtest.sizing import position_mtm_troughs
    sl = getattr(result, "sleeves", None) or {}
    if len(sl) > 1:
        parts = [position_mtm_troughs(r.trades, closes[s], r.daily_returns.index, r.cost) for s, r in sl.items()]
        t = np.concatenate(parts) if parts else np.zeros(0)
    else:
        c = next(iter(closes.values())) if isinstance(closes, dict) else closes
        t = position_mtm_troughs(result.trades, c, result.daily_returns.index, result.cost)
    n = n_sleeves(result)
    return t if n == 1 else t / n


def buy_hold_universe(closes: dict[str, pd.Series], days: pd.DatetimeIndex,
                      first_prices: dict[str, float | None] | None = None) -> pd.Series:
    """동일 비중 N자산 매수보유 일간 수익 (자산별 `metrics.buy_hold_daily` 의 평균 — 전략과 같은 슬리브 회계).
    자산 1개면 `buy_hold_daily` 그대로."""
    from btc_quant.evaluation.metrics import buy_hold_daily
    fp = first_prices or {}
    parts = {s: buy_hold_daily(c, days, first_price=fp.get(s)) for s, c in closes.items()}
    return combine_daily(parts)


def market_index_close(closes: dict[str, pd.Series]) -> pd.Series:
    """국면·알파 검사용 '시장' 일 종가: 자산 1개면 그 자산 1분 close 그대로, 여러 개면 동일 비중 일간 수익의
    누적 지수(UTC 일 종가 기준, 1.0 시작)."""
    if len(closes) == 1:
        return next(iter(closes.values()))
    rets = []
    for c in closes.values():
        d = pd.Series(c).resample("1D").last().dropna()
        rets.append(d.pct_change())
    r = pd.concat(rets, axis=1).mean(axis=1, skipna=True).fillna(0.0)
    return (1.0 + r).cumprod()


# ============================================================== 자산별 보고 (정보 전용)
def asset_metrics(res: BacktestResult, half_life_days: float = 365) -> dict:
    """슬리브 하나의 가벼운 요약 (PER_ASSET_KEYS)."""
    from btc_quant.evaluation import metrics as M
    s = pd.Series(res.daily_returns).astype(float)
    x = s.to_numpy()
    n = x.size
    tr = res.trades
    nt = int(len(tr)) if tr is not None else 0
    rec = M.recent(s)
    if nt and rec.size:
        et = pd.DatetimeIndex(pd.to_datetime(tr["exit_time"]))
        lo = rec.index.min()
        if et.tz is None and getattr(lo, "tzinfo", None) is not None:
            et = et.tz_localize(lo.tzinfo)
        recent_n = int((et.normalize() >= lo.normalize()).sum())
    else:
        recent_n = 0
    r = tr["net_ret"].to_numpy(float) if nt else np.zeros(0)
    from btc_quant.backtest.sizing import n_positions
    return {"n_trades": nt, "n_positions": n_positions(tr), "recent_n_trades": recent_n, "sharpe": M.sharpe(s),
            "sharpe_w": M.weighted_sharpe(s, half_life_days).sharpe,
            "total_return": float(np.prod(1 + x) - 1) if n else 0.0, "max_dd": M.max_drawdown(s)[0],
            "expectancy": float(r.mean()) if nt else 0.0, "win_rate": float((r > 0).mean()) if nt else 0.0,
            "trades_per_day": float(nt / n) if n else 0.0}


def per_asset_metrics(sleeves: dict[str, BacktestResult], half_life_days: float = 365) -> dict[str, dict]:
    return {sym: asset_metrics(r, half_life_days) for sym, r in sleeves.items()}


def per_asset_columns(pa: dict[str, dict], keys=("n_trades", "sharpe_w", "total_return")) -> dict:
    """결과표 열: `asset.<SYM>.<key>`."""
    return {f"asset.{sym}.{k}": v.get(k) for sym, v in pa.items() for k in keys}


def per_asset_yearly(sleeves: dict[str, BacktestResult]) -> dict[str, list[dict]]:
    from btc_quant.evaluation import metrics as M
    return {sym: M.yearly_table(r.daily_returns, r.trades).reset_index(names="year").to_dict("records")
            for sym, r in sleeves.items()}


__all__ = ["DEFAULT_SYMBOL", "DEFAULT_UNIVERSE", "FIXED_ASSETS", "PER_ASSET_KEYS", "PortfolioResult",
           "UniverseError", "asset_metrics", "asset_name", "buy_hold_universe", "combine_daily",
           "combine_matrix", "is_default", "market_code", "market_index_close", "n_sleeves",
           "normalize_universe", "per_asset_columns", "per_asset_metrics", "per_asset_yearly",
           "pooled_trades", "portfolio_result", "portfolio_trade_returns", "portfolio_trade_troughs"]
