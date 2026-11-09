"""evaluation.metrics — 구성된 시계열로 알려진 값 검증."""
import numpy as np
import pandas as pd
import pytest

from btc_quant.backtest.types import COINONE_CONSERVATIVE, TRADE_COLUMNS, BacktestResult
from btc_quant.evaluation import metrics as m


def days(n, start="2024-01-01"):
    return pd.date_range(start, periods=n, freq="D", tz="UTC")


def test_sharpe_known_value():
    x = np.array([0.01, -0.01] * 50) + 0.001
    exp = 0.001 / x.std(ddof=1) * np.sqrt(365)
    assert m.sharpe(x) == pytest.approx(exp)
    assert m.sharpe(np.zeros(10)) == 0.0 and m.sharpe([0.01]) == 0.0


def test_weighted_sharpe_uniform_limit_and_neff():
    rng = np.random.default_rng(1)
    s = pd.Series(rng.normal(0.001, 0.01, 500), index=days(500))
    w = m.weighted_sharpe(s, half_life_days=1e12)
    assert w.sharpe == pytest.approx(m.sharpe(s), rel=1e-6)
    assert w.n_eff == pytest.approx(500, rel=1e-6)
    # 반감기 1년, 1760일: Kish n_eff 해석해와 일치
    s2 = pd.Series(0.0, index=days(1760))
    r = 0.5 ** (1 / 365)
    w_ = r ** np.arange(1760)
    assert m.weighted_sharpe(s2, 365).n_eff == pytest.approx(w_.sum() ** 2 / (w_ ** 2).sum())


def test_weighted_sharpe_emphasizes_recent():
    # 앞 절반 손실, 뒤 절반 이익 → 가중 Sharpe > 비가중
    rng = np.random.default_rng(2)
    x = np.concatenate([rng.normal(-0.002, 0.01, 730), rng.normal(0.002, 0.01, 730)])
    s = pd.Series(x, index=days(1460))
    assert m.weighted_sharpe(s, 365).sharpe > m.sharpe(s) + 0.3
    # 가중치: 365일 전 = 1/2
    w = m.exp_weights(s.index, 365)
    assert w[-1] == 1.0 and w[-366] == pytest.approx(0.5)


def test_max_drawdown_and_duration():
    r = pd.Series([0.1, -0.5, 0.0, 0.2, 1.0, 0.0])
    mdd, dur = m.max_drawdown(r)
    assert mdd == pytest.approx(0.5)
    # 고점 1.1 → 0.55,0.55,0.66 수중 3일, 이후 1.32 신고점
    assert dur == 3
    assert m.max_drawdown([-0.1])[0] == pytest.approx(0.1)  # 시작 자본이 고점


def test_trade_stats():
    tr = pd.DataFrame({"net_ret": [0.02, -0.01, 0.01, -0.01], "hold_min": [10, 20, 30, 40]})
    st = m.trade_stats(tr, n_days=2)
    assert st["n_trades"] == 4 and st["win_rate"] == 0.5
    assert st["expectancy"] == pytest.approx(0.0025)
    assert st["profit_factor"] == pytest.approx(1.5)
    assert st["avg_hold_min"] == 25 and st["trades_per_day"] == 2


def test_rolling_sharpe_anchored_at_end():
    s = pd.Series(np.r_[np.full(100, -0.01), np.tile([0.02, 0.0], 200)], index=days(500))
    s.iloc[::7] -= 0.001  # 분산 확보
    r = m.rolling_sharpe(s, 182, 30)
    assert r.index[-1] == s.index[-1]
    assert len(r) == (500 - 182) // 30 + 1
    frac, k = m.rolling_positive_fraction(s)
    assert k == len(r) and 0 < frac <= 1
    assert m.rolling_positive_fraction(s.iloc[:100]) == (0.0, 0)


def _result(daily, trades):
    return BacktestResult("x", "h", COINONE_CONSERVATIVE, daily.index[0], daily.index[-1],
                          trades, daily)


def test_summarize_and_yearly():
    idx = days(730, "2024-01-01")
    rng = np.random.default_rng(3)
    daily = pd.Series(rng.normal(0.001, 0.01, 730), index=idx)
    et = idx[::5]
    trades = pd.DataFrame({c: 0 for c in TRADE_COLUMNS}, index=range(len(et)))
    trades["exit_time"] = et
    trades["entry_time"] = et
    trades["net_ret"] = rng.normal(0.001, 0.01, len(et))
    trades["hold_min"] = 60
    out = m.summarize(_result(daily, trades))
    assert out["n_days"] == 730 and out["n_trades"] == len(et)
    assert out["recent_n_trades"] == ((et > idx[-1] - pd.Timedelta(days=365))).sum()
    assert out["sharpe"] == pytest.approx(m.sharpe(daily))
    assert out["sharpe_w"] == pytest.approx(m.weighted_sharpe(daily, 365).sharpe)
    assert "sharpe_year_2024" in out and "sharpe_year_2025" in out
    yt = m.yearly_table(daily, trades)
    assert list(yt.index) == [2024, 2025] and yt["n_trades"].sum() == len(et)
    assert all(np.isscalar(v) for v in out.values())
