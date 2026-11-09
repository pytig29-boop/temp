"""params.preferences — 선택지 표 상식 검사 (합성 일간 수익)."""
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from btc_quant.params import costs as C
from btc_quant.params import preferences as PR
from btc_quant.params import proposals as P

ROOT = Path(__file__).resolve().parents[1]


def daily(mu=0.001, sd=0.015, n=730, seed=0):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mu, sd, n), index=pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"))


def test_option_table_sanity():
    r = daily()
    trades = np.random.default_rng(1).normal(0.0008, 0.01, 600)
    t = PR.option_tables(r, trades, capital_krw=10_000_000, n_sims=2000)
    dd = t.drawdown
    p = [d["p_halt_1y"] for d in dd]
    assert all(a >= b - 1e-12 for a, b in zip(p, p[1:]))            # 느슨할수록 정지 확률 ↓
    assert all(d["halts_per_year"] >= d["p_halt_1y"] - 1e-12 for d in dd)
    rec = [d["recover_days_expected"] for d in dd]
    assert all(a < b for a, b in zip(rec, rec[1:]))                  # 깊을수록 회복 오래
    # 한도를 두면 1년 최악 손실(p99)이 '한도 없음' 보다 크지 않다 (정지 시 손실 = 한도 + 하루 갭)
    assert dd[0]["worst_loss_1y_p99"] <= t.unlimited["mdd_p99"] + 1e-9
    for d in dd:
        if d["loss_at_halt_p95"] is not None:
            assert d["loss_at_halt_p95"] >= abs(d["limit"]) - 1e-9
    daily_hits = [d["days_per_year_hist"] for d in t.daily]
    assert all(a >= b for a, b in zip(daily_hits, daily_hits[1:]))
    assert t.formula_drawdown_limit is not None and t.formula_drawdown_limit <= -0.12
    assert len(t.capital) == len(PR.FRACTIONS)
    assert t.capital[-1]["loss99_krw@none"] > t.capital[0]["loss99_krw@none"]
    md = PR.render_markdown(t, "synthetic")
    for h in ("누적 낙폭 정지 한도", "일일 손실 한도", "자본 비중", "사람이 고른다"):
        assert h in md


def test_losing_strategy_never_recovers():
    t = PR.option_tables(daily(mu=-0.001), n_sims=500)
    assert all(np.isinf(d["recover_days_expected"]) for d in t.drawdown)
    assert t.drawdown[0]["p_halt_1y"] > 0.9


def test_too_short_rejected():
    with pytest.raises(ValueError):
        PR.option_tables(daily(n=10))


def test_option_proposal(tmp_path):
    (tmp_path / "config").mkdir()
    for f in ("gates.yaml", "splits.yaml", "exchanges.yaml", "params.yaml"):
        shutil.copy(ROOT / "config" / f, tmp_path / "config" / f)
    C.ensure_costs_file(tmp_path)
    t = PR.option_tables(daily(), n_sims=300)
    pid = PR.write_option_proposal(t, "gates.risk.drawdown_floor", root=tmp_path)
    doc = P.load_proposal(pid, tmp_path)
    assert doc["class"] == 3 and len(doc["options"]) == len(PR.DD_LIMITS)
    assert doc["options"][2]["changes"] == {"gates.risk.drawdown_floor": -0.12}
    pid2 = PR.write_option_proposal(t, "gates.G6.max_mdd", root=tmp_path)    # 크기(양수)로 저장되는 항목
    assert P.load_proposal(pid2, tmp_path)["options"][2]["changes"] == {"gates.G6.max_mdd": 0.12}
    with pytest.raises(ValueError):
        PR.write_option_proposal(t, "gates.G5.min_dsr", root=tmp_path)
