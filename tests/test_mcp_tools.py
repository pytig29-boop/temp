"""연구 MCP 서버 — in-process MCP 호출(공식 SDK Client ↔ MCPServer)로 도구를 합성 저장소에서 검증.

합성 시장 = research_v4_synth.make_lagged_root (코인원이 업비트를 1분 늦게 따라감, 짧은 분할).
선별 → 캠페인(+관문 파이프라인) → 리포트 왕복, 원장 조회 제한(Val 은 결과 코드만), 잠금 요청이 잠금을
열지 않음, 원장 쓰기 전 거부(선별 게이트·검정력·예산·중복), 오답노트·실험 블록 수용 검사.
"""
from __future__ import annotations

import asyncio
import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml
from mcp import Client
from research_v4_synth import aux_campaign_dict, aux_screen_dict, make_lagged_root

from btc_quant.evaluation import gates as G
from btc_quant.mcp.server import build_server
from btc_quant.mcp.tools import ResearchTools
from btc_quant.registry.ledger import Ledger
from btc_quant.registry.splits import DataAccess, overlaps_lockbox
from btc_quant.registry.strategies import StrategyRegistry
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

ROOT = Path(__file__).resolve().parents[1]
N_NULL = 200
TARGET = {"target_sharpe": 100.0}        # 합성 시장 엣지가 커서 검정력 목표를 넉넉히 선언


def call(tools: ResearchTools, name: str, **args) -> dict:
    """MCP 프로토콜을 거쳐 도구 호출 (in-process transport). 도구 오류도 is_error 가 아닌 구조화 결과."""
    async def go():
        async with Client(build_server(tools)) as c:
            r = await c.call_tool(name, args)
            assert not r.is_error, r.content
            assert json.loads(r.content[0].text) == r.structured_content
            return r.structured_content
    return asyncio.run(go())


def n_campaigns(tools: ResearchTools) -> int:
    return len(Ledger(tools.root / "research" / "ledger").records("campaigns"))


@pytest.fixture(scope="module")
def tools(tmp_path_factory):
    root = make_lagged_root(tmp_path_factory.mktemp("mcp"))
    cfg = copy.deepcopy(G.load_gate_config())
    cfg["G2"]["null_runs"] = N_NULL
    return ResearchTools(root, gate_cfg=cfg, n_null=N_NULL, load_accepted_blocks=False, progress=lambda s: None)


@pytest.fixture(scope="module")
def screened(tools):
    (tools.root / "screen.yaml").write_text(yaml.safe_dump(aux_screen_dict()), encoding="utf-8")
    return call(tools, "screen_signals", template="screen.yaml")          # YAML 경로 (저장소 안)


@pytest.fixture(scope="module")
def campaign(tools, screened):
    return call(tools, "create_and_run_campaign",
                template=aux_campaign_dict(from_screen=screened["campaign_id"], power=TARGET), max_candidates=2)


# ------------------------------------------------------------------ 블록·데이터
def test_list_blocks(tools):
    r = call(tools, "list_blocks")
    assert r["ok"]
    by = {b["name"]: b for b in r["blocks"]}
    assert by["donchian_breakout"]["status"] == "official" and by["donchian_breakout"]["grid"]
    up = by["upbit_lead_impulse"]
    assert up["status"] == "experimental" and up["requires"] == ["upbit"] and up["live_ready"] is True
    assert up["author"] and up["rationale"] and "human block review" in up["note"]
    assert r["counts"]["experimental"] >= 1
    only = call(tools, "list_blocks", kind="filter", include_experimental=False)
    assert only["blocks"] and all(b["kind"] == "filter" and b["status"] == "official" for b in only["blocks"])
    bad = call(tools, "list_blocks", kind="lockbox")
    assert bad["ok"] is False and bad["error"]["code"] == "invalid_argument"


def test_data_summary_never_touches_lockbox(tools, monkeypatch):
    calls = []
    orig = DataAccess.read_range

    def spy(self, start, end, *a, **k):
        calls.append((start, end))
        return orig(self, start, end, *a, **k)
    monkeypatch.setattr(DataAccess, "read_range", spy)
    r = call(tools, "get_data_summary")
    assert r["ok"]
    assert calls and not any(overlaps_lockbox(s, e, tools.root / "config" / "splits.yaml") for s, e in calls)
    co = next(s for s in r["series"] if s["exchange"] == "coinone")
    assert set(co["segments"]) == {"dev", "val", "forward"}
    assert co["segments"]["dev"]["rows"] > 0 and co["segments"]["dev"]["coverage"] == pytest.approx(1.0)
    up = next(s for s in r["series"] if s["exchange"] == "upbit")
    assert up["segments"]["dev"]["max_gap_bars"] >= 20                  # 합성 점검 공백 20분
    assert r["lockbox"]["data"].startswith("sealed") and "start" not in r["lockbox"]
    assert "lockbox" not in json.dumps(r["series"])
    denied = call(tools, "get_data_summary", segments=["lockbox"])
    assert denied["ok"] is False and denied["error"]["code"] == "lockbox_denied"


# ------------------------------------------------------------------ 선별 → 캠페인 → 리포트
def test_screen_round_trip(tools, screened):
    assert screened["ok"], screened
    assert screened["n_configs"] == 4 and screened["n_passed"] >= 3
    passed = [c for c in screened["configs"] if c["passed"]]
    assert all(c["entry"] == "upbit_lead_impulse" for c in passed)
    assert screened["lessons_facts"] and "from_screen" in screened["next"]
    rep = call(tools, "get_campaign_report", campaign_id=screened["campaign_id"])
    assert rep["ok"] and rep["kind"] == "signal_screen" and len(rep["configs"]) == 4 and rep["facts"]


def test_campaign_round_trip_val_outcome_only(tools, screened, campaign):
    r = campaign
    assert r["ok"], r
    assert r["kind"] == "strategy_campaign" and r["campaign"]["n_specs"] == 6
    assert r["campaign"]["screen_gate"]["from_screen"] == screened["campaign_id"]
    assert r["campaign"]["power"]["target_sharpe_ann"] == 100.0
    p = r["pipeline"]
    assert p["lockbox"] == "not accessed" and p["candidates"]
    for c in p["candidates"]:
        assert set(c["gates_dev"]) <= {"G0", "G1", "G2", "G3", "G4", "G5"}
        assert c["G6_val"] in ("pass", "fail", "not evaluated")
        assert c["metrics_dev"] and "sharpe_w" in c["metrics_dev"]
    assert any(c["dev_passed"] for c in p["candidates"])                  # 강한 합성 엣지 → Dev 생존
    blob = json.dumps(r)
    for leak in ("val_p_value", "risk_limits", "grade_checks", "mc_mdd95", "stress_sharpe", "val_holm"):
        assert leak not in blob, leak
    assert r["facts"] and r["report_path"] and Path(r["report_path"]).exists()
    again = call(tools, "get_campaign_report", campaign_id=r["campaign_id"])
    assert again["pipeline"]["verdict"] == p["verdict"]
    lst = call(tools, "list_campaigns")
    ids = [c["campaign_id"] for c in lst["campaigns"]]
    assert ids[0] == r["campaign_id"] and screened["campaign_id"] in ids
    assert call(tools, "get_campaign_report", campaign_id="camp-999999")["error"]["code"] == "not_found"


def test_campaign_refusals_before_ledger_write(tools, screened, campaign, monkeypatch):
    sid = screened["campaign_id"]
    n0 = n_campaigns(tools)
    # 선별 게이트 없음
    r = call(tools, "create_and_run_campaign", template=aux_campaign_dict(name="nogate", power=TARGET))
    assert r["error"]["code"] == "screen_gate"
    # 선별 미통과 구성 (thr_bp 20 은 선별에 없음)
    t = aux_campaign_dict(name="unscreened", from_screen=sid, power=TARGET)
    t["entry"]["grid"]["thr_bp"] = [20]
    assert call(tools, "create_and_run_campaign", template=t)["error"]["code"] == "screen_gate"
    # 검정력: 목표 미선언 → 엄격 거부
    t = aux_campaign_dict(name="nopower", from_screen=sid)
    t["exits"]["sl_atr"] = [5.0]                       # 이전 격자와 겹치지 않게 (겹치면 repeat_trial)
    assert call(tools, "create_and_run_campaign", template=t)["error"]["code"] == "power_check_failed"
    # 격자 > 템플릿 예산
    t = aux_campaign_dict(name="toobig", from_screen=sid, power=TARGET, budget=2)
    assert call(tools, "create_and_run_campaign", template=t)["error"]["code"] == "grid_budget_exceeded"
    # 정확 중복 (이미 돌린 격자) — 사유 없으면 거부
    r = call(tools, "create_and_run_campaign",
             template=aux_campaign_dict(from_screen=sid, power=TARGET))
    assert r["error"]["code"] == "repeat_trial" and campaign["campaign_id"] in r["error"]["message"]
    # 주간 예산 소진
    from btc_quant.research.lessons import LessonBook
    orig = LessonBook.trial_budget
    monkeypatch.setattr(LessonBook, "trial_budget", lambda self, fam, now=None: {**orig(self, fam), "remaining": 0})
    t = aux_campaign_dict(name="nobudget", from_screen=sid, power=TARGET)
    t["exits"]["sl_atr"] = [4.0]
    r = call(tools, "create_and_run_campaign", template=t)
    assert r["error"]["code"] == "weekly_budget_exceeded" and r["error"]["budget"]["remaining"] == 0
    assert call(tools, "screen_signals", template=aux_screen_dict(name="s2"))["error"]["code"] == "weekly_budget_exceeded"
    # 잘못된 템플릿·저장소 밖 경로
    assert call(tools, "create_and_run_campaign", template={"name": "x"})["error"]["code"] == "invalid_template"
    assert call(tools, "screen_signals", template="/etc/passwd.yaml")["error"]["code"] == "path_outside_root"
    assert n_campaigns(tools) == n0                                       # 아무것도 원장에 쓰지 않음


def test_power_and_similar(tools, campaign):
    pw = call(tools, "power_check", n_grid=6, family="upbit_lead_impulse", target_sharpe=100.0)
    assert pw["ok"] and pw["ok"] is True and pw["required_sharpe_ann"] > 0 and pw["n_family_prior"] > 0
    pw2 = call(tools, "power_check", template=aux_campaign_dict(from_screen="x"))
    assert pw2["n_grid"] == 6 and "no target" in pw2["verdict"]
    fs = call(tools, "find_similar", template=aux_campaign_dict(from_screen="x"),
              text="Coinone follows Upbit with a lag")
    assert fs["exact"] and fs["exact"][0]["campaign_id"] == campaign["campaign_id"]
    assert fs["weekly_budget"]["family"] == "upbit_lead_impulse"
    assert call(tools, "find_similar")["error"]["code"] == "invalid_argument"


# ------------------------------------------------------------------ 원장 조회 제한
def test_query_ledger_restrictions(tools, campaign):
    led = Ledger(tools.root / "research" / "ledger")
    raw_val = led.records("val_evaluations")
    assert any(r.get("event") == "result" and r.get("p_value") is not None for r in raw_val)   # 원본엔 p 값
    v = call(tools, "query_ledger", table="val_evaluations")
    assert v["n_matched"] == len(raw_val)
    for row in v["rows"]:
        assert set(row) <= {"id", "ts", "event", "val_id", "k", "logic_hash", "campaign_id", "spec_id",
                            "opening_id", "outcome"}
        assert row["outcome"] in ("pass", "fail", None)
    assert "p_value" not in json.dumps(v) and "summary" not in json.dumps(v["rows"])
    for bad in ({"table": "lockbox_approvals"}, {"table": "secrets"},
                {"table": "val_evaluations", "columns": ["p_value"]},
                {"table": "val_evaluations", "filters": {"summary": 1}},
                {"table": "transitions", "columns": ["evidence"]}):
        r = call(tools, "query_ledger", **bad)
        assert r["ok"] is False and r["error"]["code"] in ("table_not_allowed", "column_not_allowed"), bad
    # Dev 외 구간의 실행 지표는 숨김
    led.append("runs", {"campaign_id": "camp-test-val", "hypothesis_id": "hyp-x", "family": "zz_test",
                        "spec_id": "s", "logic_hash": "h" * 16, "spec": {}, "cost_name": "c",
                        "period": {"segment": "val"}, "metrics": {"sharpe": 9.9}})
    r = call(tools, "query_ledger", table="runs", filters={"family": "zz_test"})
    assert r["n_matched"] == 1 and "withheld" in r["rows"][0]["metrics"] and "9.9" not in json.dumps(r)
    cid = campaign["campaign_id"]
    runs = call(tools, "query_ledger", table="runs", filters={"campaign_id": cid, "segment": "dev"}, limit=3)
    assert runs["n_matched"] >= 6 and len(runs["rows"]) == 3 and "sharpe_w" in runs["rows"][0]["metrics"]
    vruns = call(tools, "query_ledger", table="runs", filters={"campaign_id": cid, "segment": "val"})
    assert vruns["n_matched"] >= 1                                       # G6 가 원장 runs 에 남긴 Val 실행
    assert all(set(r["metrics"]) == {"withheld"} for r in vruns["rows"])
    assert "spec" not in runs["rows"][0]                                 # 기본 열에서 제외
    tr = call(tools, "query_ledger", table="trials", filters={"family": "upbit_lead_impulse"})
    assert tr["trials"] >= 10
    allf = call(tools, "query_ledger", table="trials")
    assert allf["by_family"]["upbit_lead_impulse"] == tr["trials"]
    t = call(tools, "query_ledger", table="transitions")
    assert all("evidence" not in row for row in t["rows"])


# ------------------------------------------------------------------ 잠금 요청 (열지 않음)
def test_request_lockbox_eval_does_not_open(tools, monkeypatch):
    from btc_quant.registry import splits
    monkeypatch.setattr(DataAccess, "open_lockbox", lambda *a, **k: pytest.fail("open_lockbox called"))
    monkeypatch.setattr(splits, "write_lockbox_approval", lambda *a, **k: pytest.fail("approval written"))
    reg = StrategyRegistry(root=tools.root, ledger=Ledger(tools.root / "research" / "ledger"))
    spec = StrategySpec("mcp_lbx_cand", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(1.0, 1.2, 60))
    reg.register(spec, actor="test")
    rep_dir = tools.root / "research" / "reports"
    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / "review_bad.md").write_text("# Skeptic\nVERDICT: REJECT\n", encoding="utf-8")
    (rep_dir / "review_ok.md").write_text("# Skeptic\nVERDICT: SURVIVES  Confidence: low\n", encoding="utf-8")
    args = dict(strategy_id="mcp_lbx_cand", version=1, justification="mechanism holds on Dev robustness suite",
                review_report="research/reports/review_ok.md")
    assert call(tools, "request_lockbox_eval", **args)["error"]["code"] == "not_eligible"     # draft
    reg.transition("mcp_lbx_cand", 1, "candidate", "test", actor="test")
    reg.transition("mcp_lbx_cand", 1, "validated", "test", actor="test")
    r = call(tools, "request_lockbox_eval", **{**args, "review_report": "research/reports/review_bad.md"})
    assert r["error"]["code"] == "not_eligible"
    r = call(tools, "request_lockbox_eval", **args)
    assert r["ok"] and r["lockbox_opened"] is False and r["status"] == "pending_human_approval"
    req = yaml.safe_load(Path(r["request"]).read_text(encoding="utf-8"))
    assert req["candidate_id"] == "mcp_lbx_cand_v1" and "approve-lockbox" in req["human_next_step"]
    assert Path(r["request"]).parent == tools.root / "research" / "lockbox_requests"
    assert call(tools, "request_lockbox_eval", **args)["duplicate"] is True
    da = DataAccess(root=tools.root)
    assert da.lockbox_status()["openings"] == 0 and da.lockbox_status()["pending_approvals"] == []
    assert Ledger(tools.root / "research" / "ledger").records("lockbox") == []
    assert not (tools.root / "research" / "lockbox_approvals").exists()
    st = call(tools, "registry_status")
    assert st["strategies"]["validated"] == ["mcp_lbx_cand_v1"]
    assert st["lockbox"]["openings"] == 0 and st["lockbox_requests"][0]["candidate_id"] == "mcp_lbx_cand_v1"
    assert st["val_budget"]["used"] >= 1 and "p_value" not in json.dumps(st)
    fw = call(tools, "forward_status")
    assert fw["ok"] and fw["tracked_states"]["validated"] == ["mcp_lbx_cand_v1"] and fw["strategies"] == []
    leak = call(tools, "request_lockbox_eval", **{**args, "strategy_id": "nope"})
    assert leak["error"]["code"] == "not_eligible"


# ------------------------------------------------------------------ 오답노트
def test_lessons_tools(tools, campaign):
    v = call(tools, "lessons_view", family="upbit_lead_impulse")
    assert v["ok"] and v["integrity_problems"] == [] and v["weekly_budget"]["family"] == "upbit_lead_impulse"
    fact = next(f for f in v["facts"] if f.get("campaign_id") == campaign["campaign_id"])
    bad = call(tools, "lessons_add_interpretation", fact_id=fact["id"],
               text="Val Sharpe was 2.3 so the edge is real", confidence="low", evidence=[fact["id"]])
    assert bad["ok"] is False and bad["error"]["code"] == "restricted_info"
    ok = call(tools, "lessons_add_interpretation", fact_id=fact["id"],
              text="The edge is pure 1-minute lag; any execution delay removes it.", confidence="medium",
              evidence=[f"{fact['id']} robustness latency"], proposed_test="same screen at 2m decision bars")
    assert ok["ok"] and ok["status"] == "unverified" and ok["interpretation"]["id"].startswith("int-")
    m = call(tools, "lessons_mark", interpretation_id=ok["interpretation"]["id"], status="verified",
             by_campaign=campaign["campaign_id"])
    assert m["ok"] is False and m["error"]["code"] == "lessons_error"   # 같은 캠페인으로는 검증 불가
    d = call(tools, "lessons_derive_hypothesis", parent=fact["id"], text="Lag edge survives at 2m bars?",
             rationale="follow-up of the latency failure", success_criteria="G0-G6 pass",
             template=aux_campaign_dict(name="lag2m", family="other_family"))
    assert d["ok"] is False and d["error"]["code"] == "family_inheritance"
    d = call(tools, "lessons_derive_hypothesis", parent=fact["id"], text="Lag edge survives at 2m bars?",
             rationale="follow-up of the latency failure", success_criteria="G0-G6 pass",
             template=aux_campaign_dict(name="lag2m"))
    assert d["ok"] and d["family"] == "upbit_lead_impulse" and d["template"]["family"] == "upbit_lead_impulse"
    assert "[derived-from drv-" in d["template"]["hypothesis"]["rationale"]
    h = call(tools, "register_hypothesis", text="Upbit leads Coinone by one minute",
             mechanism="price discovery on the larger venue", counterparty="slow Coinone liquidity takers",
             success_criteria="screen pass + G0-G6")
    assert h["ok"] and h["hypothesis_id"].startswith("hyp-") and "Counterparty" in h["rationale_recorded"]
    assert call(tools, "register_hypothesis", text="x", mechanism="", counterparty="y",
                success_criteria="z")["error"]["code"] == "invalid_argument"


# ------------------------------------------------------------------ 실험 블록 수용 검사
GOOD = '''
from btc_quant.strategy.blocks.base import REGISTRY
from btc_quant.strategy.blocks.momentum import as_signal


@REGISTRY.register_experimental("entry", grid={"k": [1, 2, 3]}, description="close rose over k bars",
                                author="test", created="2026-11-09", rationale="toy momentum (test)",
                                lookback_bars=lambda p: int(p["k"]) + 1)
def mcp_toy_rise(bars, ctx, k: int = 1):
    return as_signal(bars["close"] > bars["close"].shift(k), bars.index)
'''
LEAK = GOOD.replace("mcp_toy_rise", "mcp_toy_leak").replace('shift(k)', 'shift(-k)')


def test_propose_block(tmp_path):
    bdir = tmp_path / "experimental"
    bdir.mkdir()
    (bdir / "mcp_toy_good.py").write_text(GOOD, encoding="utf-8")
    (bdir / "mcp_toy_bad.py").write_text(LEAK, encoding="utf-8")
    docs = tmp_path / "BLOCKS.md"
    docs.write_text("| `mcp_toy_rise` | entry | x | x | x | ✓ | experimental |\n"
                    "| `mcp_toy_leak` | entry | x | x | x | ✓ | experimental |\n", encoding="utf-8")
    t = ResearchTools(tmp_path, blocks_dir=bdir, docs_path=docs, load_accepted_blocks=False,
                      progress=lambda s: None)
    r = call(t, "propose_block", path=str(bdir / "mcp_toy_good.py"))
    assert r["ok"], r
    assert r["passed"] is True and r["blocks"][0]["block"] == "mcp_toy_rise" and r["loaded_in_server"] is None
    r = call(t, "propose_block", path=str(bdir / "mcp_toy_bad.py"))
    assert r["ok"] and r["passed"] is False
    assert {"causality_future", "causality_trunc"} <= set(r["blocks"][0]["failed"])
    other = tmp_path / "elsewhere.py"
    other.write_text(GOOD, encoding="utf-8")
    assert call(t, "propose_block", path=str(other))["error"]["code"] == "path_not_allowed"
    assert call(t, "propose_block", path=str(bdir / "missing.py"))["error"]["code"] == "not_found"
    (bdir / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    r = call(t, "propose_block", path=str(bdir / "broken.py"))
    assert r["error"]["code"] == "module_error" and "boom" in r["error"]["stderr_tail"]


# ------------------------------------------------------------------ 파라미터
def test_params_status_and_propose_criterion(tmp_path):
    from btc_quant.params import costs as C
    (tmp_path / "config").mkdir()
    for f in ("gates.yaml", "splits.yaml", "exchanges.yaml", "params.yaml"):
        shutil.copy(ROOT / "config" / f, tmp_path / "config" / f)
    C.ensure_costs_file(tmp_path)

    def runner(tasks):     # 가짜 합성 보정: 0.9 가 목표 충족
        return [{**t, "any_validated": False, "star_validated": t["value"] == 0.9,
                 "star_dev_passed": True} for t in tasks]
    t = ResearchTools(tmp_path, progress=lambda s: None, criteria_runner=runner)
    s = call(t, "params_status")
    assert s["ok"] and s["validation"] == "OK" and "coinone_conservative" in s["cost_models"]
    r = call(t, "propose_criterion", key="gates.G5.min_dsr", candidates=[0.9],
             motivation="loosen because camp-000003 almost passed", references=[])
    assert r["ok"] is False and r["error"]["code"] == "anti_goalpost"
    r = call(t, "propose_criterion", key="costs.coinone_stress", candidates=[1.0], motivation="synthetic study")
    assert r["error"]["code"] == "anti_goalpost"
    r = call(t, "propose_criterion", key="gates.G5.min_dsr", candidates=[0.9],
             motivation="DSR threshold power on synthetic markets", references=["synthetic:calibration_study"],
             n_null_markets=4, n_planted_markets=4)
    assert r["ok"] and r["status"] == "proposed" and r["recommended"] == 0.9 and r["proposal_id"]
    assert (tmp_path / "research" / "param_proposals" / f"{r['proposal_id']}.yaml").exists()
    assert call(t, "params_status")["pending_proposals"][0]["id"] == r["proposal_id"]
    assert not (tmp_path / "research" / "param_approvals").exists()      # 승인은 사람만
