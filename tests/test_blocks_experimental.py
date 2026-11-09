"""실험 블록 등급 (PLAN D11) + 자동 수용 검사(acceptance_check) 테스트.

이 파일이 **모든 실험 블록에 대해 acceptance_check 통과를 강제**한다 — 통과 = 사람 없이 실험 등급으로 사용 가능.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btc_quant.strategy import blocks as B
from btc_quant.strategy.blocks import REGISTRY, BlockDef, Context, PromotionError, Registry
from btc_quant.strategy.blocks.acceptance import acceptance_check
from btc_quant.strategy.blocks.momentum import as_signal
from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec

EXPERIMENTAL = REGISTRY.experimental()
PROV = dict(author="claude (AI)", created="2026-11-09", rationale="toy block for tests")


# ------------------------------------------------------------------ 실제 실험 블록 수용
def test_experimental_package_autoimported():
    assert "cross_venue" in B.experimental.MODULES
    names = {d.name for d in EXPERIMENTAL}
    assert {"upbit_lead_impulse", "venue_gap_revert", "venue_gap_closed", "upbit_not_dumping"} <= names


@pytest.mark.parametrize("d", EXPERIMENTAL, ids=[d.name for d in EXPERIMENTAL])
def test_every_experimental_block_passes_acceptance(d):
    rep = acceptance_check(d.name)
    assert rep.passed, str(rep)
    assert rep.status == "experimental"
    names = {c.name for c in rep.checks}
    assert {"causality_future", "causality_trunc", "causality_future_1m", "causality_trunc_1m", "output_types",
            "non_degenerate", "docs", "metadata", "front_window"} <= names


def test_official_block_passes_acceptance_too():
    rep = acceptance_check("donchian_breakout")
    assert rep.passed, str(rep)
    assert rep.to_dict()["passed"] and "PASS" in str(rep)


# ------------------------------------------------------------------ 등급 API
def _spec(entry: str, filters=(), exit_sig=None) -> StrategySpec:
    return StrategySpec("t", BlockRef(entry, {}), ExitSpec(1, 1, 60, BlockRef(exit_sig, {}) if exit_sig else None),
                        tuple(BlockRef(f, {}) for f in filters))


def test_status_queries():
    assert REGISTRY.is_experimental("venue_gap_revert")
    assert REGISTRY.is_experimental(BlockRef("venue_gap_revert", {}))
    assert not REGISTRY.is_experimental("ema_slope")
    assert B.is_experimental("upbit_not_dumping")
    assert REGISTRY.spec_uses_experimental(_spec("ema_slope", ["session_kst"], "ema_cross_down")) == []
    s = _spec("ema_slope", ["session_kst", "upbit_not_dumping"], "venue_gap_closed")
    assert REGISTRY.spec_uses_experimental(s) == ["upbit_not_dumping", "venue_gap_closed"]
    assert B.spec_uses_experimental(_spec("venue_gap_revert")) == ["venue_gap_revert"]
    # all() 기본 = 공식만 (하위 호환), 명시하면 포함
    assert not any(d.experimental for d in REGISTRY.all())
    assert {d.name for d in REGISTRY.experimental()} <= {d.name for d in REGISTRY.all(include_experimental=True)}
    assert REGISTRY.all("filter", include_experimental=True) != REGISTRY.all("filter")


def test_check_and_evaluate_allow_experimental_by_default():
    ref = BlockRef("venue_gap_revert", {"window": 60})
    REGISTRY.check(ref, "entry")
    with pytest.raises(ValueError, match="experimental"):
        REGISTRY.check(ref, "entry", allow_experimental=False)
    REGISTRY.check(BlockRef("ema_slope", {}), "entry", allow_experimental=False)
    _spec("venue_gap_revert").validate(REGISTRY)


def test_promote_is_a_human_code_change():
    with pytest.raises(PromotionError, match="cross_venue") as e:
        REGISTRY.promote("venue_gap_revert")
    assert "register_experimental" in str(e.value) and "docs/BLOCKS.md" in str(e.value)
    assert REGISTRY.is_experimental("venue_gap_revert")          # 런타임 상태는 변하지 않음
    with pytest.raises(PromotionError, match="already official"):
        REGISTRY.promote("ema_slope")


def test_blockdef_backward_compatible():
    fn = lambda bars, ctx: bars["close"] > 0  # noqa: E731
    d = BlockDef("x", "entry", fn, {"a": [1, 2, 3]}, "desc", (), True)
    assert d.status == "official" and not d.experimental and d.lookback_bars is None
    reg = Registry()
    reg.register("entry", grid={"a": [1, 2, 3]}, description="d")(lambda bars, ctx, a=1: bars["close"] > a)
    assert reg.all()[0].status == "official"
    assert Context().bar is None and Context(aux={}).aux == {}


def test_register_validation():
    reg = Registry()
    with pytest.raises(ValueError, match="provenance"):
        reg.register_experimental("entry", {"a": [1, 2, 3]}, "d", author="", created="2026-11-09", rationale="r")
    with pytest.raises(ValueError, match="live_ready"):
        reg.register("filter", {"a": [1, 2, 3]}, "d", requires=("binance",), live_ready=True)
    with pytest.raises(ValueError, match="status"):
        reg.register("entry", {"a": [1, 2, 3]}, "d", status="beta")
    reg.register_experimental("entry", {"a": [1, 2, 3]}, "d", **PROV, name="t")(lambda bars, ctx, a=1: bars["close"] > a)
    with pytest.raises(ValueError, match="duplicate"):
        reg.register_experimental("entry", {"a": [1, 2, 3]}, "d", **PROV, name="t")(lambda b, c, a=1: b["close"] > a)


# ------------------------------------------------------------------ 수용 검사: 장난감 블록
@pytest.fixture()
def toy(tmp_path):
    """비공개 레지스트리 + 임시 문서 (전역 REGISTRY 오염 금지)."""
    reg = Registry()

    @reg.register_experimental("entry", grid={"k": [1, 2, 3]}, description="close rose over k bars", **PROV,
                               lookback_bars=lambda p: int(p["k"]) + 1)
    def toy_good(bars, ctx, k: int = 1):
        return as_signal(bars["close"] > bars["close"].shift(k), bars.index)

    @reg.register_experimental("entry", grid={"k": [1, 2, 3]}, description="LEAK: next close higher", **PROV)
    def toy_leak_shift(bars, ctx, k: int = 1):
        return as_signal(bars["close"].shift(-k) > bars["close"], bars.index)

    @reg.register_experimental("entry", grid={"z": [0.5, 1.0, 1.5]}, description="LEAK: full-sample z", **PROV)
    def toy_leak_fullsample(bars, ctx, z: float = 1.0):
        c = np.log(bars["close"])
        return as_signal((c - c.mean()) / c.std() <= -z, bars.index)

    @reg.register_experimental("entry", grid={"k": [1, 2, 3]}, description="returns NaN", **PROV)
    def toy_nan(bars, ctx, k: int = 1):
        return (bars["close"] > bars["close"].shift(k)).astype(float).where(bars.index.minute != 0)

    @reg.register_experimental("filter", grid={"k": [1, 2, 3]}, description="always true", **PROV)
    def toy_constant(bars, ctx, k: int = 1):
        return pd.Series(True, index=bars.index)

    @reg.register_experimental("entry", grid={"k": [1, 2, 3]}, description="lies about lookback", **PROV,
                               lookback_bars=lambda p: 1)
    def toy_bad_lookback(bars, ctx, k: int = 1):
        return as_signal(bars["close"] > bars["close"].rolling(50).mean(), bars.index)

    docs = tmp_path / "BLOCKS.md"
    docs.write_text("\n".join(f"| `{n}` | entry | x | x | x | ✓ | experimental |"
                              for n in ("toy_good", "toy_leak_shift", "toy_leak_fullsample", "toy_nan",
                                        "toy_constant", "toy_bad_lookback")) + "\n", encoding="utf-8")
    return reg, docs


def test_good_toy_passes(toy):
    reg, docs = toy
    rep = acceptance_check("toy_good", registry=reg, docs_path=docs)
    assert rep.passed, str(rep)


def test_lookahead_toy_fails(toy):
    reg, docs = toy
    rep = acceptance_check("toy_leak_shift", registry=reg, docs_path=docs)
    assert not rep.passed
    assert {"causality_future", "causality_trunc", "causality_future_1m", "causality_trunc_1m"} <= set(rep.failed)


def test_full_sample_normalisation_toy_fails(toy):
    reg, docs = toy
    rep = acceptance_check("toy_leak_fullsample", registry=reg, docs_path=docs)
    assert not rep.passed and "causality_trunc" in rep.failed and "causality_future" in rep.failed


def test_type_degenerate_and_lookback_failures(toy):
    reg, docs = toy
    assert "output_types" in acceptance_check("toy_nan", registry=reg, docs_path=docs).failed
    assert acceptance_check("toy_constant", registry=reg, docs_path=docs).failed == ["non_degenerate",
                                                                                      "non_degenerate_1m"]
    assert acceptance_check("toy_bad_lookback", registry=reg, docs_path=docs).failed == ["front_window"]


def test_docs_presence_required(toy, tmp_path):
    reg, _ = toy
    empty = tmp_path / "EMPTY.md"
    empty.write_text("# nothing\n", encoding="utf-8")
    assert acceptance_check("toy_good", registry=reg, docs_path=empty).failed == ["docs"]
    official_row = tmp_path / "OFF.md"
    official_row.write_text("| `toy_good` | entry | x | x | x | ✓ | official |\n", encoding="utf-8")
    assert acceptance_check("toy_good", registry=reg, docs_path=official_row).failed == ["docs"]
    assert acceptance_check("toy_good", registry=reg, docs_path=tmp_path / "missing.md").failed == ["docs"]


def test_unknown_block_fails():
    rep = acceptance_check("no_such_block")
    assert not rep.passed and rep.failed == ["registered"]
