"""백테스트 공통 타입 (계약 모듈). 의미는 docs/ENGINE_SPEC.md 참조."""
from __future__ import annotations

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
    daily_returns: pd.Series      # UTC 일자 index, 거래 없는 날 0 포함, 청산일 기준 복리 합산
