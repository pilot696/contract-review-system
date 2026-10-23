"""MCP 工具注册测试（I6）：恰好 7 个工具，名称与设计文档 §10 一致。"""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app.mcp_server import TOOL_NAMES, list_tool_names, run_contract_rules

EXPECTED = {
    "list_pending_contract_approvals",
    "get_contract_approval",
    "download_contract_attachment",
    "parse_contract_document",
    "run_contract_rules",
    "save_review_result",
    "write_approval_comment",
}


def test_mcp_registers_exactly_seven_tools() -> None:
    registered = set(asyncio.run(list_tool_names()))
    assert registered == EXPECTED
    assert len(TOOL_NAMES) == 7


def test_mcp_error_uses_toolerror_with_code() -> None:
    with pytest.raises(ToolError) as excinfo:
        run_contract_rules("AP-NOT-EXIST")
    assert str(excinfo.value).startswith("NOT_FOUND:")
