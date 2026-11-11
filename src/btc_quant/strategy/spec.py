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

부분 사이징·분할 매수 사다리 (`sizing`, `ladder`, 2026-11-10 추가 — docs/ENGINE_SPEC.md "부분 사이징·분할 매수", PLAN D16):
- `sizing.entry_fraction` (0 < f ≤ 1, 기본 1.0): 진입마다 슬리브 자산의 f 만 쓰고 나머지는 현금(수익 0).
- `ladder`: N 개(2~5) 같은 크기 분할(max_exposure/N, 첫 체결 시 슬리브 자산 기준) — 첫 체결가 P1 아래
  `step_bp`(bp) 또는 `step_atr`(ATR 배수) 간격 P1·(1 − j·step) 에 추가 매수(시장가 트리거 또는 지정가),
  청산 `per_tranche`(분할마다 자기 익절) 또는 `average`(평균단가 익절, 전량), 손절은 필수 공동 손절
  (마지막 단 가격 × (1 − 손절폭), 전량 시장가), 만기 = 첫 분할 체결 + max_hold_min (전량).
  사다리가 있으면 sizing 은 기본값이어야 한다(크기는 max_exposure/N).
둘 다 기본값(없음)이면 dict·logic_hash 에서 생략된다 → 기존 스펙 해시 불변.
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
LADDER_ORDERS = ("market", "limit")          # 분할 주문: 시장가 트리거(보수적 관통 규칙) | 지정가
LADDER_EXITS = ("per_tranche", "average")    # 분할 청산: 분할별 익절 | 평균단가 익절(전량)
LADDER_MIN_TRANCHES, LADDER_MAX_TRANCHES = 2, 5
DEFAULT_MAX_EXPOSURE = 1.0                   # 사다리 최대 노출(슬리브 자산 대비) — class ③ 사용자 결정 2026-11-10 (D16)


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


def _num(x, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f"{what} must be a number, got {x!r}")
    return float(x)


@dataclass(frozen=True)
class SizingSpec:
    """진입 크기. entry_fraction = 진입마다 쓰는 슬리브 자산 비율 (나머지는 현금, 수익 0). 기본 1.0 = 전액(기존)."""
    entry_fraction: float = 1.0

    @property
    def is_default(self) -> bool:
        return self.entry_fraction == 1.0

    def validate(self) -> None:
        f = _num(self.entry_fraction, "sizing.entry_fraction")
        if not (0.0 < f <= 1.0):
            raise ValueError("sizing.entry_fraction must be in (0, 1]")

    def to_dict(self) -> dict:
        return {} if self.is_default else {"entry_fraction": float(self.entry_fraction)}

    @classmethod
    def from_dict(cls, d: dict | None) -> SizingSpec:
        d = dict(d or {})
        unknown = set(d) - {"entry_fraction"}
        if unknown:
            raise ValueError(f"unknown sizing fields {sorted(unknown)}")
        return cls(entry_fraction=float(d.get("entry_fraction", 1.0)))


FULL = SizingSpec()


@dataclass(frozen=True)
class LadderSpec:
    """분할 매수 사다리 (docs/ENGINE_SPEC.md "부분 사이징·분할 매수").

    tranches N (2~5) — 분할 크기 w = max_exposure / N (첫 체결 시 슬리브 자산 기준, 고정·동일 비중).
    단 j (0..N−1) 의 가격 = P1 · (1 − j · d), P1 = 첫 분할(진입) 기준가, d = step_bp/1e4 또는 step_atr × ATR(s)/o_i.
    order: "market" = 가격이 단 가격을 관통(LimitFillModel 관통 폭)하면 min(시가, 단 가격)에 시장가 체결(비용 반영),
           "limit"  = 단 가격에 매수 지정가(체결가 = 지정가, 수수료만).
    exit:  "per_tranche" = 분할마다 자기 기준가 × (1 + 익절폭) 에서 그 분할만 익절(그리드),
           "average"     = 보유 분할 평균단가(조화평균) × (1 + 익절폭) 에서 전량 익절.
    손절(필수, 전량 시장가): 마지막 단 가격 P1·(1 − (N−1)·d) × (1 − 손절폭). 만기: 첫 분할 체결 + max_hold_min (전량).
    익절폭·손절폭은 스펙 `exit` 의 % 또는 ATR 배수 그대로."""
    tranches: int
    step_bp: float | None = None
    step_atr: float | None = None
    order: str = "market"
    exit: str = "average"
    max_exposure: float = DEFAULT_MAX_EXPOSURE

    @property
    def step_mode(self) -> str:
        return "atr" if self.step_atr is not None else "bp"

    @property
    def weight(self) -> float:
        """분할 하나의 크기 (슬리브 자산 대비)."""
        return float(self.max_exposure) / int(self.tranches)

    @property
    def limit_orders(self) -> bool:
        return self.order == "limit"

    @property
    def average_exit(self) -> bool:
        return self.exit == "average"

    def n_params(self) -> int:
        """분할 수 + 간격 = 2. 주문 방식·청산 방식은 모드(격자가 아니면 0), max_exposure 는 위험 선호(class ③)."""
        return 2

    def validate(self) -> None:
        n = self.tranches
        if isinstance(n, bool) or int(n) != n or not (LADDER_MIN_TRANCHES <= n <= LADDER_MAX_TRANCHES):
            raise ValueError(f"ladder.tranches must be an integer in [{LADDER_MIN_TRANCHES}, {LADDER_MAX_TRANCHES}]")
        if (self.step_bp is None) == (self.step_atr is None):
            raise ValueError("ladder needs exactly one of step_bp / step_atr")
        if self.step_bp is not None:
            v = _num(self.step_bp, "ladder.step_bp")
            if not (v > 0 and (int(n) - 1) * v < 1e4):
                raise ValueError("ladder.step_bp must be > 0 with (tranches − 1) × step_bp < 10000")
        if self.step_atr is not None and not _num(self.step_atr, "ladder.step_atr") > 0:
            raise ValueError("ladder.step_atr must be > 0")
        if self.order not in LADDER_ORDERS:
            raise ValueError(f"ladder.order must be one of {LADDER_ORDERS}, got {self.order!r}")
        if self.exit not in LADDER_EXITS:
            raise ValueError(f"ladder.exit must be one of {LADDER_EXITS}, got {self.exit!r}")
        x = _num(self.max_exposure, "ladder.max_exposure")
        if not (0.0 < x <= 1.0):
            raise ValueError("ladder.max_exposure must be in (0, 1] (no leverage)")

    def to_dict(self) -> dict:
        d: dict = {"tranches": int(self.tranches)}
        if self.step_bp is not None:
            d["step_bp"] = float(self.step_bp)
        if self.step_atr is not None:
            d["step_atr"] = float(self.step_atr)
        d["order"] = self.order
        d["exit"] = self.exit
        if self.max_exposure != DEFAULT_MAX_EXPOSURE:
            d["max_exposure"] = float(self.max_exposure)
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> LadderSpec | None:
        if not d:
            return None
        d = dict(d)
        unknown = set(d) - {"tranches", "step_bp", "step_atr", "order", "exit", "max_exposure"}
        if unknown:
            raise ValueError(f"unknown ladder fields {sorted(unknown)}")
        if "tranches" not in d:
            raise ValueError("ladder.tranches required")
        return cls(tranches=int(d["tranches"]),
                   step_bp=None if d.get("step_bp") is None else float(d["step_bp"]),
                   step_atr=None if d.get("step_atr") is None else float(d["step_atr"]),
                   order=str(d.get("order", "market")), exit=str(d.get("exit", "average")),
                   max_exposure=float(d.get("max_exposure", DEFAULT_MAX_EXPOSURE)))


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
    sizing: SizingSpec = FULL              # 진입 크기 (기본 = 전액, 직렬화에서 생략)
    ladder: LadderSpec | None = None       # 분할 매수 사다리 (기본 없음 = 단일 진입, 직렬화에서 생략)

    def __post_init__(self):
        if not isinstance(self.universe, tuple):   # 목록으로 넘겨도 튜플로 (frozen·해시 가능)
            object.__setattr__(self, "universe", tuple(self.universe))

    @property
    def tf_min(self) -> int:
        return tf_minutes(self.timeframe)

    @property
    def is_multi_asset(self) -> bool:
        return len(self.universe) > 1

    @property
    def needs_atr(self) -> bool:
        """결정 시 ATR(창 = exit.atr_bars, 고정 % 청산이면 14)이 필요한가: ATR 청산 · ATR 오프셋 지정가 · ATR 간격 사다리."""
        return (self.exit.is_atr or self.execution.offset_mode == "atr"
                or (self.ladder is not None and self.ladder.step_mode == "atr"))

    @property
    def is_sized(self) -> bool:
        """부분 사이징 또는 사다리 (거래 목록에 weight/ladder/tranche 열이 붙는다)."""
        return self.ladder is not None or not self.sizing.is_default

    # ---- 검증 ----
    def n_params(self) -> int:
        n = len(self.entry.params) + sum(len(f.params) for f in self.filters) + self.exit.n_params()
        n += self.execution.n_params()
        if self.ladder is not None:
            n += self.ladder.n_params()
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
        self.sizing.validate()
        if self.ladder is not None:
            self.ladder.validate()
            if not self.sizing.is_default:
                raise ValueError("sizing.entry_fraction must be the default 1.0 with a ladder "
                                 "(tranche size = ladder.max_exposure / tranches)")
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
        sz = self.sizing.to_dict()
        if sz:
            d["sizing"] = sz
        if self.ladder is not None:
            d["ladder"] = self.ladder.to_dict()
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
            sizing=SizingSpec.from_dict(d.get("sizing")),
            ladder=LadderSpec.from_dict(d.get("ladder")),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> StrategySpec:
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False)

    def canonical_hash(self) -> str:
        """표기에 무관한 규칙 해시 (레드팀 발견 2026-11-11: 48 과 48.0 이 다른 logic_hash). 정수값 실수 → 정수,
        실수는 12 유효숫자. Val 재사용 규칙(같은 규칙은 Val 을 한 번만)에 쓴다 — logic_hash 자체는 기존 기록과의
        호환 때문에 바꾸지 않는다."""
        return canonical_hash_of(self.to_dict())

    def logic_hash(self) -> str:
        """id·version을 제외한 '규칙 내용'의 해시. 같은 규칙이면 같은 해시."""
        d = self.to_dict()
        d.pop("id"), d.pop("version")
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


def _canon(v):
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        if f == int(f) and abs(f) < 2 ** 53:
            return int(f)
        return float(f"{f:.12g}")
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    return v


def canonical_hash_of(spec_dict: dict) -> str:
    """스펙 dict(`to_dict()` 형식, id·version 무시) → 표기 무관 해시."""
    d = {k: v for k, v in dict(spec_dict).items() if k not in ("id", "version")}
    return hashlib.sha256(json.dumps(_canon(d), sort_keys=True).encode()).hexdigest()[:16]
