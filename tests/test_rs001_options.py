"""자율 rs-001 옵션 (기본 = 기존 동작): G6 크기에 Dev 낙폭·최악 월, 캠페인당 Val 상한 — 실제·오프라인 같은 식."""
import copy
import sys
from pathlib import Path

import pytest

from btc_quant.evaluation import gates as G
from btc_quant.research import gate_review_study as GR

sys.path.insert(0, str(Path(__file__).parent))


def _cfg(**g6):
    c = copy.deepcopy(G.load_gate_config())
    c["G6"].update(g6)
    return c


def test_defaults_off():
    c = G.load_gate_config()["G6"]
    assert c["size_includes_dev"] is False and c["worst_month_min"] is None and c["max_val_per_campaign"] is None


def test_size_with_dev_and_worst_month():
    val = {"sharpe": 1.0, "max_dd": 0.10, "worst_month": -0.04}
    st = {"sharpe": 0.5, "max_dd": 0.1}
    dev = {"max_dd": 0.40, "worst_month": -0.16}
    assert G.check_g6(val, st, 0.05, _cfg(), dev_metrics=dev)[1]["position_scale"] == 1.0
    d = G.check_g6(val, st, 0.05, _cfg(size_includes_dev=True), dev_metrics=dev)[1]
    assert d["position_scale"] == pytest.approx(0.5)
    d = G.check_g6(val, st, 0.05, _cfg(size_includes_dev=True, worst_month_min=-0.08), dev_metrics=dev)[1]
    assert d["position_scale"] == pytest.approx(0.5)            # min(0.5, 0.08/0.16)
    d = G.check_g6(val, st, 0.05, _cfg(worst_month_min=-0.02), dev_metrics=dev)[1]
    assert d["position_scale"] == pytest.approx(0.125) and not d["ok_position_scale"]
    # 오프라인 같은 식
    pkg = GR.current_package(_cfg(size_includes_dev=True, worst_month_min=-0.08))
    c = {"val_mdd": 0.1, "val_mc_mdd95": 0.05, "dev_max_dd": 0.4, "dev_worst_month": -0.16, "val_worst_month": -0.04}
    assert GR.size_scale(c, pkg) == pytest.approx(0.5)


def test_val_per_campaign_cap_offline():
    from test_gate_review_study import _cand, _rec
    pkg = GR.with_changes(GR.current_package(), val_per_campaign=1)
    ev = GR.evaluate_market(_rec([[_cand(1, val_p=0.001), _cand(2, val_p=0.001)]]), pkg)
    labs = [c["label"] for c in ev["cands"]]
    assert labs[1] == "Vcap" and ev["val_used"] == 1
