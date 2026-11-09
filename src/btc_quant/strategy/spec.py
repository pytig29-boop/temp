"""전략 스펙 — 블록 조합 (계약 모듈: 여러 모듈이 공유하므로 변경 시 전체 영향).

전략 = 진입 블록 1 + 필터 블록 0~2 + 청산(손절·익절·최대보유 + 선택적 신호 청산).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

MAX_FILTERS = 2
MAX_PARAMS = 8  # 청산 3개 포함


@dataclass(frozen=True)
class BlockRef:
    block: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExitSpec:
    stop_loss_pct: float        # 0.7 = 0.7%
    take_profit_pct: float
    max_hold_min: int           # 5의 배수
    signal_exit: BlockRef | None = None


@dataclass(frozen=True)
class StrategySpec:
    id: str
    entry: BlockRef
    exit: ExitSpec
    filters: tuple[BlockRef, ...] = ()
    version: int = 1

    # ---- 검증 ----
    def n_params(self) -> int:
        n = len(self.entry.params) + sum(len(f.params) for f in self.filters) + 3
        if self.exit.signal_exit:
            n += len(self.exit.signal_exit.params)
        return n

    def validate(self, registry=None) -> None:
        if len(self.filters) > MAX_FILTERS:
            raise ValueError(f"filters > {MAX_FILTERS}")
        if self.n_params() > MAX_PARAMS:
            raise ValueError(f"params {self.n_params()} > {MAX_PARAMS}")
        e = self.exit
        if not (e.stop_loss_pct > 0 and e.take_profit_pct > 0):
            raise ValueError("SL/TP must be > 0")
        if e.max_hold_min <= 0 or e.max_hold_min % 5:
            raise ValueError("max_hold_min must be a positive multiple of 5")
        if registry is not None:
            registry.check(self.entry, "entry")
            for f in self.filters:
                registry.check(f, "filter")
            if e.signal_exit:
                registry.check(e.signal_exit, "exit_signal")

    # ---- 직렬화 ----
    def to_dict(self) -> dict:
        d = asdict(self)
        d["filters"] = [asdict(f) for f in self.filters]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StrategySpec":
        ex = dict(d["exit"])
        se = ex.pop("signal_exit", None)
        return cls(
            id=d["id"], version=int(d.get("version", 1)),
            entry=BlockRef(d["entry"]["block"], dict(d["entry"].get("params", {}))),
            filters=tuple(BlockRef(f["block"], dict(f.get("params", {}))) for f in d.get("filters", []) or []),
            exit=ExitSpec(signal_exit=BlockRef(se["block"], dict(se.get("params", {}))) if se else None, **ex),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "StrategySpec":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False)

    def logic_hash(self) -> str:
        """id·version을 제외한 '규칙 내용'의 해시. 같은 규칙이면 같은 해시."""
        d = self.to_dict()
        d.pop("id"), d.pop("version")
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]
