"""전진 관찰 목록 (research/forward.py watchlist) — 네트워크 없음(가짜 REST), 임시 저장소.

사용자 결정 2026-11-10: G6 에서 떨어진 전략은 승격하지 않되, 사람이 관찰 목록에 올리면 Forward 데이터에서 매일
관찰만 한다. 지표 워밍업은 페이퍼처럼 Forward 직전 REST 캔들(잠금 구간 가격)을 메모리에서만 쓴다 — 저장·성과 없음.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from test_research_forward import FWD_START, NOW, env  # noqa: F401  (fixture)

from btc_quant.backtest import fast
from btc_quant.research import forward as F
from btc_quant.research.campaign import get_cost, prepare_m1
from btc_quant.research.campaign import get_sleeve_cost
from btc_quant.strategy.spec import BlockRef, ExitSpec, SizingSpec, StrategySpec

KEY = "wl_atrch_1h_v1"


def _market(seed: int = 21) -> pd.DataFrame:
    """2024-02-01 ~ 06-01 (Forward 시작 2024-04-01 이전 = 테스트 저장소의 잠금 구간 가격 포함)."""
    idx = pd.date_range("2024-02-01", "2024-06-01", freq="1min", tz="UTC", inclusive="left", name="ts")
    rng = np.random.default_rng(seed)
    c = 60_000_000 * np.exp(np.cumsum(rng.normal(0, 0.0008, len(idx))))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0002, len(idx))))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0002, len(idx))))
    v = rng.lognormal(0, 0.5, len(idx))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v, "quote_volume": v * c},
                        index=idx)


class RestFake:
    """거래소 REST 흉내. Forward 수집과 워밍업 요청을 구분해 기록한다."""

    def __init__(self, fail: set[str] = frozenset()):
        self.data = _market()
        self.calls: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
        self.fail = set(fail)

    def __call__(self, exchange, start, end):
        self.calls.append((exchange, start, end))
        if exchange in self.fail:
            raise ConnectionError("boom")
        d = self.data
        return d[(d.index >= start) & (d.index < end)].copy()

    def warm_calls(self):
        return [c for c in self.calls if c[1] < FWD_START]


def _spec() -> StrategySpec:
    return StrategySpec("wl_atrch_1h", BlockRef("atr_channel_breakout", {"span": 24, "mult": 1.0}),
                        ExitSpec(None, None, 480, stop_loss_atr=2.0, take_profit_atr=3.0),
                        filters=(BlockRef("vol_regime", {"window": 24, "min_pct": 0.5}),), timeframe="1h")


def _entry(spec: StrategySpec | None = None, **over) -> dict:
    spec = spec or _spec()
    e = {"key": F.key_of(spec.id, spec.version), "logic_hash": spec.logic_hash(), "source_campaign": None,
         "added_by": "user (test)", "added_at": "2024-04-01", "reason": "Dev survivor, failed G6; observation only",
         "spec": spec.to_dict()}
    e.update(over)
    return e


def _write(env, entries) -> Path:
    camp = env.ledger.records("campaigns")[0]["id"]
    for e in entries:
        if isinstance(e, dict) and e.get("source_campaign") is None:
            e["source_campaign"] = camp
    p = F.watchlist_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"entries": entries}, sort_keys=False))
    return p


def _wl(out) -> dict:
    return next(r for r in out["results"] if r["strategy_key"] == KEY)


# ------------------------------------------------------------------ 파싱
def test_parse_valid_and_invalid_entries(env, tmp_path):
    spec = _spec()
    sp = tmp_path / "spec.yaml"
    sp.write_text(spec.to_yaml())
    other = StrategySpec("wl_other", BlockRef("donchian_breakout", {"lookback": 24}), ExitSpec(0.6, 0.8, 60))
    p = _write(env, [
        _entry(),
        {**_entry(other), "spec_path": str(sp)},                                   # spec 과 spec_path 둘 다
        _entry(other, logic_hash="deadbeefdeadbeef"),                              # 해시 불일치
        _entry(other, reason=""),                                                  # 필수 항목 누락
        _entry(other, key="nope_v1"),                                              # key 불일치
        _entry(),                                                                  # 중복
        "not a mapping",
    ])
    entries, errors = F.load_watchlist(p, base=env.root)
    assert [m["key"] for m, _ in entries] == [KEY]
    meta, s = entries[0]
    assert s == spec and meta["added_by"] == "user (test)" and meta["logic_hash"] == spec.logic_hash()
    msgs = " | ".join(e["error"] for e in errors)
    assert len(errors) == 6
    for frag in ("exactly one", "logic_hash", "missing ['reason']", "!= spec key", "duplicate", "mapping"):
        assert frag in msgs, frag
    # spec_path (저장소 기준 상대 경로) 도 된다
    rel = Path("specs") / "w.yaml"
    (Path(env.root) / rel).parent.mkdir(parents=True, exist_ok=True)
    (Path(env.root) / rel).write_text(spec.to_yaml())
    e = _entry()
    del e["spec"]
    e["spec_path"] = str(rel)
    _write(env, [e])
    fss, errs = F.resolve_watchlist(env)
    assert errs == [] and len(fss) == 1
    fs = fss[0]
    assert (fs.state, fs.warmup, fs.exchange, fs.cost) == ("watchlist", "rest", "coinone", "coinone_conservative")
    # 파일이 없으면 비어 있음
    F.watchlist_path(env).unlink()
    assert F.resolve_watchlist(env) == ([], [])


def test_repo_watchlist_file_is_valid():
    """저장소의 config/forward_watchlist.yaml: camp-000017 대표 스펙, 해시 고정, 사람 결정 메타데이터."""
    root = Path(__file__).resolve().parents[1]
    entries, errors = F.load_watchlist(root / "config" / "forward_watchlist.yaml", base=root)
    assert errors == []
    keys = {m["key"]: (m, s) for m, s in entries}
    meta, spec = keys["c3_vol_breakout_atrch_1h_hivol_atr_3247d3023d_v1"]
    assert spec.logic_hash() == meta["logic_hash"] == "3247d3023d70d1f7"
    assert meta["added_by"] == "user (chat decision 2026-11-10)" and meta["source_campaign"] == "camp-000017"
    assert "observation only" in meta["reason"]
    # camp-000030 대표 (3자산 유니버스, 절반 사이징; 스펙은 Dev 원장 실행 run-000253 에서 복사)
    meta, spec = keys["c4_vol_breakout_atrch_1h_u3_half_4fc37395fd_v1"]
    assert spec.logic_hash() == meta["logic_hash"] == "4fc37395fd5a8a50"
    assert meta["source_campaign"] == "camp-000030" and meta["source_run"] == "run-000253"
    assert meta["added_by"] == "user (chat decision 2026-11-10)" and "observation only" in meta["reason"]
    assert spec.universe == ("BTC-KRW", "XRP-KRW", "ETH-KRW") and spec.sizing.entry_fraction == 0.5


def test_mcp_has_no_watchlist_tool():
    from btc_quant.mcp import tools
    assert not any("watchlist" in n for n in tools.TOOL_NAMES)
    src = Path(tools.__file__).read_text(encoding="utf-8")
    assert "forward_watchlist" not in src


# ------------------------------------------------------------------ 평가 · 워밍업
def test_watchlist_run_rest_warmup_forward_only(env):
    _write(env, [_entry()])
    raw_dir = Path(env.root) / "data" / "raw"
    before = {p: p.stat().st_mtime_ns for p in raw_dir.rglob("*.parquet")}
    fake = RestFake()
    out = F.run_forward(env=env, fetcher=fake, now=NOW)
    r = _wl(out)
    spec = _spec()
    lb, wmin = F.rest_warmup_window(spec)
    eval_start = FWD_START + pd.Timedelta(hours=1)
    assert r["status"] == "ok" and r["state"] == "watchlist" and r["observation_only"] is True
    assert r["watchlist"]["reason"].endswith("observation only")
    assert r["period"]["eval_start"] == eval_start.isoformat() and r["period"]["warmup_days"] == 0
    w = r["period"]["warmup"]
    assert w["mode"] == "rest_in_memory" and w["lookback_bars"] == lb and "lockbox" in w["note"]
    assert w["warmup_rows"] == {"coinone": wmin}
    # 워밍업 요청: 끝 = Forward 시작(미포함), 길이 = 페이퍼와 같은 창
    warm = fake.warm_calls()
    assert warm == [("coinone", FWD_START - pd.Timedelta(minutes=wmin), FWD_START)]
    # 레지스트리 전략(fwd24/fwd48)은 기존 규칙 그대로(14일 Forward 워밍업, REST 워밍업 없음)
    reg = next(x for x in out["results"] if x["strategy_key"] == "fwd24_v1")
    assert reg["period"]["eval_start"].startswith("2024-04-15") and "warmup" not in reg["period"]
    # 성과는 Forward 만: 직접 계산과 같고, 거래·일간 수익 모두 eval_start 이후
    end = pd.Timestamp(out["window_end"])
    fwd = env.data.read_range(FWD_START, end, "coinone", "BTC-KRW", "1m")
    m1 = prepare_m1(F._join_warmup(fake.data[fake.data.index < FWD_START].tail(wmin), fwd, FWD_START), "coinone")
    res = fast.run_fast(spec, m1, get_cost("coinone_conservative"), eval_start, end)
    assert len(res.trades) > 0 and r["n_trades"] == len(res.trades)
    assert (pd.to_datetime(res.trades["entry_time"]) >= eval_start).all()
    assert res.daily_returns.index.min() >= FWD_START and r["n_days"] == len(res.daily_returns) == 30
    assert r["net_return"] == pytest.approx(float((1 + res.daily_returns).prod() - 1), rel=1e-9, abs=1e-12)
    # 비교 지표: 노출·B&H·노출 맞춤 B&H
    for k in ("exposure_time", "exposure_days", "bh_total_return", "bh_matched_total_return",
              "excess_return_vs_matched_bh"):
        assert k in r and np.isfinite(r[k]), k
    # 같은 데이터를 워밍업 구간부터 평가하면 Forward 이전 거래가 생긴다 → 위 검사가 공허하지 않다
    pre = fast.run_fast(spec, m1, get_cost("coinone_conservative"), m1.index[0], end)
    assert (pd.to_datetime(pre.trades["entry_time"]) < FWD_START).any()
    # 워밍업 캔들은 저장되지 않는다: Forward 이전 파티션 파일은 그대로, 저장소에 Forward 이전 새 행 없음
    after = {p: p.stat().st_mtime_ns for p in raw_dir.rglob("*.parquet")}
    changed = [p for p in after if before.get(p) != after[p]]
    assert changed and all("month=03" not in str(p) and "month=02" not in str(p) for p in changed)
    rec = env.ledger.forward_results(KEY)
    assert len(rec) == 1 and rec[0]["state"] == "watchlist"
    # 리포트: 관찰 전용 섹션
    text = Path(out["report"]).read_text()
    assert "## Watchlist — OBSERVATION ONLY" in text and "not promoted" in text
    assert "exposure-matched B&H" in text and "Sample-size warning" in text and KEY in text
    assert "lockbox-period prices" in text


def test_assert_forward_only_rejects_pre_forward_rows():
    idx = pd.date_range("2024-03-31", periods=2, freq="D", tz="UTC")
    tr = pd.DataFrame({"entry_time": [pd.Timestamp("2024-03-31 10:00", tz="UTC")]})

    class R:
        trades = tr
        daily_returns = pd.Series([0.0, 0.0], index=idx)
    with pytest.raises(RuntimeError, match="trade before"):
        F._assert_forward_only(R, FWD_START + pd.Timedelta(hours=1))
    R.trades = tr.iloc[:0]
    with pytest.raises(RuntimeError, match="daily returns before"):
        F._assert_forward_only(R, FWD_START + pd.Timedelta(hours=1))


def test_watchlist_idempotent_and_next_day(env):
    _write(env, [_entry()])
    fake = RestFake()
    out1 = F.run_forward(env=env, fetcher=fake, now=NOW)
    text1 = Path(out1["report"]).read_text()
    n_calls = len(fake.calls)
    out2 = F.run_forward(env=env, fetcher=fake, now=NOW)
    assert out2["n_new"] == 0 and out2["warmup_fetches"] == 0 and len(fake.calls) == n_calls
    assert Path(out2["report"]).read_text() == text1
    assert len(env.ledger.forward_results(KEY)) == 1
    out3 = F.run_forward(env=env, fetcher=fake, now=NOW + pd.Timedelta(days=1))
    assert out3["n_new"] == 3 and _wl(out3)["n_days"] == 31
    assert _wl(out3)["period"]["eval_start"] == _wl(out1)["period"]["eval_start"]     # 같은 정의
    assert [h["as_of"] for h in env.ledger.forward_results(KEY)] == ["2024-04-30", "2024-05-01"]
    assert "2 daily watchlist evaluations" in Path(out3["report"]).read_text()


def test_watchlist_without_warmup_is_not_recorded(env):
    _write(env, [_entry()])
    fake = RestFake()
    F.fetch_forward(env, ("coinone", "upbit"), FWD_START, pd.Timestamp("2024-05-01", tz="UTC"), fake)
    # --no-fetch: REST 워밍업 없음 → warmup_unavailable, 원장 기록 없음 (레지스트리 전략은 평가)
    out = F.run_forward(env=env, fetch=False, now=NOW)
    r = _wl(out)
    assert r["status"] == "warmup_unavailable" and env.ledger.forward_results(KEY) == []
    assert next(x for x in out["results"] if x["strategy_key"] == "fwd24_v1")["status"] == "ok"
    assert "warmup_unavailable" in Path(out["report"]).read_text()
    # 워밍업 요청 실패도 같다
    out = F.run_forward(env=env, fetcher=RestFake(fail={"coinone"}), now=NOW)
    assert _wl(out)["status"] == "warmup_unavailable" and "boom" in _wl(out)["note"]
    assert env.ledger.forward_results(KEY) == []
    # 다음 실행에서 정상 기록
    out = F.run_forward(env=env, fetcher=fake, now=NOW)
    assert _wl(out)["status"] == "ok" and len(env.ledger.forward_results(KEY)) == 1


def test_watchlist_bad_hash_and_registry_duplicate(env):
    bad = _entry(logic_hash="0000000000000000")
    reg_dup = _entry(StrategySpec("fwd24", BlockRef("donchian_breakout", {"lookback": 24}), ExitSpec(0.6, 0.8, 60)))
    _write(env, [bad, reg_dup])
    out = F.run_forward(env=env, fetcher=RestFake(), now=NOW)
    assert all(r["state"] != "watchlist" for r in out["results"])
    errs = {e["key"]: e["error"] for e in out["watchlist_errors"]}
    assert "logic_hash" in errs[KEY] and "registry" in errs["fwd24_v1"]
    text = Path(out["report"]).read_text()
    assert "not evaluated" in text and env.ledger.forward_results(KEY) == []
    # 관찰 목록 끔
    out = F.run_forward(env=env, fetcher=RestFake(), now=NOW, watchlist=False)
    assert out["watchlist_errors"] == [] and "Watchlist" not in Path(out["report"]).read_text()


def test_cli_watchlist_listing(env, capsys):
    _write(env, [_entry()])
    assert F.main(["watchlist", "--root", str(env.root)]) == 0
    assert KEY in capsys.readouterr().out
    _write(env, [_entry(logic_hash="0000000000000000")])
    assert F.main(["watchlist", "--root", str(env.root)]) == 1
    assert "INVALID" in capsys.readouterr().out


# ------------------------------------------------------------------ 유니버스 · 절반 사이징 · MTM (camp-000030 형태)
UNI = ["BTC-KRW", "XRP-KRW", "ETH-KRW"]
SCALE = {"BTC-KRW": 1.0, "XRP-KRW": 1 / 20_000, "ETH-KRW": 1 / 25}


def test_watchlist_universe_half_sizing_mtm(env):
    """3자산 유니버스 + entry_fraction 0.5 + MTM 회계 캠페인의 관찰 목록 스펙: 자산별 REST 워밍업, 자산별 비용,
    절반 사이징, 원천 캠페인 매니페스트의 MTM 일간 수익 — 포트폴리오 = 슬리브 평균."""
    import json
    spec = StrategySpec("wl_u3_half", BlockRef("atr_channel_breakout", {"span": 24, "mult": 1.0}),
                        ExitSpec(None, None, 480, stop_loss_atr=2.0, take_profit_atr=3.0), timeframe="1h",
                        universe=tuple(UNI), sizing=SizingSpec(0.5))
    key = F.key_of(spec.id, spec.version)
    led = env.ledger
    h = led.register_hypothesis("u3 half", "r", "c")
    camp = led.create_campaign(h, {"data": {"exchange": "coinone"}, "universe": UNI, "sizing": {"entry_fraction": [0.5]},
                                   "costs": {"primary": "coinone_conservative"}}, 1, ["coinone_conservative"],
                               {"segment": "dev"}, family="vol_breakout")
    d = env.campaign_dir(camp)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"campaign_id": camp, "daily_returns": "mtm"}))
    assert F.campaign_manifest_accounting(env, camp) == "mtm"
    assert F.campaign_manifest_accounting(env, None) == "realized"
    _write(env, [_entry(spec, source_campaign=camp)])
    data = {sym: _market(seed=21 + i) * SCALE[sym] for i, sym in enumerate(UNI)}
    calls = []

    def fake(exchange, start, end, symbol="BTC-KRW"):
        calls.append((exchange, symbol, start, end))
        x = data[symbol]
        return x[(x.index >= start) & (x.index < end)].copy()
    out = F.run_forward(env=env, fetcher=fake, now=NOW)
    r = next(x for x in out["results"] if x["strategy_key"] == key)
    assert r["status"] == "ok" and r["state"] == "watchlist" and r["observation_only"] is True
    assert r["universe"] == UNI and set(r["per_asset"]) == set(UNI)
    assert r["accounting"] == "mtm" and r["sizing"] == {"entry_fraction": 0.5}
    _, wmin = F.rest_warmup_window(spec)
    # 자산마다 메모리 내 REST 워밍업 (끝 = Forward 시작), Forward 수집은 Forward 시작 이후만
    warm = {(ex, sym) for ex, sym, st, en in calls if st < FWD_START}
    assert warm == {("coinone", sym) for sym in UNI}
    assert all(en == FWD_START for ex, sym, st, en in calls if st < FWD_START)
    assert r["period"]["warmup"]["warmup_rows"] == {"coinone": wmin, "coinone:XRP-KRW": wmin,
                                                    "coinone:ETH-KRW": wmin}
    # 직접 계산: 자산별 비용 모델 · 절반 사이징 · MTM, 포트폴리오 = 슬리브 평균
    eval_start = pd.Timestamp(r["period"]["eval_start"])
    end = pd.Timestamp(out["window_end"])
    mtm, real, n_tr = [], [], 0
    for sym in UNI:
        fwd = env.data.read_range(FWD_START, end, "coinone", sym, "1m")
        x = data[sym]
        m1 = prepare_m1(F._join_warmup(x[x.index < FWD_START].tail(wmin), fwd, FWD_START), "coinone")
        c = get_sleeve_cost("coinone_conservative", sym)
        res = fast.run_fast(spec, m1, c, eval_start, end, accounting="mtm")
        assert len(res.trades) > 0 and (res.trades["weight"] == 0.5).all()
        assert (pd.to_datetime(res.trades["entry_time"]) >= eval_start).all()
        assert r["per_asset"][sym]["n_trades"] == len(res.trades)
        n_tr += len(res.trades)
        mtm.append(res.daily_returns.to_numpy())
        real.append(res.daily_realized.to_numpy())
    assert r["n_trades"] == n_tr
    assert r["net_return"] == pytest.approx(float(np.prod(1 + np.mean(mtm, axis=0)) - 1), abs=1e-12)
    # MTM 과 실현 기준이 실제로 다르다 → 회계 검사가 공허하지 않다
    assert not np.allclose(np.mean(mtm, axis=0), np.mean(real, axis=0))
    # 자산별 비용 모델이 BTC 와 다르다 (D17) → 자산별 비용 검사가 공허하지 않다
    assert any(get_sleeve_cost("coinone_conservative", s).side_cost != get_cost("coinone_conservative").side_cost
               or get_sleeve_cost("coinone_conservative", s).fee_rate != get_cost("coinone_conservative").fee_rate
               for s in UNI[1:])
    assert key in Path(out["report"]).read_text()
