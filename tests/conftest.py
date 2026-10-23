"""pytest fixtures：真实 PostgreSQL + 进程内 Mock 审批系统（ASGI，不占端口）。"""

from __future__ import annotations

import pytest
from sqlalchemy import text

import app.models  # noqa: F401  注册 ORM 模型
from app.db import Base, SessionLocal, engine
from app.services.approval_client import HttpApprovalClient
from mock_approval.faults import clear_faults
from mock_approval.main import app as mock_app
from mock_approval.store import STORE
from app.services.rules.seed import upsert_seed_rules
from tests.support import SyncASGITransport

_TABLES = [
    "task_logs",
    "comment_logs",
    "review_results",
    "rule_hits",
    "contract_parses",
    "review_rules",
    "approval_attachments",
    "approval_tasks",
]
_TRUNCATE_SQL = text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE")


@pytest.fixture(scope="session", autouse=True)
def _ensure_samples():
    """M2：确保真实示例合同已生成（M1 占位文件会被覆盖）。"""
    import importlib.util

    if importlib.util.find_spec("docx") and importlib.util.find_spec("reportlab"):
        from mock_approval.samples import ensure_samples

        ensure_samples()
    yield


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema(_ensure_samples):
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _clean_state():
    with engine.begin() as conn:
        conn.execute(_TRUNCATE_SQL)
    # 规则表被清空后重新写入 11 条种子，保证规则引擎测试有可用规则
    with SessionLocal() as session:
        upsert_seed_rules(session)
        session.commit()
    STORE.reset()
    clear_faults()
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def mock_transport() -> SyncASGITransport:
    return SyncASGITransport(mock_app)


@pytest.fixture()
def approval_client(mock_transport: SyncASGITransport):
    client = HttpApprovalClient(base_url="http://mock-approval", transport=mock_transport)
    try:
        yield client
    finally:
        client.close()
