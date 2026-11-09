import os

import pandas as pd

from btc_quant.data.store import write_ohlcv
from btc_quant.registry.snapshot import combine_snapshots, select_partitions, snapshot_id


def _write(root, start, periods, price=1.0):
    idx = pd.date_range(start, periods=periods, freq="1D", tz="UTC", name="ts")
    df = pd.DataFrame({"open": price, "high": price, "low": price, "close": price, "volume": 1.0,
                       "quote_volume": 1.0}, index=idx)
    write_ohlcv(df, "upbit", "KRW-BTC", "1d", root=root)


def test_snapshot_deterministic_and_sensitive(tmp_path):
    root = tmp_path / "raw"
    _write(root, "2025-01-01", 90)
    parts = select_partitions("upbit", "KRW-BTC", "1d", "2025-02-10", "2025-03-01", data_root=root)
    assert [p.parent.name for p in parts] == ["month=02"]
    a = snapshot_id("upbit", "KRW-BTC", "1d", "2025-01-01", "2025-04-01", data_root=root,
                    cache_path=tmp_path / "cache.json")
    b = snapshot_id("upbit", "KRW-BTC", "1d", "2025-01-01", "2025-04-01", data_root=root,
                    cache_path=tmp_path / "cache.json")
    assert a == b and a.startswith("snap-")
    assert (tmp_path / "cache.json").exists()
    c = snapshot_id("upbit", "KRW-BTC", "1d", "2025-01-01", "2025-03-01", data_root=root)
    assert c != a
    # 같은 내용을 다른 위치에 복사해도 같은 id
    root2 = tmp_path / "raw2"
    _write(root2, "2025-01-01", 90)
    assert snapshot_id("upbit", "KRW-BTC", "1d", "2025-01-01", "2025-04-01", data_root=root2) == a
    # 내용 변경 → 다른 id (mtime 변경으로 캐시 무효화)
    f = select_partitions("upbit", "KRW-BTC", "1d", "2025-02-01", "2025-02-02", data_root=root)[0]
    st = f.stat()
    _write(root, "2025-02-05", 1, price=2.0)
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    d = snapshot_id("upbit", "KRW-BTC", "1d", "2025-01-01", "2025-04-01", data_root=root)
    assert d != a
    assert combine_snapshots([a, c]) == combine_snapshots([c, a])
