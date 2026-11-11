"""레드팀 빠른 각본 (§4-F): rename·reuse·shop 공격이 막혀야 한다."""
from btc_quant.research import redteam as RT


def test_quick_attacks_blocked(tmp_path):
    assert RT.attack_rename(tmp_path) is None
    assert RT.attack_reuse() is None
    assert RT.attack_shop() is None
    assert RT.quick() == []


def test_canonical_hash_equivalence():
    from btc_quant.strategy.spec import canonical_hash_of
    a = {"id": "a", "version": 1, "entry": {"block": "x", "params": {"n": 48, "b": 0.5}}}
    b = {"id": "b", "version": 2, "entry": {"block": "x", "params": {"b": 0.50, "n": 48.0}}}
    c = {"id": "c", "version": 1, "entry": {"block": "x", "params": {"n": 49, "b": 0.5}}}
    assert canonical_hash_of(a) == canonical_hash_of(b) != canonical_hash_of(c)
