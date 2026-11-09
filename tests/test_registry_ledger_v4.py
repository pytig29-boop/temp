"""원장 v4: 일괄 record_runs, val_evaluations, forward_results."""
import json

import pytest

from btc_quant.registry.ledger import BudgetExceededError, Ledger, LedgerError, ValBudgetExhaustedError

from test_registry_ledger import setup_campaign, spec


def test_record_runs_matches_record_run_format(tmp_path):
    led = Ledger(tmp_path)
    _, c = setup_campaign(led, budget=3)
    rid = led.record_run(c, spec(24), "upbit_base", ("2021-01-01", "2025-11-01"), "snap", "abc", {"a": 1.0})
    ids = led.record_runs(c, [(spec(48), "upbit_base", {"a": float("nan")}),
                              (spec(48).to_dict(), "coinone_conservative", {"a": 2}),
                              (spec(96), "upbit_base", None)],
                          ("2021-01-01", "2025-11-01"), "snap", "abc")
    assert ids == ["run-000002", "run-000003", "run-000004"]
    recs = led.records("runs")
    one, many = recs[0], recs[1]
    assert set(one) == set(many)
    for k in ("campaign_id", "hypothesis_id", "family", "spec_version", "cost_name", "period",
              "data_snapshot", "code_commit"):
        assert one[k] == many[k], k
    assert many["metrics"]["a"] is None and recs[3]["metrics"] == {}
    assert [r["seq"] for r in recs] == [1, 2, 3, 4]
    assert led.trials_count(c) == 3


def test_record_runs_all_or_nothing(tmp_path):
    led = Ledger(tmp_path)
    _, c = setup_campaign(led, budget=2)
    with pytest.raises(BudgetExceededError):
        led.record_runs(c, [(spec(lb), "upbit_base", {}) for lb in (24, 48, 96)], "dev", "s", "x")
    with pytest.raises(LedgerError):
        led.record_runs(c, [(spec(24), "upbit_base", {}), (spec(48), "stress", {})], "dev", "s", "x")
    with pytest.raises(LedgerError):
        led.record_runs("camp-999999", [(spec(24), "upbit_base", {})], "dev", "s", "x")
    assert led.records("runs") == []
    assert led.record_runs(c, [], "dev", "s", "x") == []
    # 같은 logic_hash 의 비용별 실행은 예산 1개
    led.record_runs(c, [(spec(24), "upbit_base", {}), (spec(24), "coinone_conservative", {}),
                        (spec(48), "upbit_base", {})], "dev", "s", "x")
    assert led.trials_count(c) == 2


def test_record_runs_repairs_missing_newline(tmp_path):
    led = Ledger(tmp_path)
    _, c = setup_campaign(led, budget=5)
    led.record_run(c, spec(24), "upbit_base", "dev", "s", "x", {})
    p = tmp_path / "runs.jsonl"
    p.write_text(p.read_text().rstrip("\n"))
    led.record_runs(c, [(spec(48), "upbit_base", {})], "dev", "s", "x")
    assert [json.loads(x)["seq"] for x in p.read_text().splitlines()] == [1, 2]


def test_val_evaluations_sequence_and_cap(tmp_path):
    led = Ledger(tmp_path)
    a = led.open_val_evaluation("h1", "camp-1", "v1", cap=2, spec_id="s1")
    assert (a["id"], a["k"]) == ("val-000001", 1)
    with pytest.raises(LedgerError):          # 같은 logic_hash 는 Val 을 한 번만
        led.open_val_evaluation("h1", "camp-2", "v1", cap=2)
    led.record_val_result(a["id"], True, 0.01, {"sharpe": 1.2, "sharpe_year_2025": 3.0, "monthly": [1, 2],
                                                "n_trades": 40}, {"threshold": 0.1})
    with pytest.raises(LedgerError):
        led.record_val_result(a["id"], False, 0.5)
    b = led.open_val_evaluation("h2", "camp-2", "v1", cap=2)
    assert b["k"] == 2
    with pytest.raises(ValBudgetExhaustedError):
        led.open_val_evaluation("h3", "camp-3", "v1", cap=2)
    assert len(led.val_history("v1")) == 2     # 거부된 사용은 기록되지 않음
    # 다른 Val(롤링 후) 은 예산이 새로 시작
    assert led.open_val_evaluation("h1", "camp-3", "v2", cap=2)["k"] == 1
    h = led.val_history("v1")
    assert [x["logic_hash"] for x in h] == ["h1", "h2"]
    assert h[0]["passed"] is True and h[1]["result"] is None
    summ = h[0]["result"]["summary"]
    assert summ == {"sharpe": 1.2, "n_trades": 40}          # 연·월 분해는 버린다
    assert led.val_prior("v1", "h2")["k"] == 2 and led.val_prior("v1", "zz") is None
    assert led.val_prior_p_values("v1") == [0.01, 1.0]      # 결과 없는 사용은 p=1
    assert led.val_prior_p_values("v1", before_k=2) == [0.01]
    st = led.val_status("v1", 2)
    assert st == {"val_id": "v1", "cap": 2, "used": 2, "remaining": 0, "passed": 1, "pending": 1}
    df = led.query("SELECT count(*) n FROM val_evaluations WHERE event = 'open'").df()
    assert int(df["n"][0]) == 3


def test_forward_results_idempotent(tmp_path):
    led = Ledger(tmp_path)
    i1, new1 = led.record_forward_result("s_v1", "h", "2026-11-20", {"status": "ok", "n_trades": 3})
    i2, new2 = led.record_forward_result("s_v1", "h", "2026-11-20", {"status": "ok", "n_trades": 99})
    assert new1 and not new2 and i1 == i2 == "fwd-000001"
    led.record_forward_result("s_v1", "h", "2026-11-21", {"status": "ok"})
    led.record_forward_result("t_v1", "g", "2026-11-21", {"status": "warming_up"})
    assert len(led.forward_results()) == 3
    assert [r["as_of"] for r in led.forward_results("s_v1")] == ["2026-11-20", "2026-11-21"]
    assert len(led.forward_results(as_of="2026-11-21")) == 2
    assert led.forward_results("s_v1", "2026-11-20")[0]["n_trades"] == 3
