"""结果保存 + 评论回写（设计文档 §7.6，工具 6–7）。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import NotFoundError, ValidationError, WritebackFailedError
from app.models import ApprovalTask, CommentLog, ReviewResult, RuleHit
from app.services.approval_client import ApprovalSystemClient, CommentResult
from app.services.state import log_task, mark_blocked, now_iso

logger = logging.getLogger(__name__)

_RISK_LEVELS = {"low", "medium", "high"}


@dataclass
class SaveReviewResult:
    review_id: int
    case_id: str
    hit_count: int
    saved_at: str

    def to_tool_dict(self) -> dict:
        return {
            "review_id": self.review_id,
            "case_id": self.case_id,
            "hit_count": self.hit_count,
            "saved_at": self.saved_at,
        }


@dataclass
class WriteResult:
    write_status: str
    write_response: str
    written_at: str
    idempotent_replay: bool = False

    def to_tool_dict(self) -> dict:
        return {
            "write_status": self.write_status,
            "write_response": self.write_response,
            "written_at": self.written_at,
            "idempotent_replay": self.idempotent_replay,
        }


def _parse_focus_points(raw: str) -> list[dict]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise ValidationError("focus_points_json 不是合法 JSON") from exc
    if not isinstance(payload, list):
        raise ValidationError("focus_points_json 必须是 JSON 数组")
    return payload


def save_review_result(
    db: Session,
    task: ApprovalTask,
    *,
    overall_risk_level: str,
    summary_text: str,
    focus_points_json: str,
    comment_text: str,
) -> SaveReviewResult:
    """保存/覆盖审查结果（按 task 幂等 upsert）。"""
    if overall_risk_level not in _RISK_LEVELS:
        raise ValidationError(
            "overall_risk_level 必须是 low/medium/high", detail={"value": overall_risk_level}
        )
    if not summary_text.strip():
        raise ValidationError("summary_text 不能为空")
    if not comment_text.strip():
        raise ValidationError("comment_text 不能为空")
    focus_points = _parse_focus_points(focus_points_json)

    hit_count = int(
        db.scalar(
            select(func.count())
            .select_from(RuleHit)
            .where(RuleHit.task_id == task.id, RuleHit.hit_status == "hit")
        )
        or 0
    )

    record = db.scalar(select(ReviewResult).where(ReviewResult.task_id == task.id))
    if record is None:
        record = ReviewResult(task_id=task.id)
        db.add(record)

    record.overall_risk_level = overall_risk_level
    record.summary_text = summary_text
    record.focus_points_json = focus_points
    record.comment_text = comment_text
    record.hit_count = hit_count
    db.flush()

    log_task(db, task.id, "info", "review", f"审查结果已保存：review_id={record.id}")
    return SaveReviewResult(
        review_id=record.id,
        case_id=task.approval_code,
        hit_count=hit_count,
        saved_at=now_iso(),
    )


def _idempotency_key(task: ApprovalTask, review: ReviewResult) -> str:
    return f"{task.approval_code}:review:{review.id}"


def perform_writeback(
    db: Session,
    client: ApprovalSystemClient,
    task: ApprovalTask,
    review: ReviewResult,
    settings: Settings,
) -> WriteResult:
    """执行一次回写（不提交事务）：幂等键去重，失败写 failed 日志并抛出。"""
    key = _idempotency_key(task, review)
    existing = db.scalar(
        select(CommentLog).where(
            CommentLog.idempotency_key == key,
            CommentLog.write_status == "success",
        )
    )
    if existing is not None:
        task.write_status = "success"
        db.flush()
        return WriteResult(
            write_status="success",
            write_response=existing.write_response_text or "评论已回写（幂等重放）",
            written_at=existing.created_at.isoformat(timespec="seconds") if existing.created_at else now_iso(),
            idempotent_replay=True,
        )

    db.add(
        CommentLog(
            task_id=task.id,
            review_id=review.id,
            write_status="writing",
            idempotency_key=key,
        )
    )
    task.write_status = "writing"
    db.flush()

    try:
        result: CommentResult = client.post_comment(
            task.approval_code, review.comment_text, key
        )
        if result.status != "success":
            raise WritebackFailedError(f"审批系统返回失败：{result.response_text}")
    except Exception as exc:
        db.add(
            CommentLog(
                task_id=task.id,
                review_id=review.id,
                write_status="failed",
                idempotency_key=key,
                write_response_text=str(exc)[:1000],
            )
        )
        task.write_status = "failed"
        db.flush()
        log_task(db, task.id, "error", "writeback", f"评论回写失败：{exc}")
        if isinstance(exc, WritebackFailedError):
            raise
        raise WritebackFailedError(str(exc)) from exc

    db.add(
        CommentLog(
            task_id=task.id,
            review_id=review.id,
            write_status="success",
            idempotency_key=key,
            write_response_text=result.response_text[:1000],
        )
    )
    task.write_status = "success"
    db.flush()
    log_task(
        db,
        task.id,
        "info",
        "writeback",
        f"评论回写成功：review_id={review.id}, replay={result.idempotent_replay}",
    )
    return WriteResult(
        write_status="success",
        write_response=result.response_text[:1000],
        written_at=now_iso(),
        idempotent_replay=result.idempotent_replay,
    )


def write_approval_comment(
    db: Session,
    client: ApprovalSystemClient,
    task: ApprovalTask,
    review_id: int,
    settings: Settings,
) -> WriteResult:
    """工具 7：回写指定 review；失败时按配置标记 blocked，然后抛出异常。"""
    review = db.get(ReviewResult, review_id)
    if review is None or review.task_id != task.id:
        raise NotFoundError(f"审查结果不存在或不属于该任务：review_id={review_id}")

    try:
        result = perform_writeback(db, client, task, review, settings)
        db.flush()
        return result
    except WritebackFailedError:
        if settings.writeback_blocking and task.task_status == "done":
            mark_blocked(db, task, "writeback", "WRITEBACK_FAILED", task.last_error or "评论回写失败")
        db.flush()
        raise


def latest_review(db: Session, task: ApprovalTask) -> ReviewResult | None:
    return db.scalar(select(ReviewResult).where(ReviewResult.task_id == task.id))
