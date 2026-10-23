"""附件模块：下载、校验、本地存储与元数据维护（设计文档 §7.3）。"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AttachmentMissingError, DownloadFailedError
from app.models import ApprovalAttachment, ApprovalTask
from app.services.approval_client import ApprovalSystemClient, AttachmentMeta

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t\x00-\x1f]')
ALLOWED_FILE_TYPES = frozenset({"pdf", "docx", "png", "jpg", "jpeg"})


def _check_download_limits(meta: AttachmentMeta, settings: Settings) -> None:
    """扩展名白名单 + 单附件大小上限（设计文档 §6.4 / A3）。"""
    if meta.file_type.lower() not in ALLOWED_FILE_TYPES:
        raise DownloadFailedError(f"不支持的附件类型：{meta.file_type}")
    limit_bytes = settings.max_file_mb * 1024 * 1024
    if meta.file_size is not None and meta.file_size > limit_bytes:
        raise DownloadFailedError(
            f"附件超过大小上限：{meta.file_size} > {settings.max_file_mb}MB"
        )



def safe_file_name(name: str) -> str:
    """去掉路径分隔符与控制字符，防目录穿越。"""
    base = Path(name.replace("\\", "/")).name
    base = _UNSAFE_CHARS.sub("_", base).strip(" .")
    return (base[:100] or "attachment")


def safe_path_segment(value: str) -> str:
    return _UNSAFE_CHARS.sub("_", value).strip(" .")[:64] or "unknown"


def storage_path(
    settings: Settings, approval_code: str, attachment_id: str, file_name: str
) -> Path:
    return (
        Path(settings.attachment_root)
        / safe_path_segment(approval_code)
        / f"{safe_path_segment(attachment_id)}_{safe_file_name(file_name)}"
    )


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mark_failed(db: Session, record: ApprovalAttachment, error: str) -> None:
    record.download_status = "failed"
    record.download_error = error[:1000]
    db.flush()


def ensure_attachment(
    db: Session,
    client: ApprovalSystemClient,
    task: ApprovalTask,
    meta: AttachmentMeta,
    settings: Settings,
) -> ApprovalAttachment:
    """确保附件已下载到本地并落库；已下载且哈希一致时跳过网络请求。"""
    record = db.scalar(
        select(ApprovalAttachment).where(
            ApprovalAttachment.task_id == task.id,
            ApprovalAttachment.external_attachment_id == meta.attachment_id,
        )
    )

    # 类型/大小校验先于建记录，避免非法类型触发 DB CHECK 约束异常
    try:
        _check_download_limits(meta, settings)
    except DownloadFailedError as exc:
        if record is not None:
            _mark_failed(db, record, str(exc))
        raise

    if record is None:
        record = ApprovalAttachment(
            task_id=task.id,
            external_attachment_id=meta.attachment_id,
            file_name=meta.file_name,
            file_type=meta.file_type,
            download_status="pending",
        )
        db.add(record)
        db.flush()

    target = storage_path(settings, task.approval_code, meta.attachment_id, meta.file_name)
    if record.download_status == "success" and record.file_path:
        existing = Path(record.file_path)
        if existing.exists() and sha256_file(existing) == (record.file_hash or ""):
            return record

    try:
        content = client.download_attachment(task.approval_code, meta.attachment_id, meta.file_name)
    except AttachmentMissingError:
        _mark_failed(db, record, "ATTACHMENT_MISSING")
        raise
    except DownloadFailedError:
        _mark_failed(db, record, "DOWNLOAD_FAILED")
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转换为附件下载失败
        _mark_failed(db, record, str(exc))
        raise DownloadFailedError(f"附件下载失败：{exc}") from exc

    limit_bytes = settings.max_file_mb * 1024 * 1024
    if len(content) > limit_bytes:
        _mark_failed(db, record, f"附件超过大小上限：{len(content)} > {settings.max_file_mb}MB")
        raise DownloadFailedError(f"附件超过大小上限：{len(content)} 字节")

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(target.name + ".part")
    temp_path.write_bytes(content)
    os.replace(temp_path, target)

    record.file_path = str(target)
    record.file_size = len(content)
    record.file_hash = sha256_bytes(content)
    record.download_status = "success"
    record.download_error = None
    db.flush()
    return record


def ensure_all_attachments(
    db: Session,
    client: ApprovalSystemClient,
    task: ApprovalTask,
    settings: Settings,
) -> list[ApprovalAttachment]:
    """下载该任务的全部附件；无可用附件时抛 ATTACHMENT_MISSING。"""
    records = list(
        db.scalars(
            select(ApprovalAttachment)
            .where(ApprovalAttachment.task_id == task.id)
            .order_by(ApprovalAttachment.id)
        ).all()
    )
    if not records:
        raise AttachmentMissingError(f"审批单无合同附件：{task.approval_code}")

    result: list[ApprovalAttachment] = []
    for record in records:
        meta = AttachmentMeta(
            attachment_id=record.external_attachment_id,
            file_name=record.file_name,
            file_type=record.file_type,
            file_size=record.file_size,
        )
        result.append(ensure_attachment(db, client, task, meta, settings))
    return result
