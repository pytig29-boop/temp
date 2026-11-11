"""페이퍼 판정 (D28): 7일·연장·P1~P4·예상 범위·감시 강등."""
import numpy as np
import pandas as pd

from btc_quant.research import paper_verdict as PV


def _rep(n_bt=20, n_paper=20, matched=20, identical=20, net_p=0.01, net_b=0.01, po=(), bo=(), blocked=()):
    return {"trades": {"n_backtest": n_bt, "n_paper": n_paper, "n_matched_entry": matched, "n_identical": identical,
                       "paper_only": list(po), "backtest_only": list(bo), "net_paper": net_p, "net_backtest": net_b},
            "blocked_by_risk": list(blocked)}


BAND = {"q0.01": -0.08, "q0.025": -0.06, "q0.975": 0.07, "q0.995": 0.09}
COST = {"BTC-KRW": (2.0, 4.0)}


def _j(reports, **kw):
    base = dict(n_days=7, uptime=0.95, human_halts=0, band=BAND, paper_cum=0.01, cost_limits=COST,
                fills_by_asset={"BTC-KRW": [1.0] * 30})
    base.update(kw)
    return PV.judge(reports, **base)


def test_pass_and_each_failure():
    assert _j([_rep()])["verdict"] == "PASS"
    assert _j([_rep()], n_days=3)["verdict"] == "PENDING"
    assert _j([_rep()], fills_by_asset={"BTC-KRW": [3.0] * 30})["verdict"] == "FAIL"            # P1
    assert _j([_rep(matched=15, identical=15)])["verdict"] == "FAIL"                            # P2 진입
    assert _j([_rep(identical=15)])["verdict"] == "FAIL"                                        # P2 청산
    assert _j([_rep(net_p=0.03, net_b=0.0)])["verdict"] == "FAIL"                               # P3 재현 차이
    assert _j([_rep()], paper_cum=-0.07)["verdict"] == "FAIL"                                   # P3 범위 아래
    assert _j([_rep()], human_halts=1)["verdict"] == "FAIL"                                     # P4
    r = _j([_rep()], paper_cum=0.12)
    assert r["verdict"] == "PASS" and r["P3"]["above_band_check"]                               # 위쪽은 점검만


def test_extension_rules():
    quiet = [_rep(n_bt=1, n_paper=1, matched=1, identical=1)]
    assert _j(quiet, fills_by_asset={"BTC-KRW": [1.0] * 2})["verdict"] == "EXTEND"
    r = _j(quiet, fills_by_asset={"BTC-KRW": [1.0] * 2}, n_days=14, extended=True)
    assert r["verdict"] in ("PASS", "FAIL")
    assert _j([_rep()], uptime=0.5)["verdict"] == "EXTEND"
    r = _j([_rep()], uptime=0.5, extended=True, n_days=14)
    assert r["verdict"] == "FAIL" and r.get("undecidable_environment")


def test_band_and_monitor():
    rng = np.random.default_rng(0)
    d = pd.Series(rng.normal(0.001, 0.01, 800))
    b = PV.expectation_band(d, 7, n_boot=2000)
    assert b["q0.01"] < b["q0.025"] < 0 < b["q0.975"] < b["q0.995"]
    assert PV.monitor(b["q0.01"] - 0.01, b, 0.0, 0)["demote"]
    assert PV.monitor(0.0, b, 0.06, 0)["demote"] and PV.monitor(0.0, b, 0.0, 1)["demote"]
    m = PV.monitor(b["q0.995"] + 0.01, b, 0.0, 0)
    assert not m["demote"] and m["check_bug"]
