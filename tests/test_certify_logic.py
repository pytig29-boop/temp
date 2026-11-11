"""인증 판정 논리 (D30): 결선 F1~F5 식, 크기 배수, Clopper–Pearson, 은행 생성기의 귀무성."""
import numpy as np
import pandas as pd
import pytest

from btc_quant.research import certify as K


def _c(**kw):
    c = {"val_sharpe": 1.0, "lb_sharpe": 1.0, "val_p": 0.05, "lb_p": 0.1, "oos_p": 0.02, "dev_max_dd": 0.1,
         "val_mdd": 0.1, "lb_mdd": 0.1, "dev_worst_month": -0.04, "val_worst_month": -0.03, "lb_worst_month": -0.02,
         "full_mc_mdd95": 0.15, "full_sharpe": 2.0, "full_bh_sharpe": 1.0, "full_calmar": 2.0, "full_bh_calmar": 1.0}
    c.update(kw)
    return c


def test_final_flags():
    fin = dict(K.FINAL_DEFAULT)
    f = K.final_flags(_c(), 3.0, fin, 0)
    assert f["final"] and f["scale"] == 1.0
    assert not K.final_flags(_c(lb_sharpe=-0.1), 3.0, fin)["final"]                  # F1a
    assert not K.final_flags(_c(), 0.5, fin)["final"]                                # F1b: 결합 증거 약함
    assert K.final_flags(_c(), 3.0, fin, 0)["F1b"] and not K.final_flags(_c(val_p=0.3, lb_p=0.3), 3.0, fin, 3)["F1b"]
    assert not K.final_flags(_c(oos_p=0.2), 3.0, fin)["final"]                       # F1c 표본 밖 12개월
    f = K.final_flags(_c(dev_worst_month=-0.16), 3.0, fin)                          # 최악 월 −16% → 크기 0.5
    assert f["scale"] == pytest.approx(0.5) and f["F2"]
    assert not K.final_flags(_c(dev_max_dd=0.9), 3.0, fin)["F2"]                     # 0.22 < 0.25
    assert not K.final_flags(_c(full_calmar=0.5), 3.0, fin)["F5"]                    # 보유보다 못함
    assert not K.final_flags(_c(lb_p=None), 3.0, fin)["final"]


def test_clopper():
    assert K.clopper_upper(0, 150) <= 0.02 and K.clopper_upper(3, 400) <= 0.02 < K.clopper_upper(5, 400)


@pytest.mark.parametrize("kind", ["beta", "jump", "stale", "seasonal"])
def test_bank_null_generators_have_no_timing_edge(kind):
    lay = K.C.Layout("2024-01-01", "2024-01-01", "2024-03-01", "2024-03-15", "2024-04-01")
    m = K.gen_bank_market(kind, 3, 0.0, "5m", lay)["BTC-KRW"]
    r = np.log(m["close"]).diff().dropna().to_numpy()
    # 1분 수익의 다음 1분 예측력 ≈ 0 (stale 은 잡음 반전이 있으나 1봉 지연이면 사라짐: 2분 뒤 상관 ≈ 0)
    lag = 2 if kind == "stale" else 1
    assert abs(np.corrcoef(r[:-lag], r[lag:])[0, 1]) < 0.02


def test_vanishing_edge_stitch():
    lay = K.C.Layout("2024-01-01", "2024-01-01", "2024-03-01", "2024-03-15", "2024-04-01")
    m = K.gen_bank_market("vanishing", 3, 0.02, "5m", lay)["BTC-KRW"]
    cut = pd.Timestamp(lay.val_start, tz="UTC")
    a, b = m[m.index < cut], m[m.index >= cut]
    assert len(a) and len(b) and abs(b["open"].iloc[0] / a["close"].iloc[-1] - 1) < 0.01
