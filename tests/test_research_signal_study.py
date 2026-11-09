"""신호 선별 이벤트 스터디 (research/signal_study.py) — 합성 데이터."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from test_research_synth import make_root

from btc_quant.registry.ledger import Ledger
from btc_quant.research import signal_study as S
from btc_quant.research.campaign import CampaignBudgetError, CampaignError, ResearchEnv
from btc_quant.strategy.spec import BlockRef


def market(seed: int, days: int = 40, planted: bool = True, drift_bp: float = 30.0, hold: int = 60,
           p: float = 0.02, sig: float = 5e-4, hour_drift: int | None = None):
    """1분 랜덤워크 + 5분 결정 봉 진입 배열. planted: 진입 결정 시각부터 hold 분 동안 총 drift_bp 상승.
    진입은 ≥ 12봉(60분) 간격으로만 생성. hour_drift: 그 KST 시에는 진입과 무관하게 매분 드리프트."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=days * 1440, freq="1min", tz="UTC")
    n = len(idx)
    b5 = idx[::5]
    raw = np.flatnonzero(rng.random(len(b5)) < p)
    keep, last = [], -99
    for q in raw:
        if q - last >= 12:
            keep.append(q)
            last = q
    ent = np.zeros(len(b5), bool)
    ent[keep] = True
    r = rng.normal(0, sig, n)
    if planted:
        for q in keep:
            t = q * 5 + 5
            r[t:t + hold] += drift_bp * 1e-4 / hold
    if hour_drift is not None:
        r[idx.tz_convert("Asia/Seoul").hour == hour_drift] += 2e-4
    c = 1e8 * np.exp(np.cumsum(r))
    o = np.r_[1e8, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, sig * 0.3, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, sig * 0.3, n)))
    m1 = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 1.0}, index=idx)
    return m1, b5, ent


# ------------------------------------------------------------------ 구성 요소
def test_decluster_min_gap():
    t = (np.arange(0, 200, 5) * 60e9).astype(np.int64)        # 5분마다 트리거 (상태형)
    keep = S.decluster(t, 60)
    kept = t[keep] / 60e9
    assert kept[0] == 0 and np.all(np.diff(kept) >= 60) and np.all(np.diff(kept) == 60)
    assert S.decluster(t, 0).all()
    assert S.decluster(np.array([0, 59, 60, 61, 130], dtype=np.int64) * 60_000_000_000, 60).tolist() == \
        [True, False, True, False, True]


def test_decluster_in_study_counts_runs_once():
    m1, b5, _ = market(3, days=5, planted=False)
    ent = np.zeros(len(b5), bool)
    ent[100:136] = True                    # 36봉(180분) 연속 True → 간격 60분이면 3개
    ent[500] = True
    st = S.study_events(m1, b5, ent, np.ones(len(b5), bool), 5, n_null=20, min_events=1)
    assert st["n_raw_triggers"] == 37 and st["n_events"] == 4


def test_hac_se_matches_iid_without_overlap_and_grows_with_overlap():
    rng = np.random.default_rng(0)
    x = rng.normal(size=400)
    t = np.arange(400) * 1000.0
    iid = x.std(ddof=1) / np.sqrt(400)
    assert S.hac_se_mean(x, t, 60) == pytest.approx(iid, rel=1e-9)
    # 같은 값이 겹친 창에서 두 번 관측(시각 차 10분, 대역폭 60) → 분산이 커진다
    x2 = np.repeat(x[:200], 2)
    t2 = np.repeat(np.arange(200) * 1000.0, 2) + np.tile([0, 10.0], 200)
    assert S.hac_se_mean(x2, t2, 60) > 1.25 * x2.std(ddof=1) / np.sqrt(400)


def test_stratified_draws_exact_composition():
    rng = np.random.default_rng(1)
    pool = rng.integers(0, 6, 5000)
    ev_idx = rng.choice(np.flatnonzero(pool < 3), 120, replace=False)      # 층 0~2 에만 이벤트
    ev_strata = pool[ev_idx]
    d = S.stratified_draws(pool, ev_strata, 50, rng)
    assert d.shape == (50, 120)
    want = np.bincount(ev_strata, minlength=6)
    for row in d:
        assert np.array_equal(np.bincount(pool[row], minlength=6), want)
    small = np.array([0, 0, 0, 1, 1])                                     # 비복원 경로
    d2 = S.stratified_draws(small, np.array([0, 0, 1]), 30, rng)
    for row in d2:
        assert len(set(row[:2])) == 2 and set(row[:2]) <= {0, 1, 2} and row[2] in (3, 4)


def test_bh_and_holm():
    p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
    assert S.bh_reject(p, 0.05).tolist() == [True, True] + [False] * 8
    assert S.bh_reject([0.01, 0.02, 0.03], 0.05).all()
    assert S.correct([0.01, 0.02, 0.03], "holm", 0.05).tolist() == [True, True, True]
    assert S.correct([0.02, 0.03], "holm", 0.03).tolist() == [False, False]


# ------------------------------------------------------------------ 검출력·오탐
def test_planted_signal_detected_with_horizon_60():
    m1, b5, ent = market(1)
    st = S.study_events(m1, b5, ent, np.ones(len(b5), bool), 5, cost_round_trip={"c": 0.0005},
                        n_null=300, min_events=50)
    assert st["n_events"] > 100 and st["p_signal"] < 1e-6
    assert st["info_horizon"] == 60 and st["profile"] == "peaked"
    assert st["saturation_horizon"] in (30, 60)
    row60 = st["table"][st["horizons"].index(60)]
    assert row60["excess"] * 1e4 == pytest.approx(30 * (1 - 1 / 7), abs=8)   # 귀무도 일부 드리프트 구간 포함
    assert row60["mean_net[c]"] == pytest.approx((1 + row60["mean"]) * (1 - 0.0005) - 1)
    assert row60["mfe_median"] >= row60["median"] and row60["mae_median"] <= 0
    sg = st["suggestion"]
    assert sg["max_hold_min"] == 60 and sg["tp_atr"] > 0 and sg["sl_atr"] > 0


def test_pure_noise_not_significant_after_correction():
    """잡음 신호 3개로 이루어진 선별 × 30 seed: BH(q=0.10) 후 하나라도 유의한 seed ≤ 10%."""
    hits = 0
    for s in range(30):
        m1, b5, _ = market(1000 + s, days=12, planted=False)
        rng = np.random.default_rng(s)
        ps = []
        for k in range(3):
            ent = rng.random(len(b5)) < 0.03
            st = S.study_events(m1, b5, ent, np.ones(len(b5), bool), 5, n_null=100, seed=k, min_events=1)
            ps.append(st["p_signal"])
        hits += bool(S.correct(ps, "bh", 0.10).any())
    assert hits <= 3


def test_hour_stratification_removes_time_of_day_effect():
    """KST 10시 전체에 드리프트가 있고 신호가 10시에만 뜨면: 층화 없는 귀무는 '정보'로 오인, 시간 층화는 아님."""
    m1, b5, _ = market(5, days=60, planted=False, hour_drift=10)
    t_dec = (b5 + pd.Timedelta("5min")).tz_convert("Asia/Seoul")
    rng = np.random.default_rng(2)
    ent = (t_dec.hour == 10) & (t_dec.minute <= 20) & (rng.random(len(b5)) < 0.5)
    kw = {"n_null": 200, "min_events": 1, "horizons": (5, 15, 30), "primary_horizons": (15,), "min_gap_min": 30}
    none = S.study_events(m1, b5, ent, np.ones(len(b5), bool), 5, stratify="none", **kw)
    hour = S.study_events(m1, b5, ent, np.ones(len(b5), bool), 5, stratify="hour", **kw)
    assert none["p_signal"] < 1e-4
    assert hour["p_signal"] > 0.01
    # 귀무 구성: 이벤트 층 = KST 10시 하나
    assert list(hour["strata_events"]) == [10]


def test_filter_and_period_restrict_pool():
    m1, b5, ent = market(4, days=10, planted=False)
    fok = np.zeros(len(b5), bool)
    fok[: len(b5) // 2] = True
    st = S.study_events(m1, b5, ent, fok, 5, n_null=20, min_events=1)
    st2 = S.study_events(m1, b5, ent, np.ones(len(b5), bool), 5, n_null=20, min_events=1,
                         end=b5[len(b5) // 2] + pd.Timedelta("5min"))
    assert st["n_pool"] == st2["n_pool"] == pytest.approx(len(b5) // 2, abs=60)
    assert pd.Timestamp(st["last_event"]) < b5[len(b5) // 2] + pd.Timedelta("5min")


def test_one_minute_decisions_and_entry_reference():
    m1, _, _ = market(6, days=3, planted=False)
    bars = S.decision_bars(m1, 1)
    ent = np.zeros(len(bars), bool)
    ent[[100, 400]] = True
    st = S.study_events(m1, bars.index, ent, bars["complete"].to_numpy(), 1, n_null=10, min_events=1,
                        horizons=(5, 60), primary_horizons=(5,))
    # 결정 봉 100 (1분) → 진입 기준 = 1분봉 101 의 시가, 5분 수익 = close[105]/open[101] − 1
    want = np.mean([m1["close"].iloc[q + 5] / m1["open"].iloc[q + 1] - 1 for q in (100, 400)])
    assert st["table"][0]["mean"] == pytest.approx(want)


def test_study_signal_with_blocks_and_filled_bars():
    m1, _, _ = market(7, days=6, planted=False)
    m1 = m1.drop(m1.index[3000:3030])                       # 결측 → regularize → filled
    st = S.study_signal(m1, BlockRef("random_entry", {"p": 0.05, "seed": 1}),
                        (BlockRef("session_kst", {"session": "all_but_quiet"}),), 5,
                        cost_round_trip="upbit_conservative", n_null=20, min_events=1)
    assert st["n_events"] > 10 and "mean_net[upbit_conservative]" in st["table"][0]
    assert st["entry"]["block"] == "random_entry" and st["filters"][0]["block"] == "session_kst"
    assert st["suggestion"] is None or "atr_pct_median" in st["suggestion"]
    assert S.normalize_costs("upbit_conservative")["upbit_conservative"] == pytest.approx(
        2 * 0.0005 + 2 * 4e-4, rel=0.01)                 # 수수료 5bp + (스프레드/2 1bp + 슬리피지 3bp), 편도×2


# ------------------------------------------------------------------ 선별 캠페인 + 원장
def screen_dict(**over) -> dict:
    d = {
        "name": "synth_screen",
        "hypothesis": {"text": "Donchian breakouts carry ~1h of positive drift",
                       "rationale": "synthetic planted market", "success_criteria": "pass BH q=0.10"},
        "data": {"exchange": "coinone", "symbol": "BTC-KRW", "timeframe": "1m"},
        "bar_minutes": 5,
        "signals": [
            {"entry": {"block": "donchian_breakout", "grid": {"lookback": [48, 288]}}, "filters": [[]]},
            {"entry": {"block": "random_entry", "params": {"p": 0.02, "seed": 3}}, "filters": [[]]},
        ],
        "study": {"horizons": [15, 30, 60, 120, 240], "primary_horizons": [30, 60, 120], "n_null": 100,
                  "min_events": 100},
        "costs": {"primary": "coinone_base", "grade": "upbit_conservative"},
        "budget": 10,
        "family": "breakout_screen",
    }
    d.update(over)
    return d


@pytest.fixture(scope="module")
def planted_root(tmp_path_factory):
    return make_root(tmp_path_factory.mktemp("sig"), planted=True, seed=21)


def test_screen_template_validation():
    with pytest.raises(CampaignError, match="primary_horizons"):
        S.ScreenTemplate.from_dict(screen_dict(study={"horizons": [5, 15, 30, 60], "primary_horizons": [5, 15, 30, 60]}))
    with pytest.raises(CampaignError, match="bar_minutes"):
        S.ScreenTemplate.from_dict(screen_dict(bar_minutes=30))
    with pytest.raises(CampaignError, match="dev segment"):
        S.ScreenTemplate.from_dict(screen_dict(segment="val"))
    tpl = S.ScreenTemplate.from_dict(screen_dict())
    assert tpl.n_configs() == 3
    specs = [tpl.trial_spec(e, fs) for e, fs in tpl.configs()]
    assert len({s.logic_hash() for s in specs}) == 3 and specs[0].exit.max_hold_min == 240


def test_screen_budget_checked_before_ledger_write(tmp_path):
    env = ResearchEnv.at(tmp_path)
    with pytest.raises(CampaignBudgetError):
        S.screen(screen_dict(budget=2), env=env)
    assert Ledger(tmp_path / "research" / "ledger").records("campaigns") == []


def test_screen_records_one_trial_per_signal_and_detects_planted(planted_root):
    env = ResearchEnv.at(planted_root)
    res = S.screen(screen_dict(), env=env)
    t = res.table.set_index("label")
    don = t.loc["donchian_breakout(lookback=48)"]
    assert bool(don["passed"]) and don["info_h"] in (30, 60)
    assert not bool(t.loc["random_entry(p=0.02,seed=3)"]["significant"])
    runs = [r for r in env.ledger.records("runs") if r["campaign_id"] == res.campaign_id]
    assert len(runs) == 3 and {r["family"] for r in runs} == {"breakout_screen"}
    assert all(r["metrics"]["kind"] == S.KIND and "p_signal" in r["metrics"] for r in runs)
    assert env.ledger.trials_count(campaign_id=res.campaign_id) == 3
    camp = env.ledger.get("campaigns", res.campaign_id)
    assert camp["budget"] == 3 and camp["grid"]["kind"] == S.KIND
    assert (res.dir / "signal_screen.md").exists() and "신호 선별" in res.markdown()
    # 두 번째 선별: 같은 계열의 이전 p 들이 다중검정 집합에 포함된다
    res2 = S.screen(screen_dict(signals=[{"entry": {"block": "random_entry", "params": {"p": 0.02, "seed": 4}}}]),
                    env=env, save=False)
    assert res2.decision["m_prior"] == 3 and res2.decision["m_total"] == 4
    assert env.ledger.trials_count(family="breakout_screen") == 4
