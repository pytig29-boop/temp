import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from btc_quant.registry.ledger import BudgetExceededError, Ledger, LedgerError
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec


def spec(lookback=48, sid="brk", version=1):
    return StrategySpec(id=sid, version=version,
                        entry=BlockRef("donchian_breakout", {"lookback": lookback}),
                        exit=ExitSpec(0.7, 0.9, 90))


def setup_campaign(led, budget=2, costs=("coinone_conservative", "upbit_base")):
    h = led.register_hypothesis("breakouts work", "phase0", "sharpe>0")
    c = led.create_campaign(h, {"lookback": [24, 48]}, budget, list(costs), ("2021-01-01", "2025-11-01"))
    return h, c


def test_ids_monotonic_and_append_only(tmp_path):
    led = Ledger(tmp_path)
    h1 = led.register_hypothesis("a", "r", "c")
    h2 = led.register_hypothesis("b", "r", "c")
    assert (h1, h2) == ("hyp-000001", "hyp-000002")
    before = (tmp_path / "hypotheses.jsonl").read_text()
    led.register_hypothesis("c", "r", "c")
    after = (tmp_path / "hypotheses.jsonl").read_text()
    assert after.startswith(before)  # 기존 줄 불변
    assert [r["text"] for r in led.records("hypotheses")] == ["a", "b", "c"]


def test_campaign_requires_hypothesis_and_run_requires_campaign(tmp_path):
    led = Ledger(tmp_path)
    with pytest.raises(LedgerError):
        led.create_campaign("hyp-999999", {}, 10, ["x"], "dev")
    with pytest.raises(LedgerError):
        led.record_run("camp-000001", spec(), "x", "dev", "snap", "abc", {})


def test_budget_and_trials(tmp_path):
    led = Ledger(tmp_path)
    _, c = setup_campaign(led, budget=2)
    for lb in (24, 48):
        for cost in ("coinone_conservative", "upbit_base"):
            led.record_run(c, spec(lb), cost, "dev", "snap-1", "deadbeef", {"sharpe": float("nan"), "n": 3})
    # 같은 logic_hash 재실행은 허용, 새 조합은 예산 초과
    led.record_run(c, spec(24, version=2), "upbit_base", "dev", "snap-1", "deadbeef", {})
    with pytest.raises(BudgetExceededError):
        led.record_run(c, spec(96), "upbit_base", "dev", "snap-1", "deadbeef", {})
    with pytest.raises(LedgerError):  # 사전등록되지 않은 비용
        led.record_run(c, spec(24), "stress", "dev", "snap-1", "deadbeef", {})
    assert led.trials_count(c) == 2
    _, c2 = setup_campaign(led, budget=5)
    led.record_run(c2, spec(24), "upbit_base", "dev", "snap-1", "x", {})
    assert led.trials_count() == 3  # 새 캠페인 재시도는 누적
    assert led.trials_count(family="donchian_breakout") == 3
    assert led.trials_count(family="nope") == 0
    r = led.records("runs")[0]
    assert r["metrics"]["sharpe"] is None and r["data_snapshot"] == "snap-1"
    df = led.query("SELECT campaign_id, count(*) n FROM runs GROUP BY 1 ORDER BY 1").df()
    assert df["n"].tolist() == [5, 1]


def _worker(args):
    root, n = args
    led = Ledger(root)
    return [led.register_hypothesis(f"h{i}", "r", "c") for i in range(n)]


def test_concurrent_appends_unique(tmp_path):
    with ThreadPoolExecutor(8) as pool:  # 각 스레드가 별도 open() → flock 경합
        ids = sum(pool.map(_worker, [(str(tmp_path), 25)] * 4), [])
    assert len(set(ids)) == 100
    lines = (tmp_path / "hypotheses.jsonl").read_text().splitlines()
    assert len(lines) == 100
    seqs = [json.loads(x)["seq"] for x in lines]
    assert seqs == list(range(1, 101))


def test_lockbox_history_and_result(tmp_path):
    led = Ledger(tmp_path)
    o1 = led.record_lockbox_opening("c1", "lb1", {"approved_by": "me"})
    o2 = led.record_lockbox_opening("c2", "lb1", {"approved_by": "me"})
    led.record_lockbox_result(o1, {"p_value": 0.03})
    with pytest.raises(LedgerError):
        led.record_lockbox_result(o1, {"p_value": 0.5})
    h = led.lockbox_history()
    assert [(x["candidate_id"], x["k"]) for x in h] == [("c1", 1), ("c2", 2)]
    assert h[0]["result"] == {"p_value": 0.03} and h[1]["result"] is None
    assert o2 == "lbx-000002"
