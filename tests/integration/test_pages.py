"""Web 控制台页面冒烟测试（服务端渲染 + 表单动作）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_settings as get_settings_dependency
from app.config import get_settings
from app.main import app
from app.services.approval_client import HttpApprovalClient
from mock_approval.main import app as mock_app
from mock_approval.store import STORE
from tests.support import SyncASGITransport


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    transport = SyncASGITransport(mock_app)

    def _fake_build(settings=None):
        return HttpApprovalClient(base_url="http://mock-approval", transport=transport)

    test_settings = get_settings().model_copy(update={"llm_enabled": False})
    monkeypatch.setattr("app.api.pages.build_approval_client", _fake_build)
    monkeypatch.setattr("app.api.tools.build_approval_client", _fake_build)
    monkeypatch.setattr("app.services.task_service.build_approval_client", _fake_build)
    monkeypatch.setattr("app.services.task_service.get_settings", lambda: test_settings)
    app.dependency_overrides[get_settings_dependency] = lambda: test_settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_settings_dependency, None)


def test_console_index_and_pull(client: TestClient) -> None:
    assert client.get("/console").status_code == 200

    pulled = client.post("/console/actions/pull", follow_redirects=False)
    assert pulled.status_code == 303

    page = client.get("/console")
    assert page.status_code == 200
    assert "AP20261021001" in page.text
    assert "拉取完成" in page.text or "待办审批单" in page.text


def test_console_process_and_result_pages(client: TestClient) -> None:
    client.post("/console/actions/pull")

    processed = client.post(
        "/console/actions/process/AP20261021001", follow_redirects=False
    )
    assert processed.status_code == 303

    detail = client.get("/console/tasks/AP20261021001")
    assert detail.status_code == 200
    assert "已完成" in detail.text
    assert "已回写" in detail.text

    parse_page = client.get("/console/tasks/AP20261021001/parse")
    assert parse_page.status_code == 200
    assert "合同标题" in parse_page.text
    assert "软件开发服务合同" in parse_page.text

    review_page = client.get("/console/tasks/AP20261021001/review")
    assert review_page.status_code == 200
    assert "规则命中" in review_page.text

    result_page = client.get("/console/tasks/AP20261021001/result")
    assert result_page.status_code == 200
    assert "【合同智能审查意见】" in result_page.text

    assert len(STORE.list_comments("AP20261021001")) == 1


def test_console_writeback_button_is_idempotent(client: TestClient) -> None:
    client.post("/console/actions/pull")
    client.post("/console/actions/process/AP20261021001")

    write = client.post("/console/tasks/AP20261021001/writeback", follow_redirects=False)
    assert write.status_code == 303
    assert len(STORE.list_comments("AP20261021001")) == 1

    result_page = client.get("/console/tasks/AP20261021001/result")
    assert "幂等重放" in result_page.text or "回写" in result_page.text


def test_admin_pages_and_rule_toggle(client: TestClient) -> None:
    rules_page = client.get("/console/admin/rules")
    assert rules_page.status_code == 200
    assert "R001" in rules_page.text

    toggled = client.post("/console/admin/rules/R001/toggle", follow_redirects=False)
    assert toggled.status_code == 303
    assert "停用" in client.get("/console/admin/rules").text

    client.post("/console/admin/rules/R001/toggle")

    logs_page = client.get("/console/admin/logs")
    assert logs_page.status_code == 200

    faults_page = client.get("/console/admin/faults")
    assert faults_page.status_code == 200


def test_console_error_page_returns_html(client: TestClient) -> None:
    response = client.get("/console/tasks/AP-NOT-EXIST")
    assert response.status_code == 404
    assert "错误码" in response.text
    assert "NOT_FOUND" in response.text
