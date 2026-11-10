"""전략 스펙 — 블록 조합 (계약 모듈: 여러 모듈이 공유하므로 변경 시 전체 영향).

전략 = 진입 블록 1 + 필터 블록 0~2 + 청산(손절·익절·최대보유 + 선택적 신호 청산).
결정 주기(`timeframe`)는 "5m"(기본) · "1m" · (스윙 연구, PLAN §10 대안) "15m" · "1h" · "4h" — 블록은 이 주기의 봉으로
평가된다. 봉은 UTC epoch 기준 정렬(4h = 00/04/08/12/16/20 UTC = KST 09/13/17/21/01/05시, docs/ENGINE_SPEC.md).
긴 봉에서 무의미한 블록(`BlockDef.max_tf_min`)은 `validate(registry)` 가 거부한다.

청산 모드 (정확히 하나):
- 고정 %   : `stop_loss_pct`, `take_profit_pct` (기존)
- ATR 배수 : `stop_loss_atr`, `take_profit_atr`, `atr_bars`(결정 주기 봉 수, 기본 14)

직렬화·logic_hash 하위 호환: 기본값인 필드(timeframe="5m", ATR 필드 미사용, universe=[BTC-KRW])는 dict 에서
생략되므로 이 필드들이 생기기 전의 스펙은 해시가 그대로다.

자산 유니버스 (`universe`, PLAN D15): 스펙을 똑같이 적용할 자산 목록(풀링 평가, 자산별 동일 슬리브 —
`research.universe`). 파라미터가 아니므로 n_params 에 들어가지 않는다. 기본 [BTC-KRW] 가 아니면 dict·logic_hash 에
포함된다(같은 규칙이라도 다른 유니버스 = 다른 전략 = 다른 해시).

주문 집행 (`execution`, 선택, 2026-11-10 추가 — docs/ENGINE_SPEC.md "지정가 집행"):
- 진입 `market`(기본, 기존 그대로) 또는 `limit`: 진입 기준가 아래 `offset_bp`(bp) 또는 `offset_atr`(ATR 배수)에
  매수 지정가를 걸고 `tif_bars` 결정 주기 봉 동안 유지.
- 익절 `take_profit`: `market`(기본, 기존 TP 규칙) 또는 `limit`(익절가에 매도 지정가를 걸어 둠). 손절은 항상 시장가.
- 체결 규칙의 보수성 파라미터(관통 깊이·체결 확률 등)는 전략 규칙이 아니라 시장 측정치(class ①)라
  스펙이 아닌 `backtest.types.LimitFillModel` 에 있다 (비용 모델과 같은 위치).
기본값(시장가)이면 dict·logic_hash 에서 생략된다 → 기존 스펙 해시 불변.
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
ENTRY_MODES = ("market", "limit")
TP_MODES = ("market", "limit")
DEFAULT_TIF_BARS = 1
DEFAULT_UNIVERSE: tuple[str, ...] = ("BTC-KRW",)


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
class ExecutionSpec:
    """주문 집행 방식. 기본값(시장가 진입·기존 TP) = 이 필드가 생기기 전과 같은 의미론·같은 해시.

    entry="limit": 진입 결정 시점(B분 경계 1분봉 i 시가)에 기준가 ref = o_i 아래 지정가
        L = ref × (1 − offset_bp/1e4)            (bp 모드)
        L = ref × (1 − offset_atr × ATR(s)/o_i)  (ATR 모드, ATR 창 = exit.atr_bars, 고정 % 청산이면 14)
      를 걸고 tif_bars × B 분 동안 유지한다(그 뒤 취소). 체결 판정·가격은 `LimitFillModel`(ENGINE_SPEC).
    take_profit="limit": 익절가에 매도 지정가를 걸어 둔다(관통 규칙으로 체결, 체결가 = 지정가, 스프레드·슬리피지 없음).
      "market" = 기존 규칙(h ≥ tp + cost.tick → tp 에 비용 반영 체결). 손절·만기·신호·기간 종료는 항상 시장가.
    """
    entry: str = "market"
    offset_bp: float | None = None
    offset_atr: float | None = None
    tif_bars: int = DEFAULT_TIF_BARS
    take_profit: str = "market"

    @property
    def is_default(self) -> bool:
        return self == ExecutionSpec()

    @property
    def limit_entry(self) -> bool:
        return self.entry == "limit"

    @property
    def limit_tp(self) -> bool:
        return self.take_profit == "limit"

    @property
    def offset_mode(self) -> str | None:
        """'bp' | 'atr' | None (시장가 진입)."""
        if not self.limit_entry:
            return None
        return "atr" if self.offset_atr is not None else "bp"

    def n_params(self) -> int:
        """지정가 진입: 오프셋 1 + (tif_bars 가 기본 1 이 아니면) 1. 익절 방식 선택은 모드라 세지 않는다."""
        if not self.limit_entry:
            return 0
        return 1 + (1 if self.tif_bars != DEFAULT_TIF_BARS else 0)

    def validate(self) -> None:
        if self.entry not in ENTRY_MODES:
            raise ValueError(f"execution.entry must be one of {ENTRY_MODES}, got {self.entry!r}")
        if self.take_profit not in TP_MODES:
            raise ValueError(f"execution.take_profit must be one of {TP_MODES}, got {self.take_profit!r}")
        if isinstance(self.tif_bars, bool) or int(self.tif_bars) != self.tif_bars or self.tif_bars < 1:
            raise ValueError("execution.tif_bars must be a positive integer")
        if not self.limit_entry:
            if self.offset_bp is not None or self.offset_atr is not None or self.tif_bars != DEFAULT_TIF_BARS:
                raise ValueError("execution.offset_bp/offset_atr/tif_bars are only valid with entry='limit'")
            return
        if (self.offset_bp is None) == (self.offset_atr is None):
            raise ValueError("limit entry needs exactly one of execution.offset_bp / execution.offset_atr")
        if self.offset_bp is not None and not (0 <= self.offset_bp < 1e4):
            raise ValueError("execution.offset_bp must be in [0, 10000)")
        if self.offset_atr is not None and not self.offset_atr > 0:
            raise ValueError("execution.offset_atr must be > 0")

    def to_dict(self) -> dict:
        """기본값 필드 생략 (해시 안정)."""
        d: dict = {}
        if self.limit_entry:
            d["entry"] = "limit"
            if self.offset_bp is not None:
                d["offset_bp"] = self.offset_bp
            if self.offset_atr is not None:
                d["offset_atr"] = self.offset_atr
            if self.tif_bars != DEFAULT_TIF_BARS:
                d["tif_bars"] = int(self.tif_bars)
        elif self.entry != "market":
            d["entry"] = self.entry
        if self.take_profit != "market":
            d["take_profit"] = self.take_profit
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> ExecutionSpec:
        d = dict(d or {})
        unknown = set(d) - {"entry", "offset_bp", "offset_atr", "tif_bars", "take_profit"}
        if unknown:
            raise ValueError(f"unknown execution fields {sorted(unknown)}")
        return cls(entry=str(d.get("entry", "market")),
                   offset_bp=None if d.get("offset_bp") is None else float(d["offset_bp"]),
                   offset_atr=None if d.get("offset_atr") is None else float(d["offset_atr"]),
                   tif_bars=int(d.get("tif_bars", DEFAULT_TIF_BARS)),
                   take_profit=str(d.get("take_profit", "market")))


MARKET = ExecutionSpec()


@dataclass(frozen=True)
class StrategySpec:
    id: str
    entry: BlockRef
    exit: ExitSpec
    filters: tuple[BlockRef, ...] = ()
    version: int = 1
    timeframe: str = DEFAULT_TIMEFRAME     # 결정 주기 TIMEFRAMES 키 ("5m" | "1m" | "15m" | "1h" | "4h")
    universe: tuple[str, ...] = DEFAULT_UNIVERSE   # 자산 유니버스 (D15, research.universe) — 정식 순서 튜플
    execution: ExecutionSpec = MARKET      # 주문 집행 (기본 = 시장가 진입·기존 TP, 직렬화에서 생략)

    def __post_init__(self):
        if not isinstance(self.universe, tuple):   # 목록으로 넘겨도 튜플로 (frozen·해시 가능)
            object.__setattr__(self, "universe", tuple(self.universe))

    @property
    def tf_min(self) -> int:
        return tf_minutes(self.timeframe)

    @property
    def is_multi_asset(self) -> bool:
        return len(self.universe) > 1

    # ---- 검증 ----
    def n_params(self) -> int:
        n = len(self.entry.params) + sum(len(f.params) for f in self.filters) + self.exit.n_params()
        n += self.execution.n_params()
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
        self.execution.validate()
        u = self.universe
        if not u or len(set(u)) != len(u) or not all(isinstance(x, str) and x.endswith("-KRW") for x in u):
            raise ValueError(f"universe must be non-empty unique XXX-KRW symbols, got {list(u)}")
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
        if tuple(self.universe) != DEFAULT_UNIVERSE:
            d["universe"] = list(self.universe)
        ex = self.execution.to_dict()
        if ex:
            d["execution"] = ex
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
            universe=tuple(d.get("universe") or DEFAULT_UNIVERSE),
            execution=ExecutionSpec.from_dict(d.get("execution")),
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
