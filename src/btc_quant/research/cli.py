"""연구 캠페인 CLI.

  uv run python -m btc_quant.research.cli run research/campaign_templates/donchian_breakout.yaml [--max-candidates 5]
  uv run python -m btc_quant.research.cli report camp-000001
  uv run python -m btc_quant.research.cli list
"""
from __future__ import annotations

import argparse
import sys

from .campaign import CampaignError, ResearchEnv, list_campaigns, run_campaign
from .pipeline import run_pipeline
from .report import write_report


def _log(s: str) -> None:
    print(s, file=sys.stderr, flush=True)


def cmd_run(a) -> int:
    env = ResearchEnv.at(a.root)
    cr = run_campaign(a.template, env=env, progress=_log)
    if not a.no_pipeline:
        run_pipeline(cr, env=env, top_k=a.max_candidates, n_null=a.n_null, progress=_log)
    p = write_report(cr.campaign_id, env=env)
    print(f"{cr.campaign_id}\t{p}")
    return 0


def cmd_pipeline(a) -> int:
    env = ResearchEnv.at(a.root)
    run_pipeline(a.campaign_id, env=env, top_k=a.max_candidates, n_null=a.n_null, progress=_log)
    print(write_report(a.campaign_id, env=env))
    return 0


def cmd_report(a) -> int:
    print(write_report(a.campaign_id, env=ResearchEnv.at(a.root)))
    return 0


def cmd_list(a) -> int:
    rows = list_campaigns(env=ResearchEnv.at(a.root))
    if not rows:
        print("(no campaigns)")
    for r in rows:
        print(f"{r['campaign_id']}\t{r['ts']}\t{r['family']}\tbudget={r['budget']}\t"
              f"saved={r['saved']}\t{r['verdict'] or '-'}\t{(r['hypothesis'] or '')[:60]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="btc-quant-research", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="repo root (default: auto-detected)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="pre-register + run a campaign template, then gates and report")
    r.add_argument("template")
    r.add_argument("--max-candidates", type=int, default=5)
    r.add_argument("--n-null", type=int, default=None, help="null seeds (default config G2.null_runs)")
    r.add_argument("--no-pipeline", action="store_true", help="only run the grid (gates later)")
    r.set_defaults(fn=cmd_run)
    g = sub.add_parser("gates", help="run the gate pipeline on a finished campaign")
    g.add_argument("campaign_id")
    g.add_argument("--max-candidates", type=int, default=5)
    g.add_argument("--n-null", type=int, default=None)
    g.set_defaults(fn=cmd_pipeline)
    p = sub.add_parser("report", help="(re)write research/reports/campaign_<id>.md")
    p.add_argument("campaign_id")
    p.set_defaults(fn=cmd_report)
    lst = sub.add_parser("list", help="list campaigns in the ledger")
    lst.set_defaults(fn=cmd_list)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except CampaignError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
