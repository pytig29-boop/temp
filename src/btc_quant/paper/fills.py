"""호가 기반 체결 추정 ("book fill") — 모델 체결(1분봉 시가 ± 비용)과 비교해 G8 슬리피지 검증에 쓴다.

호가 스냅샷 형식은 수집기(`ws_collector._book_row`)와 같다:
    {"ts": ms, "ts_recv": s, "best_bid", "best_ask", "mid", "spread_bps",
     "bid_px": [...], "bid_qty": [...], "ask_px": [...], "ask_qty": [...]}   (최우선 → 깊은 쪽)

부호 관례: 모든 bp 값은 "불리할수록 +".
- `cost_bps`      : 호가 VWAP 의 mid 대비 불리분 (스프레드 절반 + 충격) — 실측 편도 비용.
- `model_cost_bps`: 비용 모델의 편도 가격 불리분 (`CostModel.side_cost`).
- `vs_model_bps`  : 호가 VWAP 이 모델 체결가보다 불리한 정도 (모델 체결가는 1분봉 시가 기준).
- G8 조건: 실측 편도 비용(cost_bps) ≤ 보수적 가정(COINONE_CONSERVATIVE.side_cost).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from btc_quant.backtest.types import COINONE_CONSERVATIVE, CostModel

DEFAULT_NOTIONAL = 300_000.0


@dataclass(frozen=True)
class BookFill:
    side: str               # "buy" | "sell"
    requested: float        # 매수: KRW notional, 매도: BTC qty
    qty: float              # 체결 수량 (BTC)
    notional: float         # 체결 금액 (KRW)
    vwap: float             # 평균 체결가 (깊이 부족 시 표시된 호가까지의 VWAP)
    best_bid: float
    best_ask: float
    mid: float
    spread_bps: float
    levels: int             # 사용한 호가 단계 수
    complete: bool          # 표시된 깊이로 전부 체결 가능했는지
    cost_bps: float         # mid 대비 불리분 (+ = 불리)
    book_ts: int | None     # 스냅샷 거래소 시각 (ms)
    book_age_s: float | None  # 기준 시각 대비 스냅샷 경과 초

    def to_dict(self) -> dict:
        return asdict(self)


def walk_book(book: dict, side: str, notional: float | None = None, qty: float | None = None,
              now: float | None = None) -> BookFill:
    """시장가 주문을 호가에 대입. 매수는 notional(KRW), 매도는 qty(BTC) 기준 (둘 다 주면 해당 값 우선)."""
    side = side.lower()
    if side == "buy":
        px, sz = list(book["ask_px"]), list(book["ask_qty"])
        if notional is None:
            if qty is None:
                raise ValueError("buy needs notional or qty")
            notional = qty * px[0] if px else 0.0
            target_qty, target_krw = qty, None
        else:
            target_qty, target_krw = None, float(notional)
    elif side == "sell":
        px, sz = list(book["bid_px"]), list(book["bid_qty"])
        if qty is None:
            if notional is None:
                raise ValueError("sell needs qty or notional")
            target_qty, target_krw = None, float(notional)
        else:
            target_qty, target_krw = float(qty), None
    else:
        raise ValueError("side must be buy/sell")
    best_bid = float(book["bid_px"][0]) if book.get("bid_px") else math.nan
    best_ask = float(book["ask_px"][0]) if book.get("ask_px") else math.nan
    mid = (best_bid + best_ask) / 2
    got_q, got_k, levels = 0.0, 0.0, 0
    for p, s in zip(px, sz):
        p, s = float(p), float(s)
        if s <= 0:
            continue
        if target_krw is not None:
            take_k = min(target_krw - got_k, p * s)
            take_q = take_k / p
        else:
            take_q = min(target_qty - got_q, s)
            take_k = take_q * p
        if take_q <= 0:
            break
        got_q += take_q
        got_k += take_k
        levels += 1
        done = (got_k >= target_krw - 1e-6) if target_krw is not None else (got_q >= target_qty - 1e-12)
        if done:
            break
    if target_krw is not None:
        complete = got_k >= target_krw - 1e-6
        requested = target_krw
    else:
        complete = got_q >= target_qty - 1e-12
        requested = target_qty
    vwap = got_k / got_q if got_q > 0 else math.nan
    if side == "buy":
        cost = (vwap / mid - 1) * 1e4
    else:
        cost = (1 - vwap / mid) * 1e4
    ts = book.get("ts")
    ts_ms = int(ts) if ts is not None and not isinstance(ts, pd.Timestamp) else (
        int(ts.value // 1_000_000) if isinstance(ts, pd.Timestamp) else None)
    age = None
    if now is not None and book.get("ts_recv") is not None:
        age = round(float(now) - float(book["ts_recv"]), 3)
    spread = (best_ask - best_bid) / mid * 1e4 if mid > 0 else math.nan
    return BookFill(side, float(requested), got_q, got_k, vwap, best_bid, best_ask, mid, spread,
                    levels, bool(complete), cost, ts_ms, age)


def compare_to_model(book_fill: BookFill | None, model_fill: float, ref_price: float,
                     cost: CostModel, conservative: CostModel = COINONE_CONSERVATIVE) -> dict:
    """모델 체결(기준가 ref_price ± cost) 대비 호가 체결 비교. book_fill 이 없으면 모델 값만."""
    out = {
        "model_ref": float(ref_price), "model_fill": float(model_fill),
        "model_cost_bps": cost.side_cost * 1e4,
        "conservative_cost_bps": conservative.side_cost * 1e4,
    }
    if book_fill is None or not np.isfinite(book_fill.vwap):
        out.update({"book": None, "book_cost_bps": None, "vs_model_bps": None,
                    "mid_vs_ref_bps": None, "within_conservative": None})
        return out
    buy = book_fill.side == "buy"
    vs_model = ((book_fill.vwap / model_fill - 1) if buy else (1 - book_fill.vwap / model_fill)) * 1e4
    out.update({
        "book": book_fill.to_dict(),
        "book_cost_bps": book_fill.cost_bps,
        "vs_model_bps": vs_model,
        # 결정 시점 mid 와 모델 기준가(1분봉 시가/손절가 등)의 차이: + 면 결정 시점이 모델보다 불리
        "mid_vs_ref_bps": ((book_fill.mid / ref_price - 1) if buy else (1 - book_fill.mid / ref_price)) * 1e4,
        "within_conservative": bool(book_fill.complete
                                    and book_fill.cost_bps <= conservative.side_cost * 1e4 + 1e-9),
    })
    return out


def slippage_stats(records: list[dict], conservative: CostModel = COINONE_CONSERVATIVE) -> dict:
    """fill 기록(compare_to_model 결과를 포함한 dict) 목록 → 요약. G8 슬리피지 판정 포함."""
    rows = [r for r in records if r.get("book_cost_bps") is not None]
    cons = conservative.side_cost * 1e4
    if not rows:
        return {"n": 0, "n_total": len(records), "conservative_cost_bps": cons, "g8_slippage_ok": None}
    c = np.array([r["book_cost_bps"] for r in rows], dtype=float)
    vm = np.array([r["vs_model_bps"] for r in rows], dtype=float)
    by_side = {}
    for side in ("buy", "sell"):
        cs = np.array([r["book_cost_bps"] for r in rows if r.get("side") == side], dtype=float)
        if cs.size:
            by_side[side] = {"n": int(cs.size), "mean_bps": float(cs.mean()), "median_bps": float(np.median(cs)),
                             "p90_bps": float(np.percentile(cs, 90))}
    return {
        "n": int(c.size), "n_total": len(records),
        "mean_cost_bps": float(c.mean()), "median_cost_bps": float(np.median(c)),
        "p90_cost_bps": float(np.percentile(c, 90)), "max_cost_bps": float(c.max()),
        "mean_vs_model_bps": float(vm.mean()), "median_vs_model_bps": float(np.median(vm)),
        "frac_within_conservative": float(np.mean([bool(r.get("within_conservative")) for r in rows])),
        "conservative_cost_bps": cons,
        "by_side": by_side,
        # G8: 평균 실측 편도 비용 ≤ 보수적 가정
        "g8_slippage_ok": bool(c.mean() <= cons + 1e-9),
    }
