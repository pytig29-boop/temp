"""단계별 승격 (PLAN D23, 사용자 결정 2026-11-10 Q4).

  T0 연구     — 레지스트리 draft/candidate (자본 0)
  T1 페이퍼   — validated 이후 (= 지금의 validated: Dev 관문 + 견고성 + G6), 자본 0 (페이퍼만)
  T2 소액 실전 — T1 + G8(페이퍼) + 잠금 구간 '모순 없음'(G7 재설계 후) + **사람 승인**: 총자본 × tier_capital.T2 (0.10)
  T3 증액     — T2 + 누적 전진 증거 + **사람 승인**: 총자본 × tier_capital.T3 중 사람이 고른 값 (0.30 → 1.0)

실제 자본 상한(명목) = 총자본 × capital_fraction × position_scale (G6 크기 조절, risk.yaml).

- 승격(T2·T3)은 **사람 전용**: `btc-quant-admin promote-tier` (대화형 확인). 연구 모드 hook 이 admin_cli·이 모듈의
  승격 함수 호출을 막고, MCP 에는 승격 도구가 없다.
- 강등은 자동: 리스크 관리자의 사람-해제 정지(HUMAN 수준: 최대 낙폭·수수료 변경·성능 저하)가 나면 T2/T3 → T1
  (`demote_tier`, actor=risk). 레지스트리 상태(rejected·retired)로 빠지면 단계 기록은 의미를 잃고 T0 로 본다.
- 기록: `strategies/tiers.yaml` (현재 단계·자본 비중; 사람 전용 경로) + 원장 transitions (from_state/to_state =
  'tier:<T>', event='tier'). 실거래 주문 실행기는 없다 — 이 모듈은 정의·강제 장치까지만(실행기는 별도 승인 과제).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .ledger import utcnow
from .strategies import StrategyRegistry, key_of

TIERS = ("T0", "T1", "T2", "T3")
PAPER_STATES = ("validated", "challenger", "review", "active")     # T1 이상이 될 수 있는 레지스트리 상태
# T2 승격 전 레지스트리 상태: G8(페이퍼) 통과 후 사람 검토 대기(review) 또는 이미 운용 중(active)
T2_STATES = ("review", "active")
DEFAULT_TIER_CAPITAL = {"T2": 0.10, "T3": [0.30, 1.0]}


class TierError(RuntimeError):
    pass


def tier_capital(root: str | Path | None = None) -> dict:
    """class ③ `risk_prefs.tier_capital` (config/params.yaml settings). 없으면 사용자 결정 기본값."""
    from btc_quant.params._io import read_yaml
    from btc_quant.params.registry import params_path
    try:
        doc = read_yaml(params_path(root))
    except (FileNotFoundError, OSError):
        return dict(DEFAULT_TIER_CAPITAL)
    tc = ((doc.get("settings") or {}).get("risk_prefs") or {}).get("tier_capital")
    return dict(tc) if tc else dict(DEFAULT_TIER_CAPITAL)


def allowed_fractions(tier: str, root: str | Path | None = None) -> list[float]:
    if tier in ("T0", "T1"):
        return [0.0]
    v = tier_capital(root)[tier]
    return [float(x) for x in (v if isinstance(v, (list, tuple)) else [v])]


def effective_fraction(capital_fraction: float, position_scale: float = 1.0) -> float:
    """실제 자본 비중 상한 = capital_fraction × position_scale (둘 다 [0, 1])."""
    cf, ps = float(capital_fraction), float(position_scale)
    if not (0.0 <= cf <= 1.0 and 0.0 <= ps <= 1.0):
        raise TierError(f"capital_fraction/position_scale must be in [0, 1] (got {cf}, {ps})")
    return cf * ps


def live_notional_cap(total_capital: float, capital_fraction: float, position_scale: float = 1.0) -> float:
    """전략 하나의 실거래 명목 상한(원) = 총자본 × capital_fraction × position_scale. T0/T1 = 0."""
    return float(total_capital) * effective_fraction(capital_fraction, position_scale)


def check_order_notional(notional: float, cap: float) -> None:
    """주문 명목이 단계 상한을 넘으면 거부 (실거래 실행기가 주문 전에 호출해야 하는 강제 장치)."""
    if float(notional) > float(cap) + 1e-9:
        raise TierError(f"order notional {float(notional):,.0f} exceeds tier cap {float(cap):,.0f}")


class TierBook:
    """현재 단계 기록 (strategies/tiers.yaml) + 원장 transitions 이력."""

    def __init__(self, registry: StrategyRegistry):
        self.reg = registry
        self.path = Path(registry.dir) / "tiers.yaml"

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

    def _write(self, doc: dict) -> None:
        tmp = self.path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, strategy_id: str, version: int) -> dict:
        """{tier, capital_fraction, state, since?, by?}. 기록이 없으면 레지스트리 상태로 T0/T1."""
        st = self.reg.state_of(strategy_id, version)
        rec = self._read().get(key_of(strategy_id, version))
        base = "T1" if st in PAPER_STATES else "T0"
        if rec and st in PAPER_STATES and rec.get("tier") in ("T2", "T3"):
            return {**rec, "state": st}
        return {"tier": base, "capital_fraction": 0.0, "state": st}

    def _record(self, strategy_id: str, version: int, new: str, frac: float, actor: str, reason: str,
                evidence: dict | None = None) -> dict:
        k = key_of(strategy_id, version)
        old = self.get(strategy_id, version)
        doc = self._read()
        rec = {"tier": new, "capital_fraction": float(frac), "since": utcnow(), "by": actor, "reason": reason}
        if new in ("T2", "T3"):
            doc[k] = rec
        else:
            doc.pop(k, None)
        self._write(doc)
        self.reg.ledger.record_transition(strategy_id, version, f"tier:{old['tier']}", f"tier:{new}", reason, actor,
                                          event="tier", capital_fraction=float(frac),
                                          previous_capital_fraction=float(old.get("capital_fraction", 0.0)),
                                          evidence=evidence or {})
        return {**rec, "previous": old["tier"]}

    # ------------------------------------------------------------ 사람 전용 (admin_cli promote-tier)
    def promote(self, strategy_id: str, version: int, tier: str, approved_by: str, capital_fraction: float | None = None,
                note: str = "", root: str | Path | None = None, evidence: dict | None = None) -> dict:
        if tier not in ("T2", "T3"):
            raise TierError("promotion target must be T2 or T3")
        if not approved_by or approved_by.lower() in ("ai", "agent", "gate", "risk"):
            raise TierError("tier promotion is human-only")
        with self.reg._lock():
            cur = self.get(strategy_id, version)
            st = cur["state"]
            if tier == "T2":
                if st not in T2_STATES or cur["tier"] != "T1":
                    raise TierError(f"T2 needs a T1 strategy in {T2_STATES} (G8 passed); "
                                    f"{key_of(strategy_id, version)} is {st}/{cur['tier']}")
            else:
                if cur["tier"] not in ("T2", "T3"):
                    raise TierError(f"T3 needs T2 first ({key_of(strategy_id, version)} is {cur['tier']})")
            allowed = allowed_fractions(tier, root)
            frac = allowed[0] if capital_fraction is None else float(capital_fraction)
            if not any(abs(frac - a) < 1e-12 for a in allowed):
                raise TierError(f"{tier} capital fraction must be one of {allowed} (risk_prefs.tier_capital)")
            if tier == "T3" and cur["tier"] == "T3" and frac <= float(cur["capital_fraction"]):
                raise TierError("T3 → T3 must increase the capital fraction (use demote to reduce)")
            return self._record(strategy_id, version, tier, frac, approved_by,
                                f"human tier promotion{': ' + note if note else ''}", evidence)

    # ------------------------------------------------------------ 자동 강등 (리스크 관리자) · 사람 강등
    def demote(self, strategy_id: str, version: int, reason: str, actor: str = "risk", to: str = "T1") -> dict | None:
        """T2/T3 → T1 (또는 T3 → T2). 이미 T1 이하이면 None (멱등)."""
        if to not in ("T1", "T2"):
            raise TierError("demotion target must be T1 or T2")
        with self.reg._lock():
            cur = self.get(strategy_id, version)
            if TIERS.index(cur["tier"]) <= TIERS.index(to):
                return None
            frac = 0.0 if to == "T1" else allowed_fractions("T2")[0]
            return self._record(strategy_id, version, to, frac, actor, f"demotion: {reason}")

    def history(self, strategy_id: str, version: int) -> list[dict]:
        return [t for t in self.reg.ledger.transitions(strategy_id)
                if t.get("event") == "tier" and int(t["version"]) == int(version)]
