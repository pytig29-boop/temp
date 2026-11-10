"""대기열(queue) 위치 기반 지정가 체결 재현 — 실시간 호가 스냅샷 + 체결 스트림 (페이퍼·체결 모델 보정 공용).

docs/ENGINE_SPEC.md "지정가 집행 — 대기열 재현". 1분봉 관통 규칙(`types.LimitFillModel`)이 얼마나 보수적인지
`research.fill_model` 이 이 모듈로 잰다. 페이퍼는 `QueueFillOracle` 을 `ReferenceEngine.fill_oracle` 로 꽂는다.

규칙 (보수적 = 우리 주문은 그 가격대 대기열의 맨 뒤에 선다)
- 주문 시각 τ (거래소 시계 ≈ 수신 시계). τ 시점에 받은 마지막 호가 스냅샷(수신 시각 ≤ τ, 나이 ≤ max_book_age_s)에서
  우리 가격 P 의 표시 수량 = 앞선 대기량 A. 매수 기준(매도는 대칭):
    * P ≥ 최우선 매도호가        → 즉시 체결 가능(cross): τ 에 P 로 체결된 것으로 본다 (how = "cross").
    * 최우선 매수호가 < P         → 새 최우선 가격: A = 0.
    * P 가 보이는 호가 범위 안     → A = 그 가격 수량 (그 가격 호가가 없으면 0).
    * P 가 보이는 깊이(15단계)보다 깊음 → A 모름: P 가 범위 안으로 들어온 첫 스냅샷의 표시 수량을 A 로 쓰고,
      그 전 체결은 세지 않는다 ("deep").
    * τ 에 쓸 스냅샷이 없음        → 첫 스냅샷까지 A 모름 (deep 과 같은 처리).
- 체결 스트림(시각 ≥ τ): 매수 지정가 P 는
    * P 보다 낮은 가격의 체결이 하나라도 나오면 체결 (가격-시간 우선: P 의 매수 대기열이 모두 소진됨) → "through".
    * P 에서 매도 테이커 체결 수량 누적 ≥ A + 우리 수량 이면 체결 → "queue".
  취소로 인한 대기열 전진은 인정하지 않는다(보수적). `optimistic=True` 는 상한 비교용: 스냅샷마다 남은 앞선
  대기량을 그 가격 표시 수량으로 줄여 준다(취소가 전부 우리 앞에서 났다고 가정).
- 체결가 = P (가격 개선 없음). 부분 체결은 없다(우리 수량 전부가 체결될 때 체결).

가격 비교는 호가 단위 격자 위에서 한다(P 는 호가 단위로 맞춘 값이어야 한다 — `snap_price`).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numba import njit

NS = 1_000_000_000
NS_MIN = 60 * NS
MAX_BOOK_AGE_S = 30.0
HOW = {0: None, 1: "through", 2: "queue", 3: "cross"}


def snap_price(price: float, tick: float, side: str) -> float:
    """호가 단위로 맞춤: 매수 내림, 매도 올림 (LimitFillModel.buy_price / sell_price 와 같은 연산)."""
    if tick <= 0:
        return float(price)
    return (math.floor(price / tick) if side == "buy" else math.ceil(price / tick)) * tick


def _eq(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1.0)


def displayed_at(side: str, price: float, bid_px, bid_qty, ask_px, ask_qty) -> tuple[float, str]:
    """스냅샷 한 장에서 우리 가격 P 앞의 표시 대기량 (A, 상태). 상태: cross | inside | level | gap | deep | none."""
    if side == "buy":
        own_px, own_q, opp_px = bid_px, bid_qty, ask_px
    else:
        own_px, own_q, opp_px = ask_px, ask_qty, bid_px
    own = [(float(p), float(q)) for p, q in zip(own_px, own_q) if not math.isnan(p)]
    opp = [float(p) for p in opp_px if not math.isnan(p)]
    better = (lambda a, b: a > b) if side == "buy" else (lambda a, b: a < b)
    if opp and (_eq(opp[0], price) or better(price, opp[0])):     # 반대편 최우선 호가와 맞닿음 → 즉시 체결
        return 0.0, "cross"
    if not own:
        return math.nan, "none"
    if better(price, own[0][0]):
        return 0.0, "inside"
    for p, q in own:
        if _eq(p, price):
            return q, "level"
    if better(price, own[-1][0]) or _eq(price, own[-1][0]):
        return 0.0, "gap"
    return math.nan, "deep"


# ============================================================== 단일 주문 상태 기계 (페이퍼·오라클)
@dataclass
class QueueSim:
    """주문 하나의 대기열 상태. 이벤트를 시간순으로 on_book / on_trade 로 넣는다 (τ 이전 이벤트는 무시)."""
    side: str
    price: float
    qty: float
    placed_ns: int
    optimistic: bool = False
    need: float = math.nan            # 체결까지 남은 P 가격 체결 수량 (= 앞선 대기량 + 우리 수량); nan = 아직 모름
    status: str = "none"              # τ 스냅샷 기준 상태 (cross/inside/level/gap/deep/none)
    filled_ns: int | None = None
    how: str | None = None

    def start(self, book: dict | None, book_age_s: float | None = None,
              max_book_age_s: float = MAX_BOOK_AGE_S) -> None:
        """τ 시점 스냅샷으로 초기화 (없거나 너무 오래됐으면 A 모름)."""
        if book is None or (book_age_s is not None and book_age_s > max_book_age_s):
            self.status = "none"
            return
        a, st = displayed_at(self.side, self.price, book["bid_px"], book["bid_qty"], book["ask_px"],
                             book["ask_qty"])
        self.status = st
        if st == "cross":
            self.filled_ns, self.how = int(self.placed_ns), "cross"
        elif not math.isnan(a):
            self.need = a + self.qty

    @property
    def filled(self) -> bool:
        return self.filled_ns is not None

    def on_book(self, t_ns: int, book: dict) -> None:
        if self.filled or t_ns < self.placed_ns:
            return
        a, st = displayed_at(self.side, self.price, book["bid_px"], book["bid_qty"], book["ask_px"],
                             book["ask_qty"])
        if st == "cross" or math.isnan(a):
            return
        # 처음으로 우리 가격이 보임 → 그 표시 수량이 앞선 대기량 / 낙관 변형: 표시 수량으로 줄여 줌
        if math.isnan(self.need) or (self.optimistic and a + self.qty < self.need):
            self.need = a + self.qty

    def on_trade(self, t_ns: int, price: float, qty: float, taker_side: str) -> bool:
        """체결 하나. 이번 체결로 우리 주문이 체결되면 True."""
        if self.filled or t_ns < self.placed_ns:
            return False
        P = self.price
        through = (price < P and not _eq(price, P)) if self.side == "buy" else (price > P and not _eq(price, P))
        if through:
            self.filled_ns, self.how = int(t_ns), "through"
            return True
        hits_us = taker_side == ("sell" if self.side == "buy" else "buy")
        if hits_us and _eq(price, P) and not math.isnan(self.need):
            self.need -= qty
            if self.need <= 1e-12:
                self.filled_ns, self.how = int(t_ns), "queue"
                return True
        return False


# ============================================================== 배열 기반 일괄 계산 (보정용, QueueSim 과 같은 의미론)
@njit(cache=True)
def _scan(buy, price, qty, placed_ns, end_ns, need0, tr_t, tr_p, tr_q, tr_hit, bk_t, bk_disp, optimistic, eps):
    """주문 하나: (fill_ns, how) — how 0 없음, 1 through, 2 queue. tr_hit = 우리 쪽을 치는 테이커 체결인가.
    bk_disp = 스냅샷별 우리 가격 표시 수량 (nan = 안 보임/cross). 이벤트 시각이 같으면 스냅샷 먼저."""
    need = need0
    i = np.searchsorted(tr_t, placed_ns)
    j = np.searchsorted(bk_t, placed_ns)
    nt = tr_t.shape[0]
    nb = bk_t.shape[0]
    while True:
        tt = tr_t[i] if i < nt else end_ns
        tb = bk_t[j] if j < nb else end_ns
        if tt >= end_ns and tb >= end_ns:
            return -1, 0
        if tb <= tt:
            a = bk_disp[j]
            if not np.isnan(a) and (np.isnan(need) or (optimistic and a + qty < need)):
                need = a + qty
            j += 1
            continue
        p = tr_p[i]
        if buy:
            through = p < price - eps
        else:
            through = p > price + eps
        if through:
            return tr_t[i], 1
        if tr_hit[i] and abs(p - price) <= eps and not np.isnan(need):
            need -= tr_q[i]
            if need <= 1e-12:
                return tr_t[i], 2
        i += 1


@dataclass
class BookArrays:
    """호가 스냅샷 배열 (수신 시각 오름차순). `research.execution_study.Book` 과 같은 필드."""
    t: np.ndarray
    bid_px: np.ndarray      # (n, D) 내림차순, nan 채움
    bid_qty: np.ndarray
    ask_px: np.ndarray      # (n, D) 오름차순
    ask_qty: np.ndarray
    gaps: list = field(default_factory=list)

    @classmethod
    def from_book(cls, book) -> BookArrays:
        return cls(np.asarray(book.t, np.int64), book.bid_px, book.bid_qty, book.ask_px, book.ask_qty,
                   list(getattr(book, "gaps", []) or []))

    @property
    def mid(self) -> np.ndarray:
        return (self.bid_px[:, 0] + self.ask_px[:, 0]) / 2

    def idx_at(self, q_ns, max_age_s: float = MAX_BOOK_AGE_S) -> np.ndarray:
        q = np.asarray(q_ns, dtype=np.int64)
        if len(self.t) == 0:
            return np.full(q.shape, -1)
        i = np.searchsorted(self.t, q, side="right") - 1
        i0 = np.clip(i, 0, None)
        ok = (i >= 0) & ((q - self.t[i0]) <= int(max_age_s * NS))
        for a, b in self.gaps:
            ok &= ~((q >= a) & (q <= b))
        return np.where(ok, i, -1)

    def snapshot(self, i: int) -> dict:
        return {"bid_px": self.bid_px[i], "bid_qty": self.bid_qty[i], "ask_px": self.ask_px[i],
                "ask_qty": self.ask_qty[i]}

    def displayed(self, side: str, price: float, lo: int, hi: int, eps: float) -> np.ndarray:
        """스냅샷 lo..hi-1 에서 우리 가격 표시 수량 (displayed_at 의 벡터판; cross/deep/none = nan)."""
        with np.errstate(invalid="ignore"):
            if side == "buy":
                own_px, own_q, opp0 = self.bid_px[lo:hi], self.bid_qty[lo:hi], self.ask_px[lo:hi, 0]
                better = own_px[:, 0] < price - eps
                in_range = price >= np.fmin.reduce(own_px, axis=1) - eps
                cross = opp0 <= price + eps
            else:
                own_px, own_q, opp0 = self.ask_px[lo:hi], self.ask_qty[lo:hi], self.bid_px[lo:hi, 0]
                better = own_px[:, 0] > price + eps
                in_range = price <= np.fmax.reduce(own_px, axis=1) + eps
                cross = opp0 >= price - eps
        at = np.abs(own_px - price) <= eps
        q = np.where(at, np.nan_to_num(own_q), 0.0).sum(axis=1)
        out = np.where(better, 0.0, np.where(in_range, q, np.nan))
        out[cross | ~np.isfinite(own_px[:, 0])] = np.nan
        return out


@dataclass
class TradeTape:
    """체결 배열 (거래소 시각 오름차순). taker = 'buy'/'sell'."""
    t: np.ndarray
    price: np.ndarray
    qty: np.ndarray
    taker_sell: np.ndarray    # bool

    @classmethod
    def from_frame(cls, df: pd.DataFrame, clock: str = "ts_exchange") -> TradeTape:
        if df is None or df.empty:
            z = np.zeros(0)
            return cls(np.zeros(0, np.int64), z, z, np.zeros(0, bool))
        d = df.sort_values(clock, kind="stable")
        if "trade_id" in d:
            has = d["trade_id"].astype(str).str.len() > 0
            d = d[~(has & d.duplicated(subset=["trade_id"], keep="first"))]
        t = pd.to_datetime(d[clock], utc=True).dt.as_unit("ns").astype("int64").to_numpy()
        return cls(t, d["price"].to_numpy(float), d["qty"].to_numpy(float),
                   (d["side"].astype(str).str.lower() == "sell").to_numpy())


def simulate_order(side: str, price: float, qty: float, placed_ns: int, end_ns: int, trades: TradeTape,
                   book: BookArrays, *, optimistic: bool = False, max_book_age_s: float = MAX_BOOK_AGE_S,
                   eps: float | None = None) -> tuple[int, str | None, str]:
    """주문 하나를 [placed_ns, end_ns) 동안 재현 → (fill_ns 또는 -1, how, τ 상태). `QueueSim` 과 같은 결과."""
    eps = 1e-9 * max(abs(price), 1.0) if eps is None else eps
    j0 = int(book.idx_at(np.array([placed_ns]), max_book_age_s)[0])
    need0 = math.nan
    status = "none"
    if j0 >= 0:
        s = book.snapshot(j0)
        a, status = displayed_at(side, price, s["bid_px"], s["bid_qty"], s["ask_px"], s["ask_qty"])
        if status == "cross":
            return int(placed_ns), "cross", status
        if not math.isnan(a):
            need0 = a + qty
    lo = int(np.searchsorted(book.t, placed_ns, side="left"))
    hi = int(np.searchsorted(book.t, end_ns, side="left"))
    disp = book.displayed(side, price, lo, hi, eps) if hi > lo else np.zeros(0)
    a = int(np.searchsorted(trades.t, placed_ns, side="left"))
    b = int(np.searchsorted(trades.t, end_ns, side="left"))
    hit = ~trades.taker_sell[a:b] if side == "sell" else trades.taker_sell[a:b]
    f, how = _scan(side == "buy", float(price), float(qty), np.int64(placed_ns), np.int64(end_ns), need0,
                   trades.t[a:b], trades.price[a:b], trades.qty[a:b], hit, book.t[lo:hi], disp,
                   optimistic, eps)
    return int(f), HOW[int(how)], status


# ============================================================== 엔진 오라클 (페이퍼 훅)
@dataclass
class QueueFillOracle:
    """`ReferenceEngine.fill_oracle` 구현: 엔진의 지정가 주문을 실시간(또는 기록된) 체결·호가로 대기열 재현.

    엔진은 1분봉 ts 가 닫힌 뒤 on_bar_1m 을 부르고, 걸린 주문마다 oracle(order, ts, bar) 를 묻는다.
    오라클은 그 주문의 QueueSim 을 (처음 물을 때) 만들고, 지금까지 넣어 둔 이벤트 중 [τ, ts + 1분) 구간을 처리한다.
    - 진입 주문 τ = max(주문 1분봉 시각, 그 분 첫 체결 시각) + latency  (지정가가 그 분 시가 o_i 기준이라
      첫 체결 전에는 가격을 모른다)
    - 익절 주문 τ = 진입 체결 시각 + latency (시장가 진입이면 위 진입 τ 와 같음)
    이벤트는 add_trade / add_book 으로 시간순으로 넣는다(페이퍼: 수신 즉시; 재생: from_arrays).
    """
    latency_s: float = 1.0
    notional: float = 300_000.0          # 우리 주문 금액 (수량 = notional / 지정가)
    optimistic: bool = False
    max_book_age_s: float = MAX_BOOK_AGE_S
    trades: list = field(default_factory=list)   # (t_ns, price, qty, taker_side)
    books: list = field(default_factory=list)    # (t_ns, book dict)
    sims: dict = field(default_factory=dict)     # order id → (QueueSim, trade cursor, book cursor)
    first_trade: dict = field(default_factory=dict)   # epoch minute → 첫 체결 t_ns
    last_fill_ns: int | None = None

    @classmethod
    def from_arrays(cls, trades: TradeTape, book: BookArrays, **kw) -> QueueFillOracle:
        o = cls(**kw)
        side = np.where(trades.taker_sell, "sell", "buy")
        for t, p, q, s in zip(trades.t.tolist(), trades.price.tolist(), trades.qty.tolist(), side.tolist()):
            o.add_trade(t, p, q, s)
        for i in range(len(book.t)):
            o.add_book(int(book.t[i]), book.snapshot(i))
        return o

    def add_trade(self, t_ns: int, price: float, qty: float, taker_side: str) -> None:
        self.trades.append((int(t_ns), float(price), float(qty), str(taker_side)))
        self.first_trade.setdefault(int(t_ns) // NS_MIN, int(t_ns))

    def add_book(self, t_ns: int, book: dict) -> None:
        self.books.append((int(t_ns), book))

    def _book_at(self, t_ns: int) -> tuple[dict | None, float | None]:
        ts = [b[0] for b in self.books]
        i = int(np.searchsorted(np.asarray(ts, np.int64), t_ns, side="right")) - 1 if ts else -1
        if i < 0:
            return None, None
        return self.books[i][1], (t_ns - self.books[i][0]) / NS

    def __call__(self, order, ts: pd.Timestamp, bar: dict) -> bool:
        m0 = int(pd.Timestamp(order.placed).value // NS_MIN)
        end_ns = int(pd.Timestamp(ts).value) + NS_MIN
        st = self.sims.get(order.id)
        if st is None:
            base = max(m0 * NS_MIN, self.first_trade.get(m0, m0 * NS_MIN))
            if order.kind == "tp" and self.last_fill_ns is not None and self.last_fill_ns >= m0 * NS_MIN:
                base = max(base, self.last_fill_ns)
            tau = base + int(self.latency_s * NS)
            sim = QueueSim(order.side, float(order.price), self.notional / float(order.price), tau,
                           optimistic=self.optimistic)
            book, age = self._book_at(tau)
            sim.start(book, age, self.max_book_age_s)
            ti = int(np.searchsorted(np.asarray([t[0] for t in self.trades], np.int64), tau, side="left"))
            bi = int(np.searchsorted(np.asarray([b[0] for b in self.books], np.int64), tau, side="left"))
            st = [sim, ti, bi]
            self.sims[order.id] = st
        sim, ti, bi = st
        while not sim.filled:
            tt = self.trades[ti][0] if ti < len(self.trades) else end_ns
            tb = self.books[bi][0] if bi < len(self.books) else end_ns
            if tt >= end_ns and tb >= end_ns:
                break
            if tb <= tt:
                sim.on_book(tb, self.books[bi][1])
                bi += 1
            else:
                t, p, q, s = self.trades[ti]
                sim.on_trade(t, p, q, s)
                ti += 1
        st[1], st[2] = ti, bi
        hit = sim.filled and sim.filled_ns < end_ns
        if hit:
            self.last_fill_ns = int(sim.filled_ns)
            del self.sims[order.id]
        return hit

    def forget(self, order_ids: Sequence[str]) -> None:
        for k in order_ids:
            self.sims.pop(k, None)
