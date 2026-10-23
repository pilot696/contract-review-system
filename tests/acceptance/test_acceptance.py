"""验收测试：逐条映射需求 §12 的 7 条验收标准（另含补充检查）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app.api.deps import get_settings as get_settings_dependency
from app.config import get_settings
from app.db import engine
from app.main import app
from app.models import ApprovalAttachment, ApprovalTask
from app.services.approval_client import HttpApprovalClient
from mock_approval.faults import clear_faults, set_faults
from mock_approval.main import app as mock_app
from mock_approval.store import STORE
from tests.support import SyncASGITransport

pytestmark = pytest.mark.integration

EXPECTED_AP002 = {"R001", "R002", "R003", "R004", "R005", "R011"}
EXPECTED_AP003 = {"R007", "R008", "R009", "R010"}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    transport = SyncASGITransport(mock_app)

    def _fake_build(settings=None):
        return HttpApprovalClient(base_url="http://mock-approval", transport=transport)

    test_settings = get_settings().model_copy(update={"llm_enabled": False})
    monkeypatch.setattr("app.api.tools.build_approval_client", _fake_build)
    monkeypatch.setattr("app.services.task_service.build_approval_client", _fake_build)
    monkeypatch.setattr("app.services.task_service.get_settings", lambda: test_settings)
    app.dependency_overrides[get_settings_dependency] = lambda: test_settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_settings_dependency, None)


def _pull(client: TestClient) -> dict:
    return client.post(
        "/api/tools/list_pending_contract_approvals", json={"limit": 10}
    ).json()["data"]


def _tool_detail(client: TestClient, code: str) -> dict:
    return client.post(
        "/api/tools/get_contract_approval", json={"instance_id": code}
    ).json()["data"]


def _download(client: TestClient, code: str) -> dict:
    detail = _tool_detail(client, code)
    attachment = detail["attachments"][0]
    return client.post(
        "/api/tools/download_contract_attachment",
        json={
            "instance_id": code,
            "attachment_id": attachment["attachment_id"],
            "file_name": attachment["file_name"],
        },
    ).json()["data"]


def _parse(client: TestClient, code: str) -> dict:
    document = _download(client, code)
    return client.post(
        "/api/tools/parse_contract_document", json={"document_id": document["document_id"]}
    ).json()["data"]


def _run_rules(client: TestClient, code: str) -> dict:
    return client.post("/api/tools/run_contract_rules", json={"case_id": code}).json()["data"]


def _process(client: TestClient, code: str) -> dict:
    response = client.post(f"/api/tasks/{code}/process")
    assert response.status_code == 202
    return client.get(f"/api/tasks/{code}").json()["data"]["task"]


# --------------------------------------------------------------------------- #
# 1. 拉取待办并按唯一业务标识去重
# --------------------------------------------------------------------------- #
def test_acceptance_1_pull_and_dedupe(client: TestClient, db) -> None:
    first = _pull(client)
    second = _pull(client)
    assert first["total"] == second["total"] == 4
    assert len(db.scalars(select(ApprovalTask)).all()) == 4


# --------------------------------------------------------------------------- #
# 2. 下载附件并保存附件记录
# --------------------------------------------------------------------------- #
def test_acceptance_2_download_attachment_record(client: TestClient, db) -> None:
    _pull(client)
    downloaded = _download(client, "AP20261021001")
    assert downloaded["download_status"] == "success"
    assert len(downloaded["file_hash"]) == 64
    assert Path(downloaded["file_path"]).exists()

    record = db.scalar(
        select(ApprovalAttachment).where(
            ApprovalAttachment.external_attachment_id == downloaded["attachment_id"]
        )
    )
    assert record is not None
    assert record.download_status == "success"


# --------------------------------------------------------------------------- #
# 3. 解析文档和图片扫描件，提取主要字段
# --------------------------------------------------------------------------- #
def test_acceptance_3_parse_docx_and_scan(client: TestClient) -> None:
    _pull(client)
    docx_result = _parse(client, "AP20261021001")
    assert docx_result["parse_status"] == "success"
    assert docx_result["basic_info"]["contract_title"]["value"] == "软件开发服务合同"
    assert docx_result["basic_info"]["amount"]["value"] == 500000.0

    scan_result = _parse(client, "AP20261021003")
    assert scan_result["parse_status"] == "success"
    assert scan_result["ocr_used"] is True
    assert scan_result["basic_info"]["signing_entity"]["value"] == "启明科技有限公司"


# --------------------------------------------------------------------------- #
# 4. 规则审查并返回命中证据与风险等级
# --------------------------------------------------------------------------- #
def test_acceptance_4_rule_review_evidence(client: TestClient) -> None:
    _pull(client)
    _parse(client, "AP20261021002")
    review = _run_rules(client, "AP20261021002")

    assert review["overall_risk_level"] == "high"
    assert {hit["rule_code"] for hit in review["hits"]} == EXPECTED_AP002
    for hit in review["hits"]:
        assert hit["risk_level"] in {"low", "medium", "high"}
        assert hit["evidence_text"]
        assert hit["evidence_position"]
        assert hit["suggestion"]
    assert review["focus_points"]
    assert review["summary_text"]


# --------------------------------------------------------------------------- #
# 5. 保存审查结果并写回评论区
# --------------------------------------------------------------------------- #
def test_acceptance_5_save_result_and_writeback(client: TestClient) -> None:
    import json

    _pull(client)
    _parse(client, "AP20261021001")
    review = _run_rules(client, "AP20261021001")

    saved = client.post(
        "/api/tools/save_review_result",
        json={
            "case_id": "AP20261021001",
            "overall_risk_level": review["overall_risk_level"],
            "summary_text": review["summary_text"],
            "focus_points_json": json.dumps(review["focus_points"], ensure_ascii=False),
            "comment_text": review["comment_text"],
        },
    )
    assert saved.status_code == 200
    review_id = saved.json()["data"]["review_id"]

    written = client.post(
        "/api/tools/write_approval_comment",
        json={"instance_id": "AP20261021001", "review_id": review_id},
    ).json()["data"]
    assert written["write_status"] == "success"
    assert len(STORE.list_comments("AP20261021001")) == 1

    replay = client.post(
        "/api/tools/write_approval_comment",
        json={"instance_id": "AP20261021001", "review_id": review_id},
    ).json()["data"]
    assert replay["idempotent_replay"] is True
    assert len(STORE.list_comments("AP20261021001")) == 1


# --------------------------------------------------------------------------- #
# 6. 异常任务进入阻塞并支持重试
# --------------------------------------------------------------------------- #
def test_acceptance_6_blocked_and_retry(client: TestClient) -> None:
    _pull(client)
    _tool_detail(client, "AP20261021001")
    set_faults("missing_attachment", approval_code="AP20261021001")

    client.post("/api/tasks/AP20261021001/process")
    blocked = client.get("/api/tasks/AP20261021001").json()["data"]["task"]
    assert blocked["task_status"] == "blocked"
    assert blocked["failed_stage"] == "download"
    assert blocked["blocked_reason"] == "ATTACHMENT_MISSING"

    clear_faults()
    assert client.post("/api/tasks/AP20261021001/retry").status_code == 202
    retried = client.get("/api/tasks/AP20261021001").json()["data"]["task"]
    assert retried["task_status"] == "done"
    assert retried["write_status"] == "success"
    assert retried["retry_count"] == 1


# --------------------------------------------------------------------------- #
# 7. 拉取 → 解析 → 审查 → 入库 → 回写的完整闭环
# --------------------------------------------------------------------------- #
def test_acceptance_7_full_closed_loop(client: TestClient) -> None:
    _pull(client)
    task = _process(client, "AP20261021001")
    assert task["task_status"] == "done"
    assert task["write_status"] == "success"
    assert len(STORE.list_comments("AP20261021001")) == 1

    result = client.get("/api/tasks/AP20261021001/result")
    assert result.status_code == 200
    assert "【合同智能审查意见】" in result.json()["data"]["comment_text"]
    assert client.get("/console/tasks/AP20261021001/result").status_code == 200


# --------------------------------------------------------------------------- #
# 补充检查：8 表 / 7 工具 / 11 规则 / 5 页面 / MCP
# --------------------------------------------------------------------------- #
def test_acceptance_supplementary_checks(client: TestClient, db) -> None:
    from app.mcp_server import TOOL_NAMES

    tables = set(inspect(engine).get_table_names(schema="public"))
    assert {
        "approval_tasks",
        "approval_attachments",
        "contract_parses",
        "review_rules",
        "rule_hits",
        "review_results",
        "comment_logs",
        "task_logs",
    } <= tables

    assert len(TOOL_NAMES) == 7
    assert client.get("/api/rules").json()["data"]["total"] == 11

    _pull(client)
    _process(client, "AP20261021001")
    for path in (
        "/console",
        "/console/tasks/AP20261021001",
        "/console/tasks/AP20261021001/parse",
        "/console/tasks/AP20261021001/review",
        "/console/tasks/AP20261021001/result",
        "/console/admin/rules",
        "/console/admin/logs",
        "/console/admin/faults",
    ):
        assert client.get(path).status_code == 200, path
