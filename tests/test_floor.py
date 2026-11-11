"""바닥 규칙 값 고정 (PLAN D29) — 사용자 결정 값이 바뀌면 실패한다. 이 파일과 config/floor.yaml 은 사용자만 바꾼다."""
from btc_quant.params.floor import load_floor


def test_floor_values_pinned():
    f = load_floor()
    assert f["set_by"] == "user (chat 2026-11-11)"
    assert (f["max_mdd"], f["max_mc_mdd95"], f["min_position_scale"], f["worst_month_min"]) == (0.20, 0.20, 0.25, -0.08)
    assert f["universe"] == ["BTC-KRW", "XRP-KRW", "ETH-KRW"] and f["long_only_spot"] is True
    assert f["primary_cost_roles"] == ["conservative", "stress"]
    assert f["splits_sha256"] == "7d65078dce3573a985d8492533253f9a8bcb9d5afba5ec098a2f7bb1f259e097"
    assert (f["cert_max_fpr"], f["max_loosenings"], f["oos_alpha_max"]) == (0.02, 3, 0.15)
    assert f["verdict_budget_max"] == {"val_new_uses": 10, "lockbox_openings": 5}
    assert (f["lockbox_peek"], f["real_money"], f["ledger_all_trials"]) == ("forbidden", "user_only", True)
    c = f["costs_min_roundtrip_bp"]
    assert len(c) == 18 and c["coinone_conservative"] == 4.0 and c["coinone_stress@XRP-KRW"] == 23.76
    assert c["upbit_conservative"] == 18.0 and c["coinone_conservative@ETH-KRW"] == 8.0
