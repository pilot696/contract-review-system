"""MCP 入口测试：7 工具注册、错误语义、工具 6/7 真实调用（回归命名遮蔽缺陷）。"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from app.api.deps import get_settings as get_settings_dependency
from app.config import get_settings
from app.main import app as fastapi_app
from app.mcp_server import (
    TOOL_NAMES,
    list_tool_names,
    run_contract_rules,
    save_review_result as mcp_save_review_result,
    write_approval_comment as mcp_write_approval_comment,
)
from app.services.approval_client import HttpApprovalClient
from mock_approval.main import app as mock_app
from mock_approval.store import STORE
from tests.support import SyncASGITransport

EXPECTED = {
    "list_pending_contract_approvals",
    "get_contract_approval",
    "download_contract_attachment",
    "parse_contract_document",
    "run_contract_rules",
    "save_review_result",
    "write_approval_comment",
}


@pytest.fixture()
def api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    transport = SyncASGITransport(mock_app)

    def _fake_build(settings=None):
        return HttpApprovalClient(base_url="http://mock-approval", transport=transport)

    test_settings = get_settings().model_copy(update={"llm_enabled": False})
    monkeypatch.setattr("app.api.tools.build_approval_client", _fake_build)
    monkeypatch.setattr("app.services.task_service.build_approval_client", _fake_build)
    monkeypatch.setattr("app.mcp_server.build_approval_client", _fake_build)
    fastapi_app.dependency_overrides[get_settings_dependency] = lambda: test_settings
    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.pop(get_settings_dependency, None)


def _prepare_ap001(api_client: TestClient) -> dict:
    """用 REST 工具准备一份可保存/回写的真实数据（拉取→下载→解析→规则）。"""
    api_client.post("/api/tools/list_pending_contract_approvals", json={"limit": 10})
    detail = api_client.post(
        "/api/tools/get_contract_approval", json={"instance_id": "AP20261021001"}
    ).json()["data"]
    attachment = detail["attachments"][0]
    document_id = api_client.post(
        "/api/tools/download_contract_attachment",
        json={
            "instance_id": "AP20261021001",
            "attachment_id": attachment["attachment_id"],
            "file_name": attachment["file_name"],
        },
    ).json()["data"]["document_id"]
    api_client.post("/api/tools/parse_contract_document", json={"document_id": document_id})
    return api_client.post(
        "/api/tools/run_contract_rules", json={"case_id": "AP20261021001"}
    ).json()["data"]


def test_mcp_registers_exactly_seven_tools() -> None:
    registered = set(asyncio.run(list_tool_names()))
    assert registered == EXPECTED
    assert len(TOOL_NAMES) == 7


def test_mcp_error_uses_toolerror_with_code() -> None:
    with pytest.raises(ToolError) as excinfo:
        run_contract_rules("AP-NOT-EXIST")
    assert str(excinfo.value).startswith("NOT_FOUND:")


def test_mcp_tools_are_not_shadowed_by_service_imports() -> None:
    """工具 6/7 的模块级名称必须是 MCP 工具（不能是服务层函数/被服务函数覆盖）。"""
    import app.mcp_server as mcp

    assert mcp.save_review_result is not mcp._save_review_result_service
    assert mcp.write_approval_comment is not mcp._write_approval_comment_service


def test_mcp_save_and_write_comment_success(api_client: TestClient) -> None:
    review = _prepare_ap001(api_client)

    saved = mcp_save_review_result(
        case_id="AP20261021001",
        overall_risk_level=review["overall_risk_level"],
        summary_text=review["summary_text"],
        focus_points_json=json.dumps(review["focus_points"], ensure_ascii=False),
        comment_text=review["comment_text"],
    )
    assert saved["case_id"] == "AP20261021001"
    assert saved["review_id"] >= 1

    written = mcp_write_approval_comment(
        instance_id="AP20261021001", review_id=saved["review_id"]
    )
    assert written["write_status"] == "success"
    assert len(STORE.list_comments("AP20261021001")) == 1

    replay = mcp_write_approval_comment(
        instance_id="AP20261021001", review_id=saved["review_id"]
    )
    assert replay["idempotent_replay"] is True
    assert len(STORE.list_comments("AP20261021001")) == 1
