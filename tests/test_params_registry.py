"""params.registry / params.costs — 목록 로드·검증·해시·drift·전향 적용 (네트워크 없음)."""
import shutil
from pathlib import Path

import pytest
import yaml

from btc_quant.backtest import types as T
from btc_quant.params import costs as C
from btc_quant.params import registry as R

ROOT = Path(__file__).resolve().parents[1]


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    for f in ("gates.yaml", "splits.yaml", "exchanges.yaml", "params.yaml", "costs.yaml"):
        shutil.copy(ROOT / "config" / f, tmp_path / "config" / f)
    return tmp_path


def test_repo_registry_loads_and_validates():
    reg = R.load_registry(ROOT)
    assert R.validate(reg) == []
    by = reg.by_class()
    assert len(by[1]) >= 15 and len(by[2]) >= 40 and len(by[3]) >= 8
    for p in reg.params:
        assert p.owner == R.OWNER_BY_CLASS[p.cls]
        assert p.rationale
    # 사용자가 지정한 대표 항목들의 등급
    assert reg.get("costs.coinone_stress").cls == 1
    assert reg.get("quality.flag_wick_outliers.k").cls == 1
    assert reg.get("collector.stale_seconds").cls == 1
    assert reg.get("gates.G5.min_dsr").cls == 2
    assert reg.get("gates.G6.val_budget.alpha").cls == 2
    assert reg.get("gates.G1.min_trades").cls == 2
    assert reg.get("robustness.latency_min_retention").cls == 2
    assert reg.get("splits.lockbox_cap").cls == 2
    assert reg.get("gates.risk.drawdown_floor").cls == 3
    assert reg.get("gates.G6.max_mdd").cls == 3


def test_params_version_is_stable_short_hash():
    a, b = R.params_version(ROOT), R.params_version(ROOT)
    assert a == b and len(a) == 12
    snap = R.params_snapshot(ROOT)
    assert snap["params_version"] == a
    assert set(snap["cost_versions"]) == set(C.SEED_MODELS)


def test_hash_changes_with_values_and_drift_detected(tmp_path):
    root = make_root(tmp_path)
    h0 = R.params_version(root)
    g = root / "config" / "gates.yaml"
    g.write_text(R.set_yaml_scalar(g.read_text(), "G5.min_dsr", 0.9))
    reg = R.load_registry(root, validate_=False)
    errs = R.validate(reg)
    assert any("gates.G5.min_dsr" in e and "drift" in e for e in errs)
    with pytest.raises(R.ParamRegistryError):
        R.load_registry(root)
    assert R.params_version(reg=reg) != h0


def test_uncatalogued_gate_leaf_detected(tmp_path):
    root = make_root(tmp_path)
    g = root / "config" / "gates.yaml"
    g.write_text(g.read_text() + "G9:\n  new_threshold: 3\n")
    errs = R.validate(R.load_registry(root, validate_=False))
    assert any("G9.new_threshold" in e for e in errs)


def test_owner_class_mismatch_rejected(tmp_path):
    root = make_root(tmp_path)
    p = root / "config" / "params.yaml"
    doc = yaml.safe_load(p.read_text())
    doc["params"][0]["owner"] = "human" if doc["params"][0]["class"] == 1 else "auto"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True))
    assert any("owner" in e for e in R.validate(R.load_registry(root, validate_=False)))


def test_cost_seed_identical_to_presets():
    UPBIT_STRESS = T.CostModel("upbit_stress", 0.0005, 5.0, 8.0)   # 예전 campaign.UPBIT_STRESS 프리셋
    presets = [T.COINONE_BASE, T.COINONE_CONSERVATIVE, T.COINONE_STRESS, T.UPBIT_BASE, T.UPBIT_CONSERVATIVE,
               UPBIT_STRESS]
    seed = C.all_cost_models(doc=C.seed_document())
    repo = C.all_cost_models(ROOT)
    for m in presets:
        assert seed[m.name] == m
    # config/costs.yaml 는 아직 보정이 승인·적용되지 않았으면 프리셋과 같다 (적용된 버전은 history 로 재현 가능)
    for m in presets:
        assert C.cost_model(m.name, version=1, root=ROOT) == m
    assert set(repo) == {m.name for m in presets}


def test_cost_registry_versions_and_ordering(tmp_path):
    root = make_root(tmp_path)
    new = T.CostModel("coinone_stress", 0.0, 6.0, 9.0)
    prev = C.cost_versions(root)["coinone_stress"]          # 실제 config 복사본 (승인된 버전이 있을 수 있음)
    v = C.add_version("coinone_stress", new, direction_="stricter", applied_by="auto", source="test", root=root)
    assert v == prev + 1
    assert C.cost_model("coinone_stress", root=root) == new
    assert C.cost_model("coinone_stress", version=1, root=root) == T.COINONE_STRESS
    assert C.cost_versions(root)["coinone_stress"] == v
    assert [h["version"] for h in C.cost_history("coinone_stress", root)] == list(range(1, v + 1))
    # base > conservative 이면 거부, 파일은 그대로
    before = (root / "config" / "costs.yaml").read_text()
    with pytest.raises(C.CostRegistryError):
        C.add_version("coinone_base", T.CostModel("coinone_base", 0.0, 10.0, 10.0), direction_="stricter",
                      applied_by="auto", source="test", root=root)
    assert (root / "config" / "costs.yaml").read_text() == before
    assert C.direction(T.COINONE_STRESS, new) == "stricter"
    assert C.direction(new, T.COINONE_STRESS) == "looser"
    assert C.direction(new, new) == "equal"


def test_set_yaml_scalar_preserves_comments():
    text = (ROOT / "config" / "gates.yaml").read_text()
    out = R.set_yaml_scalar(text, "G6.val_budget.alpha", 0.05)
    assert yaml.safe_load(out)["G6"]["val_budget"]["alpha"] == 0.05
    assert yaml.safe_load(out)["G7"]["alpha"] == 0.10
    assert out.count("#") == text.count("#")
    with pytest.raises(KeyError):
        R.set_yaml_scalar(text, "G6.nope", 1)


def test_gate_config_for_campaign_is_prospective(tmp_path):
    root = make_root(tmp_path)
    R.record_change("gates.G5.min_dsr", 0.9, by="human:test", proposal_id="pp-x", reason="t",
                    applies_from_campaign=5, root=root)
    assert R.load_registry(root).get("gates.G5.min_dsr").version == 2      # validates (no drift)
    assert R.gate_config_for_campaign("camp-000004", root)["G5"]["min_dsr"] == 0.95
    assert R.gate_config_for_campaign("camp-000005", root)["G5"]["min_dsr"] == 0.9
    assert R.gate_config_for_campaign(None, root)["G5"]["min_dsr"] == 0.9
    R.record_change("gates.G5.min_dsr", 0.92, by="human:test", proposal_id="pp-y", reason="t",
                    applies_from_campaign=9, root=root)
    assert R.gate_config_for_campaign(3, root)["G5"]["min_dsr"] == 0.95
    assert R.gate_config_for_campaign(7, root)["G5"]["min_dsr"] == 0.9
    assert R.gate_config_for_campaign(9, root)["G5"]["min_dsr"] == 0.92
