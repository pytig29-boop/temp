"""실시간 1분봉 생성 + 1분봉 이력(워밍업 포함) + 블록별 필요 lookback 계산.

파이프라인은 백테스트 입력과 같다: `regularize(clean_wicks(raw))` (docs/ENGINE_SPEC.md §입력).

- `MinuteBarBuilder`: 체결 스트림(거래소 시각 ms)을 UTC 분 단위로 모아 1분봉을 만든다.
  분 M 은 벽시계가 M+60초+grace 를 지나면 닫힌다(늦게 도착한 체결 대비). 이미 닫힌 분의 체결은
  `late_trades` 로 세고 버린다. 체결이 없던 분은 `filled=True` 봉으로 내보낸다(가격은 이력이 채움).
- `M1History`: 백테스트와 같은 정규화 1분봉 이력. REST 캔들로 seed 한 뒤 실시간 봉을 append.
  실제 봉은 `quality.clean_wicks` 를 직전 15개 실제 봉과 함께 적용(그 함수의 창 길이 = 15 → 전체
  이력에 적용한 결과와 동일), 빈 분은 직전 종가 평탄 봉(`quality.regularize` 와 동일).
- `required_lookback_bars(spec)`: 블록 파라미터로부터 "후행 창 평가 = 전체 이력 평가"가 성립하는 데
  필요한 5분봉 수. 유한 창(rolling/shift) 블록은 정확히, EMA 계열은 초기값 영향이 EMA_TOL 이하가
  되는 길이로 계산한다.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from btc_quant.data import quality
from btc_quant.strategy.blocks import REGISTRY
from btc_quant.strategy.blocks.base import Registry
from btc_quant.strategy.spec import BlockRef, StrategySpec

log = logging.getLogger("btc_quant.paper.bars")

ONE_MIN = pd.Timedelta(minutes=1)
NS_PER_MIN = 60_000_000_000
BARS_PER_DAY_5M = 288
DEFAULT_WARMUP_DAYS = 3.0
MAX_WARMUP_DAYS = 120.0
EMA_TOL = 1e-12          # EMA 초기값의 잔존 가중치 상한 → 비교 결과가 뒤집힐 확률 사실상 0
WICK_WINDOW = 15         # quality.clean_wicks 기본 창


# --------------------------------------------------------------------------------------------
# lookback
# --------------------------------------------------------------------------------------------

def _ema_bars(alpha: float, tol: float = EMA_TOL) -> int:
    """재귀 EMA(adjust=False)의 초기값 가중치 (1-α)^n ≤ tol 이 되는 n."""
    if alpha >= 1:
        return 1
    return int(math.ceil(math.log(tol) / math.log(1.0 - alpha)))


def _span(span: float) -> int:
    return _ema_bars(2.0 / (float(span) + 1.0))


def _block_lookback(ref: BlockRef, registry: Registry) -> int | None:
    """블록 하나에 필요한 5분봉 수 (모르는 블록이면 None)."""
    d = registry.get(ref.block)
    p = {k: v for k, v in _defaults(d.fn).items()}
    p.update(ref.params)
    n = ref.block
    if n == "random_entry" or n == "session_kst":
        return 1
    if n == "ema_slope":
        return _span(p["span"]) + int(p["span"]) + 2
    if n == "roc_threshold":
        return int(p["lookback"]) + 2
    if n in ("ema_cross_up", "ema_cross_down"):
        return _span(p["slow"]) + int(p["slow"]) + 2
    if n == "donchian_breakout":
        return int(p["lookback"]) + 2
    if n == "atr_channel_breakout":
        return _span(p["span"]) + int(p["span"]) + 2
    if n == "range_compression_break":
        return int(p["lookback"]) + BARS_PER_DAY_5M + 2
    if n == "bollinger_z_revert":
        return int(p["window"]) + 2
    if n == "rsi_oversold_bounce":
        return _ema_bars(1.0 / float(p["period"])) + 3 * int(p["period"]) + 2
    if n == "vol_regime":
        return int(p["window"]) + 7 * BARS_PER_DAY_5M + 2
    if n == "volume_z":
        return int(p["window"]) + 2
    if n == "trend_regime_1h":
        hours = _span(p["span"]) + int(p["span"]) + 3
        return hours * 12
    return None


def _defaults(fn) -> dict:
    import inspect
    return {k: v.default for k, v in inspect.signature(fn).parameters.items()
            if v.default is not inspect.Parameter.empty}


def spec_blocks(spec: StrategySpec) -> list[BlockRef]:
    refs = [spec.entry, *spec.filters]
    if spec.exit.signal_exit is not None:
        refs.append(spec.exit.signal_exit)
    return refs


def check_live_ready(spec: StrategySpec, registry: Registry = REGISTRY) -> None:
    for ref in spec_blocks(spec):
        d = registry.get(ref.block)
        if not d.live_ready or d.requires:
            raise ValueError(f"{spec.id}: block {ref.block} is not live-ready "
                             f"(requires {list(d.requires)}) — cannot paper trade")


def required_lookback_bars(spec: StrategySpec, registry: Registry = REGISTRY,
                           default_days: float = DEFAULT_WARMUP_DAYS,
                           max_days: float = MAX_WARMUP_DAYS) -> int:
    """후행 창 평가에 필요한 5분봉 수 = max(기본 일수, 블록별 요구치), max_days 로 상한."""
    need = int(default_days * BARS_PER_DAY_5M)
    for ref in spec_blocks(spec):
        n = _block_lookback(ref, registry)
        if n is None:
            log.warning("unknown block %s: lookback falls back to %.1f days", ref.block, default_days)
            continue
        need = max(need, n)
    cap = int(max_days * BARS_PER_DAY_5M)
    if need > cap:
        log.warning("%s needs %d 5m bars (%.1f days) > cap %.1f days; EMA-type signals become "
                    "approximate", spec.id, need, need / BARS_PER_DAY_5M, max_days)
        need = cap
    return need


def window_minutes(lookback_5m: int) -> int:
    """5분봉 lookback → 1분봉 창 길이(시각 정렬 여유 1시간 포함)."""
    return int(lookback_5m) * 5 + 60


# --------------------------------------------------------------------------------------------
# 실시간 1분봉 생성
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Bar:
    ts: pd.Timestamp          # 분 시작 (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float
    filled: bool
    n_trades: int = 0


def _minute_of_ms(ts_ms: int) -> int:
    return int(ts_ms) // 60_000


def minute_ts(m: int) -> pd.Timestamp:
    return pd.Timestamp(int(m) * NS_PER_MIN, tz="UTC")


def ts_minute(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).value // NS_PER_MIN)


class MinuteBarBuilder:
    """체결 → 1분봉. 분 M 은 now ≥ (M+1)·60 + grace 일 때 닫힌다.

    start_minute 이전 분의 체결은 무시한다(연결 직후의 '부분 분'을 피하기 위해 다음 분부터 시작).
    start_minute 을 정하지 않으면 첫 체결의 다음 분부터 시작.
    """

    def __init__(self, grace_seconds: float = 2.0, start_minute: int | None = None):
        self.grace = float(grace_seconds)
        self.next_minute: int | None = start_minute   # 다음에 내보낼 분
        self._buckets: dict[int, list] = {}             # m → [o, h, l, c, v, n]
        self.late_trades = 0       # 이미 닫힌 분의 체결 (버림)
        self.ignored_trades = 0    # 시작 분 이전 체결 (버림)
        self.trades = 0
        self.emitted = 0

    def start_at(self, minute: int) -> None:
        self.next_minute = int(minute)
        for m in [m for m in self._buckets if m < minute]:
            del self._buckets[m]

    def on_trade(self, ts_ms: int, price: float, qty: float) -> None:
        m = _minute_of_ms(ts_ms)
        if self.next_minute is None:
            self.next_minute = m + 1
        if m < self.next_minute:
            if self.emitted:
                self.late_trades += 1
            else:
                self.ignored_trades += 1
            return
        self.trades += 1
        b = self._buckets.get(m)
        price, qty = float(price), float(qty)
        if b is None:
            self._buckets[m] = [price, price, price, price, qty, 1]
        else:
            if price > b[1]:
                b[1] = price
            if price < b[2]:
                b[2] = price
            b[3] = price
            b[4] += qty
            b[5] += 1

    def on_time(self, now: float) -> list[Bar]:
        """now(초, epoch) 기준으로 닫을 수 있는 분을 모두 순서대로 닫는다."""
        out: list[Bar] = []
        if self.next_minute is None:
            return out
        closable = int(math.floor((now - self.grace) / 60.0)) - 1   # (m+1)*60+grace <= now
        while self.next_minute <= closable:
            m = self.next_minute
            b = self._buckets.pop(m, None)
            if b is None:
                out.append(Bar(minute_ts(m), math.nan, math.nan, math.nan, math.nan, 0.0, True, 0))
            else:
                out.append(Bar(minute_ts(m), b[0], b[1], b[2], b[3], b[4], False, b[5]))
            self.emitted += 1
            self.next_minute = m + 1
        return out


def wick_clean(o: float, h: float, l: float, c: float, prev_closes: list[float],
               k: float = 0.03) -> tuple[float, float]:
    """`quality.clean_wicks` 의 한 봉 버전 (같은 식): 기준가 = 직전 최대 15개 실제 봉 종가 중앙값
    (없으면 시가). 고가/저가가 기준·몸통에서 k 이상 벗어나면 몸통으로 자른다. (high, low) 반환."""
    ref = float(np.median(prev_closes)) if prev_closes else o
    body_hi, body_lo = max(o, c), min(o, c)
    if h > max(ref, body_hi) * (1 + k) or l < min(ref, body_lo) * (1 - k):
        return body_hi, body_lo
    return h, l


# --------------------------------------------------------------------------------------------
# 1분봉 이력
# --------------------------------------------------------------------------------------------

class M1History:
    """정규화된 연속 1분봉 이력 (백테스트 입력과 같은 형태). 메모리 상한 max_rows."""

    COLS = ("open", "high", "low", "close", "volume")

    def __init__(self, max_rows: int = 200_000):
        self.max_rows = int(max_rows)
        self._m: list[int] = []
        self._o: list[float] = []
        self._h: list[float] = []
        self._l: list[float] = []
        self._c: list[float] = []
        self._v: list[float] = []
        self._f: list[bool] = []
        self._raw_tail: list[tuple[int, float, float, float, float, float]] = []  # 실제 봉 원본

    # ---- 상태 ----
    def __len__(self) -> int:
        return len(self._m)

    @property
    def last_minute(self) -> int | None:
        return self._m[-1] if self._m else None

    @property
    def last_ts(self) -> pd.Timestamp | None:
        return minute_ts(self._m[-1]) if self._m else None

    @property
    def first_ts(self) -> pd.Timestamp | None:
        return minute_ts(self._m[0]) if self._m else None

    @property
    def last_close(self) -> float | None:
        return self._c[-1] if self._c else None

    # ---- 적재 ----
    def seed(self, raw: pd.DataFrame) -> int:
        """REST 원본 캔들(실제 봉만, UTC index)로 초기화. 기존 내용은 버린다. 행 수 반환."""
        self.__init__(self.max_rows)
        if raw is None or raw.empty:
            return 0
        raw = raw[~raw.index.duplicated(keep="last")].sort_index()
        raw = raw[list(self.COLS)].astype(float)
        m1 = quality.regularize(quality.clean_wicks(raw))
        mins = (m1.index.as_unit("ns").asi8 // NS_PER_MIN).tolist()
        self._m = [int(x) for x in mins]
        self._o, self._h, self._l, self._c = (m1[k].astype(float).tolist() for k in ("open", "high", "low", "close"))
        self._v = m1["volume"].astype(float).tolist()
        self._f = m1["filled"].astype(bool).tolist()
        tail = raw.iloc[-WICK_WINDOW:]
        tm = (tail.index.as_unit("ns").asi8 // NS_PER_MIN).tolist()
        self._raw_tail = [(int(m), *map(float, r)) for m, r in zip(tm, tail.to_numpy())]
        self._trim()
        return len(self._m)

    def append(self, bar: Bar) -> list[Bar]:
        """봉 추가. 공백은 filled 평탄 봉으로 메운다. 실제로 추가된(정규화된) 봉 목록 반환.
        이미 있는 분(≤ last)은 무시."""
        m = ts_minute(bar.ts)
        out: list[Bar] = []
        last = self.last_minute
        if last is not None and m <= last:
            return out
        if last is not None:
            for g in range(last + 1, m):
                out.append(self._push_filled(g))
        if bar.filled or not np.isfinite(bar.close):
            if self.last_close is None:
                return out        # 기준 종가 없음 → 이력 시작 전 빈 분은 버린다
            out.append(self._push_filled(m))
        else:
            out.append(self._push_real(m, bar))
        self._trim()
        return out

    def _push_filled(self, m: int) -> Bar:
        c = self._c[-1]
        self._m.append(m)
        self._o.append(c); self._h.append(c); self._l.append(c); self._c.append(c)
        self._v.append(0.0); self._f.append(True)
        return Bar(minute_ts(m), c, c, c, c, 0.0, True, 0)

    def _push_real(self, m: int, bar: Bar) -> Bar:
        row = (m, float(bar.open), float(bar.high), float(bar.low), float(bar.close), float(bar.volume))
        h, l = wick_clean(row[1], row[2], row[3], row[4], [r[4] for r in self._raw_tail[-WICK_WINDOW:]])
        self._raw_tail = (self._raw_tail + [row])[-WICK_WINDOW:]
        self._m.append(m)
        self._o.append(row[1]); self._h.append(h); self._l.append(l); self._c.append(row[4])
        self._v.append(row[5]); self._f.append(False)
        return Bar(minute_ts(m), row[1], h, l, row[4], row[5], False, bar.n_trades)

    def _trim(self) -> None:
        extra = len(self._m) - self.max_rows
        if extra > 0:
            for lst in (self._m, self._o, self._h, self._l, self._c, self._v, self._f):
                del lst[:extra]

    # ---- 조회 ----
    def frame(self, start_minute: int | None = None, end_minute: int | None = None) -> pd.DataFrame:
        """[start, end] (분, 양끝 포함) 구간 DataFrame. index 'ts'(UTC), open/high/low/close/volume/filled."""
        lo = 0 if start_minute is None else int(np.searchsorted(self._m, start_minute, "left"))
        hi = len(self._m) if end_minute is None else int(np.searchsorted(self._m, end_minute, "right"))
        idx = pd.DatetimeIndex(np.asarray(self._m[lo:hi], dtype=np.int64) * NS_PER_MIN, tz="UTC", name="ts")
        return pd.DataFrame({"open": self._o[lo:hi], "high": self._h[lo:hi], "low": self._l[lo:hi],
                             "close": self._c[lo:hi], "volume": self._v[lo:hi],
                             "filled": np.asarray(self._f[lo:hi], dtype=bool)}, index=idx)

    def window(self, end_ts: pd.Timestamp, n_minutes: int) -> pd.DataFrame:
        """end_ts(포함)까지 약 n_minutes 분. 시작은 UTC 정시로 내림 정렬(5분봉·1시간봉 경계 일치)."""
        end_m = ts_minute(end_ts)
        start_m = end_m - int(n_minutes) + 1
        start_m -= start_m % 60
        return self.frame(start_m, end_m)

    def bar_at(self, ts: pd.Timestamp) -> Bar | None:
        m = ts_minute(ts)
        i = int(np.searchsorted(self._m, m, "left"))
        if i >= len(self._m) or self._m[i] != m:
            return None
        return Bar(minute_ts(m), self._o[i], self._h[i], self._l[i], self._c[i], self._v[i], self._f[i])

    def bars_after(self, ts: pd.Timestamp | None) -> list[Bar]:
        lo = 0 if ts is None else int(np.searchsorted(self._m, ts_minute(ts), "right"))
        return [Bar(minute_ts(self._m[i]), self._o[i], self._h[i], self._l[i], self._c[i], self._v[i],
                    self._f[i]) for i in range(lo, len(self._m))]


# --------------------------------------------------------------------------------------------
# 워밍업 (REST)
# --------------------------------------------------------------------------------------------

def fetch_warmup(end: pd.Timestamp, minutes: int, market: str = "KRW-BTC",
                 fetch: Callable | None = None) -> pd.DataFrame:
    """[end − minutes, end) 의 코인원 1분 캔들 원본 (실제 봉만). end 는 분 경계(미포함)."""
    if fetch is None:
        from btc_quant.data import coinone
        fetch = coinone.fetch_candles
    end = pd.Timestamp(end).floor("min")
    start = end - pd.Timedelta(minutes=int(minutes))
    df = fetch(market, 1, start, end, progress=False)
    if df is None or df.empty:
        return pd.DataFrame(columns=list(M1History.COLS))
    df = df[(df.index >= start) & (df.index < end)]
    return df[list(M1History.COLS)].astype(float)
