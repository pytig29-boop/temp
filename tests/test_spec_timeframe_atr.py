"""StrategySpec.timeframe / ExitSpec ATR 모드: 검증, 파라미터 수, 직렬화·해시 하위 호환."""
import json

import pytest

from btc_quant.strategy.spec import (
    DEFAULT_ATR_BARS,
    MAX_PARAMS,
    BlockRef,
    ExitSpec,
    StrategySpec,
    tf_minutes,
)

# 이 기능 추가 전 코드로 계산한 값 (2026-11-09) — 기존 스펙은 해시·dict·YAML 이 바뀌면 안 된다
OLD = [
    (StrategySpec("a", BlockRef("ema_slope", {"span": 24, "min_slope_bp": 2.0}), ExitSpec(0.7, 1.0, 60)),
     "c6dc283ae043433f"),
    (StrategySpec("b", BlockRef("ema_slope", {"span": 48}),
                  ExitSpec(0.5, 1.5, 120, BlockRef("ema_cross_down", {"fast": 8, "slow": 48})),
                  (BlockRef("x", {"k": 1}),), version=3),
     "c0ed12b990316521"),
    (StrategySpec("c", BlockRef("e", {}), ExitSpec(1, 2, 5)), "0870ca160e1fb659"),
]


@pytest.mark.parametrize("spec,h", OLD)
def test_old_specs_hash_unchanged(spec, h):
    assert spec.logic_hash() == h
    d = spec.to_dict()
    assert list(d) == ["id", "entry", "exit", "filters", "version"]
    assert list(d["exit"]) == ["stop_loss_pct", "take_profit_pct", "max_hold_min", "signal_exit"]
    assert StrategySpec.from_dict(d) == spec
    assert StrategySpec.from_dict(json.loads(json.dumps(d))).logic_hash() == h


def test_explicit_5m_equals_default():
    a = StrategySpec("a", BlockRef("e"), ExitSpec(0.7, 1.0, 60))
    b = StrategySpec("a", BlockRef("e"), ExitSpec(0.7, 1.0, 60), timeframe="5m")
    assert a == b and a.logic_hash() == b.logic_hash() and "timeframe" not in b.to_dict()


def test_timeframe_1m_changes_hash_and_roundtrips(tmp_path):
    a = StrategySpec("a", BlockRef("e"), ExitSpec(0.7, 1.0, 60))
    b = StrategySpec("a", BlockRef("e"), ExitSpec(0.7, 1.0, 60), timeframe="1m")
    assert a.logic_hash() != b.logic_hash()
    assert b.to_dict()["timeframe"] == "1m" and b.tf_min == 1 and a.tf_min == 5
    p = tmp_path / "s.yaml"
    p.write_text(b.to_yaml(), encoding="utf-8")
    assert StrategySpec.from_yaml(p) == b


def test_timeframe_validation():
    assert tf_minutes("5m") == 5 and tf_minutes("1m") == 1
    with pytest.raises(ValueError):
        StrategySpec("a", BlockRef("e"), ExitSpec(0.7, 1.0, 60), timeframe="15m").validate()
    # 1m: max_hold_min 은 임의의 양의 정수 분, 5m: 5의 배수
    StrategySpec("a", BlockRef("e"), ExitSpec(0.7, 1.0, 7), timeframe="1m").validate()
    with pytest.raises(ValueError):
        StrategySpec("a", BlockRef("e"), ExitSpec(0.7, 1.0, 7)).validate()
    with pytest.raises(ValueError):
        StrategySpec("a", BlockRef("e"), ExitSpec(0.7, 1.0, 0), timeframe="1m").validate()


def atr_exit(sl=1.5, tp=3.0, hold=60, **kw):
    return ExitSpec(stop_loss_atr=sl, take_profit_atr=tp, max_hold_min=hold, **kw)


def test_atr_exit_serialization_and_hash():
    s = StrategySpec("a", BlockRef("e", {"p": 1}), atr_exit())
    d = s.to_dict()
    assert d["exit"] == {"max_hold_min": 60, "signal_exit": None,
                         "stop_loss_atr": 1.5, "take_profit_atr": 3.0}
    assert StrategySpec.from_dict(d) == s
    s14 = StrategySpec("a", BlockRef("e", {"p": 1}), atr_exit(atr_bars=DEFAULT_ATR_BARS))
    assert s14.logic_hash() == s.logic_hash()
    s20 = StrategySpec("a", BlockRef("e", {"p": 1}), atr_exit(atr_bars=20))
    assert s20.to_dict()["exit"]["atr_bars"] == 20 and s20.logic_hash() != s.logic_hash()
    assert StrategySpec.from_dict(s20.to_dict()) == s20
    pct = StrategySpec("a", BlockRef("e", {"p": 1}), ExitSpec(1.5, 3.0, 60))
    assert pct.logic_hash() != s.logic_hash()
    assert s.exit.mode == "atr" and pct.exit.mode == "pct"
    assert (s.exit.sl, s.exit.tp) == (1.5, 3.0) and (pct.exit.sl, pct.exit.tp) == (1.5, 3.0)


def test_atr_exit_validation_exactly_one_mode():
    StrategySpec("a", BlockRef("e"), atr_exit()).validate()
    bad = [
        ExitSpec(0.7, 1.0, 60, stop_loss_atr=1.0, take_profit_atr=2.0),   # 두 모드 혼합
        ExitSpec(0.7, None, 60, take_profit_atr=2.0),                      # 반반
        ExitSpec(stop_loss_atr=1.0, max_hold_min=60),                      # TP 배수 없음
        atr_exit(sl=0.0),
        atr_exit(tp=-1.0),
        atr_exit(atr_bars=0),
        ExitSpec(0.7, 1.0, 60, atr_bars=20),                               # % 모드에 atr_bars
        ExitSpec(max_hold_min=60),                                         # 모드 없음
        atr_exit(hold=7),                                                  # 5m 배수 아님
    ]
    for ex in bad:
        with pytest.raises(ValueError):
            StrategySpec("a", BlockRef("e"), ex).validate()
    StrategySpec("a", BlockRef("e"), atr_exit(hold=7), timeframe="1m").validate()


def test_param_counting():
    entry = BlockRef("e", {"a": 1, "b": 2, "c": 3, "d": 4})
    f = (BlockRef("f", {"x": 1}),)
    assert StrategySpec("a", entry, ExitSpec(1, 1, 60), f).n_params() == 8
    assert StrategySpec("a", entry, atr_exit(), f).n_params() == 8          # ATR = 3 (기본 창)
    assert StrategySpec("a", entry, atr_exit(atr_bars=14), f).n_params() == 8
    s = StrategySpec("a", entry, atr_exit(atr_bars=20), f)                   # 창 지정 → +1
    assert s.n_params() == 9 > MAX_PARAMS
    with pytest.raises(ValueError, match="params"):
        s.validate()
    StrategySpec("a", entry, atr_exit(atr_bars=20)).validate()              # 8개 이내면 통과
