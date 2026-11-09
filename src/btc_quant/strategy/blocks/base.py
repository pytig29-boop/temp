"""블록 레지스트리 (계약 모듈).

블록 함수 시그니처:
    fn(bars: pd.DataFrame, ctx: Context, **params) -> pd.Series[bool]

- bars: 결정 시간축의 봉 (5분 또는 1분, PLAN D10; 스윙 연구 15m/1h/4h). index = 봉 시작 시각 UTC,
  컬럼 open/high/low/close/volume/quote_volume.
- 반환: bars와 같은 index의 bool Series. 값 True@t 는 "봉 t가 닫힌 시점(t+봉길이)까지의 정보로 조건 성립".
  NaN 금지(워밍업 구간은 False).
- 룩백·격자는 **봉 개수** 단위. 벽시계 시간에 의존하는 블록(세션 시각, 1시간 추세, 1일/7일 창)은
  봉 길이를 `momentum.bar_delta(bars.index, ctx)` 로 구한다 (ctx.bar 지정 > index 최빈 간격 > 5분).
- 인과성: t 이후 데이터를 바꿔도 t까지의 값은 변하지 않아야 한다 (tests의 미래 변조 테스트로 강제).
- kind: "entry" | "filter" | "exit_signal"
- 결정 주기 호환성: 긴 봉에서 무의미한 블록은 `max_tf_min`(+ `tf_note`)로 표시 → `check(..., tf_min=)` 가 거부
  (docs/BLOCKS.md "결정 주기 호환성").

등급 (PLAN D11, "B안"):
- "official": 사람이 검토·병합한 블록 (blocks/ 의 일반 모듈).
- "experimental": AI 등 비검토 작성 블록. `blocks/experimental/` 하위 모듈에 두며 자동 import된다.
  `acceptance.acceptance_check(name)` 자동 검사 통과(테스트에서 강제)만으로 캠페인에 사용할 수 있으나,
  실험 블록을 쓴 후보(`spec_uses_experimental`)는 잠금(lockbox) 요청 전 사람 검토가 필수.
  승격은 런타임 연산이 아니라 사람의 코드 변경이다 (`Registry.promote` 참조).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

KINDS = ("entry", "filter", "exit_signal")
STATUSES = ("official", "experimental")
KNOWN_AUX = ("binance", "upbit")   # ctx.aux 키. binance: 실시간 불가(live_ready=False 강제), upbit: 실시간 가능


@dataclass
class Context:
    """보조 데이터 + 실행 정보.

    aux['binance'] = 바이낸스 봉, aux['upbit'] = 업비트 봉 (둘 다 대상 거래소 bars 와 같은 봉 길이·index로
    정렬된 OHLCV DataFrame; 블록은 bars.index 로 reindex 해서 사용, 없는 시각은 NaN → False).
    bar = 봉 길이 명시 (None 이면 bars.index 에서 추론).
    """
    aux: dict[str, pd.DataFrame] = field(default_factory=dict)
    bar: pd.Timedelta | None = None


@dataclass(frozen=True)
class BlockDef:
    name: str
    kind: str
    fn: Callable[..., pd.Series]
    grid: dict[str, list]          # 캠페인 기본 탐색 격자 (파라미터별 3~5개 값)
    description: str
    requires: tuple[str, ...] = ()  # 필요한 ctx.aux 키 (예: ("binance",))
    live_ready: bool = True         # 실시간 데이터로 계산 가능 여부 (False면 승격 불가)
    # ---- 등급·출처 (D11). 기본값 = 기존 공식 블록과 동일 → 기존 생성 코드와 호환
    status: str = "official"        # "official" | "experimental"
    author: str = ""                # 작성자 (예: "claude (AI)", "human:kim")
    created: str = ""               # 작성일 ISO (YYYY-MM-DD)
    rationale: str = ""             # 메커니즘/가설: 왜 이 신호가 미래 수익과 관련될 수 있는가
    # 선택: 파라미터 → 마지막 봉 값을 정확히 재현하는 데 필요한 최소 봉 수 (후행 창 평가용, paper/)
    lookback_bars: Callable[[dict], int] | None = None
    # 결정 주기 호환성 (docs/BLOCKS.md "결정 주기 호환성"): 봉 길이(분)가 max_tf_min 을 넘으면 의미가 없어
    # 검증(`Registry.check(..., tf_min=)` ← StrategySpec.validate)에서 거부. None = 제한 없음.
    max_tf_min: int | None = None
    tf_note: str = ""               # 긴 봉에서의 의미 변화·주의 (거부 사유 또는 '거칠어짐' 경고)

    def tf_problem(self, tf_min: int) -> str | None:
        """결정 주기 tf_min(분)에서 이 블록이 무의미하면 그 사유, 아니면 None."""
        if self.max_tf_min is not None and int(tf_min) > int(self.max_tf_min):
            return (f"block {self.name} is meaningless at a {int(tf_min)}-minute decision bar "
                    f"(max {self.max_tf_min} min): {self.tf_note or 'see docs/BLOCKS.md'}")
        return None

    @property
    def experimental(self) -> bool:
        return self.status == "experimental"


class PromotionError(RuntimeError):
    """실험 → 공식 승격은 런타임에서 할 수 없다 (사람의 코드 변경 필요)."""


PROMOTION_STEPS = """\
실험 블록 '{name}' 승격 절차 (사람이 수행하는 코드 변경 — 런타임 승격 없음):
  1. candidate-review 수준으로 코드·메커니즘·테스트를 검토한다 (acceptance_check('{name}') 보고서 첨부).
  2. 함수를 {module} 에서 공식 모듈(momentum/breakout/meanrev/cross/filters/exits.py 등)로 옮기고,
     데코레이터를 REGISTRY.register_experimental(...) → REGISTRY.register(...) 로 바꾼다
     (author/created/rationale 은 유지해도 된다; status 기본값 = "official").
  3. docs/BLOCKS.md 의 해당 행 상태 열을 'experimental' → 'official' 로 바꾼다.
  4. uv run pytest -q tests/test_blocks*.py 통과 확인 후 리뷰를 거쳐 병합한다.
블록 이름·의미는 바꾸지 않는다 (의미 변경 = 새 이름의 새 블록)."""


class Registry:
    def __init__(self):
        self._defs: dict[str, BlockDef] = {}

    # ------------------------------------------------------------ 등록
    def register(self, kind: str, grid: dict[str, list], description: str,
                 requires: tuple[str, ...] = (), live_ready: bool = True, name: str | None = None, *,
                 status: str = "official", author: str = "", created: str = "", rationale: str = "",
                 lookback_bars: Callable[[dict], int] | None = None,
                 max_tf_min: int | None = None, tf_note: str = ""):
        if kind not in KINDS:
            raise ValueError(kind)
        if status not in STATUSES:
            raise ValueError(f"status {status!r} not in {STATUSES}")
        requires = tuple(requires)
        if "binance" in requires and live_ready:
            raise ValueError("blocks requiring 'binance' aux must be live_ready=False (no live Binance feed)")
        if status == "experimental" and not (author and created and rationale):
            raise ValueError("experimental blocks need author, created and rationale (provenance)")

        def deco(fn):
            n = name or fn.__name__
            if n in self._defs:
                raise ValueError(f"duplicate block {n}")
            self._defs[n] = BlockDef(n, kind, fn, grid, description, requires, live_ready,
                                     status=status, author=author, created=created, rationale=rationale,
                                     lookback_bars=lookback_bars, max_tf_min=max_tf_min, tf_note=tf_note)
            return fn
        return deco

    def register_experimental(self, kind: str, grid: dict[str, list], description: str, *,
                              author: str, created: str, rationale: str,
                              requires: tuple[str, ...] = (), live_ready: bool = True,
                              name: str | None = None,
                              lookback_bars: Callable[[dict], int] | None = None,
                              max_tf_min: int | None = None, tf_note: str = ""):
        """실험 블록 등록 (출처 필수). `blocks/experimental/*.py` 에서 사용."""
        return self.register(kind, grid, description, requires=requires, live_ready=live_ready, name=name,
                             status="experimental", author=author, created=created, rationale=rationale,
                             lookback_bars=lookback_bars, max_tf_min=max_tf_min, tf_note=tf_note)

    # ------------------------------------------------------------ 조회
    def get(self, name: str) -> BlockDef:
        return self._defs[name]

    def __contains__(self, name: str) -> bool:
        return name in self._defs

    def all(self, kind: str | None = None, *, include_experimental: bool = False) -> list[BlockDef]:
        """등록 블록 목록. 기본은 **공식 블록만** (기존 호출부 호환). 실험 블록 포함은 명시적으로."""
        return [d for d in self._defs.values()
                if (kind is None or d.kind == kind) and (include_experimental or not d.experimental)]

    def experimental(self, kind: str | None = None) -> list[BlockDef]:
        return [d for d in self.all(kind, include_experimental=True) if d.experimental]

    @staticmethod
    def _name(ref) -> str:
        return ref if isinstance(ref, str) else ref.block

    def is_experimental(self, ref) -> bool:
        """ref = 블록 이름 또는 BlockRef."""
        return self._defs[self._name(ref)].experimental

    def spec_uses_experimental(self, spec) -> list[str]:
        """스펙(진입·필터·신호청산)에 쓰인 실험 블록 이름 목록 (없으면 [] → falsy).
        비어 있지 않으면 잠금 요청 전 사람 검토가 필수 (D11)."""
        refs = [spec.entry, *spec.filters]
        if spec.exit.signal_exit is not None:
            refs.append(spec.exit.signal_exit)
        return sorted({r.block for r in refs if r.block in self._defs and self.is_experimental(r)})

    def promote(self, name: str) -> None:
        """승격은 코드 변경이다 — 항상 PromotionError(절차 안내 포함)를 던진다."""
        d = self._defs[name]
        if not d.experimental:
            raise PromotionError(f"{name} is already official")
        raise PromotionError(PROMOTION_STEPS.format(name=name, module=getattr(d.fn, "__module__", "?")))

    # ------------------------------------------------------------ 검증·평가
    def tf_problem(self, ref, tf_min: int) -> str | None:
        """ref(이름 또는 BlockRef)가 결정 주기 tf_min(분)에서 무의미하면 사유, 아니면 None."""
        return self._defs[self._name(ref)].tf_problem(tf_min)

    def check(self, ref, kind: str, *, allow_experimental: bool = True, tf_min: int | None = None) -> None:
        """tf_min: 결정 주기(분). 주면 그 주기에서 무의미한 블록(`BlockDef.max_tf_min`)을 거부한다."""
        if ref.block not in self._defs:
            raise KeyError(f"unknown block {ref.block}")
        d = self._defs[ref.block]
        if d.kind != kind:
            raise ValueError(f"{ref.block} is {d.kind}, used as {kind}")
        unknown = set(ref.params) - set(d.grid)
        if unknown:
            raise ValueError(f"{ref.block}: unknown params {unknown}")
        if d.experimental and not allow_experimental:
            raise ValueError(f"{ref.block} is experimental (not allowed here; needs human review/promotion)")
        if tf_min is not None:
            prob = d.tf_problem(tf_min)
            if prob:
                raise ValueError(prob)

    def evaluate(self, ref, bars: pd.DataFrame, ctx: Context | None = None) -> pd.Series:
        d = self._defs[ref.block]
        ctx = ctx or Context()
        missing = [k for k in d.requires if k not in ctx.aux]
        if missing:
            raise KeyError(f"block {ref.block} requires ctx.aux {missing}")
        out = d.fn(bars, ctx, **ref.params)
        return out.reindex(bars.index, fill_value=False).astype(bool)


REGISTRY = Registry()


def is_experimental(ref) -> bool:
    return REGISTRY.is_experimental(ref)


def spec_uses_experimental(spec) -> list[str]:
    return REGISTRY.spec_uses_experimental(spec)
