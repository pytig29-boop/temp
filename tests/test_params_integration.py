"""파라미터 거버넌스 연결 (docs/PARAMS.md §4): 원장 params_version 필드, 비용 모델 레지스트리 경유,
캠페인 시점 관문 설정, 견고성 기준의 gates.yaml 이관, ScreenTemplate 의 data.aux 직접 지원."""
from __future__ import annotations

import copy
import json

import pytest
import yaml
from research_v4_synth import aux_screen_dict

from btc_quant.backtest.types import CostModel
from btc_quant.evaluation import gates as G
from btc_quant.params import all_cost_models, cost_model, gate_config_for_campaign
from btc_quant.params.registry import load_registry, validate
from btc_quant.registry.ledger import BudgetExceededError, Ledger
from btc_quant.research import campaign as C
from btc_quant.research import robustness as R
from btc_quant.research.campaign import CampaignError, ResearchEnv, load_screen_template
from btc_quant.research.signal_study import ScreenTemplate
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec


def _spec(lb=48):
    return StrategySpec(f"brk{lb}", BlockRef("donchian_breakout", {"lookback": lb}), ExitSpec(0.7, 0.9, 90))


# ------------------------------------------------------------------ 원장
def test_ledger_param_fields_optional_and_old_records_readable(tmp_path):
    led = Ledger(tmp_path)
    h = led.register_hypothesis("h", "r", "c")
    cid = led.create_campaign(h, {"a": [1, 2]}, 2, ["coinone_conservative"], ("2023-01-01", "2024-01-01"))
    old = led.record_run(cid, _spec(24), "coinone_conservative", ("2023-01-01", "2024-01-01"), "s", "c", {"x": 1})
    cv = {"coinone_conservative": 2, "upbit_stress": 1}
    new = led.record_runs(cid, [(_spec(24), "coinone_conservative", {"x": 2}),
                                (_spec(48), "coinone_conservative", {"x": 3})],
                          ("2023-01-01", "2024-01-01"), "s", "c", params_version="abc123def456", cost_versions=cv)
    recs = {r["id"]: r for r in led.records("runs")}
    assert "params_version" not in recs[old] and "cost_versions" not in recs[old]   # 이전 형식 그대로
    for rid in new:
        assert recs[rid]["params_version"] == "abc123def456" and recs[rid]["cost_versions"] == cv
    one = led.record_run(cid, _spec(48), "coinone_conservative", ("2023-01-01", "2024-01-01"), "s", "c", {},
                         params_version="p" * 12, cost_versions=cv)
    assert led.get("runs", one)["params_version"] == "p" * 12
    # 섞인 레코드에서도 시도 수·예산 판정은 그대로 (고유 logic_hash 2개)
    assert led.trials_count(campaign_id=cid) == 2
    assert len(led.query("SELECT params_version FROM runs").fetchall()) == 4
    with pytest.raises(BudgetExceededError):
        led.record_runs(cid, [(_spec(96), "coinone_conservative", {})], ("2023-01-01", "2024-01-01"), "s", "c",
                        params_version="q" * 12)
    # 파일은 줄 단위 JSON (추가 전용)
    lines = (tmp_path / "runs.jsonl").read_text().splitlines()
    assert len(lines) == 4 and all(json.loads(x)["campaign_id"] == cid for x in lines)


# ------------------------------------------------------------------ 비용 모델
def test_get_cost_reads_param_registry(tmp_path):
    for name, m in all_cost_models().items():
        assert C.get_cost(name) == m == cost_model(name)
        assert m.name == name
    with pytest.raises(CampaignError, match="unknown cost model"):
        C.get_cost("nope")
    assert C.COST_MODELS == all_cost_models()                      # 호환 속성 (지연 계산)
    assert not hasattr(C, "UPBIT_STRESS")
    # 다른 루트의 새 버전은 mtime 캐시를 넘어 바로 보인다
    from btc_quant.params import costs as PC
    (tmp_path / "config").mkdir()
    PC.ensure_costs_file(tmp_path)
    assert C.get_cost("coinone_stress", tmp_path) == PC.cost_model("coinone_stress", root=tmp_path)
    new = CostModel("coinone_stress", 0.0, 9.0, 11.0)
    PC.add_version("coinone_stress", new, direction_="stricter", applied_by="auto", source="t", root=tmp_path)
    assert C.get_cost("coinone_stress", tmp_path) == new


def test_run_params_snapshot_and_config_root(tmp_path):
    env = ResearchEnv.at(tmp_path)                 # config/ 없는 임시 연구 저장소 → 코드 저장소 설정
    assert C.config_root(env) == env.code_root
    snap = C.run_params(env)
    assert len(snap["params_version"]) == 12 and snap["cost_versions"]["coinone_conservative"] >= 1
    assert C.run_param_fields(snap) == {"params_version": snap["params_version"],
                                        "cost_versions": snap["cost_versions"]}
    # 레지스트리가 깨져 있어도 연구를 멈추지 않고 오류를 기록
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "gates.yaml").write_text((env.code_root / "config" / "gates.yaml").read_text())
    assert C.config_root(env) == tmp_path
    bad = C.run_params(env)                        # params.yaml 없음
    assert bad["params_version"] is None and "params_error" in bad and bad["cost_versions"]


# ------------------------------------------------------------------ 관문 설정 (전향 적용)
def test_pipeline_gate_config_for_campaign(monkeypatch):
    from btc_quant.research import pipeline as P

    seen = []

    def fake(cid, root=None, base=None):
        seen.append((cid, root))
        return gate_config_for_campaign(cid, root=root)

    monkeypatch.setattr(P, "gate_config_for_campaign", fake)

    class _Data:
        def segment_bounds(self, seg):
            return None, None

    class _Env:
        root = C.repo_root()
        code_root = C.repo_root()
        data = _Data()

    class _CR:
        campaign_id = "camp-000003"
        template = type("T", (), {"costs": {"primary": "coinone_conservative", "grade": "upbit_conservative"}})()

        def primary(self):
            return None
        results = __import__("pandas").DataFrame({"cost": [], "logic_hash": []})

    p = P.Pipeline(_CR(), _Env(), lessons=False)
    assert seen == [("camp-000003", C.repo_root())]
    assert p.cfg == gate_config_for_campaign("camp-000003")
    assert p.params["params_version"] and p.params["cost_versions"]
    # 넘겨받은 설정(lru 캐시된 공유 dict)은 복사 — 파이프라인이 바꿔도 캐시가 오염되지 않는다
    shared = G.load_gate_config()
    before = copy.deepcopy(shared)
    p2 = P.Pipeline(_CR(), _Env(), gate_cfg=shared, lessons=False)
    assert p2.cfg == shared and p2.cfg is not shared and p2.cfg["G5"] is not shared["G5"]
    p2.cfg["G5"]["min_dsr"] = 0.0
    assert G.load_gate_config() == before


def test_gate_config_for_campaign_prospective(tmp_path):
    """승인된 class ② 변경(applies_from_campaign=5)은 캠페인 5 부터, 그 전 캠페인은 이전 값."""
    root = C.repo_root()
    (tmp_path / "config").mkdir()
    for f in ("gates.yaml", "params.yaml"):
        (tmp_path / "config" / f).write_text((root / "config" / f).read_text())
    doc = yaml.safe_load((tmp_path / "config" / "params.yaml").read_text())
    ent = next(e for e in doc["params"] if e["key"] == "gates.G5.min_dsr")
    ent["history"] = [{"version": 2, "previous": 0.95, "value": 0.9, "applies_from_campaign": 5}]
    (tmp_path / "config" / "params.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))
    g = (tmp_path / "config" / "gates.yaml").read_text().replace("min_dsr: 0.95", "min_dsr: 0.9 ")
    (tmp_path / "config" / "gates.yaml").write_text(g)
    assert gate_config_for_campaign("camp-000004", root=tmp_path)["G5"]["min_dsr"] == 0.95
    assert gate_config_for_campaign("camp-000005", root=tmp_path)["G5"]["min_dsr"] == 0.9
    env = ResearchEnv.at(tmp_path)
    assert C.config_root(env) == tmp_path


# ------------------------------------------------------------------ 견고성 기준 (gates.yaml)
def test_robustness_thresholds_in_gates_yaml_and_catalogued():
    cfg = G.load_gate_config()
    # 같은 값으로 이관 — 이후 승인된 변경(D22: alpha_min_tstat 2.0 → 1.5, camp-000032 부터)만 다르다
    assert {**cfg["robustness"], "alpha_min_tstat": 2.0} == R.DEFAULT_THRESHOLDS == R.THRESHOLDS
    assert cfg["robustness"]["alpha_min_tstat"] == 1.5
    assert R.load_thresholds() == {**R.DEFAULT_THRESHOLDS, "alpha_min_tstat": 1.5}
    reg = load_registry()                                                  # 검증 통과 (drift 없음, 전부 수록)
    assert validate(reg) == []
    for k in R.DEFAULT_THRESHOLDS:
        p = reg.get(f"robustness.{k}")
        assert p.source == f"yaml:config/gates.yaml#robustness.{k}"
        assert (p.cls, p.owner) == ((1, "auto") if k == "latency_delays_min" else (2, "human"))
        assert p.rationale.strip()
    # 일부만 있는 설정 → 나머지는 상수 대체값, 반환은 새 객체
    th = R.load_thresholds({"robustness": {"alpha_min_tstat": 3.0}})
    assert th["alpha_min_tstat"] == 3.0 and th["cost_mults"] == [1.5, 2.0]
    assert R.load_thresholds({"cross_venue_min_ratio": 0.8})["cross_venue_min_ratio"] == 0.8
    th["cost_mults"].append(9)
    assert R.DEFAULT_THRESHOLDS["cost_mults"] == [1.5, 2.0]


def test_robustness_suite_uses_given_thresholds():
    from test_research_robustness import START, _end, _signal_fn, make_case
    m1, pos = make_case(days=200, seed=5, jump=0.004)
    cand = R.Candidate.from_signals(_signal_fn(pos), 3.0, 3.0, 30, name="jump")
    base = R.run_suite(cand, m1, START, _end(m1), checks=("latency",))
    assert not base["checks"]["latency"]["passed"]
    assert base["thresholds"]["latency_min_retention"] == 0.5
    loose = R.run_suite(cand, m1, START, _end(m1), checks=("latency",),
                        thresholds={"robustness": {"latency_min_retention": -1e9}})
    assert loose["checks"]["latency"]["passed"] and loose["thresholds"]["latency_min_retention"] == -1e9
    assert loose["checks"]["latency"]["min_retention"] == base["checks"]["latency"]["min_retention"]


# ------------------------------------------------------------------ ScreenTemplate data.aux
def test_screen_template_accepts_aux_natively():
    d = aux_screen_dict()
    tpl = ScreenTemplate.from_dict(d)
    assert tpl.aux == ["upbit"] and tpl.raw == d and tpl.exchange == "coinone"
    assert tpl.to_record()["data"]["aux"] == ["upbit"]
    # 하위 호환 래퍼: 같은 값, 원장 기록 형식도 예전 그대로 (aux 를 항상 남김)
    w = load_screen_template(d)
    assert isinstance(w, ScreenTemplate) and w.aux == tpl.aux and w.configs() == tpl.configs()
    assert w.to_record() == tpl.to_record()
    plain = {**d, "data": {k: v for k, v in d["data"].items() if k != "aux"},
             "signals": [{"entry": {"block": "donchian_breakout", "params": {"lookback": 60}}}]}
    assert "aux" not in ScreenTemplate.from_dict(plain).to_record()["data"]       # aux 없으면 예전 형식
    assert load_screen_template(plain).to_record()["data"]["aux"] == []
    assert load_screen_template(tpl) is tpl
    # 검증 규칙 (예전 래퍼와 같은 오류)
    for data, msg in ((["binance"], "not supported"), (["upbit", "upbit"], "duplicates"),
                      (["coinone"], "not supported")):
        with pytest.raises(CampaignError, match=msg):
            ScreenTemplate.from_dict({**d, "data": {**d["data"], "aux": data}})
    with pytest.raises(CampaignError, match="target exchange"):
        ScreenTemplate.from_dict({**d, "data": {**d["data"], "exchange": "upbit"}})
    with pytest.raises(CampaignError, match="require aux data"):
        ScreenTemplate.from_dict({**d, "data": {k: v for k, v in d["data"].items() if k != "aux"}})
