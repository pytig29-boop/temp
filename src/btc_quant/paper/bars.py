"""실시간 1분봉 생성 + 1분봉 이력(워밍업 포함) + 블록별 필요 lookback 계산.

파이프라인은 백테스트 입력과 같다: `quality.prepare_venue_m1(raw, exchange)` = 거래소별 clean_wicks(코인원 k=0.01,
그 밖 0.03, `quality.wick_params`) → regularize → 30분 이상 공백 직후 첫 실제 봉 filled (docs/ENGINE_SPEC.md §입력,
research.campaign.prepare_m1 과 같은 함수).

- `MinuteBarBuilder`: 체결 스트림(거래소 시각 ms)을 UTC 분 단위로 모아 1분봉을 만든다.
  분 M 은 벽시계가 M+60초+grace 를 지나면 닫힌다(늦게 도착한 체결 대비). 이미 닫힌 분의 체결은
  `late_trades` 로 세고 버린다. 체결이 없던 분은 `filled=True` 봉으로 내보낸다(가격은 이력이 채움).
- `M1History(exchange=…)`: 백테스트와 같은 정규화 1분봉 이력. REST 캔들로 seed 한 뒤 실시간 봉을 append.
  실제 봉은 `quality.clean_wicks` 를 직전 window(=거래소별 창, 15)개 실제 봉과 함께 적용(그 함수의 창 길이와
  같으므로 전체 이력에 적용한 결과와 동일), 빈 분은 직전 종가 평탄 봉(`quality.regularize` 와 동일), 긴 공백 직후
  워밍업 봉은 `quality.gap_warmup_mask` 와 같은 정의로 증분 표시.
- `required_lookback_bars(spec)`: 블록 파라미터로부터 "후행 창 평가 = 전체 이력 평가"가 성립하는 데
  필요한 결정 주기 봉 수(스펙 timeframe: 1m/5m/15m/1h/4h). 상한은 MAX_WARMUP_DAYS(120일)의 봉 수 — 4h 에서
  EMA 계열 큰 span(예: ema_slope span 96 → 1,425봉 ≈ 238일)은 상한에 걸려 근사가 되고 경고를 남긴다
  (120일 = 720봉이면 초기값 잔존 가중 ≈ (1−2/97)^720 ≈ 3e-7). 유한 창(rolling/shift) 블록은 정확히, EMA 계열은 초기값
  영향이 EMA_TOL 이하가 되는 길이로 계산한다. 블록이 `lookback_bars` 를 제공하면 그것을 쓴다.
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


def _bars_per_day(tf_min: int) -> int:
    return 1440 // int(tf_min)


def _block_lookback(ref: BlockRef, registry: Registry, tf_min: int = 5) -> int | None:
    """블록 하나에 필요한 결정 주기 봉 수 (모르는 블록이면 None).
    블록이 `lookback_bars(params)` 를 제공하면 그 값을 쓴다(실험 블록 등)."""
    d = registry.get(ref.block)
    p = dict(_defaults(d.fn))
    p.update(ref.params)
    if d.lookback_bars is not None:
        return int(d.lookback_bars(p)) + 1
    day = _bars_per_day(tf_min)
    n = ref.block
    if n in ("random_entry", "session_kst"):
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
        return int(p["lookback"]) + day + 2
    if n == "bollinger_z_revert":
        return int(p["window"]) + 2
    if n == "rsi_oversold_bounce":
        return _ema_bars(1.0 / float(p["period"])) + 3 * int(p["period"]) + 2
    if n == "vol_regime":
        return int(p["window"]) + 7 * day + 2
    if n == "volume_z":
        return int(p["window"]) + 2
    if n == "trend_regime_1h":
        hours = _span(p["span"]) + int(p["span"]) + 3
        return hours * (60 // int(tf_min))
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


LIVE_AUX = ("upbit",)    # 실시간으로 만들 수 있는 보조 데이터 (업비트 체결 스트림)


def spec_aux(spec: StrategySpec, registry: Registry = REGISTRY) -> tuple[str, ...]:
    """스펙이 요구하는 ctx.aux 키."""
    keys: list[str] = []
    for ref in spec_blocks(spec):
        for k in registry.get(ref.block).requires:
            if k not in keys:
                keys.append(k)
    return tuple(keys)


def check_live_ready(spec: StrategySpec, registry: Registry = REGISTRY,
                     available_aux: tuple[str, ...] = LIVE_AUX) -> None:
    for ref in spec_blocks(spec):
        d = registry.get(ref.block)
        missing = [k for k in d.requires if k not in available_aux]
        if not d.live_ready or missing:
            raise ValueError(f"{spec.id}: block {ref.block} is not live-ready "
                             f"(requires {list(d.requires)}) — cannot paper trade")


def required_lookback_bars(spec: StrategySpec, registry: Registry = REGISTRY,
                           default_days: float = DEFAULT_WARMUP_DAYS,
                           max_days: float = MAX_WARMUP_DAYS) -> int:
    """후행 창 평가에 필요한 결정 주기 봉 수 = max(기본 일수, 블록별 요구치, ATR 창), max_days 로 상한."""
    tf = spec.tf_min
    day = _bars_per_day(tf)
    need = int(default_days * day)
    for ref in spec_blocks(spec):
        n = _block_lookback(ref, registry, tf)
        if n is None:
            log.warning("unknown block %s: lookback falls back to %.1f days", ref.block, default_days)
            continue
        need = max(need, n)
    if spec.exit.is_atr:
        need = max(need, int(spec.exit.atr_bars) + 2)
    cap = int(max_days * day)
    if need > cap:
        log.warning("%s needs %d bars (%.1f days) > cap %.1f days; EMA-type signals become "
                    "approximate", spec.id, need, need / day, max_days)
        need = cap
    return need


def window_minutes(lookback_bars: int, tf_min: int = 5) -> int:
    """결정 주기 봉 lookback → 1분봉 창 길이(시각 정렬 여유 포함: max(1시간, 봉 1개)).
    B ≤ 60 이면 기존과 같은 +60분. 4h 는 +240분 → 창의 가장 오래된 봉도 완전한 봉(부분 봉 없음)."""
    return int(lookback_bars) * int(tf_min) + max(60, int(tf_min))


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
               k: float = 0.03) -> tuple[float, float]:   # k: 거래소별 값은 quality.wick_params
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

    def __init__(self, max_rows: int = 200_000, exchange: str | None = None):
        self.max_rows = int(max_rows)
        self.exchange = exchange
        self.wick_k, self.wick_window = quality.wick_params(exchange)     # 연구 prepare_m1 과 같은 값
        self.gap_min, self.gap_warmup = quality.GAP_WARMUP_MIN_GAP, quality.GAP_WARMUP_BARS
        self._run = 0           # 끝에서부터 연속된 (원래) filled 봉 수
        self._warm_left = 0     # 긴 공백 뒤 남은 워밍업 실제 봉 수
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
        self.__init__(self.max_rows, self.exchange)
        if raw is None or raw.empty:
            return 0
        raw = raw[~raw.index.duplicated(keep="last")].sort_index()
        raw = raw[list(self.COLS)].astype(float)
        base = quality.regularize(quality.clean_wicks(raw, k=self.wick_k, window=self.wick_window))
        m1 = quality.mark_gap_warmup(base, self.gap_min, self.gap_warmup)   # = prepare_venue_m1(raw, exchange)
        self._seed_gap_state(base["filled"].to_numpy(bool))
        mins = (m1.index.as_unit("ns").asi8 // NS_PER_MIN).tolist()
        self._m = [int(x) for x in mins]
        self._o, self._h, self._l, self._c = (m1[k].astype(float).tolist() for k in ("open", "high", "low", "close"))
        self._v = m1["volume"].astype(float).tolist()
        self._f = m1["filled"].astype(bool).tolist()
        tail = raw.iloc[-self.wick_window:]
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

    def _seed_gap_state(self, filled: np.ndarray) -> None:
        """seed 끝의 워밍업 상태 (quality.gap_warmup_mask 와 같은 정의)."""
        f = np.asarray(filled, bool)
        nz = np.flatnonzero(~f)
        self._run = int(len(f) - (nz[-1] + 1)) if len(nz) else len(f)
        self._warm_left = 0
        if self.gap_warmup <= 0 or self.gap_min <= 0:
            return
        if self._run >= self.gap_min:
            self._warm_left = self.gap_warmup
            return
        w = quality.gap_warmup_mask(np.concatenate([f, [False] * self.gap_warmup]), self.gap_min, self.gap_warmup)
        self._warm_left = int(w[len(f):].sum())       # 다음 실제 봉들 중 워밍업으로 남은 수

    def _push_filled(self, m: int) -> Bar:
        self._run += 1
        if self.gap_warmup > 0 and self.gap_min > 0 and self._run == self.gap_min:
            self._warm_left = self.gap_warmup
        c = self._c[-1]
        self._m.append(m)
        self._o.append(c); self._h.append(c); self._l.append(c); self._c.append(c)
        self._v.append(0.0); self._f.append(True)
        return Bar(minute_ts(m), c, c, c, c, 0.0, True, 0)

    def _push_real(self, m: int, bar: Bar) -> Bar:
        row = (m, float(bar.open), float(bar.high), float(bar.low), float(bar.close), float(bar.volume))
        w = self.wick_window
        h, l = wick_clean(row[1], row[2], row[3], row[4], [r[4] for r in self._raw_tail[-w:]], k=self.wick_k)
        self._raw_tail = (self._raw_tail + [row])[-w:]
        self._run = 0
        warm = self._warm_left > 0          # 긴 공백 직후 워밍업: 가격은 실제, filled 표시(진입·신호만 차단)
        if warm:
            self._warm_left -= 1
        self._m.append(m)
        self._o.append(row[1]); self._h.append(h); self._l.append(l); self._c.append(row[4])
        self._v.append(row[5]); self._f.append(warm)
        return Bar(minute_ts(m), row[1], h, l, row[4], row[5], warm, bar.n_trades)

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
                 fetch: Callable | None = None, exchange: str = "coinone") -> pd.DataFrame:
    """[end − minutes, end) 의 1분 캔들 원본 (실제 봉만). end 는 분 경계(미포함).
    fetch(market, unit, start, end, progress=False) 를 주면 그것을 쓴다(테스트용)."""
    if fetch is None:
        if exchange == "coinone":
            from btc_quant.data import coinone
            fetch = coinone.fetch_candles
        elif exchange == "upbit":
            from btc_quant.data import upbit

            def fetch(market, unit, start, end, progress=False):
                return upbit.fetch_candles("upbit", market, unit, start.to_pydatetime(),
                                           end.to_pydatetime(), progress=progress)
        else:
            raise ValueError(f"no REST candle source for {exchange}")
    end = pd.Timestamp(end).floor("min")
    start = end - pd.Timedelta(minutes=int(minutes))
    df = fetch(market, 1, start, end, progress=False)
    if df is None or df.empty:
        return pd.DataFrame(columns=list(M1History.COLS))
    df = df[(df.index >= start) & (df.index < end)]
    return df[list(M1History.COLS)].astype(float)
