"""任务编排：状态机 CAS、后台流水线、blocked/重试、启动恢复（设计文档 §5、§7.8）。"""

from __future__ import annotations

import json
import logging
import threading
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal, session_scope
from app.errors import (
    ConflictError,
    DownloadFailedError,
    ExternalServiceError,
    NotFoundError,
    ParseRequiredError,
    AttachmentMissingError,
)
from app.models import ApprovalAttachment, ApprovalTask
from app.services.approval_client import build_approval_client
from app.services.attachment_service import ensure_all_attachments
from app.services.parsing import parse_and_save
from app.services.pull_service import get_and_upsert_detail
from app.services.rules import run_rules
from app.services.state import log_task, mark_blocked, now
from app.services.writeback_service import latest_review, perform_writeback, save_review_result

logger = logging.getLogger(__name__)

_BLOCKED_STAGE_RETRY_TARGET = {
    "pull": "parsing",
    "download": "parsing",
    "parse": "parsing",
    "review": "reviewing",
    "writeback": "done",
}

_semaphore: threading.Semaphore | None = None
_semaphore_lock = threading.Lock()
_inflight: set[str] = set()
_inflight_lock = threading.Lock()


class BlockedError(Exception):
    """流水线内部信号：任务进入 blocked。"""

    def __init__(self, stage: str, reason: str, detail: str = "") -> None:
        self.stage = stage
        self.reason = reason
        self.detail = detail
        super().__init__(f"blocked at {stage}: {reason} {detail}".strip())


# --------------------------------------------------------------------------- #
# 查询与状态
# --------------------------------------------------------------------------- #
def load_task(db: Session, case_id: str) -> ApprovalTask:
    task = db.scalar(select(ApprovalTask).where(ApprovalTask.approval_code == case_id))
    if task is None:
        raise NotFoundError(f"任务不存在：{case_id}")
    return task


def cas_transition(
    db: Session,
    task_id: int,
    expected: str,
    target: str,
    **fields: object,
) -> bool:
    """CAS 状态转换：只有当前状态等于 expected 时才成功。"""
    values: dict[str, object] = {"task_status": target, "updated_at": func.now()}
    values.update(fields)
    result = db.execute(
        update(ApprovalTask)
        .where(ApprovalTask.id == task_id, ApprovalTask.task_status == expected)
        .values(**values)
    )
    return result.rowcount == 1


def start_processing(db: Session, task: ApprovalTask) -> str:
    """API 入口：pending → parsing（CAS），返回新状态。"""
    if task.task_status in {"parsing", "reviewing"}:
        raise ConflictError("任务正在处理中")
    if task.task_status == "done":
        raise ConflictError("任务已完成")
    if task.task_status == "blocked":
        raise ConflictError("任务已阻塞，请使用重试")
    if not cas_transition(db, task.id, "pending", "parsing"):
        raise ConflictError("任务状态已被其他执行者变更")
    log_task(db, task.id, "info", "state", "开始处理：pending → parsing")
    db.flush()
    return "parsing"


def start_retry(db: Session, task: ApprovalTask, settings: Settings) -> str:
    """API 入口：blocked → 续跑阶段（CAS），返回新状态。"""
    if task.task_status != "blocked":
        raise ConflictError("仅 blocked 任务可重试")
    if task.retry_count >= settings.max_retry:
        raise ConflictError(f"重试次数已达上限：{settings.max_retry}")
    target = _BLOCKED_STAGE_RETRY_TARGET.get(task.failed_stage or "parse", "parsing")
    previous_stage = task.failed_stage
    ok = cas_transition(
        db,
        task.id,
        "blocked",
        target,
        retry_count=ApprovalTask.retry_count + 1,
        failed_stage=None,
        blocked_reason=None,
        last_error=None,
    )
    if not ok:
        raise ConflictError("任务状态已被其他执行者变更")
    log_task(
        db,
        task.id,
        "info",
        "state",
        f"人工重试：blocked({previous_stage}) → {target}",
    )
    db.flush()
    return target


def task_to_dict(task: ApprovalTask) -> dict:
    return {
        "id": task.id,
        "approval_code": task.approval_code,
        "title": task.approval_title,
        "applicant_name": task.applicant_name,
        "apply_date": task.applied_at.date().isoformat() if task.applied_at else None,
        "attachment_count": task.attachment_count,
        "task_status": task.task_status,
        "write_status": task.write_status,
        "failed_stage": task.failed_stage,
        "blocked_reason": task.blocked_reason,
        "retry_count": task.retry_count,
        "last_error": task.last_error,
        "created_at": task.created_at.isoformat(timespec="seconds") if task.created_at else None,
        "updated_at": task.updated_at.isoformat(timespec="seconds") if task.updated_at else None,
    }


# --------------------------------------------------------------------------- #
# 后台流水线
# --------------------------------------------------------------------------- #
def _semaphore_for(settings: Settings) -> threading.Semaphore:
    global _semaphore
    with _semaphore_lock:
        if _semaphore is None:
            _semaphore = threading.Semaphore(max(1, settings.pipeline_concurrency))
        return _semaphore


def run_pipeline(case_id: str) -> None:
    """BackgroundTasks 入口：限流 + 防重入后执行流水线。"""
    settings = get_settings()
    semaphore = _semaphore_for(settings)
    with semaphore:
        with _inflight_lock:
            if case_id in _inflight:
                logger.info("pipeline already running, skip: %s", case_id)
                return
            _inflight.add(case_id)
        try:
            _run_pipeline(case_id, settings)
        finally:
            with _inflight_lock:
                _inflight.discard(case_id)


def _run_pipeline(case_id: str, settings: Settings) -> None:
    client = build_approval_client(settings)
    db = SessionLocal()
    try:
        for _ in range(6):
            task = db.scalar(select(ApprovalTask).where(ApprovalTask.approval_code == case_id))
            if task is None:
                return
            if task.task_status == "parsing":
                _parse_phase(db, task, client, settings)
                continue
            if task.task_status == "reviewing":
                _review_phase(db, task, settings)
                continue
            if task.task_status == "done" and task.write_status != "success":
                _writeback_phase(db, task, client, settings)
                continue
            return
    except BlockedError as exc:
        db.rollback()
        _mark_blocked(case_id, exc.stage, exc.reason, exc.detail)
    except Exception as exc:  # noqa: BLE001 - 兜底避免任务悬挂
        logger.exception("pipeline crashed: %s", case_id)
        db.rollback()
        _mark_blocked(case_id, "parse", "INTERNAL_ERROR", str(exc))
    finally:
        db.close()
        client.close()


def _parse_phase(db: Session, task: ApprovalTask, client, settings: Settings) -> None:
    # 附件元数据可能尚未拉取（只调用了待办拉取工具）：先补一次审批详情
    existing = db.scalar(
        select(func.count())
        .select_from(ApprovalAttachment)
        .where(ApprovalAttachment.task_id == task.id)
    )
    if not existing:
        try:
            get_and_upsert_detail(db, client, task.approval_code)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - 详情拉取失败归为审批接口异常
            db.rollback()
            raise BlockedError("download", "APPROVAL_API_ERROR", f"拉取审批详情失败：{exc}") from exc

    try:
        attachments = ensure_all_attachments(db, client, task, settings)
    except AttachmentMissingError as exc:
        db.commit()
        raise BlockedError("download", "ATTACHMENT_MISSING", str(exc)) from exc
    except DownloadFailedError as exc:
        db.commit()
        raise BlockedError("download", "DOWNLOAD_FAILED", str(exc)) from exc
    except ExternalServiceError as exc:
        db.commit()
        raise BlockedError("download", "APPROVAL_API_ERROR", str(exc)) from exc
    db.commit()

    failures: list[tuple[object, object]] = []
    for attachment in attachments:
        _record, outcome = parse_and_save(db, attachment, settings)
        if outcome.parse_status != "success":
            failures.append((attachment, outcome))
        db.commit()

    if failures:
        attachment, outcome = failures[0]
        code = outcome.parse_error or "PARSE_FAILED"
        raise BlockedError("parse", code, f"{attachment.file_name}: {code}")

    if not cas_transition(db, task.id, "parsing", "reviewing"):
        raise BlockedError("parse", "INTERNAL_ERROR", "任务状态已被其他执行者变更")
    log_task(db, task.id, "info", "state", "解析完成：parsing → reviewing")
    db.commit()
    logger.info("parse phase done: %s", task.approval_code)


def _review_phase(db: Session, task: ApprovalTask, settings: Settings) -> None:
    try:
        result = run_rules(db, task, comment_max_chars=settings.comment_max_chars)
    except ParseRequiredError as exc:
        db.rollback()
        raise BlockedError("parse", "PARSE_FAILED", str(exc)) from exc
    except ConflictError as exc:
        db.rollback()
        raise BlockedError("review", "RULE_ENGINE_ERROR", str(exc)) from exc

    save_review_result(
        db,
        task,
        overall_risk_level=result.overall_risk_level,
        summary_text=result.summary_text,
        focus_points_json=json.dumps(result.focus_points, ensure_ascii=False),
        comment_text=result.comment_text,
    )
    db.flush()
    if not cas_transition(db, task.id, "reviewing", "done"):
        raise BlockedError("review", "INTERNAL_ERROR", "任务状态已被其他执行者变更")
    log_task(db, task.id, "info", "state", "规则审查完成：reviewing → done")
    db.commit()
    logger.info("review phase done: %s", task.approval_code)


def _writeback_phase(db: Session, task: ApprovalTask, client, settings: Settings) -> None:
    review = latest_review(db, task)
    if review is None:
        raise BlockedError("writeback", "INTERNAL_ERROR", "缺少审查结果")
    try:
        perform_writeback(db, client, task, review, settings)
        db.commit()
        logger.info("writeback phase done: %s", task.approval_code)
    except Exception as exc:  # noqa: BLE001 - 回写失败按配置阻塞
        task.write_status = "failed"
        log_task(db, task.id, "error", "writeback", f"评论回写失败：{exc}")
        if settings.writeback_blocking:
            mark_blocked(db, task, "writeback", "WRITEBACK_FAILED", str(exc))
        db.commit()
        logger.warning("writeback failed: %s (%s)", task.approval_code, exc)


def _mark_blocked(case_id: str, stage: str, reason: str, detail: str) -> None:
    try:
        with session_scope() as db:
            task = db.scalar(select(ApprovalTask).where(ApprovalTask.approval_code == case_id))
            if task is None:
                return
            mark_blocked(db, task, stage, reason, detail)
    except Exception:  # noqa: BLE001 - 记录阻塞失败时只打日志
        logger.exception("failed to mark blocked: %s", case_id)


# --------------------------------------------------------------------------- #
# 启动恢复
# --------------------------------------------------------------------------- #
def recover_stale_tasks(settings: Settings | None = None) -> int:
    """服务重启时把悬挂的 parsing/reviewing/writing 任务置为 blocked（可重试）。"""
    settings = settings or get_settings()
    cutoff = now() - timedelta(seconds=settings.stale_task_seconds)
    recovered = 0
    with session_scope() as db:
        stale = list(
            db.scalars(
                select(ApprovalTask).where(
                    ApprovalTask.task_status.in_(("parsing", "reviewing"))
                )
            ).all()
        )
        for task in stale:
            if task.updated_at and task.updated_at < cutoff:
                stage = "parse" if task.task_status == "parsing" else "review"
                mark_blocked(db, task, stage, "INTERNAL_ERROR", "服务重启时发现悬挂任务")
                recovered += 1
        writing = list(
            db.scalars(
                select(ApprovalTask).where(ApprovalTask.write_status == "writing")
            ).all()
        )
        for task in writing:
            if task.updated_at and task.updated_at < cutoff:
                mark_blocked(db, task, "writeback", "WRITEBACK_FAILED", "服务重启时发现未完成回写")
                recovered += 1
    if recovered:
        logger.warning("recovered %s stale tasks as blocked", recovered)
    return recovered
