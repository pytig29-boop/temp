"""최종 보고 부록 (§4-H): 원장의 캠페인·기준 변경이 하나도 빠지지 않는다."""
from btc_quant.registry.ledger import Ledger
from btc_quant.research import final_report as FR


def test_all_campaigns_and_revisions_present(tmp_path):
    led = Ledger(tmp_path / "research" / "ledger")
    h = None
    led.append("campaigns", {"family": "fam", "grid": {"n_specs": 4}, "hypothesis_id": h})
    led.append("campaigns", {"family": "fam2", "grid": {"n_specs": 2}, "hypothesis_id": h})
    led.record_criteria_revision("stricter", ["k"], {}, {}, cert_id="c", ruleset_before="a", ruleset_after="b",
                                 reason="r")
    d = FR.collect(tmp_path)
    text = FR.render(d)
    for c in led.records("campaigns"):
        assert c["id"] in text
    for r in led.criteria_revisions():
        assert r["id"] in text
    assert "바닥 규칙" in text
