"""파라미터 관리 CLI (docs/PARAMS.md).

  python -m btc_quant.params status
  python -m btc_quant.params sample --venues coinone,upbit --seconds 1200
  python -m btc_quant.params calibrate [--venues coinone,upbit] [--dry-run]
  python -m btc_quant.params propose-criteria gates.G5.min_dsr --values 0.9 0.975 --motivation "..." [--mode bounded]
  python -m btc_quant.params options --returns val_daily.parquet [--trades trades.parquet] [--capital 1e7] [--propose KEY]
  python -m btc_quant.params approve <proposal_id> [--choice <label>]     # 사람 전용 (대화형 확인)
  python -m btc_quant.params reject <proposal_id> --reason "..."         # 사람 전용
  python -m btc_quant.params history [KEY | costs | <cost model>]
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

import pandas as pd


def _root(a):
    return Path(a.root) if a.root else None


def cmd_floor_verify(a) -> int:
    import json
    from .floor import floor_verify
    r = floor_verify(getattr(a, "root", None))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if not r["violations"] else 2


def cmd_status(a) -> int:
    from .costs import all_cost_models, cost_versions, round_trip_bps, side_cost_bps
    from .proposals import list_proposals
    from .registry import load_registry, params_version, validate
    reg = load_registry(_root(a), validate_=False)
    errs = validate(reg)
    by = reg.by_class()
    print(f"params_version {params_version(reg=reg)}  ({len(reg.params)} params: "
          + ", ".join(f"class {c}={len(v)}" for c, v in by.items()) + ")")
    print("validation: " + ("OK" if not errs else f"{len(errs)} problem(s)"))
    for e in errs:
        print(f"  ! {e}")
    vs = cost_versions(_root(a))
    print("\ncost models (current):")
    for n, m in all_cost_models(_root(a)).items():
        print(f"  {n:22s} v{vs[n]}  side {side_cost_bps(m):6.2f}bp  fee {m.fee_rate * 1e4:4.1f}bp  "
              f"round trip {round_trip_bps(m):6.2f}bp")
    pend = list_proposals(_root(a), status="pending")
    print(f"\npending proposals: {len(pend)}")
    for p in pend:
        print(f"  {p['id']}  class {p['class']} {p['kind']}: {p.get('title')}")
    return 0 if not errs else 1


def cmd_sample(a) -> int:
    from .sampling import sample_many
    res = sample_many([v.strip() for v in a.venues.split(",")], a.seconds, a.interval,
                      root=(_root(a) or Path(".")) / "data" / "raw")
    print(json.dumps({k: str(v) for k, v in res.items()}, indent=2))
    return 0


def cmd_calibrate(a) -> int:
    from .calibrate import (
        InsufficientDataError,
        apply_calibration,
        calibrate,
        report_markdown,
    )
    try:
        res = calibrate(tuple(v.strip() for v in a.venues.split(",")), root=_root(a), notional=a.notional,
                        since=pd.Timestamp(a.since, tz="UTC") if a.since else None)
    except InsufficientDataError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if a.dry_run:
        print(report_markdown(res))
        return 0
    res = apply_calibration(res, root=_root(a))
    print(f"report → {res.report_path}")
    if res.applied:
        print("auto-applied (stricter): " + ", ".join(f"{k} v{v}" for k, v in res.applied.items()))
    if res.proposal_id:
        print(f"proposal (needs human approval) → research/param_proposals/{res.proposal_id}.yaml\n"
              f"  python -m btc_quant.params approve {res.proposal_id}")
    return 0


def _parse_val(s: str):
    import yaml
    return yaml.safe_load(s)


def cmd_propose_criteria(a) -> int:
    from .criteria import GoalpostError, Targets, propose_criterion
    try:
        st = propose_criterion(a.key, [_parse_val(v) for v in a.values],
                               Targets(a.max_fpr, a.min_power, a.power_sharpe), a.motivation,
                               references=a.ref or [], mode=a.mode, n_null_markets=a.null_markets,
                               n_planted_markets=a.planted_markets, jobs=a.jobs, root=_root(a),
                               n_null_runs=a.null_runs)
    except GoalpostError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(f"{st.status}; recommended={st.recommended}; report → {st.report_path}")
    if st.proposal_id:
        print(f"proposal → research/param_proposals/{st.proposal_id}.yaml")
    return 0


def _read_series(path: str) -> pd.Series:
    p = Path(path)
    df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, index_col=0, parse_dates=True)
    if isinstance(df, pd.Series):
        return df
    for c in ("net_ret", "ret", "return", "daily_return"):
        if c in df.columns:
            return df[c]
    return df.iloc[:, -1]


def cmd_options(a) -> int:
    from .preferences import option_tables, render_markdown, write_option_proposal
    t = option_tables(_read_series(a.returns), _read_series(a.trades).to_numpy() if a.trades else None,
                      capital_krw=a.capital, n_sims=a.sims)
    md = render_markdown(t, a.title or "")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(md, encoding="utf-8")
        print(f"report → {a.out}")
    else:
        print(md)
    if a.propose:
        pid = write_option_proposal(t, a.propose, title=a.title or "", evidence=a.out, root=_root(a))
        print(f"preference proposal → {pid} (human: python -m btc_quant.params approve {pid} --choice <limit>)")
    return 0


def cmd_approve(a) -> int:
    from .proposals import ConfirmationError, ProposalError, approve
    try:
        rec = approve(a.id, a.by or getpass.getuser(), root=_root(a), choice=a.choice, note=a.note or "")
    except (ConfirmationError, ProposalError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(rec, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_reject(a) -> int:
    from .proposals import ConfirmationError, ProposalError, reject
    try:
        rec = reject(a.id, a.by or getpass.getuser(), a.reason, root=_root(a))
    except (ConfirmationError, ProposalError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(rec, indent=2, ensure_ascii=False))
    return 0


def cmd_history(a) -> int:
    from .costs import cost_history, load_costs
    from .registry import load_registry
    root = _root(a)
    if a.key in (None, "costs"):
        for n, e in load_costs(root)["models"].items():
            print(f"{n} (current v{e['current']})")
            for v in e["versions"]:
                print(f"  v{v['version']} {v['created']} spread {v['spread_bps']} slip {v['slip_bps']} fee "
                      f"{v['fee_rate']} [{v['direction']}, {v['applied_by']}] {v.get('evidence') or ''}")
        if a.key is None:
            reg = load_registry(root, validate_=False)
            changed = [p for p in reg.params if p.history]
            print(f"\nparameters with history: {len(changed)}")
            for p in changed:
                for h in p.history:
                    print(f"  {p.key} v{h['version']} {h['date']}: {h['previous']} → {h['value']} by {h['by']} "
                          f"(applies_from_campaign={h.get('applies_from_campaign')}, {h.get('proposal_id')})")
        return 0
    if a.key in load_costs(root)["models"]:
        print(json.dumps(cost_history(a.key, root), indent=2, ensure_ascii=False))
        return 0
    p = load_registry(root, validate_=False).get(a.key)
    print(json.dumps({"key": p.key, "class": p.cls, "source": p.source, "value": p.value, "version": p.version,
                      "owner": p.owner, "rationale": p.rationale, "evidence": p.evidence, "history": p.history},
                     indent=2, ensure_ascii=False, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m btc_quant.params", description="parameter governance (docs/PARAMS.md)")
    ap.add_argument("--root", default=None, help="repo root (default: auto)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("floor-verify", help="floor.yaml hash, git history, current config check").set_defaults(
        fn=cmd_floor_verify)

    p = sub.add_parser("sample", help="REST orderbook sampler (both sides, full book)")
    p.add_argument("--venues", default="coinone,upbit")
    p.add_argument("--seconds", type=float, default=1200)
    p.add_argument("--interval", type=float, default=2.0)
    p.set_defaults(fn=cmd_sample)

    p = sub.add_parser("calibrate", help="class ① cost calibration (asymmetric rule)")
    p.add_argument("--venues", default="coinone,upbit")
    p.add_argument("--notional", type=float, default=None)
    p.add_argument("--since", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("propose-criteria", help="class ② synthetic-calibration proposal for one criterion")
    p.add_argument("key")
    p.add_argument("--values", nargs="+", required=True)
    p.add_argument("--motivation", required=True)
    p.add_argument("--ref", action="append")
    p.add_argument("--max-fpr", type=float, default=0.05)
    p.add_argument("--min-power", type=float, default=0.70)
    p.add_argument("--power-sharpe", type=float, default=2.0)
    p.add_argument("--mode", choices=["quick", "bounded"], default="bounded")
    p.add_argument("--null-markets", type=int, default=20)
    p.add_argument("--planted-markets", type=int, default=10)
    p.add_argument("--null-runs", type=int, default=None, help="G2 null runs (default: config)")
    p.add_argument("--jobs", type=int, default=1)
    p.set_defaults(fn=cmd_propose_criteria)

    p = sub.add_parser("options", help="class ③ option tables for a validated strategy")
    p.add_argument("--returns", required=True, help="daily returns (parquet/csv)")
    p.add_argument("--trades", default=None, help="trade net returns (parquet/csv, column net_ret)")
    p.add_argument("--capital", type=float, default=None)
    p.add_argument("--sims", type=int, default=5000)
    p.add_argument("--title", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--propose", default=None, help="write a preference proposal for this class-③ key")
    p.set_defaults(fn=cmd_options)

    p = sub.add_parser("approve", help="HUMAN-ONLY: approve a proposal (interactive confirmation)")
    p.add_argument("id")
    p.add_argument("--choice", default=None)
    p.add_argument("--by", default=None)
    p.add_argument("--note", default=None)
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("reject", help="HUMAN-ONLY: reject a proposal")
    p.add_argument("id")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", default=None)
    p.set_defaults(fn=cmd_reject)

    p = sub.add_parser("history", help="version history (params or cost models)")
    p.add_argument("key", nargs="?", default=None)
    p.set_defaults(fn=cmd_history)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
