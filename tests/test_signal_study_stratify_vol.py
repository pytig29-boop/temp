"""선별 귀무의 변동성만 맞추는 층 'vol' (R4 단계 1.2) — 벽시계 진입 전용."""
from __future__ import annotations

import glob

import numpy as np
import pandas as pd
import pytest
import yaml

from btc_quant.research import signal_study as S
from btc_quant.research.campaign import CampaignError

KW = {"min_events": 1, "horizons": (60, 120, 240), "primary_horizons": (120,), "min_gap_min": 480}


def clock_market(seed: int, days: int = 60, drift_hours=None, drift_bp_per_min: float = 0.0,
                 vol_hours=None, sig: float = 5e-4):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=days * 1440, freq="1min", tz="UTC")
    sd = np.full(len(idx), sig)
    if vol_hours is not None:
        sd[np.isin(idx.hour, vol_hours)] *= 3.0
    r = rng.normal(0, 1, len(idx)) * sd
    if drift_hours is not None:
        r[np.isin(idx.hour, drift_hours)] += drift_bp_per_min * 1e-4
    c = 1e8 * np.exp(np.cumsum(r))
    o = np.r_[1e8, c[:-1]]
    m1 = pd.DataFrame({"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c, "volume": 1.0},
                      index=idx)
    b1h = idx[::60]
    ent = np.asarray((b1h + pd.Timedelta("1h")).hour == 21)        # clock_entry_utc(21, 0) on 1h bars
    return m1, b1h, ent


def _p(m1, b, ent, strat, n_null=200, seed=0):
    return S.study_events(m1, b, ent, np.ones(len(b), bool), 60, stratify=strat, n_null=n_null, seed=seed, **KW)


def test_a_clock_drift_hour_vol_blind_vol_detects():
    m1, b, ent = clock_market(1, days=90, drift_hours=[21, 22], drift_bp_per_min=0.4)
    hv = _p(m1, b, ent, "hour_vol")
    v = _p(m1, b, ent, "vol")
    assert hv["p_signal"] > 0.05                         # 시각 짝짓기 귀무 = 신호 자체 → 초과 ≈ 0
    assert v["p_signal"] < 0.01


def test_b_null_calibration_vol():
    hits = 0
    for s in range(100):
        m1, b, ent = clock_market(100 + s, days=40)
        hits += _p(m1, b, ent, "vol", n_null=100, seed=s)["p_signal"] <= 0.05
    assert hits <= 10


def test_c_hourly_vol_pattern_zero_mean_not_significant():
    hits = 0
    for s in range(20):
        m1, b, ent = clock_market(500 + s, days=40, vol_hours=[21, 22, 23])
        hits += _p(m1, b, ent, "vol", n_null=100, seed=s)["p_signal"] <= 0.05
    assert hits <= 3


def _tpl(entry, stratify):
    return {"name": "t", "family": "f", "data": {"exchange": "coinone", "timeframe": "1m", "aux": []},
            "universe": ["BTC-KRW"], "bar_minutes": 60, "signals": [{"entry": entry, "filters": [[]]}],
            "study": {"stratify": stratify, "horizons": [60, 120, 240], "primary_horizons": [120]},
            "budget": 1, "hypothesis": {"text": "x", "rationale": "y", "success_criteria": "z"}}


def test_template_rules():
    clock = {"block": "clock_entry_utc", "params": {"hour": 21, "minute": 0}}
    other = {"block": "donchian_breakout", "params": {"lookback": 48}}
    assert S.ScreenTemplate.from_dict(_tpl(clock, "vol")).stratify == "vol"
    for st in ("hour_vol", "hour"):
        with pytest.raises(CampaignError, match="stratify 'vol'"):
            S.ScreenTemplate.from_dict(_tpl(clock, st))
    with pytest.raises(CampaignError, match="only allowed for wall-clock"):
        S.ScreenTemplate.from_dict(_tpl(other, "vol"))


def test_d_existing_templates_parse_unchanged():
    n = 0
    for p in sorted(glob.glob("research/campaign_templates/screens/*.yaml")):
        d = yaml.safe_load(open(p, encoding="utf-8"))
        if d["name"].startswith("r4e_"):
            continue
        try:
            t = S.ScreenTemplate.from_dict(d)
        except CampaignError as e:      # 지금 규칙으로 거부되는 옛 템플릿(예: 4h 무효 블록)은 'vol' 규칙과 무관해야 한다
            assert "stratify" not in str(e)
            continue
        assert t.stratify == (d.get("study") or {}).get("stratify", "hour_vol")
        n += 1
    assert n >= 10
