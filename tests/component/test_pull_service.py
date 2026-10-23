"""拉取模块测试：去重、状态不被覆盖、附件元数据 upsert（C4）。"""

from __future__ import annotations

from sqlalchemy import select

from app.models import ApprovalAttachment, ApprovalTask
from app.services.pull_service import get_and_upsert_detail, pull_and_upsert


def test_pull_creates_tasks_and_dedupes(db, approval_client) -> None:
    first = pull_and_upsert(db, approval_client, 10)
    db.commit()
    assert first.total == 4
    assert len(first.tasks) == 4
    assert {task.approval_code for task in first.tasks} == {
        "AP20261021001",
        "AP20261021002",
        "AP20261021003",
        "AP20261021004",
    }

    # 模拟人工推进状态后重复拉取：行数不变、状态不被重置
    task = db.scalar(
        select(ApprovalTask).where(ApprovalTask.approval_code == "AP20261021001")
    )
    assert task is not None
    task.task_status = "parsing"
    db.commit()

    second = pull_and_upsert(db, approval_client, 10)
    db.commit()
    assert second.total == 4

    rows = db.scalars(select(ApprovalTask)).all()
    assert len(rows) == 4
    same = db.scalar(
        select(ApprovalTask).where(ApprovalTask.approval_code == "AP20261021001")
    )
    assert same.task_status == "parsing"


def test_get_detail_upserts_attachments_once(db, approval_client) -> None:
    task, detail = get_and_upsert_detail(db, approval_client, "AP20261021002")
    db.commit()
    assert task.approval_code == "AP20261021002"
    assert len(detail.attachments) == 1
    attachment = db.scalar(
        select(ApprovalAttachment).where(ApprovalAttachment.task_id == task.id)
    )
    assert attachment is not None
    assert attachment.external_attachment_id == "ATT-002"
    assert attachment.download_status == "pending"

    get_and_upsert_detail(db, approval_client, "AP20261021002")
    db.commit()
    rows = db.scalars(
        select(ApprovalAttachment).where(ApprovalAttachment.task_id == task.id)
    ).all()
    assert len(rows) == 1


def test_missing_attachment_metadata_is_kept(db, approval_client) -> None:
    task, detail = get_and_upsert_detail(db, approval_client, "AP20261021004")
    db.commit()
    assert task.attachment_count == 1
    assert detail.attachments[0].attachment_id == "ATT-004"
