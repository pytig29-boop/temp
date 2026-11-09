"""블록 자동 수용 검사 (PLAN D11). AI 작성 블록이 사람 없이 '실험' 등급으로 쓰이기 위한 관문.

`acceptance_check(name) -> AcceptanceReport` 는 합성 시장(대상 + 바이낸스 + 업비트, 5분봉·1분봉)에서:

  metadata          kind/status 유효, 설명, 격자 1~2 파라미터 × 3~5 값, requires ⊆ KNOWN_AUX,
                    binance 요구 → live_ready=False, 실험 블록은 author/created/rationale 필수
  signature         fn(bars, ctx, **params), 격자 키는 기본값 있는 파라미터
  output_types      기본값/중앙/처음/끝 + 축별 격자 전체 값: bool Series, index 일치, NaN 없음 (5분·1분봉)
  purity            입력(bars, aux) 변경 없음, 같은 입력 두 번 → 같은 출력
  causality_future  t 이후 봉(대상·aux 모두)의 가격·거래량 변조 → t까지 출력 불변 (5분: 3 파라미터 × 4 지점,
                    1분: 2 지점)
  causality_trunc   앞부분만 잘라 계산 → 겹치는 구간 출력 동일 (5분 3 지점, 1분 2 지점)
  front_window      lookback_bars 가 선언된 경우: 마지막 lookback_bars(p)+여유 봉만으로 계산한 마지막 값 ==
                    전체 이력 값 (paper 후행 창 평가의 정확성)
  non_degenerate    5분봉 격자 중앙값 신호 비율: filter 1%~99%, entry/exit 0.1%~60%
  non_degenerate_1m 1분봉: 처음/중앙/끝 파라미터 중 하나 이상에서 0 < 비율 < 1
  docs              docs/BLOCKS.md 에 "| `name` |" 행 존재, 실험 블록이면 그 행에 'experimental'

합성 데이터는 시장 결과를 의미하지 않는다 (논리·인과성 검사용). 이 검사를 통과해도 '엣지'가 있다는 뜻이 아니다.
"""
from __future__ import annotations

import inspect
import re
import traceback
import zlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .base import KINDS, KNOWN_AUX, REGISTRY, STATUSES, BlockDef, Context, Registry

DOCS_PATH = Path(__file__).resolve().parents[4] / "docs" / "BLOCKS.md"
OHLCV = ["open", "high", "low", "close", "volume", "quote_volume"]


# ===================================================================== 합성 시장
def _m1_from_logret(r: np.ndarray, idx: pd.DatetimeIndex, p0: float, vol: np.ndarray, rng) -> pd.DataFrame:
    close = p0 * np.exp(np.cumsum(r))
    open_ = np.r_[p0, close[:-1]]
    wick = np.abs(rng.normal(0, 0.0004, (2, len(r))))
    high = np.maximum(open_, close) * (1 + wick[0])
    low = np.minimum(open_, close) * (1 - wick[1])
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": vol, "quote_volume": vol * close}, index=idx)


def resample_bars(m1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """1분봉 → minutes분봉 (index = 봉 시작, left/left)."""
    if minutes == 1:
        return m1[OHLCV].copy()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "quote_volume": "sum"}
    return m1.resample(f"{minutes}min", label="left", closed="left").agg(agg).dropna(subset=["close"])[OHLCV]


def synthetic_market(days: int = 60, seed: int = 0, bar_minutes: int = 5
                     ) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """(대상 KRW 봉, {"binance": USDT 봉, "upbit": KRW 봉}) — 모두 bar_minutes 봉, 같은 index.

    구조 (1분 단위로 생성 후 집계):
      바이낸스 수익률 rb (변동성 국면 + KST 22~01·09시 계절성)
      업비트   ru_t = 0.85·rb_{t-1} + 잡음                      (바이낸스 1분 지연)
      대상     log p_t = log p^upbit_{t-L} + premium_t + ou_t   (L = bar_minutes → 정확히 1봉 지연,
               premium = 느린 랜덤워크 표류, ou = 평균회귀 잡음)
    """
    rng = np.random.default_rng(seed)
    n = days * 1440
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC", name="ts")
    regime = np.exp(np.convolve(rng.normal(0, 0.05, n), np.ones(720) / np.sqrt(720), mode="same"))
    hour_kst = (idx.hour + 9) % 24
    season = np.where(np.isin(hour_kst, [22, 23, 0, 9]), 1.5, 1.0)
    sig = 0.0011 * regime * season
    rb = rng.normal(0, 1, n) * sig
    ru = 0.85 * np.r_[0.0, rb[:-1]] + rng.normal(0, 1, n) * sig * 0.5
    lag = int(bar_minutes)
    premium = np.cumsum(rng.normal(0, 0.5e-4, n))
    ou = np.zeros(n)
    eps = rng.normal(0, 3e-4, n)
    for i in range(1, n):                       # AR(1) 0.9 → 정상 표준편차 ≈ 7bp
        ou[i] = 0.9 * ou[i - 1] + eps[i]
    log_up = np.cumsum(ru)
    log_tg = np.r_[np.zeros(lag), log_up[:-lag]] + premium + ou
    rl = np.diff(np.r_[0.0, log_tg])
    vb = np.exp(rng.normal(3, 0.5, n)) * season * regime
    vu = np.exp(rng.normal(1, 0.6, n)) * season * regime
    vl = np.exp(rng.normal(0, 0.6, n)) * season * regime
    bn = resample_bars(_m1_from_logret(rb, idx, 60_000.0, vb, rng), lag)
    up = resample_bars(_m1_from_logret(ru, idx, 90_000_000.0, vu, rng), lag)
    tg = resample_bars(_m1_from_logret(rl, idx, 90_000_000.0, vl, rng), lag)
    return tg, {"binance": bn.reindex(tg.index), "upbit": up.reindex(tg.index)}


@lru_cache(maxsize=8)
def _market(days: int, seed: int, minutes: int):
    return synthetic_market(days, seed, minutes)


def perturb_after(frames: list[pd.DataFrame], t, rng) -> list[pd.DataFrame]:
    """t 이후 봉의 가격(OHLC 일관 유지)·거래량을 무작위 변조한 복사본."""
    out = []
    for f in frames:
        f = f.copy()
        after = f.index > t
        k = int(after.sum())
        if k:
            mult = np.exp(rng.normal(0, 0.02, (k, 1)) + rng.normal(0, 0.003, (k, 4)))
            px = f.loc[after, ["open", "high", "low", "close"]].to_numpy() * mult
            f.loc[after, ["open", "high", "low", "close"]] = px
            f.loc[after, "high"] = px.max(axis=1)
            f.loc[after, "low"] = px.min(axis=1)
            f.loc[after, ["volume", "quote_volume"]] *= np.exp(rng.normal(0, 1.0, (k, 1)))
        out.append(f)
    return out


# ===================================================================== 보고서
@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class AcceptanceReport:
    block: str
    status: str = ""
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def failed(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]

    def to_dict(self) -> dict:
        return {"block": self.block, "status": self.status, "passed": self.passed,
                "checks": [c.__dict__.copy() for c in self.checks]}

    def __str__(self) -> str:
        head = f"acceptance_check({self.block}) [{self.status}]: {'PASS' if self.passed else 'FAIL'}"
        lines = [f"  {'ok  ' if c.passed else 'FAIL'} {c.name}: {c.detail}" for c in self.checks]
        return "\n".join([head, *lines])


# ===================================================================== 검사
def _param_sets(d: BlockDef) -> list[dict]:
    mid = {k: v[len(v) // 2] for k, v in d.grid.items()}
    first = {k: v[0] for k, v in d.grid.items()}
    last = {k: v[-1] for k, v in d.grid.items()}
    out = []
    for p in (mid, first, last):
        if p not in out:
            out.append(p)
    return out


def _call(d: BlockDef, bars: pd.DataFrame, aux: dict, **params) -> pd.Series:
    ctx = Context(aux={k: aux[k] for k in d.requires if k in aux})
    return d.fn(bars, ctx, **params)


def _type_problem(out, bars: pd.DataFrame) -> str:
    if not isinstance(out, pd.Series):
        return f"returned {type(out).__name__}, not Series"
    if out.dtype != bool:
        return f"dtype {out.dtype}, not bool"
    if not out.index.equals(bars.index):
        return "index differs from bars.index"
    if out.isna().any():
        return "contains NaN"
    return ""


def _check_metadata(d: BlockDef) -> CheckResult:
    p = []
    if d.kind not in KINDS:
        p.append(f"kind {d.kind}")
    if d.status not in STATUSES:
        p.append(f"status {d.status}")
    if not d.description.strip():
        p.append("empty description")
    if not 1 <= len(d.grid) <= 2:
        p.append(f"{len(d.grid)} grid params (1–2 allowed)")
    bad = [k for k, v in d.grid.items() if not 3 <= len(v) <= 5]
    if bad:
        p.append(f"grid sizes not 3–5: {bad}")
    unk = [r for r in d.requires if r not in KNOWN_AUX]
    if unk:
        p.append(f"unknown aux {unk}")
    if "binance" in d.requires and d.live_ready:
        p.append("requires binance but live_ready=True")
    if d.status == "experimental":
        miss = [f for f in ("author", "created", "rationale") if not getattr(d, f).strip()]
        if miss:
            p.append(f"missing provenance {miss}")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.created):
            p.append("created must be YYYY-MM-DD")
    return CheckResult("metadata", not p, "; ".join(p) or f"kind={d.kind} requires={list(d.requires)} "
                                                           f"live_ready={d.live_ready}")


def _check_signature(d: BlockDef) -> CheckResult:
    sig = inspect.signature(d.fn)
    names = list(sig.parameters)
    p = []
    if len(names) < 2:
        p.append("signature must start with (bars, ctx)")
    for k in d.grid:
        if k not in sig.parameters:
            p.append(f"grid key {k} not a parameter")
        elif sig.parameters[k].default is inspect.Parameter.empty:
            p.append(f"param {k} has no default")
    return CheckResult("signature", not p, "; ".join(p) or f"{names}")


def _check_output_types(d: BlockDef, data: dict) -> CheckResult:
    sets = [{}] + _param_sets(d)
    mid = _param_sets(d)[0]
    for k, values in d.grid.items():
        sets += [{**mid, k: v} for v in values]
    n = 0
    for tf, (bars, aux) in data.items():
        for p in sets:
            prob = _type_problem(_call(d, bars, aux, **p), bars)
            if prob:
                return CheckResult("output_types", False, f"{tf} {p}: {prob}")
            n += 1
    return CheckResult("output_types", True, f"{n} evaluations ok")


def _check_purity(d: BlockDef, bars: pd.DataFrame, aux: dict) -> CheckResult:
    p = _param_sets(d)[0]
    b2, a2 = bars.copy(), {k: v.copy() for k, v in aux.items()}
    o1 = _call(d, b2, a2, **p)
    o2 = _call(d, b2, a2, **p)
    if not b2.equals(bars) or any(not a2[k].equals(aux[k]) for k in aux):
        return CheckResult("purity", False, "block mutated its inputs")
    if not o1.equals(o2):
        return CheckResult("purity", False, "non-deterministic output")
    return CheckResult("purity", True, "inputs unchanged, deterministic")


def _check_future(d: BlockDef, bars, aux, param_sets, n_cuts: int, tag: str) -> CheckResult:
    rng = np.random.default_rng(zlib.crc32((d.name + tag).encode()))
    keys = list(aux)
    n = len(bars)
    k = 0
    for p in param_sets:
        base = _call(d, bars, aux, **p)
        for cut in rng.integers(n // 10, n - 10, size=n_cuts):
            t = bars.index[cut]
            frames = perturb_after([bars, *[aux[x] for x in keys]], t, rng)
            out = _call(d, frames[0], dict(zip(keys, frames[1:])), **p)
            if not out.loc[:t].equals(base.loc[:t]):
                diff = out.loc[:t] != base.loc[:t]
                first = diff[diff].index[0]
                return CheckResult(f"causality_future{tag}", False,
                                   f"{p} cut={t}: output at {first} changed when only bars after cut changed")
            k += 1
    return CheckResult(f"causality_future{tag}", True, f"{k} perturbations ok")


def _check_trunc(d: BlockDef, bars, aux, params, cuts, tag: str) -> CheckResult:
    base = _call(d, bars, aux, **params)
    for c in cuts:
        out = _call(d, bars.iloc[:c], {k: v.iloc[:c] for k, v in aux.items()}, **params)
        if not out.equals(base.iloc[:c]):
            diff = out != base.iloc[:c]
            first = diff[diff].index[0] if diff.any() else "?"
            return CheckResult(f"causality_trunc{tag}", False, f"{params} cut={c}: differs at {first}")
    return CheckResult(f"causality_trunc{tag}", True, f"{len(cuts)} cuts ok")


def _check_front_window(d: BlockDef, bars, aux) -> CheckResult:
    if d.lookback_bars is None:
        return CheckResult("front_window", True, "skipped (no lookback_bars declared)")
    n = len(bars)
    for p in _param_sets(d):
        full_p = {**{k: v.default for k, v in inspect.signature(d.fn).parameters.items()
                     if v.default is not inspect.Parameter.empty}, **p}
        lb = int(d.lookback_bars(full_p))
        if lb + 2 > n // 2:
            return CheckResult("front_window", False, f"lookback_bars={lb} too large to verify")
        full = _call(d, bars, aux, **p)
        for e in np.linspace(n // 2, n - 1, 25).astype(int):
            s = slice(e - lb - 2, e + 1)
            part = _call(d, bars.iloc[s], {k: v.iloc[s] for k, v in aux.items()}, **p)
            if bool(part.iloc[-1]) != bool(full.iloc[e]):
                return CheckResult("front_window", False,
                                   f"{p}: last value from trailing {lb + 3} bars != full history at {bars.index[e]}")
    return CheckResult("front_window", True, "trailing-window last values match full history")


def _check_non_degenerate(d: BlockDef, bars, aux) -> CheckResult:
    p = _param_sets(d)[0]
    rate = float(_call(d, bars, aux, **p).mean())
    lo, hi = (0.01, 0.99) if d.kind == "filter" else (0.001, 0.60)
    return CheckResult("non_degenerate", lo < rate < hi, f"rate={rate:.4f} at {p}, allowed ({lo}, {hi})")


def _check_non_degenerate_1m(d: BlockDef, bars, aux) -> CheckResult:
    rates = {str(p): float(_call(d, bars, aux, **p).mean()) for p in _param_sets(d)}
    ok = any(0 < r < 1 for r in rates.values())
    return CheckResult("non_degenerate_1m", ok, ", ".join(f"{k}: {v:.4f}" for k, v in rates.items()))


def _check_docs(d: BlockDef, docs_path: Path) -> CheckResult:
    if not docs_path.exists():
        return CheckResult("docs", False, f"{docs_path} missing")
    rows = [ln for ln in docs_path.read_text(encoding="utf-8").splitlines() if ln.startswith(f"| `{d.name}` |")]
    if not rows:
        return CheckResult("docs", False, f"no '| `{d.name}` |' row in {docs_path.name}")
    if d.status == "experimental" and "experimental" not in rows[0]:
        return CheckResult("docs", False, "docs row must mark the block 'experimental'")
    return CheckResult("docs", True, f"row present in {docs_path.name}")


def acceptance_check(block_name: str, registry: Registry | None = None, docs_path: str | Path | None = None,
                     days_5m: int = 60, days_1m: int = 15, seed: int = 11) -> AcceptanceReport:
    """블록 자동 수용 검사. 반환 보고서의 `.passed` 가 True 여야 실험 블록으로 사용 가능."""
    reg = registry or REGISTRY
    rep = AcceptanceReport(block_name)
    if block_name not in reg:
        rep.checks.append(CheckResult("registered", False, "unknown block"))
        return rep
    d = reg.get(block_name)
    rep.status = d.status
    rep.checks.append(CheckResult("registered", True, d.status))
    docs = Path(docs_path) if docs_path is not None else DOCS_PATH

    m5 = _market(days_5m, seed, 5)
    m1 = _market(days_1m, seed + 1, 1)
    small = {"5m": (m5[0].iloc[: 10 * 288], {k: v.iloc[: 10 * 288] for k, v in m5[1].items()}),
             "1m": (m1[0].iloc[: 3 * 1440], {k: v.iloc[: 3 * 1440] for k, v in m1[1].items()})}
    n5, n1 = len(m5[0]), len(m1[0])
    mid = _param_sets(d)[0]
    steps = [
        ("metadata", lambda: _check_metadata(d)),
        ("signature", lambda: _check_signature(d)),
        ("output_types", lambda: _check_output_types(d, small)),
        ("purity", lambda: _check_purity(d, *small["5m"])),
        ("causality_future", lambda: _check_future(d, *m5, _param_sets(d), 4, "")),
        ("causality_future_1m", lambda: _check_future(d, *m1, [mid], 2, "_1m")),
        ("causality_trunc", lambda: _check_trunc(d, *m5, mid, (n5 // 3, n5 // 2 + 7, n5 - 1), "")),
        ("causality_trunc_1m", lambda: _check_trunc(d, *m1, mid, (n1 // 2 + 3, n1 - 1), "_1m")),
        ("front_window", lambda: _check_front_window(d, *m5)),
        ("non_degenerate", lambda: _check_non_degenerate(d, *m5)),
        ("non_degenerate_1m", lambda: _check_non_degenerate_1m(d, *m1)),
        ("docs", lambda: _check_docs(d, docs)),
    ]
    for name, step in steps:
        try:
            rep.checks.append(step())
        except Exception as e:  # noqa: BLE001 — 예외도 검사 실패로 기록
            msg = traceback.format_exception_only(type(e), e)[-1].strip()
            rep.checks.append(CheckResult(name, False, f"raised {msg}"))
    return rep
