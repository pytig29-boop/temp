"""레드팀 각본 묶음 (autonomous_research_plan §4-F). 인증마다 실행: 성공(가짜가 최종 통과) = 인증 실패.

무거운 각본은 인증 은행의 귀무 묶음으로 대신한다(같은 공격을 실제 파이프라인으로 수백 시장에서 이미 돌린다):
  brute    → program_null (8캠페인 연속) + C4 '기준 쇼핑'(L=3) 평가
  beta     → hard_beta, hard_beta_low (상승 표류만)
  lottery  → hard_jump (0 평균 점프)
  stale    → hard_stale (1분 잡음 반전)
  seasonal → hard_seasonal
빠른 각본(이 모듈): rename(계열 이름 바꾸기로 시도 수 초기화), reuse(Val 탈락 스펙 표기만 바꿔 재제출), shop(느슨화 소진 후 문턱).

    python -m btc_quant.research.redteam quick     # 빠른 각본 → 발견 목록(JSON), 없으면 []
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import yaml


def attack_rename(tmp: Path) -> str | None:
    """프로그램 기간에 계열 이름만 바꾸면 DSR 시도 수가 줄어드는가 → 줄면 발견."""
    from btc_quant.registry.ledger import Ledger
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "autonomy.yaml").write_text(yaml.safe_dump({"program_start": "2000-01-01T00:00:00+00:00"}))
    led = Ledger(tmp / "research" / "ledger")
    for i in range(50):
        led.append("runs", {"campaign_id": "camp-x", "logic_hash": f"h{i}", "family": "old_family"})
    n_prog = len({(r["campaign_id"], r["logic_hash"]) for r in led.records("runs")})
    n_fam_new = led.trials_count(family="renamed_family")
    # 파이프라인은 프로그램 기간이면 max(계열, 프로그램 전체)를 쓴다 (Pipeline.campaign_stats n_program_trials)
    if max(n_fam_new, n_prog) < n_prog:
        return "rename resets DSR trials"
    return None


def attack_reuse() -> str | None:
    """같은 스펙을 파라미터 표기(정수/실수, 키 순서)만 바꿔 내면 logic_hash 가 달라지는가 → 다르면 발견."""
    from btc_quant.strategy.spec import BlockRef, ExitSpec, StrategySpec
    a = StrategySpec("x", BlockRef("donchian_breakout", {"lookback": 48}), ExitSpec(0.7, 0.9, 90))
    b = StrategySpec("y", BlockRef("donchian_breakout", {"lookback": 48.0}), ExitSpec(0.70, 0.900, 90))
    if a.canonical_hash() != b.canonical_hash():
        return "canonical hash depends on parameter formatting (Val reuse rule bypass)"
    return None


def attack_shop() -> str | None:
    """느슨화 3회를 다 쓰면 결선 문턱이 더 엄격해지는가(같은 증거로 L=0 통과, L=3 탈락이어야 함)."""
    from . import certify as K
    c = {"val_sharpe": 1, "lb_sharpe": 1, "val_p": 0.2, "lb_p": 0.2, "oos_p": 0.01, "dev_max_dd": 0.1,
         "val_mdd": 0.1, "lb_mdd": 0.1, "full_mc_mdd95": 0.1, "full_sharpe": 2, "full_bh_sharpe": 1,
         "full_calmar": 2, "full_bh_calmar": 1}
    a = K.final_flags(c, 2.5, dict(K.FINAL_DEFAULT), 0)["F1b"]
    b = K.final_flags(c, 2.5, dict(K.FINAL_DEFAULT), 3)["F1b"]
    if not (a and not b):
        return "loosening penalty does not tighten the final threshold"
    return None


def quick() -> list[str]:
    out = []
    with tempfile.TemporaryDirectory() as d:
        f = attack_rename(Path(d))
        if f:
            out.append(f)
    for fn in (attack_reuse, attack_shop):
        f = fn()
        if f:
            out.append(f)
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["quick"]:
        f = quick()
        print(json.dumps(f, ensure_ascii=False))
        return 1 if f else 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
