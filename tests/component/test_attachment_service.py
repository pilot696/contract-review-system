"""附件模块测试：下载/哈希/幂等/缺附件阻塞（C5）。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.errors import AttachmentMissingError, DownloadFailedError
from app.models import ApprovalAttachment
from app.services.attachment_service import (
    ensure_all_attachments,
    ensure_attachment,
    safe_file_name,
)
from app.services.pull_service import get_and_upsert_detail
from mock_approval.faults import set_faults


def test_download_writes_file_and_hash(db, approval_client) -> None:
    settings = get_settings()
    task, detail = get_and_upsert_detail(db, approval_client, "AP20261021001")
    db.commit()
    meta = detail.attachments[0]

    record = ensure_attachment(db, approval_client, task, meta, settings)
    db.commit()

    assert record.download_status == "success"
    path = Path(record.file_path)
    assert path.exists()
    content = path.read_bytes()
    assert len(content) == record.file_size
    assert hashlib.sha256(content).hexdigest() == record.file_hash

    # 幂等：文件与哈希一致时不重复下载，返回同一条记录
    again = ensure_attachment(db, approval_client, task, meta, settings)
    db.commit()
    assert again.id == record.id
    assert again.download_status == "success"


def test_ensure_all_attachments(db, approval_client) -> None:
    settings = get_settings()
    task, _ = get_and_upsert_detail(db, approval_client, "AP20261021003")
    db.commit()
    records = ensure_all_attachments(db, approval_client, task, settings)
    db.commit()
    assert len(records) == 1
    assert records[0].file_type == "png"
    assert records[0].download_status == "success"


def test_missing_attachment_marks_failed(db, approval_client) -> None:
    settings = get_settings()
    task, _ = get_and_upsert_detail(db, approval_client, "AP20261021004")
    db.commit()

    with pytest.raises(AttachmentMissingError):
        ensure_all_attachments(db, approval_client, task, settings)
    db.commit()

    record = db.scalar(
        select(ApprovalAttachment).where(ApprovalAttachment.task_id == task.id)
    )
    assert record is not None
    assert record.download_status == "failed"
    assert record.download_error == "ATTACHMENT_MISSING"


def test_download_500_marks_failed(db, approval_client) -> None:
    settings = get_settings()
    set_faults("download_500", approval_code="AP20261021002")
    task, detail = get_and_upsert_detail(db, approval_client, "AP20261021002")
    db.commit()

    with pytest.raises(DownloadFailedError):
        ensure_attachment(db, approval_client, task, detail.attachments[0], settings)
    db.commit()

    record = db.scalar(
        select(ApprovalAttachment).where(ApprovalAttachment.task_id == task.id)
    )
    assert record is not None
    assert record.download_status == "failed"
    assert record.download_error == "DOWNLOAD_FAILED"


def test_safe_file_name_blocks_traversal() -> None:
    assert safe_file_name("../../etc/passwd") == "passwd"
    assert safe_file_name("合同 2026.pdf") == "合同 2026.pdf"
    assert safe_file_name("a/b\\c:d.pdf") == "c_d.pdf"


def test_unsupported_file_type_rejected(db, approval_client) -> None:
    from app.services.approval_client import AttachmentMeta

    settings = get_settings()
    task, _ = get_and_upsert_detail(db, approval_client, "AP20261021001")
    db.commit()
    meta = AttachmentMeta(
        attachment_id="ATT-001", file_name="evil.exe", file_type="exe", file_size=10
    )
    with pytest.raises(DownloadFailedError):
        ensure_attachment(db, approval_client, task, meta, settings)
    db.commit()

    record = db.scalar(
        select(ApprovalAttachment).where(
            ApprovalAttachment.task_id == task.id,
            ApprovalAttachment.external_attachment_id == "ATT-001",
        )
    )
    assert record is not None and record.download_status == "failed"
    assert "不支持的附件类型" in (record.download_error or "")


def test_oversized_attachment_rejected(db, approval_client) -> None:
    from app.services.approval_client import AttachmentMeta

    settings = get_settings()
    task, _ = get_and_upsert_detail(db, approval_client, "AP20261021001")
    db.commit()
    meta = AttachmentMeta(
        attachment_id="ATT-001",
        file_name="sample_01_standard.docx",
        file_type="docx",
        file_size=settings.max_file_mb * 1024 * 1024 + 1,
    )
    with pytest.raises(DownloadFailedError):
        ensure_attachment(db, approval_client, task, meta, settings)
    db.commit()

    record = db.scalar(
        select(ApprovalAttachment).where(
            ApprovalAttachment.task_id == task.id,
            ApprovalAttachment.external_attachment_id == "ATT-001",
        )
    )
    assert record is not None and record.download_status == "failed"
    assert "大小上限" in (record.download_error or "")
