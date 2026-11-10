"""백테스트 공통 타입 (계약 모듈). 의미는 docs/ENGINE_SPEC.md 참조."""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

TRADE_COLUMNS = [
    "entry_time",   # 진입 체결 1분봉 시각(UTC)
    "entry_price",  # 비용 반영 체결가
    "exit_time",    # 청산 체결 1분봉 시각(UTC)
    "exit_price",   # 비용 반영 체결가
    "exit_reason",  # "sl" | "tp" | "signal" | "time" | "end"
    "gross_ret",    # 비용 전 수익률 (기준가 대비)
    "net_ret",      # 비용 후 수익률 = exit_price/entry_price - 1 - fees
    "hold_min",
]


@dataclass(frozen=True)
class CostModel:
    name: str
    fee_rate: float          # 편도 수수료 (0.0005 = 0.05%)
    spread_bps: float        # 전체 스프레드. 시장가 체결 시 mid 대비 절반씩 부담
    slip_bps: float          # 편도 추가 슬리피지
    tick: float = 0.0        # 호가 단위(원). 익절은 tp_level + tick 이상 도달해야 체결

    @property
    def side_cost(self) -> float:
        """편도 가격 불리분 (비율)."""
        return self.spread_bps / 2e4 + self.slip_bps / 1e4

    def buy_fill(self, ref_price: float) -> float:
        return ref_price * (1 + self.side_cost)

    def sell_fill(self, ref_price: float) -> float:
        return ref_price * (1 - self.side_cost)

    def net_return(self, entry_fill: float, exit_fill: float) -> float:
        return (exit_fill * (1 - self.fee_rate)) / (entry_fill * (1 + self.fee_rate)) - 1


@dataclass(frozen=True)
class LimitFillModel:
    """지정가(메이커) 주문의 1분봉 체결 규칙 — 보수적 '관통' 규칙 (docs/ENGINE_SPEC.md "지정가 집행").

    매수 지정가 L 은 1분봉 k 에서 `low_k ≤ L − thr(L)` 일 때만 체결(가격이 L 을 관통해 거래됨 — 터치는 대기열
    때문에 체결 보장 없음). 매도 지정가 T 는 `high_k ≥ T + thr(T)`. 체결가 = 지정가 (가격 개선 없음),
    스프레드·슬리피지 없음(메이커), 수수료 = CostModel.fee_rate.
        thr(P) = max(through_ticks × tick, P × through_bp × 1e-4)
    - 호가 단위 tick: `tick_from_data=True`(기본)면 1분봉 가격에서 UTC 일자별로 추정한 호가 단위
      (`signals.infer_ticks` — 코인원 BTC 는 2024-09-29 무렵 1,000 → 10,000원으로 바뀜; 자산·거래소마다 다름),
      아니면 고정값 `tick`. 주문 시각(진입 = 주문 1분봉, 익절 = 진입 체결 봉)의 tick 을 쓴다. tick > 0 이면 지정가를
      호가 단위로 맞춘다(매수 내림, 매도 올림). 추정 tick 이 없으면(0/NaN) 그 지정가는 걸지 않는다(진입) /
      체결되지 않는다(익절) — 보수적.
    - tick = 0 (tick_from_data=False) 이면 연속 가격 + bp 조건만; 이때 thr ≥ 1 tick 이어야 실제 격자 주문의
      관통 판정과 같다(기록 체결가는 실제보다 불리 = 보수적). thr < 1 tick 이면 터치 체결이 섞여 낙관적이 된다.
    - min_volume > 0: 그 1분봉 거래량 ≥ min_volume 일 때만 체결 (거래량 모름 → 체결 없음).
    - fill_prob < 1: 관통 조건을 만족해도 주문마다 결정적 해시 u(주문 시각 분, 매수/매도, seed) < fill_prob 일
      때만 체결 (재현 가능한 추가 할인; 해시 불합격 주문은 그 주문 수명 동안 체결되지 않는다).
    - filled(합성) 1분봉에서는 지정가가 체결되지 않는다.
    값은 시장 측정치(class ①) — `btc_quant.research.fill_model` 이 실시간 호가·체결 대기열 재현으로 보정한다
    (2026-11-10 잠정, 약 7시간: 1 tick 관통의 '정밀도' ≈ 99.5 % (95 % 하한 ≈ 99 %), 터치 규칙 ≈ 74 % —
    research/reports/fill_model_20261110.md).
    """
    name: str = "through_1tick"
    through_ticks: int = 1
    tick: float = 0.0
    through_bp: float = 0.0
    fill_prob: float = 1.0
    min_volume: float = 0.0
    seed: int = 0
    tick_from_data: bool = True

    def validate(self) -> None:
        if int(self.through_ticks) != self.through_ticks or self.through_ticks < 1:
            raise ValueError("through_ticks (m) must be an integer >= 1")
        if not (self.tick >= 0 and self.through_bp >= 0 and self.min_volume >= 0):
            raise ValueError("tick, through_bp, min_volume must be >= 0")
        if not (self.tick_from_data or self.tick > 0 or self.through_bp > 0):
            raise ValueError("limit fills need a trade-through margin: tick_from_data, tick > 0 or through_bp > 0 "
                             "(touch fills are not allowed)")
        if not (0 < self.fill_prob <= 1):
            raise ValueError("fill_prob must be in (0, 1]")
        if int(self.seed) != self.seed or not (0 <= self.seed < 2 ** 20):
            raise ValueError("seed must be an integer in [0, 2^20)")

    def order_tick(self, bar_tick: float | None) -> float:
        """주문에 쓸 호가 단위: tick_from_data 면 그 1분봉의 추정 tick (없으면 NaN = 주문 불가), 아니면 고정 tick."""
        if self.tick_from_data:
            v = float(bar_tick) if bar_tick is not None else math.nan
            return v if v > 0 else math.nan
        return float(self.tick)

    # 아래 계산은 빠른 엔진(fast._core_x)이 연산 순서까지 그대로 복제한다 (비트 단위 일치).
    def buy_price(self, price: float, tick: float | None = None) -> float:
        t = self.tick if tick is None else tick
        return math.floor(price / t) * t if t > 0 else price

    def sell_price(self, price: float, tick: float | None = None) -> float:
        t = self.tick if tick is None else tick
        return math.ceil(price / t) * t if t > 0 else price

    def threshold(self, price: float, tick: float | None = None) -> float:
        t = self.tick if tick is None else tick
        a = self.through_ticks * t
        b = price * self.through_bp * 1e-4
        return max(b, a)

    def buy_trigger(self, price: float, tick: float | None = None) -> float:
        """매수 지정가 price 가 체결되려면 low ≤ 이 값."""
        return price - self.threshold(price, tick)

    def sell_trigger(self, price: float, tick: float | None = None) -> float:
        """매도 지정가 price 가 체결되려면 high ≥ 이 값."""
        return price + self.threshold(price, tick)

    def draw_ok(self, tmin: int, side: int) -> bool:
        """주문별 결정적 체결 허용 (fill_prob < 1). tmin = 주문 시각 UTC epoch 분, side 0 = 매수, 1 = 매도."""
        return self.fill_prob >= 1.0 or hash_u01(int(tmin), int(self.seed) * 2 + int(side)) < self.fill_prob


def hash_u01(key: int, salt: int) -> float:
    """결정적 균등 난수 [0, 1). 32비트 정수 혼합 — 빠른 엔진 `fast._hash_u01` 과 같은 값(정수 연산만)."""
    x = (key ^ (salt * 0x9E3779B1)) & 0xFFFFFFFF
    for _ in range(3):
        x = ((x ^ (x >> 16)) * 0x45D9F3B) & 0xFFFFFFFF
    x = x ^ (x >> 16)
    return x / 4294967296.0


# 기본(보수적) 체결 모델 — 데이터 추정 호가 단위로 1 tick 관통, 체결 확률 1 (fill_model 보정 2026-11-10, 잠정).
LIMIT_FILL_DEFAULT = LimitFillModel()


COINONE_BASE = CostModel("coinone_base", 0.0, 1.0, 1.0)
COINONE_CONSERVATIVE = CostModel("coinone_conservative", 0.0, 2.0, 3.0)
COINONE_STRESS = CostModel("coinone_stress", 0.0, 5.0, 8.0)
UPBIT_BASE = CostModel("upbit_base", 0.0005, 1.4, 1.0)
UPBIT_CONSERVATIVE = CostModel("upbit_conservative", 0.0005, 2.0, 3.0)


@dataclass
class BacktestResult:
    spec_id: str
    logic_hash: str
    cost: CostModel
    start: pd.Timestamp
    end: pd.Timestamp
    trades: pd.DataFrame          # TRADE_COLUMNS
    daily_returns: pd.Series      # UTC 일자 index, 거래 없는 날 0 포함, 청산일 기준 복리 합산 (accounting='mtm' 이면 평가 기준)
    # 관문 일간 수익 회계 (PLAN D18): 'realized'(기본, 위 설명) | 'mtm'(보유 분할을 매 UTC 일 종가에 청산 평가).
    # mtm 이면 daily_returns = 평가 기준, daily_realized = 실현 기준(보고용).
    accounting: str = "realized"
    daily_realized: pd.Series | None = None
