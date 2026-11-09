import pandas as pd
import pytest
import yaml

from btc_quant.data.store import write_ohlcv
from btc_quant.registry.splits import (DataAccess, LockboxAccessError, LockboxApprovalError,
                                       LockboxExhaustedError, LockboxKey, overlaps_lockbox,
                                       segment_bounds, write_lockbox_approval)

CFG = {
    "lockbox_id": "lb1", "lockbox_cap": 3,
    "segments": {
        "dev": {"start": "2021-01-01", "end": "2025-11-01"},
        "val": {"start": "2025-11-01", "end": "2026-05-01"},
        "lockbox": {"start": "2026-05-01", "end": "2026-11-09"},
        "forward": {"start": "2026-11-09", "end": None},
    },
}


@pytest.fixture
def da(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "splits.yaml").write_text(yaml.safe_dump(CFG))
    idx = pd.date_range("2025-10-01", "2026-06-01", freq="1D", tz="UTC", name="ts", inclusive="left")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
                       "quote_volume": 1.0}, index=idx)
    write_ohlcv(df, "upbit", "KRW-BTC", "1d", root=tmp_path / "data" / "raw")
    return DataAccess(root=tmp_path)


def test_repo_config_matches_plan():
    assert segment_bounds("dev")[0] == pd.Timestamp("2021-01-01", tz="UTC")
    assert segment_bounds("val") == (pd.Timestamp("2025-11-01", tz="UTC"), pd.Timestamp("2026-05-01", tz="UTC"))
    assert segment_bounds("lockbox") == (pd.Timestamp("2026-05-01", tz="UTC"), pd.Timestamp("2026-11-09", tz="UTC"))
    fs, fe = segment_bounds("forward")
    assert fs == pd.Timestamp("2026-11-09", tz="UTC") and fe >= fs


def test_segment_read_and_lockbox_block(da):
    val = da.read("val", "upbit", "KRW-BTC", "1d")
    assert val.index.min() == pd.Timestamp("2025-11-01", tz="UTC")
    assert val.index.max() == pd.Timestamp("2026-04-30", tz="UTC")
    dev = da.read("dev", "upbit", "KRW-BTC", "1d")
    assert dev.index.max() < pd.Timestamp("2025-11-01", tz="UTC")
    with pytest.raises(LockboxAccessError):
        da.read("lockbox", "upbit", "KRW-BTC", "1d")
    with pytest.raises(LockboxAccessError):
        da.read_range("2026-04-01", "2026-05-02", "upbit", "KRW-BTC", "1d")
    with pytest.raises(LockboxAccessError):  # 위조 키
        da.read("lockbox", "upbit", "KRW-BTC", "1d", key=LockboxKey("c1", "lb1", "lbx-000001", "forged"))
    assert not overlaps_lockbox("2026-04-01", "2026-05-01", da.config)
    assert overlaps_lockbox(None, None, da.config)


def test_open_lockbox_flow(da, tmp_path):
    with pytest.raises(LockboxApprovalError):
        da.open_lockbox("c1")
    write_lockbox_approval(tmp_path, "c1", "human")
    key = da.open_lockbox("c1")
    lb = da.read("lockbox", "upbit", "KRW-BTC", "1d", key=key)
    assert lb.index.min() == pd.Timestamp("2026-05-01", tz="UTC")
    assert not (tmp_path / "research" / "lockbox_approvals" / "c1.yaml").exists()  # 소비됨
    assert len(list((tmp_path / "research" / "lockbox_approvals" / "consumed").glob("c1__*.yaml"))) == 1
    h = da.ledger.lockbox_history()
    assert h[0]["candidate_id"] == "c1" and h[0]["approval"]["approved_by"] == "human"
    # 같은 후보 재개봉 불가 (승인 파일이 있어도)
    write_lockbox_approval(tmp_path, "c1", "human")
    with pytest.raises(LockboxApprovalError):
        da.open_lockbox("c1")


def test_lockbox_cap(da, tmp_path):
    for c in ("a", "b", "c"):
        write_lockbox_approval(tmp_path, c, "human")
        da.open_lockbox(c)
    write_lockbox_approval(tmp_path, "d", "human")
    with pytest.raises(LockboxExhaustedError, match="Roll the lockbox"):
        da.open_lockbox("d")
    assert da.lockbox_status()["remaining"] == 0
    # 롤링(새 lockbox_id) 후에는 다시 가능, 이전 키는 무효
    old_key_cfg = dict(CFG, lockbox_id="lb2")
    (tmp_path / "config" / "splits.yaml").write_text(yaml.safe_dump(old_key_cfg))
    with pytest.raises(LockboxApprovalError):  # 승인 파일이 lb1 용
        da.open_lockbox("d")
    write_lockbox_approval(tmp_path, "d", "human")
    assert da.open_lockbox("d").lockbox_id == "lb2"


def test_bad_candidate_id(da):
    with pytest.raises(ValueError):
        da.open_lockbox("../etc/passwd")
