"""바닥 규칙 강제 지점 (PLAN D29): 느슨한 설정·비용·분할·템플릿 거부, 더 엄격한 것은 허용."""
import copy
import sys
from pathlib import Path

import pytest
import yaml

from btc_quant.evaluation import gates as G
from btc_quant.params import floor as F

sys.path.insert(0, str(Path(__file__).parent))


def test_gate_floor():
    cfg = copy.deepcopy(G.load_gate_config())
    F.check_gate_floor(cfg)
    for key, val in (("max_mdd", 0.25), ("max_mc_mdd95", 0.3), ("min_position_scale", 0.2)):
        c = copy.deepcopy(cfg)
        c["G6"][key] = val
        with pytest.raises(F.FloorViolation):
            F.check_gate_floor(c)
    c = copy.deepcopy(cfg)
    c["G6"].update(max_mdd=0.15, min_position_scale=0.3)          # 더 엄격 = 허용
    F.check_gate_floor(c)
    c["final"] = {"worst_month_min": -0.10}
    with pytest.raises(F.FloorViolation):
        F.check_gate_floor(c)
    c["final"] = {"worst_month_min": -0.05, "oos_alpha": 0.2}
    with pytest.raises(F.FloorViolation):
        F.check_gate_floor(c)


def test_cost_floor():
    from btc_quant.params.costs import load_costs
    doc = copy.deepcopy(load_costs())
    F.check_cost_floor(doc)
    ent = doc["models"]["coinone_conservative"]
    rec = next(v for v in ent["versions"] if int(v["version"]) == int(ent["current"]))
    rec["slip_bps"] = float(rec["slip_bps"]) - 0.5
    with pytest.raises(F.FloorViolation):
        F.check_cost_floor(doc)
    rec["slip_bps"] = float(rec["slip_bps"]) + 2.0                    # 더 비쌈 = 허용
    F.check_cost_floor(doc)


def test_template_floor_and_splits(tmp_path):
    from test_research_synth import template_dict

    from btc_quant.research.campaign import CampaignTemplate
    F.check_template_floor(CampaignTemplate.from_dict(template_dict(name="ok")))
    d = template_dict(name="cheap")
    d["costs"] = {**d.get("costs", {}), "primary": "coinone_base", "grade": "upbit_conservative",
                  "stress": "coinone_stress", "grade_stress": "upbit_stress"}
    with pytest.raises(F.FloorViolation):
        F.check_template_floor(CampaignTemplate.from_dict(d))
    # 실제 분할 파일 해시 확인 (임시 연구 저장소의 자체 분할은 대상 아님)
    F.check_splits_floor()
    F.check_splits_floor(tmp_path)


def test_floor_verify_clean():
    r = F.floor_verify()
    assert r["violations"] is None and len(r["floor_sha256"]) == 64
