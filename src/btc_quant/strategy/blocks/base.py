"""블록 레지스트리 (계약 모듈).

블록 함수 시그니처:
    fn(bars: pd.DataFrame, ctx: Context, **params) -> pd.Series[bool]

- bars: 5분봉 (index = 봉 시작 시각 UTC, 컬럼 open/high/low/close/volume/quote_volume)
- 반환: bars와 같은 index의 bool Series. 값 True@t 는 "봉 t가 닫힌 시점(t+5분)까지의 정보로 조건 성립".
  NaN 금지(워밍업 구간은 False).
- 인과성: t 이후 데이터를 바꿔도 t까지의 값은 변하지 않아야 한다 (tests의 미래 변조 테스트로 강제).
- kind: "entry" | "filter" | "exit_signal"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

KINDS = ("entry", "filter", "exit_signal")


@dataclass
class Context:
    """보조 데이터. aux['binance'] = 바이낸스 5분봉(같은 index로 정렬된 DataFrame) 등."""
    aux: dict[str, pd.DataFrame] = field(default_factory=dict)


@dataclass(frozen=True)
class BlockDef:
    name: str
    kind: str
    fn: Callable[..., pd.Series]
    grid: dict[str, list]          # 캠페인 기본 탐색 격자 (파라미터별 3~5개 값)
    description: str
    requires: tuple[str, ...] = ()  # 필요한 ctx.aux 키 (예: ("binance",))
    live_ready: bool = True         # 실시간 데이터로 계산 가능 여부 (False면 승격 불가)


class Registry:
    def __init__(self):
        self._defs: dict[str, BlockDef] = {}

    def register(self, kind: str, grid: dict[str, list], description: str,
                 requires: tuple[str, ...] = (), live_ready: bool = True, name: str | None = None):
        if kind not in KINDS:
            raise ValueError(kind)

        def deco(fn):
            n = name or fn.__name__
            if n in self._defs:
                raise ValueError(f"duplicate block {n}")
            self._defs[n] = BlockDef(n, kind, fn, grid, description, requires, live_ready)
            return fn
        return deco

    def get(self, name: str) -> BlockDef:
        return self._defs[name]

    def all(self, kind: str | None = None) -> list[BlockDef]:
        return [d for d in self._defs.values() if kind is None or d.kind == kind]

    def check(self, ref, kind: str) -> None:
        if ref.block not in self._defs:
            raise KeyError(f"unknown block {ref.block}")
        d = self._defs[ref.block]
        if d.kind != kind:
            raise ValueError(f"{ref.block} is {d.kind}, used as {kind}")
        unknown = set(ref.params) - set(d.grid)
        if unknown:
            raise ValueError(f"{ref.block}: unknown params {unknown}")

    def evaluate(self, ref, bars: pd.DataFrame, ctx: Context | None = None) -> pd.Series:
        d = self._defs[ref.block]
        ctx = ctx or Context()
        missing = [k for k in d.requires if k not in ctx.aux]
        if missing:
            raise KeyError(f"block {ref.block} requires ctx.aux {missing}")
        out = d.fn(bars, ctx, **ref.params)
        return out.reindex(bars.index, fill_value=False).astype(bool)


REGISTRY = Registry()
