"""관문 파이프라인 end-to-end (합성 시장).

(a) 돌파가 실제로 통하는 시장 → 올바른 스펙(donchian lookback=48)이 G1~G4(및 G0~G6) 통과, 레지스트리 validated.
(b) 무작위 보행 → 후보는 G2 또는 G5 에서 실패, 레지스트리 등록 없음.
(e) 잠금 구간 접근 시도 없음 (DataAccess 읽기 범위 감시 + 예외 없음).
"""
from __future__ import annotations

import json

import pytest

from btc_quant.registry.splits import overlaps_lockbox
from btc_quant.research import cli
from btc_quant.research.campaign import CampaignTemplate, ResearchEnv, run_campaign
from btc_quant.research.pipeline import run_pipeline
from btc_quant.research.report import write_report

from test_research_synth import make_root, template_dict


def _spy_reads(env: ResearchEnv) -> list:
    calls = []
    orig = env.data.read_range

    def spy(start, end, *a, **k):
        calls.append((start, end, k.get("key")))
        return orig(start, end, *a, **k)
    env.data.read_range = spy          # DataAccess.read 도 self.read_range 를 거친다
    return calls


def _run(root, tpl_dict, top_k=2):
    env = ResearchEnv.at(root)
    calls = _spy_reads(env)
    cr = run_campaign(CampaignTemplate.from_dict(tpl_dict), env=env)
    out = run_pipeline(cr, env=env, top_k=top_k)
    report = write_report(cr.campaign_id, env=env)
    return env, cr, out, report, calls


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    root = make_root(tmp_path_factory.mktemp("planted"), planted=True, seed=7)
    d = template_dict(name="planted")
    d["filters"] = [[{"block": "session_kst", "grid": {"session": ["all_but_quiet"]}}]]  # 기여도 경로 실행
    # 파이프라인 동작 검증용: stress 비용을 base 로 둔다. 고정 stress 프리셋(왕복 21bp)은 이 합성 엣지(하루 수 회
    # 거래)를 음수로 만들어 G6(b: stress Sharpe>0)에서 탈락시킨다 — stress 비용 수준 자체는 데이터 기반으로
    # 재설정 예정(PLAN §11, 2026-11-09).
    d.setdefault("costs", {})["stress"] = "coinone_base"
    return _run(root, d)


@pytest.fixture(scope="module")
def randomwalk(tmp_path_factory):
    root = make_root(tmp_path_factory.mktemp("rw"), planted=False, seed=7)
    return _run(root, template_dict(name="rw"))


def test_planted_edge_passes(planted):
    env, cr, out, report, _ = planted
    top = out["candidates"][0]
    assert top["spec"]["entry"]["params"] == {"lookback": 48}
    for g in ("G0", "G1", "G2", "G3", "G4"):
        assert top["gates"][g]["passed"], (g, top["gates"][g])
    g2 = top["gates"]["G2"]
    assert g2["n_null"] == 1000 and g2["p_value"] <= 0.05 and g2["calibration"] == "trade_count"
    assert abs(g2["calibration_probe_trades"] - g2["candidate_n_trades"]) <= 0.1 * g2["candidate_n_trades"]
    assert top["gates"]["G3"]["n_neighbors"] >= 3
    assert top["gates"]["G5"]["passed"] and top["dev_passed"]
    g6 = top["gates"]["G6"]; assert g6["passed"] and g6["reference_engine_match"], {k: v for k, v in g6.items() if k.startswith("ok_") or k in ("rule","max_dd","stress_sharpe","val_holm_passed","val_p_value","reference_engine_match")}
    assert top["grade"] in ("A", "B")
    # 레지스트리: draft → candidate → validated, 증거 첨부
    sid = top["spec_id"]
    assert env.registry.state_of(sid, 1) == "validated"
    trs = env.ledger.transitions(sid)
    assert [t["to_state"] for t in trs] == ["draft", "candidate", "validated"]
    assert "G2" in trs[1]["evidence"] and "G6" in trs[2]["evidence"]
    # 기여도: 필터 제거 스펙은 격자 밖 → 별도 ablation 캠페인으로 사전등록·기록, 스펙은 바뀌지 않음
    abl = top["ablation"]
    assert abl and abl["ablation_campaign"] and len(abl["filters"]) == 1
    row = abl["filters"][0]
    assert row["removable"] == (row["delta"] < row["se"])
    if row["removable"]:
        assert abl["simplified"]["spec"]["filters"] == [] and top["spec"]["filters"]
    abl_runs = [r for r in env.ledger.records("runs") if r["campaign_id"] == abl["ablation_campaign"]]
    n_abl = sum(1 for c in out["candidates"] if c["dev_passed"])     # 생존자마다 필터 1개 제거
    assert len(abl_runs) == n_abl and all(r["spec"]["filters"] == [] for r in abl_runs)
    assert env.ledger.get("campaigns", abl["ablation_campaign"])["budget"] == n_abl
    assert env.ledger.trials_count(family="donchian_breakout") == cr.manifest["n_specs"] + n_abl
    # Val 실행도 원장에 (같은 logic_hash → 시도 수 증가 없음)
    val_runs = [r for r in env.ledger.records("runs")
                if r["campaign_id"] == cr.campaign_id and r["period"].get("segment") == "val"]
    assert {r["cost_name"] for r in val_runs} >= {"coinone_conservative", "coinone_base"}
    # 파라미터 버전: pipeline.json·ablation·Val 실행 기록 모두 캠페인과 같은 params_version
    assert out["params_version"] == cr.manifest["params_version"] and out["cost_versions"]
    assert all(r["params_version"] == out["params_version"] for r in val_runs + abl_runs)
    # 리포트
    text = report.read_text()
    assert "candidate(s) passed G0–G6" in text and "Ablation" in text and "Yearly" in text
    assert json.loads((cr.dir / "pipeline.json").read_text())["n_validated"] >= 1


def test_random_walk_fails(randomwalk):
    env, cr, out, report, _ = randomwalk
    assert out["n_validated"] == 0 and out["verdict"] == "no candidate passed"
    for c in out["candidates"]:
        assert not (c["gates"]["G2"]["passed"] and c["gates"]["G5"]["passed"])
        assert c.get("G6") is None and "G6" not in c["gates"]
    assert all(not env.registry.list(s) for s in ("draft", "candidate", "validated"))
    assert "No candidate passed" in report.read_text()


@pytest.mark.parametrize("which", ["planted", "randomwalk"])
def test_no_lockbox_access(which, request):
    env, cr, out, _, calls = request.getfixturevalue(which)
    assert calls, "DataAccess was not used"
    cfg = env.data.config
    for start, end, key in calls:
        assert key is None and not overlaps_lockbox(start, end, cfg)
    assert env.ledger.records("lockbox") == []
    assert "not accessed" in out["lockbox"]


def test_cli_report(planted, capsys):
    env, cr, *_ = planted
    assert cli.main(["--root", str(env.root), "report", cr.campaign_id]) == 0
    assert f"campaign_{cr.campaign_id}.md" in capsys.readouterr().out
