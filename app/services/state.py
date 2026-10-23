"""任务状态辅助：日志、blocked 标记、时间工具（供 task/writeback 服务共用）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.logging import redact_text
from app.models import ApprovalTask, TaskLog

_TZ = timezone(timedelta(hours=8))


def now() -> datetime:
    return datetime.now(_TZ)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def log_task(
    db: Session,
    task_id: int | None,
    level: str,
    log_type: str,
    content: str,
    context: dict[str, Any] | None = None,
) -> None:
    db.add(
        TaskLog(
            task_id=task_id,
            log_level=level,
            log_type=log_type,
            log_content=redact_text(content, max_chars=2000),
            context_json=context,
        )
    )


def mark_blocked(
    db: Session,
    task: ApprovalTask,
    stage: str,
    reason: str,
    detail: str = "",
) -> None:
    task.task_status = "blocked"
    task.failed_stage = stage
    task.blocked_reason = reason
    task.last_error = (detail or reason)[:1000]
    log_task(
        db,
        task.id,
        "error",
        "state",
        f"任务阻塞：stage={stage}, reason={reason}, detail={detail[:300]}",
        {"stage": stage, "reason": reason},
    )


def clear_blocked(task: ApprovalTask) -> None:
    task.failed_stage = None
    task.blocked_reason = None
    task.last_error = None
