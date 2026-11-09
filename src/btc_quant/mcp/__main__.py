"""stdio 연구 MCP 서버: `uv run python -m btc_quant.mcp [--root DIR]` (.mcp.json 이 이것을 띄운다).

연구 모드 의미론: 이 프로세스는 항상 BTCQ_RESEARCH_MODE=1 로 동작한다(계열 override 등 사람 전용 경로 거부).
stdout 은 MCP 프로토콜 전용 — 진행 로그는 stderr 로만 쓴다.
"""
from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m btc_quant.mcp", description=__doc__)
    ap.add_argument("--root", default=os.environ.get("BTC_QUANT_ROOT"),
                    help="research repository root (default: BTC_QUANT_ROOT or the code repository)")
    a = ap.parse_args(argv)
    os.environ["BTCQ_RESEARCH_MODE"] = "1"
    from .server import build_server
    from .tools import ResearchTools
    srv = build_server(ResearchTools(root=a.root))
    print(f"btcq research MCP server (stdio), root={ResearchTools(root=a.root).root}", file=sys.stderr, flush=True)
    srv.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
