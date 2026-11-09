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
    )


def regularize(df: pd.DataFrame, freq: str = "1min") -> pd.DataFrame:
    """빈 봉을 직전 종가로 채운 평탄 봉으로 메우고 `filled` 플래그를 남긴다.

    거래소 REST는 무거래 봉과 수집 실패를 구분해 주지 않으므로, 분석에서는
    filled 봉이 포함된 윈도를 제외하는 보수적 처리를 기본으로 한다.
    """
    d = df[~df.index.duplicated(keep="last")].sort_index()
    full = pd.date_range(d.index[0], d.index[-1], freq=freq, name="ts")
    out = d.reindex(full)
    out["filled"] = out["close"].isna()
    out["close"] = out["close"].ffill()
    for k in ("open", "high", "low"):
        out[k] = out[k].fillna(out["close"])
    out["volume"] = out["volume"].fillna(0.0)
    return out
