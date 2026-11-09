"""전략 스펙 — 블록 조합 (계약 모듈: 여러 모듈이 공유하므로 변경 시 전체 영향).

전략 = 진입 블록 1 + 필터 블록 0~2 + 청산(손절·익절·최대보유 + 선택적 신호 청산).
결정 주기(`timeframe`)는 "5m"(기본) · "1m" · (스윙 연구, PLAN §10 대안) "15m" · "1h" · "4h" — 블록은 이 주기의 봉으로
평가된다. 봉은 UTC epoch 기준 정렬(4h = 00/04/08/12/16/20 UTC = KST 09/13/17/21/01/05시, docs/ENGINE_SPEC.md).
긴 봉에서 무의미한 블록(`BlockDef.max_tf_min`)은 `validate(registry)` 가 거부한다.

청산 모드 (정확히 하나):
- 고정 %   : `stop_loss_pct`, `take_profit_pct` (기존)
- ATR 배수 : `stop_loss_atr`, `take_profit_atr`, `atr_bars`(결정 주기 봉 수, 기본 14)

직렬화·logic_hash 하위 호환: 기본값인 필드(timeframe="5m", ATR 필드 미사용)는 dict 에서 생략되므로
이 필드들이 생기기 전의 스펙은 해시가 그대로다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

MAX_FILTERS = 2
MAX_PARAMS = 8  # 청산 3개 포함 (ATR 모드: 손절 배수·익절 배수·최대보유 = 3, atr_bars 가 기본값이 아니면 +1)

# 결정 주기 → 봉 길이(분). 15m/1h/4h 는 스윙 연구용 (PLAN §10 "아무것도 통과하지 못하면": 4시간~1일 지평).
# 모든 값은 1440 의 약수 → UTC 자정(= epoch) 정렬 봉.
TIMEFRAMES: dict[str, int] = {"5m": 5, "1m": 1, "15m": 15, "1h": 60, "4h": 240}
DEFAULT_TIMEFRAME = "5m"
DEFAULT_ATR_BARS = 14


def tf_minutes(timeframe: str) -> int:
    """'5m' → 5, '1m' → 1, '1h' → 60, '4h' → 240."""
    try:
        return TIMEFRAMES[timeframe]
    except KeyError:
        raise ValueError(f"timeframe must be one of {list(TIMEFRAMES)}, got {timeframe!r}") from None


def tf_name(minutes: int) -> str:
    """봉 길이(분) → 정식 timeframe 이름 (5 → '5m', 60 → '1h', 240 → '4h'). 모르는 길이는 ValueError.
    f"{B}m" 로 이름을 만들면 '60m' 처럼 TIMEFRAMES 에 없는 이름(→ 다른 logic_hash)이 생기므로 항상 이 함수를 쓴다."""
    m = int(minutes)
    for k, v in TIMEFRAMES.items():
        if v == m:
            return k
    raise ValueError(f"no timeframe with {m}-minute bars; one of {TIMEFRAMES}")


@dataclass(frozen=True)
class BlockRef:
    block: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExitSpec:
    stop_loss_pct: float | None = None     # 0.7 = 0.7%   (고정 % 모드)
    take_profit_pct: float | None = None
    max_hold_min: int = 0                  # 결정 주기(분)의 양의 배수
    signal_exit: BlockRef | None = None
    stop_loss_atr: float | None = None     # 손절 = 진입 기준가 − k·ATR   (ATR 모드)
    take_profit_atr: float | None = None
    atr_bars: int = DEFAULT_ATR_BARS       # ATR 창 (결정 주기 봉 수). ATR 모드에서만 의미

    @property
    def mode(self) -> str:
        """'atr' (ATR 배수 필드가 하나라도 있으면) 또는 'pct'."""
        return "atr" if (self.stop_loss_atr is not None or self.take_profit_atr is not None) else "pct"

    @property
    def is_atr(self) -> bool:
        return self.mode == "atr"

    @property
    def sl(self) -> float:
        """모드에 맞는 손절 값 (% 또는 ATR 배수)."""
        return self.stop_loss_atr if self.is_atr else self.stop_loss_pct

    @property
    def tp(self) -> float:
        return self.take_profit_atr if self.is_atr else self.take_profit_pct

    def n_params(self) -> int:
        """청산 파라미터 수: 손절·익절·최대보유 = 3, ATR 모드에서 atr_bars ≠ 기본(14) 이면 +1."""
        n = 3
        if self.is_atr and self.atr_bars != DEFAULT_ATR_BARS:
            n += 1
        return n

    def validate(self, tf_min: int = 5) -> None:
        pct = (self.stop_loss_pct, self.take_profit_pct)
        atr = (self.stop_loss_atr, self.take_profit_atr)
        if self.is_atr:
            if any(v is not None for v in pct):
                raise ValueError("exit: exactly one mode — fixed % (stop_loss_pct/take_profit_pct) "
                                 "or ATR (stop_loss_atr/take_profit_atr), not both")
            if any(v is None for v in atr) or not all(v > 0 for v in atr):
                raise ValueError("ATR SL/TP multiples must both be given and > 0")
            if int(self.atr_bars) != self.atr_bars or self.atr_bars < 1:
                raise ValueError("atr_bars must be a positive integer")
        else:
            if any(v is None for v in pct) or not all(v > 0 for v in pct):
                raise ValueError("SL/TP must be > 0")
            if self.atr_bars != DEFAULT_ATR_BARS:
                raise ValueError("atr_bars is only valid with ATR exits")
        if self.max_hold_min <= 0 or self.max_hold_min % tf_min:
            raise ValueError(f"max_hold_min must be a positive multiple of {tf_min}")

    def to_dict(self) -> dict:
        """기본값 필드 생략(해시 하위 호환). 고정 % 모드 = 기존 4키 그대로."""
        d = {}
        if not self.is_atr:
            d["stop_loss_pct"] = self.stop_loss_pct
            d["take_profit_pct"] = self.take_profit_pct
        d["max_hold_min"] = self.max_hold_min
        d["signal_exit"] = asdict(self.signal_exit) if self.signal_exit is not None else None
        if self.is_atr:
            d["stop_loss_atr"] = self.stop_loss_atr
            d["take_profit_atr"] = self.take_profit_atr
            if self.atr_bars != DEFAULT_ATR_BARS:
                d["atr_bars"] = self.atr_bars
        return d


@dataclass(frozen=True)
class StrategySpec:
    id: str
    entry: BlockRef
    exit: ExitSpec
    filters: tuple[BlockRef, ...] = ()
    version: int = 1
    timeframe: str = DEFAULT_TIMEFRAME     # 결정 주기 TIMEFRAMES 키 ("5m" | "1m" | "15m" | "1h" | "4h")

    @property
    def tf_min(self) -> int:
        return tf_minutes(self.timeframe)

    # ---- 검증 ----
    def n_params(self) -> int:
        n = len(self.entry.params) + sum(len(f.params) for f in self.filters) + self.exit.n_params()
        if self.exit.signal_exit:
            n += len(self.exit.signal_exit.params)
        return n

    def validate(self, registry=None) -> None:
        tf = tf_minutes(self.timeframe)
        if len(self.filters) > MAX_FILTERS:
            raise ValueError(f"filters > {MAX_FILTERS}")
        if self.n_params() > MAX_PARAMS:
            raise ValueError(f"params {self.n_params()} > {MAX_PARAMS}")
        e = self.exit
        e.validate(tf)
        if registry is not None:   # tf_min: 이 결정 주기에서 무의미한 블록 거부 (BlockDef.max_tf_min)
            registry.check(self.entry, "entry", tf_min=tf)
            for f in self.filters:
                registry.check(f, "filter", tf_min=tf)
            if e.signal_exit:
                registry.check(e.signal_exit, "exit_signal", tf_min=tf)

    # ---- 직렬화 ----
    def to_dict(self) -> dict:
        d = {"id": self.id, "entry": asdict(self.entry), "exit": self.exit.to_dict(),
             "filters": [asdict(f) for f in self.filters], "version": self.version}
        if self.timeframe != DEFAULT_TIMEFRAME:
            d["timeframe"] = self.timeframe
        return d

    @classmethod
    def from_dict(cls, d: dict) -> StrategySpec:
        ex = dict(d["exit"])
        se = ex.pop("signal_exit", None)
        return cls(
            id=d["id"], version=int(d.get("version", 1)),
            entry=BlockRef(d["entry"]["block"], dict(d["entry"].get("params", {}))),
            filters=tuple(BlockRef(f["block"], dict(f.get("params", {}))) for f in d.get("filters", []) or []),
            exit=ExitSpec(signal_exit=BlockRef(se["block"], dict(se.get("params", {}))) if se else None, **ex),
            timeframe=str(d.get("timeframe", DEFAULT_TIMEFRAME)),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> StrategySpec:
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False)

    def logic_hash(self) -> str:
        """id·version을 제외한 '규칙 내용'의 해시. 같은 규칙이면 같은 해시."""
        d = self.to_dict()
        d.pop("id"), d.pop("version")
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]
