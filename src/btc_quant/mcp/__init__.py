"""연구 MCP 서버 (PLAN §8, docs/MCP.md).

`btc_quant.mcp.tools.ResearchTools` = 얇은 검증 래퍼(순수 파이썬, JSON 요약만 반환),
`btc_quant.mcp.server.build_server()` = 공식 MCP Python SDK(`mcp` 2.x, `MCPServer`) 등록,
`python -m btc_quant.mcp` = stdio 서버 (.mcp.json).

승인·활성화·롤백·잠금 개봉·관문/비용/분할/원장 파일 편집·주문·키 도구는 **의도적으로 없다**.
"""
from .tools import FORBIDDEN_CAPABILITIES, TOOL_NAMES, ResearchTools, ToolFailure

__all__ = ["FORBIDDEN_CAPABILITIES", "TOOL_NAMES", "ResearchTools", "ToolFailure"]
