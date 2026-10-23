"""M0 冒烟测试：健康检查与根路径（需要可达的 PostgreSQL）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.integration


def test_health_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["version"] == "0.1.0"


def test_root_placeholder() -> None:
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "合同审批审查系统"
