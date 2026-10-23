"""工具接口：7 个工具统一入口 POST /api/tools/{tool_name}（设计文档 §6）。

请求体即工具入参，响应 {"ok": true, "data": {...}}；错误由全局异常处理器统一为
{"ok": false, "error": {...}}。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.config import Settings
from app.errors import AppError, NotFoundError
from app.models import ApprovalAttachment, ApprovalTask
from app.schemas import (
    DownloadAttachmentRequest,
    GetApprovalRequest,
    ListPendingRequest,
    ParseDocumentRequest,
    RunRulesRequest,
    SaveReviewResultRequest,
    WriteCommentRequest,
)
from app.services.approval_client import AttachmentMeta, build_approval_client
from app.services.attachment_service import ensure_attachment
from app.services.parsing import parse_and_save
from app.services.pull_service import get_and_upsert_detail, pull_and_upsert
from app.services.rules import run_rules
from app.services.task_service import load_task
from app.services.writeback_service import save_review_result as save_review_result_service
from app.services.writeback_service import write_approval_comment as write_comment_service

router = APIRouter(prefix="/api/tools", tags=["tools"])

_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def _iso_date(value: datetime | None) -> str | None:
    return value.date().isoformat() if value else None


@router.post("/list_pending_contract_approvals")
def list_pending_contract_approvals(
    request: ListPendingRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    client = build_approval_client(settings)
    try:
        result = pull_and_upsert(db, client, request.limit)
        db.commit()
    finally:
        client.close()

    return _ok(
        {
            "approvals": [
                {
                    "approval_code": task.approval_code,
                    "title": task.approval_title,
                    "applicant_name": task.applicant_name,
                    "apply_date": _iso_date(task.applied_at),
                    "attachment_count": task.attachment_count,
                    "task_status": task.task_status,
                }
                for task in result.tasks
            ],
            "total": result.total,
            "pulled_at": _now_iso(),
        }
    )


@router.post("/get_contract_approval")
def get_contract_approval(
    request: GetApprovalRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    client = build_approval_client(settings)
    try:
        task, detail = get_and_upsert_detail(db, client, request.instance_id)
        db.commit()
    finally:
        client.close()

    records = {
        record.external_attachment_id: record
        for record in db.scalars(
            select(ApprovalAttachment).where(ApprovalAttachment.task_id == task.id)
        )
    }
    attachments = []
    for meta in detail.attachments:
        record = records.get(meta.attachment_id)
        attachments.append(
            {
                "attachment_id": meta.attachment_id,
                "file_name": meta.file_name,
                "file_type": meta.file_type,
                "file_size": meta.file_size,
                "download_status": record.download_status if record else "pending",
                "local_path": record.file_path if record else None,
            }
        )

    return _ok(
        {
            "approval_info": {
                "approval_code": task.approval_code,
                "title": task.approval_title,
                "applicant_name": task.applicant_name,
                "apply_date": _iso_date(task.applied_at),
                "status": detail.status,
            },
            "form_data": detail.form_data,
            "attachments": attachments,
            "current_status": {
                "task_status": task.task_status,
                "write_status": task.write_status,
                "failed_stage": task.failed_stage,
                "blocked_reason": task.blocked_reason,
            },
        }
    )


@router.post("/download_contract_attachment")
def download_contract_attachment(
    request: DownloadAttachmentRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    task = db.scalar(
        select(ApprovalTask).where(ApprovalTask.approval_code == request.instance_id)
    )
    if task is None:
        raise NotFoundError("任务不存在，请先调用 list_pending_contract_approvals")

    record = db.scalar(
        select(ApprovalAttachment).where(
            ApprovalAttachment.task_id == task.id,
            ApprovalAttachment.external_attachment_id == request.attachment_id,
        )
    )
    if record is None:
        raise NotFoundError("附件元数据不存在，请先调用 get_contract_approval")

    meta = AttachmentMeta(
        attachment_id=record.external_attachment_id,
        file_name=request.file_name or record.file_name,
        file_type=record.file_type,
        file_size=record.file_size,
    )
    client = build_approval_client(settings)
    try:
        saved = ensure_attachment(db, client, task, meta, settings)
        db.commit()
    finally:
        client.close()

    return _ok(
        {
            "attachment_id": saved.external_attachment_id,
            "document_id": saved.id,
            "file_name": saved.file_name,
            "file_type": saved.file_type,
            "file_path": saved.file_path,
            "file_size": saved.file_size,
            "file_hash": saved.file_hash,
            "download_status": saved.download_status,
        }
    )


@router.post("/parse_contract_document")
def parse_contract_document(
    request: ParseDocumentRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    attachment = db.get(ApprovalAttachment, request.document_id)
    if attachment is None:
        raise NotFoundError(
            "附件记录不存在（document_id 无效，请先调用 download_contract_attachment）"
        )
    if not attachment.file_path or not Path(attachment.file_path).exists():
        raise NotFoundError("附件尚未下载，请先调用 download_contract_attachment")

    _record, outcome = parse_and_save(db, attachment, settings)
    db.commit()
    return _ok(outcome.to_tool_dict(attachment.id))


@router.post("/run_contract_rules")
def run_contract_rules(
    request: RunRulesRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    task = db.scalar(
        select(ApprovalTask).where(ApprovalTask.approval_code == request.case_id)
    )
    if task is None:
        raise NotFoundError("任务不存在，请先调用 list_pending_contract_approvals")

    result = run_rules(db, task, comment_max_chars=settings.comment_max_chars)
    db.commit()
    return _ok(result.to_tool_dict())


@router.post("/save_review_result")
def save_review_result(
    request: SaveReviewResultRequest,
    db: Session = Depends(get_db),
) -> dict:
    task = load_task(db, request.case_id)
    result = save_review_result_service(
        db,
        task,
        overall_risk_level=request.overall_risk_level,
        summary_text=request.summary_text,
        focus_points_json=request.focus_points_json,
        comment_text=request.comment_text,
    )
    db.commit()
    return _ok(result.to_tool_dict())


@router.post("/write_approval_comment")
def write_approval_comment(
    request: WriteCommentRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    task = load_task(db, request.instance_id)
    client = build_approval_client(settings)
    try:
        result = write_comment_service(db, client, task, request.review_id, settings)
        db.commit()
    except AppError:
        db.commit()  # 保留 write_status=failed / blocked 状态
        raise
    finally:
        client.close()
    return _ok(result.to_tool_dict())
