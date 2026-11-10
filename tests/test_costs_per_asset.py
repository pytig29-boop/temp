"""자산별 비용 모델 (PLAN D17, 사용자 승인 2026-11-10) — 레지스트리 값·검증·생성 절차, 그리고 모든 연구·페이퍼 경로가
XRP-KRW / ETH-KRW 슬리브에 `<name>@<SYM>` 모델을 쓰는지."""
from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from btc_quant.backtest.types import CostModel
from btc_quant.params import costs as K
from btc_quant.params import proposals as P
from btc_quant.research import campaign as C

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_RT = {   # 왕복 bp (사용자 지정 코인원 + 보수 도출 업비트)
    "coinone_base@XRP-KRW": 8.0, "coinone_conservative@XRP-KRW": 10.0, "coinone_stress@XRP-KRW": 23.76,   # v2 (Q7, 2026-11-10)
    "coinone_base@ETH-KRW": 5.0, "coinone_conservative@ETH-KRW": 8.0, "coinone_stress@ETH-KRW": 14.0,
    "upbit_base@XRP-KRW": 18.7, "upbit_conservative@XRP-KRW": 22.7, "upbit_stress@XRP-KRW": 39.4,
    "upbit_base@ETH-KRW": 15.5, "upbit_conservative@ETH-KRW": 19.5, "upbit_stress@ETH-KRW": 36.4,
}


def test_registry_values_and_validation():
    doc = K.load_costs()
    assert K.validate_costs(doc) == []
    for name, rt in EXPECTED_RT.items():
        m = K.cost_model(name, doc=doc)
        assert K.round_trip_bps(m) == pytest.approx(rt, abs=1e-9), name
        assert doc["models"][name]["symbol"] == name.split("@")[1]
        rec = doc["models"][name]["versions"][-1]
        assert rec["proposal_id"].startswith("pp-20261110-per-asset-cost-models") or (
            name == "coinone_stress@XRP-KRW" and rec["proposal_id"].startswith("pp-20261110-coinone-xrp-stress"))
        assert rec["applied_by"] == "human:user (explicit chat approval 2026-11-10)"
    # 코인원: 2·(spread/2 + slip) = 왕복 (수수료 0)
    for name in [n for n in EXPECTED_RT if n.startswith("coinone")]:
        m = K.cost_model(name, doc=doc)
        assert m.fee_rate == 0.0 and 2 * (m.spread_bps / 2 + m.slip_bps) == pytest.approx(EXPECTED_RT[name])
    # BTC 모델 불변 (option B v2: 2 / 4 / 8 bp)
    assert [K.round_trip_bps(K.cost_model(f"coinone_{r}", doc=doc)) for r in K.ROLES] == pytest.approx([2, 4, 8])
    # 보정의 거래소 기본 모델 조회에 자산별 모델이 섞이지 않는다
    assert {K.model_for("coinone", r, doc=doc) for r in K.ROLES} == {"coinone_base", "coinone_conservative",
                                                                     "coinone_stress"}
    assert K.model_for("upbit", "stress", doc=doc, symbol="ETH-KRW") == "upbit_stress@ETH-KRW"
    # params.yaml 카탈로그 (class ①)
    from btc_quant.params import load_registry
    reg = load_registry()
    for name in EXPECTED_RT:
        p = reg.get(f"costs.{name}")
        assert p.cls == 1 and reg.resolve(p)["round_trip_bps"] == pytest.approx(EXPECTED_RT[name])


def test_validation_is_per_venue_and_symbol():
    doc = copy.deepcopy(K.load_costs())
    # XRP base 가 XRP conservative 보다 비싸면 거부 (BTC 모델과는 따로 검사)
    v = doc["models"]["coinone_base@XRP-KRW"]["versions"][-1]
    v["slip_bps"] = 5.0
    errs = K.validate_costs(doc)
    assert any("coinone@XRP-KRW" in e and "base ≤ conservative ≤ stress" in e for e in errs)
    doc2 = copy.deepcopy(K.load_costs())
    doc2["models"]["coinone_base@XRP-KRW"]["symbol"] = "ETH-KRW"
    assert any("does not match" in e for e in K.validate_costs(doc2))


def test_sleeve_lookup_uses_per_asset_models():
    for base in ("coinone_base", "coinone_conservative", "coinone_stress", "upbit_base", "upbit_conservative",
                 "upbit_stress"):
        assert C.sleeve_cost_name(base, "BTC-KRW") == base
        for sym in ("XRP-KRW", "ETH-KRW"):
            assert C.sleeve_cost_name(base, sym) == f"{base}@{sym}"
            assert C.get_sleeve_cost(base, sym) == K.cost_model(f"{base}@{sym}")


def _tmp_cost_root(tmp_path) -> Path:
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "costs.yaml", tmp_path / "config" / "costs.yaml")
    return tmp_path


def test_create_proposal_flow(tmp_path):
    root = _tmp_cost_root(tmp_path)
    new = CostModel("coinone_base@SOL-KRW", 0.0, 4.0, 1.0)
    old = K.cost_model("coinone_base", root=root)
    ch = {"target": "costs:coinone_base@SOL-KRW", "name": "coinone_base@SOL-KRW",
          "create": {"venue": "coinone", "role": "base", "symbol": "SOL-KRW"}, "from_model": "coinone_base",
          "from": {"fee_rate": old.fee_rate, "spread_bps": old.spread_bps, "slip_bps": old.slip_bps},
          "to": {"fee_rate": 0.0, "spread_bps": 4.0, "slip_bps": 1.0, "tick": 0.0}}
    pid = P.write_proposal({"class": 1, "kind": "cost_models", "title": "t", "evidence": "x", "changes": [ch]}, root)
    with pytest.raises(P.ConfirmationError):
        P.approve(pid, by="x", root=root)                                    # 기본은 대화형 확인 (테스트는 tty 아님)
    rec = P.approve(pid, by="tester", root=root, confirm=lambda e, w: None)
    assert rec["applied"] == [{"name": "coinone_base@SOL-KRW", "version": 1}]
    doc = K.load_costs(root)
    e = doc["models"]["coinone_base@SOL-KRW"]
    assert e["symbol"] == "SOL-KRW" and e["versions"][0]["direction"] == "initial"
    assert e["versions"][0]["derived_from"] == "coinone_base" and K.cost_model("coinone_base@SOL-KRW", root=root) == new
    # 같은 이름을 다시 만들려는 제안은 낡은 제안으로 거부
    pid2 = P.write_proposal({"class": 1, "kind": "cost_models", "title": "t2", "evidence": "x", "changes": [ch]}, root)
    with pytest.raises(P.ProposalError, match="already exists"):
        P.approve(pid2, by="tester", root=root, confirm=lambda e, w: None)
    # 검증을 깨는 생성(base > 같은 심볼 conservative 가 없으므로 단독은 통과) — 직접 add_versions 로 역순 생성은 거부
    with pytest.raises(K.CostRegistryError):
        K.add_versions([{"name": "coinone_stress@SOL-KRW", "model": CostModel("x", 0.0, 0.0, 0.0),
                         "create": {"venue": "coinone", "role": "stress", "symbol": "SOL-KRW"},
                         "direction": "initial", "applied_by": "t", "source": "t"}], root=root)
    assert "coinone_stress@SOL-KRW" not in K.load_costs(root)["models"]       # 아무것도 쓰지 않음


# ------------------------------------------------------------------ 모든 경로가 자산별 모델을 쓴다
def test_campaign_pipeline_screen_forward_paper_use_per_asset_costs(tmp_path, monkeypatch):
    # 캠페인: 매니페스트 슬리브 비용
    from test_universe import make_universe_root, uni_template

    from btc_quant.paper import service as S
    from btc_quant.research import forward as F
    from btc_quant.research import pipeline as PL
    from btc_quant.research import signal_study as SS
    from btc_quant.research.campaign import CampaignTemplate, ResearchEnv, run_campaign
    root = make_universe_root(tmp_path / "u", upbit=False)
    env = ResearchEnv.at(root)
    tpl = uni_template(name="pac")
    tpl["entry"] = {"block": "donchian_breakout", "grid": {"lookback": [48]}}
    tpl["exits"] = {"sl_pct": [0.6], "tp_pct": [0.8], "max_hold_min": [60]}
    cr = run_campaign(CampaignTemplate.from_dict(tpl), env=env)
    costs = cr.manifest["sleeves"]["costs"]
    assert costs["BTC-KRW"] == {"coinone_conservative": "coinone_conservative", "upbit_conservative": "upbit_conservative"}
    for sym in ("XRP-KRW", "ETH-KRW"):
        assert costs[sym] == {"coinone_conservative": f"coinone_conservative@{sym}",
                              "upbit_conservative": f"upbit_conservative@{sym}"}
    # 파이프라인: Dev 슬리브 결과의 비용 모델
    p = PL.Pipeline(cr, env, lessons=False, register=False)
    spec = next(iter(cr.specs.values()))
    sl = p.dev_sleeves(spec, "coinone_stress")
    assert {s: r.cost.name for s, r in sl.items()} == {"BTC-KRW": "coinone_stress", "XRP-KRW": "coinone_stress@XRP-KRW",
                                                      "ETH-KRW": "coinone_stress@ETH-KRW"}
    # 단일 비-BTC 자산 유니버스도 자기 모델
    tpl1 = uni_template(name="pac1", universe=["XRP-KRW"])
    tpl1["entry"], tpl1["exits"] = tpl["entry"], tpl["exits"]
    cr1 = run_campaign(CampaignTemplate.from_dict(tpl1), env=env)
    p1 = PL.Pipeline(cr1, env, lessons=False, register=False)
    r1 = p1.dev_sleeves(next(iter(cr1.specs.values())), "coinone_conservative")
    assert r1["XRP-KRW"].cost.name == "coinone_conservative@XRP-KRW"
    # 신호 선별(풀링): 이벤트마다 자기 자산 비용
    captured = {}
    orig = SS.study_events_pooled

    def spy(assets, *a, **k):
        captured["costs"] = {x["symbol"]: x.get("costs") for x in assets}
        return orig(assets, *a, **k)
    monkeypatch.setattr(SS, "study_events_pooled", spy)
    d = {"name": "pacs", "hypothesis": {"text": "x", "rationale": "y", "success_criteria": "z"},
         "data": {"exchange": "coinone", "timeframe": "1m"}, "universe": ["BTC-KRW", "XRP-KRW", "ETH-KRW"],
         "bar_minutes": 5, "signals": [{"entry": {"block": "donchian_breakout", "params": {"lookback": 48}}}],
         "study": {"n_null": 20, "min_events": 50}, "budget": 5}
    st = C.run_screen(d, env=env, lessons=False)
    assert captured["costs"]["XRP-KRW"] == {"coinone_conservative": "coinone_conservative@XRP-KRW",
                                            "upbit_conservative": "upbit_conservative@XRP-KRW"}
    one = st.studies[0]
    assert one["costs_by_asset"]["XRP-KRW"]["coinone_conservative"] == pytest.approx(10e-4, rel=1e-3)
    assert one["costs_by_asset"]["BTC-KRW"]["coinone_conservative"] == pytest.approx(4e-4, rel=1e-3)
    # 전진 인큐베이션: 슬리브 비용 조회
    assert "get_sleeve_cost(fs.cost, sym" in Path(F.__file__).read_text(encoding="utf-8")
    # 페이퍼: 슬리브 트레이더 비용 = `<cost>@<SYM>` (서비스가 레지스트리를 그대로 읽는다)
    reg = S.load_costs()
    assert reg["coinone_conservative@XRP-KRW"] == K.cost_model("coinone_conservative@XRP-KRW")
    src = Path(S.__file__).read_text(encoding="utf-8")
    assert 'self._costs.get(f"{self.cfg.cost}@{symbol}", self.cost)' in src


def test_paper_sleeve_trader_cost(tmp_path):
    import numpy as np
    import pandas as pd
    from test_paper_service import Clock, FakeCollector, FakeNotifier
    from test_paper_trader import synth_raw

    from btc_quant.live.risk import RiskLimits
    from btc_quant.paper import service as S
    from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec
    t0 = pd.Timestamp("2026-11-12 23:00", tz="UTC")
    uni = ("BTC-KRW", "XRP-KRW", "ETH-KRW")
    spec = StrategySpec("pc_uni", BlockRef("random_entry", {"p": 0.5, "seed": 1}), ExitSpec(0.1, 0.1, 10), universe=uni)
    p0 = {"BTC-KRW": 1e8, "XRP-KRW": 3_000.0, "ETH-KRW": 5_000_000.0}
    rng = np.random.default_rng(5)
    raws = {S._market(s): synth_raw(rng, t0 - pd.Timedelta(days=2), 2 * 1440 + 10, p0=p0[s]) for s in uni}

    def fetch(market, unit, start, end, progress=False):
        r = raws[market]
        return r[(r.index >= start) & (r.index < end)]
    slot = S.StrategySlot(spec, "spec", "B", RiskLimits(-0.5, -0.5, -0.5, 1000))
    cfg = S.ServiceConfig(runtime_dir=tmp_path / "rt", heartbeat_seconds=1, summary_hour_kst=9, notional=300_000,
                          cost="coinone_conservative")
    svc = S.PaperService([slot], cfg, notifier=FakeNotifier(), collector=FakeCollector(), fetch=fetch,
                         clock=Clock(t0.timestamp() - 30))
    names = {k.split("@")[1]: tr.cost.name for k, tr in svc.traders.items()}
    assert names == {"BTC-KRW": "coinone_conservative", "XRP-KRW": "coinone_conservative@XRP-KRW",
                     "ETH-KRW": "coinone_conservative@ETH-KRW"}
