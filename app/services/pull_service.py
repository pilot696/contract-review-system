"""拉取模块：待办/详情拉取 + 按 approval_code 去重 upsert（设计文档 §7.2）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.errors import ExternalServiceError
from app.models import ApprovalAttachment, ApprovalTask
from app.services.approval_client import (
    ApprovalDetail,
    ApprovalSystemClient,
)
from app.services.attachment_service import ALLOWED_FILE_TYPES

_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class PullResult:
    tasks: list[ApprovalTask]
    total: int


def _applied_at(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=_TZ)


def pull_and_upsert(db: Session, client: ApprovalSystemClient, limit: int) -> PullResult:
    """拉取待办列表并按 approval_code 去重入库。

    已存在的任务只更新标题/申请人/申请时间/附件数，**不覆盖**任务状态与回写状态。
    """
    summaries = client.list_pending(limit)
    for summary in summaries:
        stmt = (
            pg_insert(ApprovalTask)
            .values(
                approval_code=summary.approval_code,
                approval_title=summary.title,
                applicant_name=summary.applicant_name,
                applied_at=_applied_at(summary.apply_date),
                attachment_count=summary.attachment_count,
                source="mock",
            )
            .on_conflict_do_update(
                index_elements=["approval_code"],
                set_={
                    "approval_title": summary.title,
                    "applicant_name": summary.applicant_name,
                    "applied_at": _applied_at(summary.apply_date),
                    "attachment_count": summary.attachment_count,
                    "updated_at": func.now(),
                },
            )
        )
        db.execute(stmt)
    db.flush()

    codes = [summary.approval_code for summary in summaries]
    if not codes:
        return PullResult(tasks=[], total=0)
    tasks = list(
        db.scalars(
            select(ApprovalTask)
            .where(ApprovalTask.approval_code.in_(codes))
            .order_by(ApprovalTask.id)
        ).all()
    )
    return PullResult(tasks=tasks, total=len(summaries))


def get_and_upsert_detail(
    db: Session, client: ApprovalSystemClient, case_id: str
) -> tuple[ApprovalTask, ApprovalDetail]:
    """拉取单个审批单详情，更新任务核心字段与附件元数据（保留下载状态）。"""
    detail = client.get_approval(case_id)

    stmt = (
        pg_insert(ApprovalTask)
        .values(
            approval_code=detail.approval_code,
            approval_title=detail.title,
            applicant_name=detail.applicant_name,
            applied_at=_applied_at(detail.apply_date),
            attachment_count=len(detail.attachments),
            source="mock",
        )
        .on_conflict_do_update(
            index_elements=["approval_code"],
            set_={
                "approval_title": detail.title,
                "applicant_name": detail.applicant_name,
                "applied_at": _applied_at(detail.apply_date),
                "attachment_count": len(detail.attachments),
                "updated_at": func.now(),
            },
        )
    )
    db.execute(stmt)
    db.flush()

    task = db.scalar(select(ApprovalTask).where(ApprovalTask.approval_code == case_id))
    if task is None:  # pragma: no cover - upsert 后必然存在
        raise RuntimeError(f"任务 upsert 失败：{case_id}")

    for meta in detail.attachments:
        if meta.file_type.lower() not in ALLOWED_FILE_TYPES:
            raise ExternalServiceError(
                f"审批单包含不支持的附件类型：{meta.file_type}（{meta.file_name}）"
            )

    for meta in detail.attachments:
        att_stmt = (
            pg_insert(ApprovalAttachment)
            .values(
                task_id=task.id,
                external_attachment_id=meta.attachment_id,
                file_name=meta.file_name,
                file_type=meta.file_type,
                download_status="pending",
            )
            .on_conflict_do_update(
                constraint="uq_attachments_task_external",
                set_={
                    "file_name": meta.file_name,
                    "file_type": meta.file_type,
                    "updated_at": func.now(),
                },
            )
        )
        db.execute(att_stmt)
    db.flush()
    return task, detail
