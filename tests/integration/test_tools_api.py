"""工具接口集成测试：工具 1–3 的完整调用链与错误信封。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_settings as get_settings_dependency
from app.config import get_settings
from app.main import app
from app.services.approval_client import HttpApprovalClient
from mock_approval.faults import set_faults
from mock_approval.main import app as mock_app
from tests.support import SyncASGITransport


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """让工具层通过进程内 ASGI transport 访问 Mock；测试期间关闭可选 LLM 补抽。"""
    transport = SyncASGITransport(mock_app)

    def _fake_build(settings=None):
        return HttpApprovalClient(base_url="http://mock-approval", transport=transport)

    monkeypatch.setattr("app.api.tools.build_approval_client", _fake_build)
    app.dependency_overrides[get_settings_dependency] = (
        lambda: get_settings().model_copy(update={"llm_enabled": False})
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_settings_dependency, None)


def test_tools_closed_loop(client: TestClient) -> None:
    # 1) 拉取待办（两次调用：验证去重）
    first = client.post("/api/tools/list_pending_contract_approvals", json={"limit": 10})
    assert first.status_code == 200
    body = first.json()
    assert body["ok"] is True
    approvals = body["data"]["approvals"]
    assert len(approvals) == 4
    assert body["data"]["total"] == 4

    second = client.post("/api/tools/list_pending_contract_approvals", json={"limit": 10})
    assert second.status_code == 200
    assert len(second.json()["data"]["approvals"]) == 4

    # 2) 审批详情
    code = "AP20261021002"
    detail_resp = client.post("/api/tools/get_contract_approval", json={"instance_id": code})
    assert detail_resp.status_code == 200
    detail = detail_resp.json()["data"]
    assert detail["approval_info"]["approval_code"] == code
    assert detail["form_data"]["币种"] == "CNY"
    attachment = detail["attachments"][0]
    assert attachment["download_status"] == "pending"

    # 3) 附件下载
    download_resp = client.post(
        "/api/tools/download_contract_attachment",
        json={
            "instance_id": code,
            "attachment_id": attachment["attachment_id"],
            "file_name": attachment["file_name"],
        },
    )
    assert download_resp.status_code == 200
    downloaded = download_resp.json()["data"]
    assert downloaded["download_status"] == "success"
    assert downloaded["file_hash"]
    assert downloaded["document_id"] > 0
    assert downloaded["file_size"] > 0

    # 再次查询详情，下载状态已更新
    again = client.post("/api/tools/get_contract_approval", json={"instance_id": code})
    assert again.json()["data"]["attachments"][0]["download_status"] == "success"


def test_tool_errors_use_envelope(client: TestClient) -> None:
    missing = client.post("/api/tools/get_contract_approval", json={"instance_id": "NOT-EXIST"})
    assert missing.status_code == 404
    payload = missing.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NOT_FOUND"

    invalid = client.post("/api/tools/list_pending_contract_approvals", json={"limit": 999})
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"


def test_download_missing_attachment_returns_error(client: TestClient) -> None:
    # 先拉详情建立附件记录
    detail = client.post(
        "/api/tools/get_contract_approval", json={"instance_id": "AP20261021004"}
    ).json()["data"]
    attachment = detail["attachments"][0]

    response = client.post(
        "/api/tools/download_contract_attachment",
        json={
            "instance_id": "AP20261021004",
            "attachment_id": attachment["attachment_id"],
            "file_name": attachment["file_name"],
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ATTACHMENT_MISSING"


def test_fault_injection_missing_attachment(client: TestClient) -> None:
    detail = client.post(
        "/api/tools/get_contract_approval", json={"instance_id": "AP20261021003"}
    ).json()["data"]
    attachment = detail["attachments"][0]

    set_faults("missing_attachment", approval_code="AP20261021003")
    response = client.post(
        "/api/tools/download_contract_attachment",
        json={
            "instance_id": "AP20261021003",
            "attachment_id": attachment["attachment_id"],
            "file_name": attachment["file_name"],
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ATTACHMENT_MISSING"


def _download_first_attachment(client: TestClient, instance_id: str) -> int:
    """拉详情 + 下载附件，返回 document_id。"""
    client.post("/api/tools/list_pending_contract_approvals", json={"limit": 10})
    detail = client.post(
        "/api/tools/get_contract_approval", json={"instance_id": instance_id}
    ).json()["data"]
    attachment = detail["attachments"][0]
    response = client.post(
        "/api/tools/download_contract_attachment",
        json={
            "instance_id": instance_id,
            "attachment_id": attachment["attachment_id"],
            "file_name": attachment["file_name"],
        },
    )
    assert response.status_code == 200
    return int(response.json()["data"]["document_id"])


def test_parse_document_tool_and_persist(client: TestClient, db) -> None:
    document_id = _download_first_attachment(client, "AP20261021001")

    response = client.post(
        "/api/tools/parse_contract_document", json={"document_id": document_id}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["parse_status"] == "success"
    assert data["basic_info"]["contract_title"]["value"] == "软件开发服务合同"
    assert data["basic_info"]["amount"]["value"] == 500000.0
    assert data["clauses"]["payment_terms"]["extract_status"] == "extracted"
    assert data["basic_info"]["contract_title"]["evidence"]["text"]

    from sqlalchemy import select

    from app.models import ContractParse

    record = db.scalar(select(ContractParse).where(ContractParse.attachment_id == document_id))
    assert record is not None
    assert record.parse_status == "success"
    assert record.page_count is None


def test_parse_requires_downloaded_attachment(client: TestClient, db) -> None:
    from sqlalchemy import select

    from app.models import ApprovalAttachment

    client.post("/api/tools/list_pending_contract_approvals", json={"limit": 10})
    client.post("/api/tools/get_contract_approval", json={"instance_id": "AP20261021002"})
    attachment = db.scalar(
        select(ApprovalAttachment).where(
            ApprovalAttachment.external_attachment_id == "ATT-002"
        )
    )
    assert attachment is not None
    assert attachment.download_status == "pending"

    response = client.post(
        "/api/tools/parse_contract_document", json={"document_id": attachment.id}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def _parse_via_tools(client: TestClient, instance_id: str) -> int:
    document_id = _download_first_attachment(client, instance_id)
    response = client.post(
        "/api/tools/parse_contract_document", json={"document_id": document_id}
    )
    assert response.status_code == 200
    assert response.json()["data"]["parse_status"] == "success"
    return document_id


def test_run_contract_rules_tool(client: TestClient, db) -> None:
    from sqlalchemy import func, select

    from app.models import ReviewResult, RuleHit

    _parse_via_tools(client, "AP20261021002")

    response = client.post(
        "/api/tools/run_contract_rules", json={"case_id": "AP20261021002"}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["overall_risk_level"] == "high"
    assert data["risk_level_label"] == "高"
    assert data["hit_count"] == 6
    codes = {hit["rule_code"] for hit in data["hits"]}
    assert codes == {"R001", "R002", "R003", "R004", "R005", "R011"}, sorted(codes)
    assert data["focus_points"]
    assert "【合同智能审查意见】" in data["comment_text"]
    assert "AP20261021002" in data["comment_text"]

    first_count = db.scalar(select(func.count()).select_from(RuleHit))
    assert first_count == len(data["hits"])
    assert db.scalar(select(func.count()).select_from(ReviewResult)) == 0  # 工具 5 不写结果表

    # 重跑幂等：先删后插，命中数不叠加
    again = client.post(
        "/api/tools/run_contract_rules", json={"case_id": "AP20261021002"}
    ).json()["data"]
    assert {hit["rule_code"] for hit in again["hits"]} == codes
    assert db.scalar(select(func.count()).select_from(RuleHit)) == first_count


def test_run_rules_requires_parse(client: TestClient) -> None:
    client.post("/api/tools/list_pending_contract_approvals", json={"limit": 10})
    client.post("/api/tools/get_contract_approval", json={"instance_id": "AP20261021001"})

    response = client.post(
        "/api/tools/run_contract_rules", json={"case_id": "AP20261021001"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PARSE_REQUIRED"
