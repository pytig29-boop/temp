"""연구 MCP 서버 — '의도적 부재' 와 stdio 기동.

1. 도구 목록에 승인·활성화·롤백·잠금 개봉·승격·관문/비용/분할/원장 편집·주문·키 기능이 없다.
2. 도구 구현 코드가 사람 전용 API(open_lockbox, write_*_approval, activate, rollback, 상태 전이, 파라미터 승인,
   비용 버전 추가)를 부르지 않는다 (정적 검사).
3. stdio 서버(`python -m btc_quant.mcp`)가 떠서 도구 목록을 돌려준다.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from btc_quant.mcp import FORBIDDEN_CAPABILITIES, TOOL_NAMES, ResearchTools
from btc_quant.mcp import tools as T
from btc_quant.mcp.server import DESCRIPTIONS, build_server

ROOT = Path(__file__).resolve().parents[1]


def listed(server) -> list:
    async def go():
        async with Client(server) as c:
            return (await c.list_tools()).tools
    return asyncio.run(go())


def test_tool_list_is_exactly_the_research_set():
    tools = listed(build_server(ResearchTools(progress=lambda s: None)))
    names = [t.name for t in tools]
    assert sorted(names) == sorted(TOOL_NAMES) and len(names) == len(set(names))
    assert set(DESCRIPTIONS) == set(TOOL_NAMES)
    public = {n for n, f in inspect.getmembers(ResearchTools, inspect.isfunction) if getattr(f, "is_tool", False)}
    assert public == set(TOOL_NAMES)
    for t in tools:
        assert t.description and t.input_schema["type"] == "object"


@pytest.mark.parametrize("capability", sorted(FORBIDDEN_CAPABILITIES))
def test_forbidden_capabilities_absent(capability):
    rx = re.compile(FORBIDDEN_CAPABILITIES[capability], re.IGNORECASE)
    assert not [n for n in TOOL_NAMES if rx.search(n)], capability


def test_no_tool_parameter_grants_human_powers():
    """인자 이름으로도 사람 전용 동작을 켤 수 없다 (override·승인·키·강제 등)."""
    bad = re.compile(r"override|approv|activat|rollback|force|lockbox_key|api_key|secret|unlock|human", re.I)
    for t in listed(build_server(ResearchTools(progress=lambda s: None))):
        props = t.input_schema.get("properties") or {}
        assert not [p for p in props if bad.search(p)], (t.name, list(props))


def test_tool_code_never_calls_human_only_apis():
    src = Path(T.__file__).read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())       # 주석 제외
    for pat in (r"\.open_lockbox\(", r"write_lockbox_approval", r"write_activation_approval", r"\.activate\(",
                r"\.rollback\(", r"\.transition\(", r"\.reject\(", r"\.promote\(", r"proposals import[^\n]*approve",
                r"\bapprove\(", r"add_versions?\(", r"record_change\(", r"LockboxKey", r"_append_locked",
                r"ledger\.append\(", r"import[^\n]*admin_cli", r"admin_cli\.(main|cmd_)"):
        assert not re.search(pat, code), pat
    srv = Path(T.__file__).with_name("server.py").read_text(encoding="utf-8")
    assert "add_tool" in srv and "open_lockbox" not in srv


def test_errors_are_structured_not_raised(tmp_path):
    r = ResearchTools(tmp_path, progress=lambda s: None).get_campaign_report("camp-000001")
    assert r["ok"] is False and r["error"]["code"] == "not_found" and r["tool"] == "get_campaign_report"
    r = ResearchTools(tmp_path, progress=lambda s: None).query_ledger("lockbox_approvals")
    assert r["error"]["code"] == "table_not_allowed"


def test_mcp_json_registers_stdio_server():
    import json
    cfg = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    s = cfg["mcpServers"]["btcq"]
    assert s["command"] == "uv" and s["args"][-2:] == ["-m", "btc_quant.mcp"]
    assert s["env"]["BTCQ_RESEARCH_MODE"] == "1"


def test_stdio_server_lists_tools(tmp_path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "btc_quant.mcp", "--root", str(tmp_path)],
                                   cwd=str(ROOT))

    async def go():
        async with Client(params, read_timeout_seconds=120) as c:
            tools = (await c.list_tools()).tools
            r = await c.call_tool("list_blocks", {"kind": "exit_signal"})
            q = await c.call_tool("query_ledger", {"table": "trials"})
            return tools, r, q
    tools, r, q = asyncio.run(asyncio.wait_for(go(), 180))
    assert sorted(t.name for t in tools) == sorted(TOOL_NAMES)
    assert r.structured_content["ok"] and all(b["kind"] == "exit_signal" for b in r.structured_content["blocks"])
    assert q.structured_content["trials"] == 0


def test_guard_allows_every_research_tool_and_knows_the_forbidden_ones():
    import importlib.util
    spec = importlib.util.spec_from_file_location("guard_mod", ROOT / ".claude" / "hooks" / "guard.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    assert [n for n in TOOL_NAMES if g.check_mcp_name(f"mcp__btcq__{n}")] == []
    for n in ("approve_lockbox", "activate", "rollback", "open_lockbox", "place_order"):
        assert g.check_mcp_name(f"mcp__btcq__{n}")
