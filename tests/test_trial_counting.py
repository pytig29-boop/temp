"""시도 계수 (§4-D): 벌점 L 이 오르면 DSR 시도 수가 늘고 결선 문턱이 엄격해진다. 엄격화는 L 불변."""
from btc_quant.registry.ledger import Ledger
from btc_quant.research import certify as K
from btc_quant.research import gate_review_study as GR


def test_offline_trials_multiplier_and_final_threshold():
    assert GR.n_trials_for({"dsr_mult": 4.0}, 2, 12, 60, 7) == 4 * GR.n_trials_for({}, 2, 12, 60, 7)
    c = {"val_sharpe": 1, "lb_sharpe": 1, "val_p": 0.02, "lb_p": 0.03, "oos_p": 0.01, "dev_max_dd": 0.1, "val_mdd": 0.1,
         "lb_mdd": 0.1, "full_mc_mdd95": 0.1, "full_sharpe": 2, "full_bh_sharpe": 1, "full_calmar": 2,
         "full_bh_calmar": 1}
    c.update(val_p=0.2, lb_p=0.2)
    p0 = K.final_flags(c, 2.5, dict(K.FINAL_DEFAULT), 0)
    p3 = K.final_flags(c, 2.5, dict(K.FINAL_DEFAULT), 3)
    assert p0["p3"] == p3["p3"] and p0["F1b"] and not p3["F1b"]       # 같은 증거, 문턱만 0.01 → 0.0025


def test_stricter_does_not_count(tmp_path):
    led = Ledger(tmp_path)
    led.record_criteria_revision("stricter", ["a"], {}, {}, cert_id="c", ruleset_before="x", ruleset_after="y",
                                 reason="r")
    led.record_criteria_revision("neutral", ["b"], {}, {}, cert_id="c", ruleset_before="y", ruleset_after="z",
                                 reason="r")
    assert led.loosenings() == 0
