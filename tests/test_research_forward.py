"""전진 인큐베이션 (research/forward.py) — 네트워크 없음(가짜 수집기), 임시 저장소."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btc_quant.registry.splits import overlaps_lockbox
from btc_quant.research import forward as F
from btc_quant.research.campaign import ResearchEnv
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

from test_research_pipeline import _spy_reads
from test_research_synth import make_root

FWD_START = pd.Timestamp("2024-04-01", tz="UTC")
NOW = pd.Timestamp("2024-05-01 05:00", tz="UTC")


def _market(seed: int = 3) -> pd.DataFrame:
    idx = pd.date_range("2024-04-01", "2024-06-01", freq="1min", tz="UTC", inclusive="left", name="ts")
    rng = np.random.default_rng(seed)
    c = 60_000_000 * np.exp(np.cumsum(rng.normal(0, 0.0006, len(idx))))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0002, len(idx))))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0002, len(idx))))
    v = rng.lognormal(0, 0.5, len(idx))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v, "quote_volume": v * c},
                        index=idx)


class FakeFetcher:
    def __init__(self, fail: set[str] = frozenset()):
        self.data = _market()
        self.calls: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
        self.fail = fail

    def __call__(self, exchange, start, end):
        self.calls.append((exchange, start, end))
        if exchange in self.fail:
            raise ConnectionError("boom")
        d = self.data
        return d[(d.index >= start) & (d.index < end)].copy()


def _spec(lookback: int, sl: float = 0.6) -> StrategySpec:
    return StrategySpec(f"fwd{lookback}", BlockRef("donchian_breakout", {"lookback": lookback}),
                        ExitSpec(sl, 0.8, 60))


@pytest.fixture()
def env(tmp_path):
    root = make_root(tmp_path, planted=False, seed=11)   # Dev~잠금 구간 데이터 (Forward 는 없음)
    env = ResearchEnv.at(root)
    led = env.ledger
    h = led.register_hypothesis("fwd test", "r", "c")
    camp = led.create_campaign(h, {"data": {"exchange": "coinone"},
                                   "costs": {"primary": "coinone_conservative"}}, 10,
                               ["coinone_conservative"], {"segment": "dev"}, family="donchian_breakout")
    specs = [_spec(24), _spec(48), _spec(96)]
    led.record_runs(camp, [(s, "coinone_conservative", {"mean_daily": 0.0005, "std_daily": 0.01,
                                                         "trades_per_day": 3.0, "sharpe": 1.0})
                           for s in specs], {"segment": "dev", "start": "2023-01-01", "end": "2024-01-01"},
                    "snap", "abc")
    reg = env.registry
    for s, target in zip(specs, ("validated", "challenger", "candidate")):
        reg.register(s, actor="test")
        reg.transition(s.id, 1, "candidate", "dev", actor="test", evidence={"campaign_id": camp})
        if target in ("validated", "challenger"):
            reg.transition(s.id, 1, "validated", "val", actor="test", evidence={})
        if target == "challenger":
            reg.transition(s.id, 1, "challenger", "slot", actor="test")
    return env


def test_forward_run_fetch_record_report(env):
    calls = _spy_reads(env)
    fake = FakeFetcher()
    out = F.run_forward(env=env, fetcher=fake, now=NOW)
    assert out["as_of"] == "2024-04-30" and out["window_end"].startswith("2024-05-01")
    # 수집: 두 거래소, 요청 시작은 Forward 시작 이후 (잠금 구간 미접근)
    assert sorted(x[0] for x in fake.calls) == ["coinone", "upbit"]
    assert all(s >= FWD_START and e <= NOW for _, s, e in fake.calls)
    assert out["fetch"]["coinone"]["fetched"] == 30 * 1440
    assert out["coverage"]["upbit"]["rows"] == 30 * 1440
    # 평가: validated + challenger 만 (candidate 제외)
    res = {r["strategy_key"]: r for r in out["results"]}
    assert set(res) == {"fwd24_v1", "fwd48_v1"}
    r = res["fwd24_v1"]
    assert r["status"] == "ok" and r["state"] == "validated" and res["fwd48_v1"]["state"] == "challenger"
    assert r["period"]["eval_start"].startswith("2024-04-15") and r["n_days"] == 16
    assert r["n_trades"] > 0 and r["expected"]["n_days"] == 16 and isinstance(r["within_band"], bool)
    assert r["expected"]["return_lo"] < r["expected"]["expected_return"] < r["expected"]["return_hi"]
    assert r["exchange"] == "coinone" and r["cost"] == "coinone_conservative"
    # 잠금 구간 무접근
    cfg = env.data.config
    assert calls and all(k is None and not overlaps_lockbox(s, e, cfg) for s, e, k in calls)
    assert env.ledger.records("lockbox") == []
    text = (env.reports_dir / "forward_2024-04-30.md").read_text()
    assert "fwd24_v1" in text and "fwd96_v1" not in text and "Data coverage" in text


def test_forward_idempotent_per_day(env):
    fake = FakeFetcher()
    F.run_forward(env=env, fetcher=fake, now=NOW)
    rep = env.reports_dir / "forward_2024-04-30.md"
    text1, n1 = rep.read_text(), len(env.ledger.records("forward_results"))
    n_calls = len(fake.calls)
    out2 = F.run_forward(env=env, fetcher=fake, now=NOW)
    assert out2["n_new"] == 0 and len(env.ledger.records("forward_results")) == n1 == 2
    assert len(fake.calls) == n_calls                   # 이미 저장된 구간은 다시 받지 않음
    assert out2["fetch"]["coinone"]["note"] == "up to date"
    assert rep.read_text() == text1
    # 다음 날: 증분 수집(그 날만) + 새 레코드
    out3 = F.run_forward(env=env, fetcher=fake, now=NOW + pd.Timedelta(days=1))
    assert out3["as_of"] == "2024-05-01" and out3["n_new"] == 2
    new_calls = fake.calls[n_calls:]
    assert all(s == pd.Timestamp("2024-05-01", tz="UTC") for _, s, _ in new_calls)
    assert out3["fetch"]["upbit"]["fetched"] == 1440
    hist = env.ledger.forward_results("fwd24_v1")
    assert [h["as_of"] for h in hist] == ["2024-04-30", "2024-05-01"]
    assert "2 daily evaluations" in (env.reports_dir / "forward_2024-05-01.md").read_text()


def test_forward_warmup_and_bad_dates(env):
    fake = FakeFetcher()
    out = F.run_forward(env=env, fetcher=fake, now=NOW, as_of="2024-04-05")
    assert {r["status"] for r in out["results"]} == {"warming_up"}
    assert out["results"][0]["days_until_eval"] == 9
    # 수집은 as_of 까지만
    assert all(e <= pd.Timestamp("2024-04-06", tz="UTC") for _, _, e in fake.calls)
    with pytest.raises(ValueError, match="completed"):
        F.run_forward(env=env, fetcher=fake, now=NOW, as_of="2024-05-01")
    with pytest.raises(ValueError, match="before the forward"):
        F.run_forward(env=env, fetcher=fake, now=NOW, as_of="2024-03-20")


def test_forward_fetch_failure_and_no_fetch(env):
    out = F.run_forward(env=env, fetcher=FakeFetcher(fail={"upbit"}), now=NOW)
    assert "error" in out["fetch"]["upbit"] and out["fetch"]["coinone"]["fetched"] > 0
    assert all(r["status"] == "ok" for r in out["results"])     # 코인원 전략은 계속 평가


def test_forward_no_data_and_cli(env, capsys):
    out = F.run_forward(env=env, fetch=False, now=NOW)
    assert out["fetch"] is None and {r["status"] for r in out["results"]} == {"no_data"}
    # CLI (--no-fetch: 네트워크 없음). now = 실제 현재 → 2024-04-20 은 완료된 날
    assert F.main(["run", "--root", str(env.root), "--as-of", "2024-04-20", "--no-fetch"]) == 0
    assert "forward 2024-04-20" in capsys.readouterr().out
    assert (env.reports_dir / "forward_2024-04-20.md").exists()
