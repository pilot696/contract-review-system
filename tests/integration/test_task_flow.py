"""M4 编排与回写集成测试：全流程、幂等回写、blocked/重试、工具 6–7（I1/I2/I5）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_settings as get_settings_dependency
from app.config import get_settings
from app.main import app
from app.services.approval_client import HttpApprovalClient
from mock_approval.faults import set_faults
from mock_approval.main import app as mock_app
from mock_approval.store import STORE
from tests.support import SyncASGITransport


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


def _pull_and_detail(client: TestClient, instance_id: str) -> None:
    client.post("/api/tools/list_pending_contract_approvals", json={"limit": 10})
    response = client.post(
        "/api/tools/get_contract_approval", json={"instance_id": instance_id}
    )
    assert response.status_code == 200


def _task(client: TestClient, instance_id: str) -> dict:
    return client.get(f"/api/tasks/{instance_id}").json()["data"]["task"]


def test_full_pipeline_reaches_done_and_writes_comment(client: TestClient) -> None:
    _pull_and_detail(client, "AP20261021001")

    response = client.post("/api/tasks/AP20261021001/process")
    assert response.status_code == 202

    task = _task(client, "AP20261021001")
    assert task["task_status"] == "done"
    assert task["write_status"] == "success"
    assert task["failed_stage"] is None
    assert len(STORE.list_comments("AP20261021001")) == 1

    result = client.get("/api/tasks/AP20261021001/result").json()["data"]
    assert result["overall_risk_level"] == "low"
    assert result["write_status"] == "success"
    assert "【合同智能审查意见】" in result["comment_text"]

    logs = client.get("/api/tasks/AP20261021001/logs").json()["data"]
    assert logs["total"] > 0


def test_writeback_is_idempotent(client: TestClient) -> None:
    _pull_and_detail(client, "AP20261021001")
    client.post("/api/tasks/AP20261021001/process")
    result = client.get("/api/tasks/AP20261021001/result").json()["data"]

    replay = client.post(
        "/api/tools/write_approval_comment",
        json={"instance_id": "AP20261021001", "review_id": result["review_id"]},
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["write_status"] == "success"
    assert replay.json()["data"]["idempotent_replay"] is True
    assert len(STORE.list_comments("AP20261021001")) == 1  # 评论不重复


def test_missing_attachment_blocks_then_retry_succeeds(client: TestClient) -> None:
    _pull_and_detail(client, "AP20261021001")
    set_faults("missing_attachment", approval_code="AP20261021001")

    client.post("/api/tasks/AP20261021001/process")
    task = _task(client, "AP20261021001")
    assert task["task_status"] == "blocked"
    assert task["failed_stage"] == "download"
    assert task["blocked_reason"] == "ATTACHMENT_MISSING"
    assert task["retry_count"] == 0

    # 清障后重试：从 download 阶段续跑
    from mock_approval.faults import clear_faults

    clear_faults()
    retry = client.post("/api/tasks/AP20261021001/retry")
    assert retry.status_code == 202
    task = _task(client, "AP20261021001")
    assert task["task_status"] == "done"
    assert task["write_status"] == "success"
    assert task["retry_count"] == 1


def test_writeback_failure_blocks_and_retry_only_writeback(client: TestClient) -> None:
    _pull_and_detail(client, "AP20261021002")
    set_faults("comment_500", approval_code="AP20261021002")

    client.post("/api/tasks/AP20261021002/process")
    task = _task(client, "AP20261021002")
    assert task["task_status"] == "blocked"
    assert task["failed_stage"] == "writeback"
    assert task["blocked_reason"] == "WRITEBACK_FAILED"
    assert task["write_status"] == "failed"
    assert STORE.list_comments("AP20261021002") == []

    from mock_approval.faults import clear_faults

    clear_faults()
    client.post("/api/tasks/AP20261021002/retry")
    task = _task(client, "AP20261021002")
    assert task["task_status"] == "done"
    assert task["write_status"] == "success"
    assert len(STORE.list_comments("AP20261021002")) == 1


def test_process_conflicts(client: TestClient) -> None:
    _pull_and_detail(client, "AP20261021001")
    assert client.post("/api/tasks/AP20261021001/process").status_code == 202
    again = client.post("/api/tasks/AP20261021001/process")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CONFLICT"


def test_tools_6_and_7_manual_flow(client: TestClient) -> None:
    _pull_and_detail(client, "AP20261021001")
    detail = client.post(
        "/api/tools/get_contract_approval", json={"instance_id": "AP20261021001"}
    ).json()["data"]
    attachment = detail["attachments"][0]
    document_id = client.post(
        "/api/tools/download_contract_attachment",
        json={
            "instance_id": "AP20261021001",
            "attachment_id": attachment["attachment_id"],
            "file_name": attachment["file_name"],
        },
    ).json()["data"]["document_id"]
    client.post("/api/tools/parse_contract_document", json={"document_id": document_id})

    review = client.post(
        "/api/tools/run_contract_rules", json={"case_id": "AP20261021001"}
    ).json()["data"]

    import json

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
    )
    assert written.status_code == 200
    assert written.json()["data"]["write_status"] == "success"
    assert len(STORE.list_comments("AP20261021001")) == 1


def test_rules_and_list_endpoints(client: TestClient) -> None:
    rules = client.get("/api/rules").json()["data"]
    assert rules["total"] == 11

    _pull_and_detail(client, "AP20261021001")
    listed = client.get("/api/tasks").json()["data"]
    assert listed["total"] == 4

    stats = client.get("/api/stats").json()["data"]
    assert stats["rules_active"] == 11
    assert "success_rate" in stats
    assert "avg_duration_seconds" in stats


def test_startup_recovery_marks_stale_task_blocked(client: TestClient, db) -> None:
    from datetime import timedelta

    from app.config import get_settings
    from app.models import ApprovalTask
    from app.services.state import now as state_now
    from app.services.task_service import recover_stale_tasks

    _pull_and_detail(client, "AP20261021001")
    task = db.query(ApprovalTask).filter_by(approval_code="AP20261021001").one()
    task.task_status = "parsing"
    task.updated_at = state_now() - timedelta(minutes=30)
    db.commit()

    recovered = recover_stale_tasks(get_settings().model_copy(update={"stale_task_seconds": 60}))
    assert recovered >= 1

    db.expire_all()
    task = db.query(ApprovalTask).filter_by(approval_code="AP20261021001").one()
    assert task.task_status == "blocked"
    assert task.failed_stage == "parse"
    assert task.blocked_reason == "INTERNAL_ERROR"
    assert task.retry_count == 0
