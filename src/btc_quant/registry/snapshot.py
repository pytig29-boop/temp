"""결정적 데이터 스냅샷 id (PLAN §2.3).

snapshot id = sha256( 범위 정보 + 정렬된 "상대경로:파일 sha256" 목록 ).
파일 해시는 (경로, 크기, mtime_ns) 키로 캐시한다(메모리 + 선택적 JSON 파일).
월 파티션 단위로 파일을 고르므로, 같은 파일 내용·같은 범위면 같은 id.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from .ledger import repo_root

_MEM_CACHE: dict[tuple[str, int, int], str] = {}


class FileHashCache:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self._disk: dict[str, dict] = {}
        self._dirty = False
        if self.path is not None and self.path.exists():
            try:
                self._disk = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self._disk = {}

    def file_hash(self, p: Path) -> str:
        p = Path(p).resolve()
        st = p.stat()
        key = (str(p), st.st_size, st.st_mtime_ns)
        h = _MEM_CACHE.get(key)
        if h is None:
            d = self._disk.get(str(p))
            if d and d.get("size") == st.st_size and d.get("mtime_ns") == st.st_mtime_ns:
                h = d["sha256"]
        if h is None:
            hasher = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    hasher.update(chunk)
            h = hasher.hexdigest()
        _MEM_CACHE[key] = h
        if self.path is not None and self._disk.get(str(p), {}).get("sha256") != h:
            self._disk[str(p)] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": h}
            self._dirty = True
        return h

    def save(self) -> None:
        if self.path is None or not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path) + ".lock", "a+") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            merged = {}
            if self.path.exists():
                try:
                    merged = json.loads(self.path.read_text(encoding="utf-8"))
                except ValueError:
                    merged = {}
            merged.update(self._disk)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(merged, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
            fcntl.flock(lk, fcntl.LOCK_UN)
        self._dirty = False


def _utc(x) -> pd.Timestamp | None:
    if x is None:
        return None
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _month_of(p: Path) -> tuple[int, int] | None:
    y = m = None
    for part in p.parts:
        if part.startswith("year="):
            y = int(part[5:])
        elif part.startswith("month="):
            m = int(part[6:])
    return (y, m) if y is not None and m is not None else None


def select_partitions(exchange: str, symbol: str, timeframe: str, start=None, end=None,
                      data_root: str | Path | None = None) -> list[Path]:
    """[start, end) 과 겹치는 월 파티션 parquet 파일 (정렬)."""
    root = Path(data_root) if data_root is not None else repo_root() / "data" / "raw"
    base = root / f"exchange={exchange}" / f"symbol={symbol}" / f"timeframe={timeframe}"
    s, e = _utc(start), _utc(end)
    out = []
    for f in sorted(base.rglob("*.parquet")):
        ym = _month_of(f.relative_to(base))
        if ym is None:
            continue
        m_start = pd.Timestamp(year=ym[0], month=ym[1], day=1, tz="UTC")
        m_end = m_start + pd.offsets.MonthBegin(1)
        if (s is None or m_end > s) and (e is None or m_start < e):
            out.append(f)
    return out


def snapshot_of_files(files: list[str | Path], base: str | Path | None = None,
                      extra: dict | None = None, cache: FileHashCache | None = None) -> str:
    """임의 파일 집합의 스냅샷 id. 경로는 base 기준 상대경로로 정규화(머신 독립)."""
    cache = cache or FileHashCache()
    base = Path(base).resolve() if base is not None else None
    lines = []
    for f in files:
        f = Path(f)
        rel = f.resolve().relative_to(base).as_posix() if base is not None else f.as_posix()
        lines.append(f"{rel}:{cache.file_hash(f)}")
    lines.sort()
    h = hashlib.sha256()
    h.update(json.dumps(extra or {}, sort_keys=True, default=str).encode())
    for line in lines:
        h.update(b"\n" + line.encode())
    cache.save()
    return "snap-" + h.hexdigest()[:20]


def snapshot_id(exchange: str, symbol: str, timeframe: str, start=None, end=None,
                data_root: str | Path | None = None, cache_path: str | Path | None = None) -> str:
    """한 시계열의 [start, end) 스냅샷 id. 해당 파티션이 없으면 FileNotFoundError."""
    root = Path(data_root) if data_root is not None else repo_root() / "data" / "raw"
    files = select_partitions(exchange, symbol, timeframe, start, end, root)
    if not files:
        raise FileNotFoundError(f"no partitions for {exchange}/{symbol}/{timeframe} in [{start}, {end})")
    extra = {"series": [exchange, symbol, timeframe],
             "start": str(_utc(start)), "end": str(_utc(end))}
    return snapshot_of_files(files, base=root, extra=extra, cache=FileHashCache(cache_path))


def combine_snapshots(ids: list[str]) -> str:
    """여러 시계열 스냅샷을 하나로 (순서 무관)."""
    h = hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:20]
    return "snap-" + h
