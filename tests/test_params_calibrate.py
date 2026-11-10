"""params.calibrate — 비대칭 규칙, 표본 부족 거부, 잠정 처리, 호가 파일 적재 (합성 데이터, 네트워크 없음)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_quant.params import calibrate as K
from btc_quant.params import costs as C
from btc_quant.params import proposals as P

ROOT = Path(__file__).resolve().parents[1]


def make_root(tmp_path: Path) -> Path:
    from _config_root import make_config_root
    return make_config_root(tmp_path)      # 프리셋과 같은 version 1 (+ 자산별 모델·ticks.yaml — 카탈로그 일치)


def samples(level_bps: float, spread_bps: float, days: int, hours_per_day: int = 24, per_hour: int = 80,
            seed: int = 0, start="2026-11-01") -> pd.DataFrame:
    """편도 비용 ~ level × lognormal. 시간마다 per_hour 스냅샷 × 매수·매도."""
    rng = np.random.default_rng(seed)
    rows = []
    t0 = pd.Timestamp(start, tz="UTC")
    k = 0
    for d in range(days):
        for h in range(hours_per_day):
            for i in range(per_hour):
                ts = t0 + pd.Timedelta(days=d, hours=h, seconds=i * 30)
                for side in ("buy", "sell"):
                    rows.append((ts, f"s:{k}", side, level_bps * rng.lognormal(0, 0.3), spread_bps, True, "rest:x"))
                k += 1
    return pd.DataFrame(rows, columns=["ts", "snap", "side", "cost_bps", "spread_bps", "complete", "source"])


def test_insufficient_data_refused(tmp_path):
    root = make_root(tmp_path)
    small = samples(0.5, 1.0, days=1, hours_per_day=1, per_hour=100)
    with pytest.raises(K.InsufficientDataError):
        K.calibrate(("coinone",), root=root, samples={"coinone": small})
    with pytest.raises(K.InsufficientDataError):
        K.calibrate(("coinone",), root=root, samples={"coinone": small.iloc[:0]})
    # 한 거래소만 부족하면 그 거래소만 refused, 변경 없음
    res = K.calibrate(("coinone", "upbit"), root=root,
                      samples={"coinone": small, "upbit": samples(3.0, 6.0, days=8)})
    assert res.venues["coinone"].status == "refused" and not res.venues["coinone"].decisions
    assert res.venues["upbit"].status == "final"


def test_stricter_auto_applies_without_proposal(tmp_path):
    root = make_root(tmp_path)
    # 실측 편도 비용이 모든 현재 모델보다 비쌈 → 모두 stricter → 자동 적용
    res = K.calibrate(("coinone",), root=root, samples={"coinone": samples(12.0, 6.0, days=8)})
    assert res.venues["coinone"].status == "final"
    assert all(d["direction"] == "stricter" and d["action"] == "auto"
               for d in res.venues["coinone"].decisions.values())
    res = K.apply_calibration(res, root=root, report_name="cal_test.md")
    assert res.proposal_id is None
    assert set(res.applied) == {"coinone_base", "coinone_conservative", "coinone_stress"}
    for n in res.applied:
        h = C.cost_history(n, root)
        assert h[-1]["applied_by"] == "auto" and h[-1]["direction"] == "stricter"
        assert C.round_trip_bps(C.cost_model(n, root=root)) > C.round_trip_bps(C.cost_model(n, 1, root=root))
    s = {r: C.cost_model(f"coinone_{r}", root=root) for r in C.ROLES}
    assert C.side_cost_bps(s["base"]) <= C.side_cost_bps(s["conservative"]) <= C.side_cost_bps(s["stress"])
    assert (root / "research" / "reports" / "cal_test.md").exists()
    assert not P.list_proposals(root)


def test_looser_makes_proposal_and_leaves_costs(tmp_path):
    root = make_root(tmp_path)
    before = (root / "config" / "costs.yaml").read_text()
    res = K.calibrate(("coinone",), root=root, samples={"coinone": samples(0.5, 1.0, days=8)})
    assert all(d["direction"] == "looser" and d["action"] == "propose"
               for d in res.venues["coinone"].decisions.values())
    res = K.apply_calibration(res, root=root, report_name="cal_test.md")
    assert res.applied == {}
    assert (root / "config" / "costs.yaml").read_text() == before
    doc = P.load_proposal(res.proposal_id, root)
    assert doc["class"] == 1 and doc["kind"] == "cost_models" and doc["status"] == "pending"
    assert {c["name"] for c in doc["changes"]} == {"coinone_base", "coinone_conservative", "coinone_stress"}
    stress = next(c for c in doc["changes"] if c["name"] == "coinone_stress")
    assert stress["from"]["round_trip_bps"] == pytest.approx(21.0)
    assert stress["to"]["round_trip_bps"] < 21.0


def test_mixed_directions_split(tmp_path):
    root = make_root(tmp_path)
    # 분포 꼬리가 두꺼움: 중앙값은 싸고 p99 × 1.5 는 21bp 보다 비쌈
    s = samples(0.5, 1.0, days=8)
    s.loc[s.sample(frac=0.03, random_state=1).index, "cost_bps"] = 20.0
    res = K.apply_calibration(K.calibrate(("coinone",), root=root, samples={"coinone": s}), root=root,
                              report_name="cal_test.md")
    dec = res.venues["coinone"].decisions
    assert dec["coinone_base"]["action"] == "propose"
    assert dec["coinone_stress"]["action"] == "auto"
    assert "coinone_stress" in res.applied and res.proposal_id
    assert [c["name"] for c in P.load_proposal(res.proposal_id, root)["changes"]] != []


def test_provisional_uses_upper_bound_and_never_auto_applies(tmp_path):
    root = make_root(tmp_path)
    s = samples(12.0, 6.0, days=1, hours_per_day=3, per_hour=120)   # 3 시간 → provisional
    res = K.calibrate(("coinone",), root=root, samples={"coinone": s})
    vc = res.venues["coinone"]
    assert vc.status == "provisional" and res.provisional
    point = float(np.quantile(s["cost_bps"], 0.5))
    assert vc.estimates["base"]["side_bps"] >= point - 1e-9           # 부트스트랩 상한 ≥ 점추정
    assert all(d["action"] == "propose" for d in vc.decisions.values())  # 엄격 변경도 제안
    res = K.apply_calibration(res, root=root, report_name="cal_test.md")
    assert res.applied == {} and P.load_proposal(res.proposal_id, root)["provisional"] is True
    assert "잠정" in (root / "research" / "reports" / "cal_test.md").read_text()


def _book(mid=100_000_000.0, half_spread_bps=0.5, qty=0.5, levels=15, tick_bps=0.5):
    bids = [mid * (1 - (half_spread_bps + i * tick_bps) / 1e4) for i in range(levels)]
    asks = [mid * (1 + (half_spread_bps + i * tick_bps) / 1e4) for i in range(levels)]
    return {"bid_px": bids, "bid_qty": [qty] * levels, "ask_px": asks, "ask_qty": [qty] * levels,
            "best_bid": bids[0], "best_ask": asks[0], "mid": mid, "spread_bps": 2 * half_spread_bps}


def test_book_costs_from_collector_schema(tmp_path):
    root = make_root(tmp_path)
    d = root / "data" / "raw" / "exchange=coinone" / "symbol=BTC-KRW" / "orderbook"
    (d / "date=2026-11-09").mkdir(parents=True)
    t0 = pd.Timestamp("2026-11-09T00:00:00Z").value // 1_000_000
    rows = [{"ts": t0 + i * 1000, "ts_recv": 0.0, **_book()} for i in range(10)]
    pd.DataFrame(rows).to_parquet(d / "date=2026-11-09" / "00-0001.parquet", index=False)
    pd.DataFrame(rows).to_parquet(d / "rest_20261109T000000.parquet", index=False)
    # 구 요약 스키마 (매수 쪽만)
    pd.DataFrame({"ts": pd.to_datetime([t0], unit="ms", utc=True), "spread_bps": [1.0],
                  "buy_impact_bps_300000": [0.6]}).set_index("ts").to_parquet(d / "snap_20261109T000000.parquet")
    out = K.load_orderbook_costs("coinone", root, notional=300_000)
    assert len(out) == 10 * 2 * 2 + 1
    ws = out[out["source"].str.startswith("ws")]
    assert ws["cost_bps"].between(0.49, 0.51).all()       # 30만 원은 최우선 호가 안: 스프레드/2
    assert set(ws["side"]) == {"buy", "sell"}
    assert out["complete"].all()
    big = K.load_orderbook_costs("coinone", root, notional=1e12)
    assert np.isinf(big[big["source"].str.startswith("ws")]["cost_bps"]).all()   # 깊이 부족 = ∞ (보수)
