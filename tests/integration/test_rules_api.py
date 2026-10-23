"""规则 CRUD 接口测试（设计文档 §6.9、§7.5.8）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_settings as get_settings_dependency
from app.config import get_settings
from app.main import app
from app.models import ApprovalTask, ReviewRule, RuleHit


@pytest.fixture()
def client() -> TestClient:
    app.dependency_overrides[get_settings_dependency] = lambda: get_settings().model_copy(
        update={"llm_enabled": False}
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_settings_dependency, None)


def test_rule_crud_lifecycle(client: TestClient) -> None:
    payload = {
        "rule_code": "R900",
        "rule_name": "测试规则",
        "risk_level": "medium",
        "match_mode": "keyword",
        "match_text": "测试关键词|另一个关键词",
        "suggestion_text": "请人工确认测试条款。",
        "priority": 50,
    }
    created = client.post("/api/rules", json=payload)
    assert created.status_code == 201
    assert created.json()["data"]["rule_code"] == "R900"
    assert client.get("/api/rules").json()["data"]["total"] == 12

    payload.update({"rule_name": "测试规则（已改）", "risk_level": "high", "priority": 10})
    updated = client.put("/api/rules/R900", json=payload)
    assert updated.status_code == 200
    assert updated.json()["data"]["rule_name"] == "测试规则（已改）"

    toggled = client.post("/api/rules/R900/toggle")
    assert toggled.status_code == 200
    assert toggled.json()["data"]["rule_status"] == "inactive"

    deleted = client.delete("/api/rules/R900")
    assert deleted.status_code == 200
    assert client.get("/api/rules").json()["data"]["total"] == 11


def test_rule_validation_errors(client: TestClient) -> None:
    invalid_regex = client.post(
        "/api/rules",
        json={
            "rule_code": "R901",
            "rule_name": "坏正则",
            "risk_level": "high",
            "match_mode": "regex",
            "match_text": "(",
            "suggestion_text": "x",
        },
    )
    assert invalid_regex.status_code == 400
    assert invalid_regex.json()["error"]["code"] == "VALIDATION_ERROR"

    invalid_field = client.post(
        "/api/rules",
        json={
            "rule_code": "R902",
            "rule_name": "坏字段",
            "risk_level": "high",
            "match_mode": "field_absent",
            "match_text": "not_a_field",
            "suggestion_text": "x",
        },
    )
    assert invalid_field.status_code == 400

    duplicate = client.post(
        "/api/rules",
        json={
            "rule_code": "R001",
            "rule_name": "重复",
            "risk_level": "high",
            "match_mode": "keyword",
            "match_text": "x",
            "suggestion_text": "x",
        },
    )
    assert duplicate.status_code == 409


def test_delete_rule_with_hits_is_refused(client: TestClient, db) -> None:
    task = ApprovalTask(approval_code="AP-RULE-TEST", approval_title="规则引用测试")
    db.add(task)
    db.flush()
    rule = db.query(ReviewRule).filter_by(rule_code="R001").one()
    db.add(
        RuleHit(
            task_id=task.id,
            rule_id=rule.id,
            evidence_text="证据",
            evidence_position="第1页",
            hit_status="hit",
        )
    )
    db.commit()

    response = client.delete("/api/rules/R001")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"

    # 停用仍可用，且不影响已有命中
    disabled = client.post("/api/rules/R001/toggle")
    assert disabled.status_code == 200
    assert disabled.json()["data"]["rule_status"] == "inactive"
