"""任务资源接口（Web 控制台与验收使用，设计文档 §6.9）。"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.config import Settings
from app.errors import NotFoundError
from app.models import ApprovalAttachment, ApprovalTask, ContractParse, ReviewResult, RuleHit, TaskLog, ReviewRule
from app.services.task_service import (
    load_task,
    run_pipeline,
    start_processing,
    start_retry,
    task_to_dict,
)
from app.services.writeback_service import latest_review, write_approval_comment

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def _serialize_log(log: TaskLog) -> dict:
    return {
        "id": log.id,
        "log_level": log.log_level,
        "log_type": log.log_type,
        "log_content": log.log_content,
        "created_at": log.created_at.isoformat(timespec="seconds") if log.created_at else None,
    }


@router.get("")
def list_tasks(
    status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(ApprovalTask)
    count_stmt = select(func.count()).select_from(ApprovalTask)
    if status:
        stmt = stmt.where(ApprovalTask.task_status == status)
        count_stmt = count_stmt.where(ApprovalTask.task_status == status)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        stmt = stmt.where(
            ApprovalTask.approval_code.ilike(pattern) | ApprovalTask.approval_title.ilike(pattern)
        )
        count_stmt = count_stmt.where(
            ApprovalTask.approval_code.ilike(pattern) | ApprovalTask.approval_title.ilike(pattern)
        )
    total = int(db.scalar(count_stmt) or 0)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    items = list(
        db.scalars(
            stmt.order_by(ApprovalTask.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return _ok(
        {
            "items": [task_to_dict(task) for task in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/{case_id}")
def get_task(case_id: str, db: Session = Depends(get_db)) -> dict:
    task = load_task(db, case_id)
    attachments = list(
        db.scalars(
            select(ApprovalAttachment)
            .where(ApprovalAttachment.task_id == task.id)
            .order_by(ApprovalAttachment.id)
        ).all()
    )
    parses = list(
        db.scalars(select(ContractParse).where(ContractParse.task_id == task.id)).all()
    )
    review = latest_review(db, task)
    return _ok(
        {
            "task": task_to_dict(task),
            "attachments": [
                {
                    "document_id": att.id,
                    "attachment_id": att.external_attachment_id,
                    "file_name": att.file_name,
                    "file_type": att.file_type,
                    "file_size": att.file_size,
                    "file_hash": att.file_hash,
                    "download_status": att.download_status,
                    "file_path": att.file_path,
                }
                for att in attachments
            ],
            "parse_summary": [
                {
                    "document_id": parse.attachment_id,
                    "parse_status": parse.parse_status,
                    "parse_error": parse.parse_error,
                    "ocr_used": parse.ocr_used,
                    "page_count": parse.page_count,
                }
                for parse in parses
            ],
            "review": (
                {
                    "review_id": review.id,
                    "overall_risk_level": review.overall_risk_level,
                    "hit_count": review.hit_count,
                }
                if review
                else None
            ),
        }
    )


@router.post("/{case_id}/process", status_code=202)
def process_task(
    case_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    task = load_task(db, case_id)
    new_status = start_processing(db, task)
    db.commit()
    background.add_task(run_pipeline, case_id)
    return _ok({"approval_code": case_id, "task_status": new_status})


@router.post("/{case_id}/retry", status_code=202)
def retry_task(
    case_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    task = load_task(db, case_id)
    new_status = start_retry(db, task, settings)
    db.commit()
    background.add_task(run_pipeline, case_id)
    return _ok({"approval_code": case_id, "task_status": new_status})


@router.get("/{case_id}/parse")
def get_parse(case_id: str, db: Session = Depends(get_db)) -> dict:
    task = load_task(db, case_id)
    attachments = {
        att.id: att
        for att in db.scalars(
            select(ApprovalAttachment).where(ApprovalAttachment.task_id == task.id)
        ).all()
    }
    parses = list(
        db.scalars(select(ContractParse).where(ContractParse.task_id == task.id)).all()
    )
    return _ok(
        {
            "approval_code": case_id,
            "items": [
                {
                    "document_id": parse.attachment_id,
                    "file_name": attachments.get(parse.attachment_id).file_name
                    if attachments.get(parse.attachment_id)
                    else None,
                    "parse_status": parse.parse_status,
                    "parse_error": parse.parse_error,
                    "ocr_used": parse.ocr_used,
                    "page_count": parse.page_count,
                    "basic_info": parse.basic_info_json,
                    "clauses": parse.clause_info_json,
                }
                for parse in parses
            ],
        }
    )


@router.get("/{case_id}/review")
def get_review(case_id: str, db: Session = Depends(get_db)) -> dict:
    task = load_task(db, case_id)
    rules = {rule.id: rule for rule in db.scalars(select(ReviewRule)).all()}
    hits = list(
        db.scalars(select(RuleHit).where(RuleHit.task_id == task.id)).all()
    )
    review = latest_review(db, task)
    return _ok(
        {
            "approval_code": case_id,
            "overall_risk_level": review.overall_risk_level if review else None,
            "hits": [
                {
                    "rule_code": rules[hit.rule_id].rule_code if hit.rule_id in rules else None,
                    "rule_name": rules[hit.rule_id].rule_name if hit.rule_id in rules else None,
                    "risk_level": rules[hit.rule_id].risk_level if hit.rule_id in rules else None,
                    "evidence_text": hit.evidence_text,
                    "evidence_position": hit.evidence_position,
                    "evidence_page": hit.evidence_page,
                    "hit_status": hit.hit_status,
                    "suggestion": rules[hit.rule_id].suggestion_text if hit.rule_id in rules else None,
                }
                for hit in hits
            ],
            "focus_points": review.focus_points_json if review else [],
        }
    )


@router.get("/{case_id}/result")
def get_result(case_id: str, db: Session = Depends(get_db)) -> dict:
    task = load_task(db, case_id)
    review = latest_review(db, task)
    if review is None:
        raise NotFoundError("该任务尚无审查结果")
    return _ok(
        {
            "review_id": review.id,
            "overall_risk_level": review.overall_risk_level,
            "summary_text": review.summary_text,
            "focus_points": review.focus_points_json,
            "comment_text": review.comment_text,
            "hit_count": review.hit_count,
            "write_status": task.write_status,
            "created_at": review.created_at.isoformat(timespec="seconds")
            if review.created_at
            else None,
        }
    )


@router.post("/{case_id}/result/writeback")
def writeback_result(
    case_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    from app.services.approval_client import build_approval_client

    task = load_task(db, case_id)
    review = latest_review(db, task)
    if review is None:
        raise NotFoundError("该任务尚无审查结果")
    client = build_approval_client(settings)
    try:
        result = write_approval_comment(db, client, task, review.id, settings)
        db.commit()
    except Exception:
        db.commit()
        raise
    finally:
        client.close()
    return _ok(result.to_tool_dict())


@router.get("/{case_id}/logs")
def get_logs(
    case_id: str,
    level: str | None = None,
    log_type: str | None = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
) -> dict:
    task = load_task(db, case_id)
    stmt = select(TaskLog).where(TaskLog.task_id == task.id)
    count_stmt = select(func.count()).select_from(TaskLog).where(TaskLog.task_id == task.id)
    if level:
        stmt = stmt.where(TaskLog.log_level == level)
        count_stmt = count_stmt.where(TaskLog.log_level == level)
    if log_type:
        stmt = stmt.where(TaskLog.log_type == log_type)
        count_stmt = count_stmt.where(TaskLog.log_type == log_type)
    total = int(db.scalar(count_stmt) or 0)
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    items = list(
        db.scalars(
            stmt.order_by(TaskLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return _ok(
        {
            "items": [_serialize_log(item) for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )
