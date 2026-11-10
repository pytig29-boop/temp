"""노출·B&H 기준 지표, 노출 인식 관문 변형(기본 OFF), Val 재사용 = 이전 결정, 탐색 시도 기록 (PLAN D14 초안)."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from btc_quant.backtest.types import COINONE_CONSERVATIVE, BacktestResult
from btc_quant.evaluation import gates as G
from btc_quant.evaluation import metrics as M

ROOT = Path(__file__).resolve().parents[1]
DAYS = pd.date_range("2022-01-01", periods=400, freq="D", tz="UTC")


def _trades(spans) -> pd.DataFrame:
    rows = [{"entry_time": a, "exit_time": b, "hold_min": int((b - a) / pd.Timedelta(minutes=1)),
             "net_ret": 0.0, "gross_ret": 0.0, "entry_price": 1.0, "exit_price": 1.0, "exit_reason": "time"}
            for a, b in spans]
    return pd.DataFrame(rows, columns=["entry_time", "entry_price", "exit_time", "exit_price", "exit_reason",
                                       "gross_ret", "net_ret", "hold_min"])


# ================================================================ 지표
def test_buy_hold_daily_close_to_close():
    idx = pd.date_range("2024-01-01", periods=3 * 1440, freq="1min", tz="UTC")
    close = pd.Series(np.linspace(100.0, 130.0, idx.size), index=idx)
    d = idx[::1440].normalize()
    bh = M.buy_hold_daily(close, pd.DatetimeIndex(d))
    assert bh.iloc[0] == pytest.approx(close.iloc[1439] / close.iloc[0] - 1)
    assert bh.iloc[1] == pytest.approx(close.iloc[2879] / close.iloc[1439] - 1)
    assert np.prod(1 + bh.to_numpy()) - 1 == pytest.approx(close.iloc[-1] / close.iloc[0] - 1)
    # 기준가 지정(Val: 워밍업 마지막 종가)
    bh2 = M.buy_hold_daily(close, pd.DatetimeIndex(d), first_price=90.0)
    assert bh2.iloc[0] == pytest.approx(close.iloc[1439] / 90.0 - 1)


def test_days_in_market_handles_units_and_multi_day_trades():
    t = _trades([(DAYS[3] + pd.Timedelta(hours=23), DAYS[5] + pd.Timedelta(hours=1)),
                 (DAYS[10] + pd.Timedelta(hours=2), DAYS[10] + pd.Timedelta(hours=3))])
    t["entry_time"] = pd.DatetimeIndex(t["entry_time"]).as_unit("ns")
    inm = M.days_in_market(t, DAYS.as_unit("us"))
    assert np.flatnonzero(inm).tolist() == [3, 4, 5, 10]
    assert M.days_in_market(None, DAYS).sum() == 0


def test_benchmark_metrics_capture_episodes_and_exposure():
    rng = np.random.default_rng(1)
    bh = pd.Series(rng.normal(0, 0.03, DAYS.size), index=DAYS)
    strat = 0.5 * bh                                  # 반만 따라가는 전략: capture 0.5
    t = _trades([(DAYS[i], DAYS[i] + pd.Timedelta(hours=12)) for i in range(DAYS.size)])
    m = M.benchmark_metrics(strat, t, bh)
    assert m["up_capture"] == pytest.approx(0.5) and m["down_capture"] == pytest.approx(0.5)
    assert m["up_regime_capture"] == pytest.approx(0.5) and m["down_regime_capture"] == pytest.approx(0.5)
    assert m["exposure_days"] == pytest.approx(1.0) and m["exposure_time"] == pytest.approx(0.5)
    assert m["bh_total_return"] == pytest.approx(np.prod(1 + bh.to_numpy()) - 1)
    assert m["bh_max_dd"] == pytest.approx(M.max_drawdown(bh)[0])
    assert m["bh_dd1_depth"] == pytest.approx(m["bh_max_dd"]) and m["bh_dd1_depth"] >= m["bh_dd2_depth"]
    a = DAYS.get_loc(pd.Timestamp(m["bh_dd1_start"], tz="UTC"))
    z = DAYS.get_loc(pd.Timestamp(m["bh_dd1_end"], tz="UTC"))
    seg = bh.to_numpy()[a:z + 1]
    assert 1 - np.prod(1 + seg) == pytest.approx(m["bh_dd1_depth"])          # 구간 = 고점 다음날 ~ 저점
    assert m["bh_dd1_strategy_return"] == pytest.approx(np.prod(1 + 0.5 * seg) - 1)
    assert m["bh_matched_total_return"] == pytest.approx(np.prod(1 + 0.5 * bh.to_numpy()) - 1)
    assert m["downside_dev"] > 0 and m["rolling_n_neutral"] == 0


def test_rolling_exposure_aware_neutral_windows():
    x = np.zeros(DAYS.size)
    inm = np.zeros(DAYS.size, dtype=bool)
    inm[:150] = True                                   # 처음 150일만 거래, 나머지는 쉼(수익 0)
    x[:150] = np.where(np.arange(150) % 2, 0.01, -0.005)
    d = M.rolling_exposure_aware(pd.Series(x, index=DAYS), inm, 0.10)
    assert d["rolling_n_windows"] == M.rolling_positive_fraction(pd.Series(x, index=DAYS))[1]
    assert d["rolling_n_neutral"] >= 1 and d["rolling_n_active"] + d["rolling_n_neutral"] == d["rolling_n_windows"]
    assert d["rolling_frac_positive_active"] == pytest.approx(1.0)
    base = M.rolling_positive_fraction(pd.Series(x, index=DAYS))[0]
    assert base < 1.0                                  # 기존 규칙은 쉬는 창(Sharpe 0)을 실패로 센다


def test_summarize_has_benchmark_keys_and_old_keys_unchanged():
    rng = np.random.default_rng(2)
    daily = pd.Series(rng.normal(0.001, 0.01, DAYS.size), index=DAYS)
    t = _trades([(DAYS[i], DAYS[i] + pd.Timedelta(hours=1)) for i in range(0, DAYS.size, 3)])
    res = BacktestResult("s", "lh", COINONE_CONSERVATIVE, DAYS[0], DAYS[-1], t, daily)
    a = M.summarize(res)
    b = M.summarize(res, benchmark=pd.Series(rng.normal(0, 0.03, DAYS.size), index=DAYS))
    for k in ("sharpe", "sharpe_w", "rolling_frac_positive", "recent_sharpe", "max_dd", "n_trades"):
        assert a[k] == b[k]
    assert "exposure_days" in a and "bh_total_return" not in a
    assert {"bh_total_return", "bh_sharpe", "bh_max_dd", "up_capture", "down_capture", "worst_bh_dd_strategy_return",
            "excess_return_vs_matched_bh", "downside_dev", "up_regime_return"} <= set(b)
    assert set(M.BENCHMARK_CONSTANT_KEYS) <= set(b)


# ================================================================ 관문 변형 (기본 OFF)
def test_repo_config_variant_is_off_and_catalogued():
    cfg = yaml.safe_load((ROOT / "config" / "gates.yaml").read_text(encoding="utf-8"))
    assert cfg["exposure_aware"]["enabled"] is False
    from btc_quant.params.registry import load_registry
    reg = load_registry(ROOT)
    keys = [p for p in reg.params if p.key.startswith("gates.exposure_aware.")]
    assert len(keys) == 12 and all(p.cls == 2 and p.owner == "human" for p in keys)
    assert reg.get("gates.exposure_aware.enabled").value is False


def _cfg(enabled: bool) -> dict:
    c = copy.deepcopy(G.load_gate_config())
    c["exposure_aware"]["enabled"] = enabled
    return c


def _g4_metrics(**over) -> dict:
    m = {"rolling_frac_positive": 0.45, "rolling_n_windows": 50, "recent_sharpe": 1.0,
         "rolling_n_active": 30, "rolling_n_neutral": 20, "rolling_frac_positive_active": 0.75,
         "rolling_neutral_exposure": 0.10, "exposure_days": 0.5}
    m.update(over)
    return m


def test_g4_variant_off_by_default_shadow_only():
    ok, d = G.check_g4(_g4_metrics())
    assert not ok and d["exposure_aware"]["enabled"] is False and d["exposure_aware"]["passed"] is True
    assert "base_passed" not in d


def test_g4_variant_on_excludes_neutral_windows_with_guards():
    on = _cfg(True)
    assert G.check_g4(_g4_metrics(), on)[0]
    assert not G.check_g4(_g4_metrics(rolling_n_active=10), on)[0]          # 비중립 창 부족
    assert not G.check_g4(_g4_metrics(exposure_days=0.05), on)[0]           # 거의 거래 안 함 → 변형 불가
    assert not G.check_g4(_g4_metrics(rolling_frac_positive_active=0.5), on)[0]
    assert not G.check_g4(_g4_metrics(recent_sharpe=0.0), on)[0]            # 최근 12개월 조건 그대로
    assert not G.check_g4(_g4_metrics(rolling_neutral_exposure=0.2), on)[0]  # 다른 임계값으로 계산된 지표
    # 상위 집합: 기존 통과는 변형에서도 통과 (활성 창이 적어도)
    assert G.check_g4(_g4_metrics(rolling_frac_positive=0.7, rolling_n_active=0, exposure_days=0.0), on)[0]


def test_g4_never_trading_strategy_cannot_pass():
    on = _cfg(True)
    m = _g4_metrics(rolling_frac_positive=0.0, rolling_n_active=0, rolling_n_neutral=50,
                    rolling_frac_positive_active=0.0, exposure_days=0.0, recent_sharpe=0.0)
    ok, d = G.check_g4(m, on)
    assert not ok and not d["exposure_aware"]["passed"]


def _val(**over) -> dict:
    v = {"sharpe": -0.4, "max_dd": 0.03, "total_return": -0.01, "exposure_days": 0.1, "n_trades": 25,
         "bh_total_return": -0.35, "bh_max_dd": 0.45}
    v.update(over)
    return v


STRESS = {"sharpe": -0.8, "max_dd": 0.04, "total_return": -0.02}
DEV = {"exposure_days": 0.6}


def test_g6_variant_off_by_default_no_decision_change():
    ok, d = G.check_g6(_val(), STRESS, 0.05, dev_metrics=DEV)
    assert not ok and d["path"] is None
    assert d["exposure_aware"]["enabled"] is False and d["exposure_aware"]["status"] == "flat_pass"


def test_g6_variant_flat_path_and_insufficient_evidence():
    on = _cfg(True)
    ok, d = G.check_g6(_val(), STRESS, 0.05, on, dev_metrics=DEV)
    assert ok and d["path"] == "flat" and d["base_passed"] is False
    ok, d = G.check_g6(_val(n_trades=3), STRESS, 0.05, on, dev_metrics=DEV)
    assert not ok and d["exposure_aware"]["status"] == "insufficient_evidence"
    ok, d = G.check_g6(_val(n_trades=0, total_return=0.0, max_dd=0.0), STRESS, 0.0, on, dev_metrics=DEV)
    assert not ok and d["exposure_aware"]["status"] == "insufficient_evidence"   # 전혀 안 거래 = 증거 부족
    for bad in (dict(bh_total_return=0.2), dict(total_return=-0.05), dict(exposure_days=0.6),
                dict(max_dd=0.3), dict(bh_max_dd=0.04)):
        assert not G.check_g6(_val(**bad), STRESS, 0.05, on, dev_metrics=DEV)[0], bad
    assert not G.check_g6(_val(), {**STRESS, "total_return": -0.1}, 0.05, on, dev_metrics=DEV)[0]
    assert not G.check_g6(_val(), STRESS, 0.5, on, dev_metrics=DEV)[0]               # MC MDD95 그대로
    assert not G.check_g6(_val(), STRESS, 0.05, on, dev_metrics={"exposure_days": 0.05})[0]   # Dev 에서 거의 쉼
    assert not G.check_g6(_val(), STRESS, 0.05, on, dev_metrics=None)[0]
    # 기존 통과는 그대로 통과(경로 sharpe)
    ok, d = G.check_g6(_val(sharpe=1.5, total_return=0.05), {**STRESS, "sharpe": 0.5}, 0.05, on, dev_metrics=DEV)
    assert ok and d["path"] == "sharpe"


# ================================================================ 파이프라인: Val 재사용 = 이전 결정 (구조)
@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_research_synth import make_root, template_dict

    from btc_quant.research.campaign import CampaignTemplate, ResearchEnv, run_campaign
    env = ResearchEnv.at(make_root(tmp_path_factory.mktemp("ea"), planted=True, seed=7))
    cr = run_campaign(CampaignTemplate.from_dict(template_dict(name="ea", filters=[[]])), env=env)
    return env, cr


def test_val_reuse_returns_prior_decision_and_cannot_be_rescued(planted):
    """이전 Val 사용이 실패였으면(Holm 은 통과했더라도) 같은 logic_hash 는 나중 기준(완화·노출 인식 변형 ON)으로도
    같은 Val 에서 통과할 수 없다 — anti-goalpost (camp-000013 유형의 사후 구제 차단)."""
    from btc_quant.research.pipeline import Pipeline
    env, cr = planted
    lh = cr.ranked()["logic_hash"].iloc[0]
    spec = cr.specs[lh]
    for enabled in (False, True):
        cfg = _cfg(enabled)
        p = Pipeline(cr, env, gate_cfg=cfg, register=False, reference_check=False, robustness=False, lessons=False)
        m1v, vs, ve = p.load_val()
        vid = p.val_id(vs, ve)
        if env.ledger.val_prior(vid, lh) is None:          # 이전 사용: 검사 실패(통과 아님), Holm 은 통과로 기록
            op = env.ledger.open_val_evaluation(lh, cr.campaign_id, vid, 20)
            env.ledger.record_val_result(op["id"], False, 0.001, {}, {"holm_passed": True})
        g, *_ = p.g6(spec, m1v, vs, ve, cr.template.costs["primary"], cr.template.costs["stress"],
                     record=False, dev_metrics={"exposure_days": 0.9})
        assert g["val_budget"]["reused"] and g["val_budget"]["prior_passed"] is False
        assert g["passed"] is False and g["val_reuse_prior_passed"] is False
        assert len(env.ledger.val_history(vid)) == 1          # 새 사용으로 세지 않음


def test_pipeline_records_benchmark_and_shadow_and_redacts_val(planted, tmp_path):
    from btc_quant.research.pipeline import public_pipeline, run_pipeline
    from btc_quant.research.report import render
    env, cr = planted
    cfg = _cfg(False)
    cfg["G2"]["null_runs"] = 100                     # 테스트 속도 (귀무 100회로 G2 판정 허용)
    out = run_pipeline(cr, env=env, top_k=1, n_null=100, gate_cfg=cfg, register=False, reference_check=False)
    c = out["candidates"][0]
    assert c["dev_passed"], c["first_failed"]          # 심은 시장: Dev 생존 → G6(Val) 평가됨
    assert c["metrics"]["bh_total_return"] is not None and "exposure_days" in c["metrics"]
    assert c["gates"]["G4"]["exposure_aware"]["enabled"] is False
    man = json.loads((cr.dir / "manifest.json").read_text(encoding="utf-8"))
    assert man["benchmark"]["bh_total_return"] is not None
    assert c["gates"]["G6"]["benchmark_val"]["bh_total_return"] is not None
    pub = public_pipeline(out)["candidates"][0]["gates"]["G6"]
    assert "benchmark_val" not in pub and "exposure_aware" not in pub and "path" not in pub
    human = render(cr.campaign_id, env=env, audience="human")
    agent = render(cr.campaign_id, env=env, audience="agent")
    assert "**Val** — HUMAN-ONLY" in human and "**Val** — HUMAN-ONLY" not in agent
    assert "Buy & hold reference (Dev" in agent
    # 원장 Dev 실행 기록에는 B&H 자체 값(캠페인 상수)이 없고 전략 노출 값은 있다
    run = next(r for r in env.ledger.records("runs") if r["campaign_id"] == cr.campaign_id)
    assert "exposure_days" in run["metrics"] and "bh_total_return" not in run["metrics"]
    # Val 실행 기록: B&H 낙폭 구간 날짜(구조 누설) 없음
    for r in env.ledger.records("runs"):
        if (r.get("period") or {}).get("segment") == "val":
            assert not any(k.startswith("bh_dd") and k.endswith(("_start", "_end")) for k in r["metrics"])


def test_mcp_campaign_report_has_dev_benchmark_only(planted):
    from btc_quant.mcp import ResearchTools
    env, cr = planted
    t = ResearchTools(env.root)
    rep = t.get_campaign_report(cr.campaign_id)
    assert rep["ok"]
    if rep.get("pipeline"):
        c = rep["pipeline"]["candidates"][0]
        assert c["benchmark_dev"] and "bh_total_return" in c["benchmark_dev"]
        assert c["G6_outcome"] in ("pass", "fail", "budget_exhausted", "engine_mismatch", "not evaluated")
        blob = json.dumps(rep)
        assert "benchmark_val" not in blob and "flat_pass" not in blob and "insufficient_evidence" not in blob


# ================================================================ 탐색 시도 기록
def _expl_env(tmp_path):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_research_synth import SPLITS

    from btc_quant.research.campaign import ResearchEnv
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "splits.yaml").write_text(yaml.safe_dump(SPLITS))
    return ResearchEnv.at(tmp_path)


def test_log_exploration_counts_trials_and_refuses_val(tmp_path):
    from btc_quant.research.exploration import ExplorationError, log_exploration
    env = _expl_env(tmp_path)
    assert env.ledger.trials_count(family="donchian_breakout") == 0
    out = log_exploration("donchian_breakout", "per-year event study of donchian(48) on dev", 5, env=env)
    assert len(out["run_ids"]) == 5 and out["family_trials_total"] == 5
    assert env.ledger.trials_count(family="donchian_breakout") == 5
    camp = env.ledger.get("campaigns", out["campaign_id"])
    assert camp["grid"]["kind"] == "exploration" and camp["family"] == "donchian_breakout"
    log_exploration("donchian_breakout", "same study again with two more lookbacks", 2, env=env)
    assert env.ledger.trials_count(family="donchian_breakout") == 7
    assert out["weekly_budget"]["used_7d"] == 5
    for seg in ("val", "lockbox", "forward"):
        with pytest.raises(ExplorationError):
            log_exploration("donchian_breakout", "peeking at the validation segment", 1, segment=seg, env=env)
    with pytest.raises(ExplorationError):
        log_exploration("donchian_breakout", "x", 1, env=env)
    with pytest.raises(ExplorationError):
        log_exploration("bad family!", "per-year event study", 1, env=env)


def test_log_exploration_mixed_with_strategy_runs_is_countable(tmp_path):
    """exploration runs(spec 없음) 와 일반 runs 가 섞여도 duckdb trials_count 가 동작."""
    from btc_quant.registry.ledger import Ledger
    from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec
    led = Ledger(tmp_path)
    h = led.register_hypothesis("t", "r", "s")
    c = led.create_campaign(h, {"x": [1, 2]}, 2, ["coinone_conservative"], {"segment": "dev"}, family="f")
    sp = StrategySpec("a", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(1.0, 1.2, 60))
    led.record_runs(c, [(sp, "coinone_conservative", {"sharpe": 1.0, "n_trades": 3})], {"segment": "dev"},
                    "snap", "commit", "f")
    led.record_exploration("f", "side calc", 3, {"segment": "dev"}, labels=["a", "b", "c"])
    assert led.trials_count(family="f") == 4 and led.trials_count() == 4


def test_mcp_log_exploration_tool(tmp_path):
    from btc_quant.mcp import TOOL_NAMES, ResearchTools
    assert "log_exploration" in TOOL_NAMES
    env = _expl_env(tmp_path)
    t = ResearchTools(env.root)
    r = t.log_exploration("rsi_oversold_bounce", "per-regime split of rsi event study on dev", 4)
    assert r["ok"] and r["configs_evaluated"] == 4 and r["family_trials_total"] == 4
    r = t.log_exploration("rsi_oversold_bounce", "looked at val", 1, segment="val")
    assert not r["ok"] and r["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize("cmd", [
    "python -c 'from btc_quant.research.exploration import log_exploration; log_exploration(\"f\",\"d\",3)'",
    "python -c 'import btc_quant.research.exploration as E; E.log_exploration(\"f\",\"d\",3)'",
    "python -c 'from btc_quant.registry.ledger import Ledger; Ledger().record_exploration(\"f\",\"d\",3,{})'",
])
def test_guard_blocks_direct_exploration_writes_in_research_mode(tmp_path, cmd):
    hook = ROOT / ".claude" / "hooks" / "guard.py"

    def run(research: bool):                       # tests/test_guard_hook.py 와 같은 방식 (로그는 tmp 아래)
        import os
        env = {k: v for k, v in os.environ.items() if k != "BTCQ_RESEARCH_MODE"}
        env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
        if research:
            env["BTCQ_RESEARCH_MODE"] = "1"
        p = subprocess.run([sys.executable, str(hook)], input=json.dumps(
            {"session_id": "s1", "cwd": str(tmp_path), "hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": cmd}, "tool_use_id": "t"}), capture_output=True, text=True, env=env, timeout=30)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout) if p.stdout.strip() else None
    out = run(True)
    assert out and out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert run(False) is None


def test_guard_allows_mcp_log_exploration_name():
    sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
    import guard as g
    assert g.check_mcp_name("mcp__btcq__log_exploration") is None


# ================================================================ 합성 국면 시장
def test_regime_market_structure():
    from btc_quant.research import calibration_study as CS
    s = CS.regime_schedule("2021-01-01", "2024-01-01", "2024-07-01", seed=5)
    assert set(s.unique()) <= set(CS.REGIMES)
    assert (s[s.index >= pd.Timestamp("2024-01-01", tz="UTC")] == CS.REGIMES[5 % 3]).all()
    # 국면 길이는 REGIME_DAYS 범위 (마지막 Dev 구간은 잘릴 수 있음)
    dev = s[s.index < pd.Timestamp("2024-01-01", tz="UTC")]
    runs = (dev != dev.shift()).cumsum()
    lens = dev.groupby(runs).agg(["first", "size"])
    for _, (r, n) in lens.iloc[:-1].iterrows():
        lo, hi = CS.REGIME_DAYS[r]
        assert lo <= n <= hi
    df, sched = CS.gen_regime_market("2023-01-01", "2023-05-01", "2023-07-01", seed=3, val_regime="bear")
    assert (sched.iloc[-30:] == "bear").all() and len(df) == 181 * 1440
    # 같은 seed·k=0 → 같은 시장 (재현)
    df2, _ = CS.gen_regime_market("2023-01-01", "2023-05-01", "2023-07-01", seed=3, val_regime="bear")
    assert np.array_equal(df["close"].to_numpy(), df2["close"].to_numpy())


def test_regime_study_quick_runs_both_variants():
    from btc_quant.research import calibration_study as CS
    cfg = CS.regime_quick_config()
    cfg.n_markets = {"null": 1, "regime_cond": 0, "regime_filter": 1}
    res = CS.run_regime_study(cfg, progress=lambda s: None,
                              edge={"k_for": {"null": 0.0, "regime_filter": 0.05}, "ks": [], "kinds": {}})
    assert {r["variant"] for r in res["runs"]} == {"current", "variant"}
    assert len(res["runs"]) == 4 and all("star_g6_path" in r for r in res["runs"])
    md = CS.regime_tables_markdown(res)
    assert "current" in md and "variant" in md
