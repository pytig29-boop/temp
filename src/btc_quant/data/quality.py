"""OHLCV 데이터 품질 검사."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class QualityReport:
    rows: int
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    duplicates: int
    unsorted: bool
    ohlc_violations: int
    nonpositive_price: int
    negative_volume: int
    missing_bars: int
    expected_bars: int
    largest_gaps: list[tuple[pd.Timestamp, pd.Timestamp, int]] = field(default_factory=list)
    extreme_moves: int = 0
    wick_outliers: int = 0

    @property
    def ok(self) -> bool:
        return (self.duplicates == 0 and not self.unsorted and self.ohlc_violations == 0
                and self.nonpositive_price == 0 and self.negative_volume == 0)

    def to_markdown(self) -> str:
        cov = 1 - self.missing_bars / self.expected_bars if self.expected_bars else float("nan")
        lines = [
            f"- 기간: {self.start} ~ {self.end} ({self.rows:,} rows)",
            f"- 커버리지: {cov:.4%} (누락 {self.missing_bars:,} / 기대 {self.expected_bars:,})",
            f"- 중복 ts: {self.duplicates}, 정렬 오류: {self.unsorted}",
            f"- OHLC 관계 위반: {self.ohlc_violations}, 가격<=0: {self.nonpositive_price}, "
            f"음수 거래량: {self.negative_volume}",
            f"- 극단 변동(봉간 |log ret| 임계 초과): {self.extreme_moves}",
            f"- 비정상 꼬리(직전 중앙값 대비 고가·저가 이탈): {self.wick_outliers}",
            f"- 판정: {'PASS' if self.ok else 'FAIL'}",
        ]
        if self.largest_gaps:
            lines.append("- 가장 긴 공백: " + ", ".join(
                f"{a:%Y-%m-%d %H:%M}~{b:%H:%M}Z({n}봉)" for a, b, n in self.largest_gaps))
        return "\n".join(lines)


def check_ohlcv(df: pd.DataFrame, freq: str = "1min", extreme_logret: float = 0.05,
                top_gaps: int = 5) -> QualityReport:
    idx = df.index
    if len(df) == 0:
        return QualityReport(0, None, None, 0, False, 0, 0, 0, 0, 0)
    dup = int(idx.duplicated().sum())
    unsorted = not idx.is_monotonic_increasing
    d = df[~idx.duplicated()].sort_index()
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
    viol = int(((l > np.minimum(o, c)) | (h < np.maximum(o, c)) | (l > h)).sum())
    nonpos = int((d[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
    negvol = int((d["volume"] < 0).sum())
    full = pd.date_range(d.index[0], d.index[-1], freq=freq)
    missing_mask = ~full.isin(d.index)
    gaps = []
    if missing_mask.any():
        runs = pd.Series(missing_mask, index=full)
        grp = (runs != runs.shift()).cumsum()
        for _, g in runs[runs].groupby(grp[runs]):
            gaps.append((g.index[0], g.index[-1], len(g)))
        gaps.sort(key=lambda t: -t[2])
    lr = np.abs(np.diff(np.log(c)))
    return QualityReport(
        rows=len(df), start=d.index[0], end=d.index[-1], duplicates=dup, unsorted=unsorted,
        ohlc_violations=viol, nonpositive_price=nonpos, negative_volume=negvol,
        missing_bars=int(missing_mask.sum()), expected_bars=len(full),
        largest_gaps=gaps[:top_gaps], extreme_moves=int((lr > extreme_logret).sum()),
        wick_outliers=int(flag_wick_outliers(d).sum()),
    )


def flag_wick_outliers(df: pd.DataFrame, k: float = 0.03, window: int = 15) -> pd.Series:
    """고가/저가가 기준가(직전 window개 종가 중앙값과 현재 시·종가 중 가까운 값)에서 k 이상 벗어난 봉.

    얇은 호가에서 생기는 오체결(예: 코빗 문서 예시의 고가 99억)을 잡기 위한 인과적(과거만 사용) 검사.
    """
    ref = df["close"].shift(1).rolling(window, min_periods=1).median().fillna(df["open"])
    body_hi = df[["open", "close"]].max(axis=1)
    body_lo = df[["open", "close"]].min(axis=1)
    hi_ref = pd.concat([ref, body_hi], axis=1).max(axis=1)
    lo_ref = pd.concat([ref, body_lo], axis=1).min(axis=1)
    return (df["high"] > hi_ref * (1 + k)) | (df["low"] < lo_ref * (1 - k))


def clean_wicks(df: pd.DataFrame, k: float = 0.03, window: int = 15) -> pd.DataFrame:
    """비정상 꼬리 봉의 고가·저가를 몸통(시·종가)으로 잘라낸 사본. 원천 데이터는 수정하지 않는다."""
    out = df.copy()
    m = flag_wick_outliers(df, k, window)
    out.loc[m, "high"] = out.loc[m, ["open", "close"]].max(axis=1)
    out.loc[m, "low"] = out.loc[m, ["open", "close"]].min(axis=1)
    out["wick_cleaned"] = m
    return out


# ---------------------------------------------------------------------------------------------
# 거래소별 꼬리 정리 (class ① 데이터 품질 임계값, config/params.yaml `quality.clean_wicks.coinone.*`)
# 근거: research/reports/data_audit_20261109.md Issue 3 — 코인원 1분봉의 비정상 꼬리는 거의 모두 거래소 고유의
# 얇은 호가 오체결(k=1% 에서 106건 중 103건이 업비트 같은 분 범위 밖)이라 k=0.03 으로는 대부분 남는다.
# 업비트·바이낸스는 k=1% 에서 실제 시장 움직임을 잘라내므로 k=0.03 유지. 더 엄격한(더 많이 자르는) 변경 =
# 더 보수적(도달 불가능한 TP/SL 터치 제거) → class ① 비대칭 규칙상 자동 적용 허용.
# 연구 m1 준비(research.campaign.prepare_m1)와 페이퍼 M1History 가 **같은** 값을 쓴다 (wick_params).
COINONE_WICK_K = 0.01
COINONE_WICK_WINDOW = 15
WICK_PARAMS_BY_VENUE = {"coinone": ("COINONE_WICK_K", "COINONE_WICK_WINDOW")}

# 긴 공백 직후 워밍업 (데이터 점검 권고: "the first bar after each gap of ≥30 min"). filled 연속 구간(수집
# 공백 + 거래량 0 평탄 봉 = 점검 시간)이 GAP_WARMUP_MIN_GAP 분 이상이면, 그 뒤 첫 GAP_WARMUP_BARS 개의 실제 봉도
# filled 로 표시한다(가격은 그대로 → 청산 계산은 그대로, 그 봉 시가 진입·그 봉을 포함한 결정 봉 신호만 막음).
GAP_WARMUP_MIN_GAP = 30
GAP_WARMUP_BARS = 1


def wick_params(exchange: str | None = None) -> tuple[float, int]:
    """거래소별 (k, window). 모르는 거래소·None = clean_wicks 기본값(0.03, 15)."""
    names = WICK_PARAMS_BY_VENUE.get(str(exchange or "").lower())
    if names is None:
        import inspect
        sig = inspect.signature(clean_wicks).parameters
        return float(sig["k"].default), int(sig["window"].default)
    g = globals()
    return float(g[names[0]]), int(g[names[1]])


def clean_wicks_for(df: pd.DataFrame, exchange: str | None) -> pd.DataFrame:
    """거래소별 꼬리 정리 (`wick_params`)."""
    k, w = wick_params(exchange)
    return clean_wicks(df, k=k, window=w)


def gap_warmup_mask(filled, min_gap: int = GAP_WARMUP_MIN_GAP, warmup_bars: int = GAP_WARMUP_BARS) -> np.ndarray:
    """filled 연속 구간 길이 ≥ min_gap 이 끝난 뒤 첫 warmup_bars 개의 **실제(filled 아님)** 봉 = True.

    짧은 filled 구간은 워밍업 계수를 리셋하지도 소모하지도 않는다(실제 봉만 센다). 페이퍼 M1History 의
    증분 계산과 같은 정의 (paper/bars.py)."""
    f = np.asarray(filled, dtype=bool)
    n = len(f)
    out = np.zeros(n, dtype=bool)
    if n == 0 or warmup_bars <= 0 or min_gap <= 0:
        return out
    real_cum = np.cumsum(~f)
    # filled 구간의 끝 위치와 길이
    d = np.diff(np.concatenate([[0], f.astype(np.int8), [0]]))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1
    long_ends = ends[(ends - starts + 1) >= min_gap]
    if len(long_ends) == 0:
        return out
    base = np.full(n, -1, dtype=np.int64)
    base[long_ends] = real_cum[long_ends]
    base = np.maximum.accumulate(base)
    k = real_cum - base
    out = (~f) & (base >= 0) & (k >= 1) & (k <= warmup_bars)
    return out


def mark_gap_warmup(m1: pd.DataFrame, min_gap: int = GAP_WARMUP_MIN_GAP,
                    warmup_bars: int = GAP_WARMUP_BARS, flag_column: bool = False) -> pd.DataFrame:
    """regularize 결과에 긴 공백 직후 워밍업 봉을 filled 로 추가 표시한 사본
    (flag_column=True 면 `gap_warmup` 열 = 추가된 봉)."""
    out = m1.copy()
    w = gap_warmup_mask(out["filled"].to_numpy(bool), min_gap, warmup_bars)
    if flag_column:
        out["gap_warmup"] = w
    out["filled"] = out["filled"].to_numpy(bool) | w
    return out


def prepare_venue_m1(raw: pd.DataFrame, exchange: str | None = None) -> pd.DataFrame:
    """연구·페이퍼 공통 1분봉 준비: 거래소별 clean_wicks → regularize(거래량 0 평탄 봉 = filled)
    → 긴 공백 직후 워밍업 봉 filled. 페이퍼 `M1History(exchange=…)` 와 비트 단위로 같다(tests/test_paper_bars.py)."""
    return mark_gap_warmup(regularize(clean_wicks_for(raw, exchange)))


# 데이터 점검(2026-11-09)이 권고한 고정 제외 구간 — **선택** 마스크 (기본 미적용). UTC, 양끝 포함 분.
# 실제 데이터이므로 지우지 않고, 교차 거래소 모델 적합·꼬리 통계에서 제외하거나 "포함/제외" 두 결과를 보고한다.
# (코인원 점검 = 거래량 0 평탄 봉 → regularize 가 이미 filled, 바이낸스 거래 정지 구간도 같은 규칙으로 filled.)
AUDIT_EXCLUSION_WINDOWS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("2024-12-03 13:20", "2024-12-03 15:30", "martial-law cross-venue dislocation (real data; tail-dominating)",
     ("*",)),
    ("2021-02-11 02:20", "2021-02-11 04:59", "binance frozen trading + gap", ("binance",)),
    ("2023-03-24 11:28", "2023-03-24 13:59", "binance frozen trading + gap", ("binance",)),
)


def exclusion_mask(index: pd.DatetimeIndex, exchange: str | None = None,
                   windows=AUDIT_EXCLUSION_WINDOWS) -> np.ndarray:
    """index 중 제외 구간(start..end 분, 양끝 포함)에 드는 봉 = True. windows 의 거래소 목록에 exchange 또는
    '*' 가 있는 구간만."""
    idx = pd.DatetimeIndex(index)
    out = np.zeros(len(idx), dtype=bool)
    ex = str(exchange or "").lower()
    for start, end, _why, venues in windows:
        if "*" not in venues and ex not in venues:
            continue
        a = pd.Timestamp(start, tz="UTC")
        b = pd.Timestamp(end, tz="UTC")
        out |= np.asarray((idx >= a) & (idx <= b))
    return out


def apply_exclusions(m1: pd.DataFrame, exchange: str | None = None, windows=AUDIT_EXCLUSION_WINDOWS
                     ) -> pd.DataFrame:
    """선택 적용: 제외 구간의 봉을 filled 로 표시한 사본 (`excluded` 열). 진입·신호만 막고 가격은 그대로."""
    out = m1.copy()
    m = exclusion_mask(out.index, exchange, windows)
    out["excluded"] = m
    out["filled"] = out["filled"].to_numpy(bool) | m
    return out


def regularize(df: pd.DataFrame, freq: str = "1min", zero_volume_as_filled: bool = True) -> pd.DataFrame:
    """빈 봉을 직전 종가로 채운 평탄 봉으로 메우고 `filled` 플래그를 남긴다.

    거래소 REST는 무거래 봉과 수집 실패를 구분해 주지 않으므로, 분석에서는
    filled 봉이 포함된 윈도를 제외하는 보수적 처리를 기본으로 한다.

    `zero_volume_as_filled` (기본 True): 거래소가 무거래 분을 **거래량 0 평탄 봉**으로 채워 주는 경우
    (코인원 1m — 2026-11-09 데이터 점검, 개발 구간 7.6%, 점검 시간 최대 7.8시간)도 filled 로 표시한다.
    실시간 봉 생성기(paper/bars.py)는 무거래 분을 filled 로 만들므로 이 규칙이 백테스트=페이퍼 일치를 유지한다.
    """
    d = df[~df.index.duplicated(keep="last")].sort_index()
    full = pd.date_range(d.index[0], d.index[-1], freq=freq, name="ts")
    out = d.reindex(full)
    out["filled"] = out["close"].isna()
    out["close"] = out["close"].ffill()
    for k in ("open", "high", "low"):
        out[k] = out[k].fillna(out["close"])
    out["volume"] = out["volume"].fillna(0.0)
    if zero_volume_as_filled:
        flat = (out["open"] == out["high"]) & (out["high"] == out["low"]) & (out["low"] == out["close"])
        out["filled"] = out["filled"] | ((out["volume"] == 0) & flat)
    return out
