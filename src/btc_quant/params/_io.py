"""파일 입출력 공통: 저장소 루트, 원자적 YAML 쓰기, 단순 파일 잠금."""
from __future__ import annotations

import contextlib
import fcntl
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml


def repo_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    from btc_quant.registry.ledger import repo_root as _rr
    return _rr()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_yaml(path: Path, doc: dict, header: str = "") -> Path:
    """임시 파일에 쓴 뒤 os.replace (중간에 죽어도 반쯤 쓴 파일이 남지 않음)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(header + body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """`path` 별 배타 잠금 (같은 파일을 동시에 고치는 프로세스 직렬화). 잠금 파일은 임시 디렉터리에 둔다."""
    import hashlib
    key = hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:16]
    lp = Path(tempfile.gettempdir()) / "btcq_param_locks" / f"{key}.lock"
    lp.parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
