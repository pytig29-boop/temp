"""원장 확장 (자율 체제 D30·D31): 기준 변경 기록·벌점 L·프로그램 기준 Val 사용 수."""
import pytest

from btc_quant.registry.ledger import Ledger, LedgerError


def test_criteria_revisions_and_loosenings(tmp_path):
    led = Ledger(tmp_path)
    with pytest.raises(ValueError):
        led.record_criteria_revision("relaxed", [], {}, {}, cert_id=None, ruleset_before=None, ruleset_after=None,
                                     reason="x")
    with pytest.raises(LedgerError):        # 느슨화는 인증 id 필수
        led.record_criteria_revision("looser", ["G3"], {}, {}, cert_id=None, ruleset_before="a", ruleset_after="b",
                                     reason="x")
    led.record_criteria_revision("initial", ["*"], {}, {}, cert_id="c0", ruleset_before=None, ruleset_after="a",
                                 reason="rs-001")
    led.record_criteria_revision("stricter", ["robustness.alpha_min_tstat"], {"v": 1.5}, {"v": 2.0}, cert_id="c1",
                                 ruleset_before="a", ruleset_after="b", reason="redteam")
    i = led.record_criteria_revision("looser", ["G3.neighbor_median_ratio"], {"v": 0.25}, {"v": 0.2}, cert_id="c2",
                                     ruleset_before="b", ruleset_after="c", reason="synthetic", scope="dev")
    assert i.startswith("crv-") and led.loosenings() == 1
    assert led.loosenings(scopes=("val",)) == 0 and led.loosenings(scopes=("dev",)) == 1
    assert led.loosenings(since_ts="2999") == 0
    assert [r["kind"] for r in led.criteria_revisions()] == ["initial", "stricter", "looser"]


def test_val_uses_since_and_extra(tmp_path):
    led = Ledger(tmp_path)
    t0 = "2000-01-01"
    led.open_val_evaluation("a", "camp-1", "v1", 20, extra={"ruleset": "rs-001"})
    led.open_val_evaluation("b", "camp-1", "v1", 20)
    assert led.val_uses_since(t0) == 2 and led.val_uses_since("2999") == 0
    assert led.val_history("v1")[0]["ruleset"] == "rs-001"
