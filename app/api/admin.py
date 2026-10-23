"""系统接口：健康检查、统计、故障注入代理、演示重置。"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import ExternalServiceError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


class FaultRequest(BaseModel):
    mode: str
    approval_code: str | None = None
    latency_ms: int = Field(default=0, ge=0, le=60000)


@router.get("/")
def root() -> dict:
    return {
        "service": "合同审批审查系统",
        "version": __version__,
        "docs": "/docs",
        "health": "/api/health",
        "console": "/console",
    }


@router.get("/api/health")
def health(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - health 需要兜底为 503
        logger.error("health check database failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "db": "error",
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "version": __version__,
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "db": "ok",
            "app_env": settings.app_env,
            "version": __version__,
        },
    )


@router.get("/api/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    status_rows = db.execute(
        text("SELECT task_status, count(*) FROM approval_tasks GROUP BY task_status")
    ).all()
    hit_rows = db.execute(
        text(
            "SELECT r.risk_level, count(*) FROM rule_hits h "
            "JOIN review_rules r ON r.id = h.rule_id "
            "WHERE h.hit_status = 'hit' GROUP BY r.risk_level"
        )
    ).all()
    write_rows = db.execute(
        text("SELECT write_status, count(*) FROM approval_tasks GROUP BY write_status")
    ).all()
    duration = db.execute(
        text(
            "SELECT count(*) FILTER (WHERE task_status = 'done') AS done_count, "
            "count(*) AS total_count, "
            "avg(EXTRACT(EPOCH FROM (updated_at - created_at))) "
            "  FILTER (WHERE task_status = 'done') AS avg_seconds "
            "FROM approval_tasks"
        )
    ).one()
    done_count, total_count, avg_seconds = duration
    return {
        "ok": True,
        "data": {
            "task_status": {row[0]: row[1] for row in status_rows},
            "write_status": {row[0]: row[1] for row in write_rows},
            "blocked": int(
                db.execute(
                    text("SELECT count(*) FROM approval_tasks WHERE task_status='blocked'")
                ).scalar_one()
                or 0
            ),
            "hits_by_risk": {row[0]: row[1] for row in hit_rows},
            "rules_active": int(
                db.execute(
                    text("SELECT count(*) FROM review_rules WHERE rule_status='active'")
                ).scalar_one()
                or 0
            ),
            "success_rate": round(float(done_count) / int(total_count), 4) if total_count else 0.0,
            "avg_duration_seconds": round(float(avg_seconds), 1) if avg_seconds is not None else None,
        },
    }


def _mock_url(settings: Settings, path: str) -> str:
    return f"{settings.approval_system_base_url.rstrip('/')}{path}"


@router.post("/api/admin/faults")
def set_faults(
    body: FaultRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.is_dev:
        raise ExternalServiceError("故障注入仅在 dev 环境可用")
    try:
        response = httpx.post(
            _mock_url(settings, "/admin/faults"),
            json=body.model_dump(),
            timeout=settings.approval_system_timeout_seconds,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        raise ExternalServiceError(f"Mock 审批系统不可达：{exc}") from exc
    if response.status_code != 200:
        raise ExternalServiceError(f"设置故障注入失败：HTTP {response.status_code}")
    return {"ok": True, "data": response.json()}


@router.delete("/api/admin/faults")
def clear_faults(settings: Settings = Depends(get_settings)) -> dict:
    if not settings.is_dev:
        raise ExternalServiceError("故障注入仅在 dev 环境可用")
    try:
        response = httpx.delete(
            _mock_url(settings, "/admin/faults"),
            timeout=settings.approval_system_timeout_seconds,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        raise ExternalServiceError(f"Mock 审批系统不可达：{exc}") from exc
    return {"ok": True, "data": response.json()}


@router.post("/api/admin/reset")
def reset_demo(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """清空业务数据并重置 Mock（仅 dev）。规则表保留。"""
    if not settings.is_dev:
        raise ExternalServiceError("演示重置仅在 dev 环境可用")
    try:
        httpx.post(
            _mock_url(settings, "/admin/reset"),
            timeout=settings.approval_system_timeout_seconds,
            trust_env=False,
        )
    except httpx.HTTPError:  # Mock 未启动时也允许清库
        logger.warning("mock approval system unreachable during reset")
    db.execute(
        text(
            "TRUNCATE task_logs, comment_logs, review_results, rule_hits, contract_parses, "
            "approval_attachments, approval_tasks RESTART IDENTITY CASCADE"
        )
    )
    db.commit()
    return {"ok": True, "data": {"reset": True}}
