"""사람 전용 관리 명령 (PLAN §5.3, §6, §8).

  python -m btc_quant.registry.admin_cli approve-lockbox <candidate_id>
  python -m btc_quant.registry.admin_cli activate <id> <version>
  python -m btc_quant.registry.admin_cli rollback
  python -m btc_quant.registry.admin_cli reject <id> <version> --reason "..."
  python -m btc_quant.registry.admin_cli status
  python -m btc_quant.registry.admin_cli promote-tier <id> <version> T2|T3 [--fraction F]   (PLAN D23)
  python -m btc_quant.registry.admin_cli demote-tier <id> <version> [--to T1|T2] --reason "..."

승인성 명령은 대화형 터미널에서 대상 이름을 직접 타이핑해야 진행된다(속도 방지턱).
AI 사용 차단의 실제 장치는 hook 이다.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .ledger import Ledger
from .splits import DataAccess, write_lockbox_approval
from .strategies import StrategyRegistry, key_of


class ConfirmationError(RuntimeError):
    pass


def _confirm(expected: str, what: str) -> None:
    if not sys.stdin.isatty():
        raise ConfirmationError("refused: this command must be run from an interactive terminal by a human")
    print(f"[HUMAN-ONLY] {what}")
    typed = input(f"Type '{expected}' to confirm: ").strip()
    if typed != expected:
        raise ConfirmationError("confirmation did not match; aborted")


def _actor(args) -> str:
    return args.by or getpass.getuser()


def cmd_approve_lockbox(args) -> int:
    da = DataAccess(root=args.root)
    st = da.lockbox_status()
    _confirm(args.candidate_id,
             f"Approve ONE lockbox opening ({st['lockbox_id']}, {st['openings']}/{st['cap']} used) "
             f"for candidate {args.candidate_id}")
    p = write_lockbox_approval(args.root, args.candidate_id, _actor(args),
                               logic_hash=args.logic_hash, note=args.note or "")
    print(f"approval written: {p}")
    return 0


def cmd_activate(args) -> int:
    reg = StrategyRegistry(root=args.root)
    k = key_of(args.id, args.version)
    st = reg.state_of(args.id, args.version)
    if st != "review":
        print(f"error: {k} is in state {st!r}; only 'review' can be activated", file=sys.stderr)
        return 2
    cur = reg.active()
    cur_s = key_of(cur["id"], cur["version"]) if cur else "none"
    _confirm(k, f"Activate {k} (current active: {cur_s} → retired)")
    reg.write_activation_approval(args.id, args.version, _actor(args), note=args.note or "")
    prev = reg.activate(args.id, args.version, actor=_actor(args))
    print(f"activated {k}" + (f"; retired {key_of(prev['id'], prev['version'])}" if prev else ""))
    return 0


def cmd_rollback(args) -> int:
    reg = StrategyRegistry(root=args.root)
    cur = reg.active()
    if cur is None:
        print("error: no active strategy", file=sys.stderr)
        return 2
    _confirm("rollback", f"Roll back active {key_of(cur['id'], cur['version'])} to the previous active version")
    frm, to = reg.rollback(actor=_actor(args), reason=args.reason or "rollback")
    print(f"rolled back {frm} → {to}")
    return 0


def cmd_reject(args) -> int:
    reg = StrategyRegistry(root=args.root)
    k = key_of(args.id, args.version)
    _confirm(k, f"Reject {k} permanently (reason: {args.reason})")
    reg.reject(args.id, args.version, args.reason, actor=_actor(args))
    print(f"rejected {k}")
    return 0


def cmd_promote_tier(args) -> int:
    from .tiers import TierBook, allowed_fractions, tier_capital
    reg = StrategyRegistry(root=args.root)
    tb = TierBook(reg)
    k = key_of(args.id, args.version)
    cur = tb.get(args.id, args.version)
    frac = args.fraction if args.fraction is not None else allowed_fractions(args.tier, args.root)[0]
    _confirm(f"{k} {args.tier}",
             f"Promote {k} from {cur['tier']} ({cur['state']}) to {args.tier}: REAL capital fraction {frac:g} of total "
             f"(× position_scale from risk.yaml); tier_capital = {tier_capital(args.root)}")
    rec = tb.promote(args.id, args.version, args.tier, _actor(args), capital_fraction=frac, note=args.note or "",
                     root=args.root)
    print(json.dumps(rec, ensure_ascii=False))
    return 0


def cmd_demote_tier(args) -> int:
    from .tiers import TierBook
    reg = StrategyRegistry(root=args.root)
    k = key_of(args.id, args.version)
    _confirm(k, f"Demote {k} to {args.to} (reason: {args.reason})")
    rec = TierBook(reg).demote(args.id, args.version, args.reason, actor=_actor(args), to=args.to)
    print(json.dumps(rec, ensure_ascii=False) if rec else f"{k} already at or below {args.to}")
    return 0


def _tiers(reg) -> dict:
    from .tiers import PAPER_STATES, TierBook
    tb = TierBook(reg)
    return {key_of(x["id"], x["version"]): tb.get(x["id"], x["version"])
            for s in PAPER_STATES for x in reg.list(s)}


def cmd_status(args) -> int:
    reg = StrategyRegistry(root=args.root)
    da = DataAccess(root=args.root)
    led = Ledger(Path(da.root) / "research" / "ledger")
    out = {
        "strategies": reg.status(),
        "lockbox": da.lockbox_status(),
        "pending_activation_approvals": sorted(p.stem for p in reg.approvals_dir.glob("*.yaml"))
        if reg.approvals_dir.exists() else [],
        "trials_total": led.trials_count(),
        "tiers": _tiers(reg),
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="btc-quant-admin", description="Human-only registry/lockbox commands")
    ap.add_argument("--root", default=None, help="repo root (default: auto)")
    ap.add_argument("--by", default=None, help="approver name (default: OS user)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("approve-lockbox", help="approve one lockbox opening for a candidate")
    p.add_argument("candidate_id")
    p.add_argument("--logic-hash", default=None)
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_approve_lockbox)

    p = sub.add_parser("activate", help="review → active (previous active retired)")
    p.add_argument("id")
    p.add_argument("version", type=int)
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser("rollback", help="restore previous active version")
    p.add_argument("--reason", default=None)
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("reject", help="move any state → rejected")
    p.add_argument("id")
    p.add_argument("version", type=int)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("promote-tier", help="HUMAN-ONLY: T1→T2 (small live) or T2→T3 (scale up), PLAN D23")
    p.add_argument("id")
    p.add_argument("version", type=int)
    p.add_argument("tier", choices=["T2", "T3"])
    p.add_argument("--fraction", type=float, default=None, help="capital fraction (one of risk_prefs.tier_capital)")
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_promote_tier)

    p = sub.add_parser("demote-tier", help="T3/T2 → T2/T1")
    p.add_argument("id")
    p.add_argument("version", type=int)
    p.add_argument("--to", choices=["T1", "T2"], default="T1")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_demote_tier)

    p = sub.add_parser("status", help="show registry and lockbox status")
    p.set_defaults(func=cmd_status)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfirmationError as e:
        print(str(e), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
