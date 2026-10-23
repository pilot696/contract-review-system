"""Web 控制台页面（服务端渲染 + 表单提交，无前端构建链）。"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.api.rules import RulePayload, validate_match_text
from app.config import Settings
from app.errors import AppError
from app.models import (
    ApprovalAttachment,
    ApprovalTask,
    ContractParse,
    ReviewResult,
    ReviewRule,
    RuleHit,
    TaskLog,
)
from app.services.approval_client import build_approval_client
from app.services.pull_service import pull_and_upsert
from app.services.rules import LEVEL_LABEL
from app.services.task_service import (
    load_task,
    run_pipeline,
    start_processing,
    start_retry,
)
from app.services.writeback_service import latest_review, write_approval_comment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/console", tags=["console"])

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "web" / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

def _render(name: str, context: dict):
    """兼容新版 Starlette 的 TemplateResponse(request, name, context) 签名。"""
    return templates.TemplateResponse(request=context["request"], name=name, context=context)


def error_page(request: Request, exc: AppError):
    """控制台页面的统一 HTML 错误页；API 请求仍由全局处理器返回 JSON。"""
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "request": request,
            "code": exc.code,
            "message": exc.message,
            "status_code": exc.http_status,
        },
        status_code=exc.http_status,
    )


TASK_LABELS = {
    "pending": "待处理",
    "parsing": "解析中",
    "reviewing": "审查中",
    "blocked": "已阻塞",
    "done": "已完成",
}
WRITE_LABELS = {
    "not_written": "未回写",
    "writing": "回写中",
    "success": "已回写",
    "failed": "回写失败",
}
RISK_LABELS = {"low": "低", "medium": "中", "high": "高"}
BLOCKED_LABELS = {
    "ATTACHMENT_MISSING": "附件缺失",
    "DOWNLOAD_FAILED": "下载失败",
    "OCR_FAILED": "OCR 失败",
    "DOCUMENT_EMPTY": "文档为空",
    "PARSE_FAILED": "解析失败",
    "APPROVAL_API_ERROR": "审批接口失败",
    "RULE_ENGINE_ERROR": "规则引擎异常",
    "WRITEBACK_FAILED": "回写失败",
    "INTERNAL_ERROR": "内部错误",
}

FIELD_LABELS = {
    "contract_title": "合同标题",
    "contract_no": "合同编号",
    "signing_entity": "签约主体",
    "counterparty_name": "对方名称",
    "amount": "金额",
    "currency": "币种",
    "effective_date": "生效时间",
    "expiration_date": "到期时间",
    "payment_terms": "付款条款",
    "delivery_terms": "交付条款",
    "acceptance_terms": "验收条款",
    "breach_terms": "违约条款",
    "confidentiality_terms": "保密条款",
    "data_terms": "数据条款",
    "ip_terms": "知识产权条款",
    "dispute_resolution_terms": "争议解决条款",
}


def _redirect(path: str, message: str | None = None) -> RedirectResponse:
    if message:
        separator = "&" if "?" in path else "?"
        path = f"{path}{separator}message={quote(message)}"
    return RedirectResponse(path, status_code=303)


def _base_context(request: Request, **extra: object) -> dict:
    context: dict = {
        "request": request,
        "task_labels": TASK_LABELS,
        "write_labels": WRITE_LABELS,
        "risk_labels": RISK_LABELS,
        "blocked_labels": BLOCKED_LABELS,
        "field_labels": FIELD_LABELS,
        "level_label": LEVEL_LABEL,
        "message": request.query_params.get("message"),
    }
    context.update(extra)
    return context


# --------------------------------------------------------------------------- #
# 页面：列表 / 详情 / 解析 / 命中 / 结果
# --------------------------------------------------------------------------- #
@router.get("")
def index(
    request: Request,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(ApprovalTask).order_by(ApprovalTask.created_at.desc()).limit(100)
    if status:
        stmt = select(ApprovalTask).where(ApprovalTask.task_status == status).order_by(
            ApprovalTask.created_at.desc()
        ).limit(100)
    tasks = list(db.scalars(stmt).all())
    auto_refresh = 2 if any(task.task_status in {"parsing", "reviewing"} for task in tasks) else None
    return _render(
        "index.html",
        _base_context(
            request,
            tasks=tasks,
            current_status=status,
            auto_refresh=auto_refresh,
        ),
    )


@router.post("/actions/pull")
def pull_action(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    client = build_approval_client(settings)
    try:
        result = pull_and_upsert(db, client, 50)
        db.commit()
        message = f"拉取完成：{result.total} 条待办"
    except AppError as exc:
        message = f"拉取失败：{exc.code} {exc.message}"
    finally:
        client.close()
    return _redirect("/console", message)


@router.post("/actions/process/{case_id}")
def process_action(
    case_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        task = load_task(db, case_id)
        start_processing(db, task)
        db.commit()
        background.add_task(run_pipeline, case_id)
    except AppError as exc:
        db.rollback()
        return _redirect(f"/console/tasks/{case_id}", f"处理失败：{exc.code} {exc.message}")
    return _redirect(f"/console/tasks/{case_id}", "已开始处理，页面将自动刷新")


@router.post("/actions/retry/{case_id}")
def retry_action(
    case_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        task = load_task(db, case_id)
        start_retry(db, task, settings)
        db.commit()
        background.add_task(run_pipeline, case_id)
    except AppError as exc:
        db.rollback()
        return _redirect(f"/console/tasks/{case_id}", f"重试失败：{exc.code} {exc.message}")
    return _redirect(f"/console/tasks/{case_id}", "已触发重试，页面将自动刷新")


@router.get("/tasks/{case_id}")
def task_detail(request: Request, case_id: str, db: Session = Depends(get_db)):
    task = load_task(db, case_id)
    attachments = list(
        db.scalars(
            select(ApprovalAttachment)
            .where(ApprovalAttachment.task_id == task.id)
            .order_by(ApprovalAttachment.id)
        ).all()
    )
    parses = list(db.scalars(select(ContractParse).where(ContractParse.task_id == task.id)).all())
    review = latest_review(db, task)
    auto_refresh = 2 if task.task_status in {"parsing", "reviewing"} or task.write_status == "writing" else None
    return _render(
        "task_detail.html",
        _base_context(
            request,
            task=task,
            attachments=attachments,
            parses=parses,
            review=review,
            auto_refresh=auto_refresh,
        ),
    )


@router.get("/tasks/{case_id}/parse")
def task_parse(request: Request, case_id: str, db: Session = Depends(get_db)):
    task = load_task(db, case_id)
    attachments = {
        att.id: att
        for att in db.scalars(
            select(ApprovalAttachment).where(ApprovalAttachment.task_id == task.id)
        ).all()
    }
    parses = list(db.scalars(select(ContractParse).where(ContractParse.task_id == task.id)).all())
    return _render(
        "task_parse.html",
        _base_context(request, task=task, parses=parses, attachments=attachments),
    )


@router.get("/tasks/{case_id}/review")
def task_review(request: Request, case_id: str, db: Session = Depends(get_db)):
    task = load_task(db, case_id)
    rules = {rule.id: rule for rule in db.scalars(select(ReviewRule)).all()}
    hits = list(db.scalars(select(RuleHit).where(RuleHit.task_id == task.id)).all())
    review = latest_review(db, task)
    return _render(
        "task_review.html",
        _base_context(request, task=task, hits=hits, rules=rules, review=review),
    )


@router.get("/tasks/{case_id}/result")
def task_result(request: Request, case_id: str, db: Session = Depends(get_db)):
    task = load_task(db, case_id)
    review = latest_review(db, task)
    return _render(
        "task_result.html",
        _base_context(request, task=task, review=review, level_label=LEVEL_LABEL),
    )


@router.get("/tasks/{case_id}/logs")
def task_logs(
    request: Request,
    case_id: str,
    level: str | None = None,
    log_type: str | None = None,
    db: Session = Depends(get_db),
):
    task = load_task(db, case_id)
    stmt = select(TaskLog).where(TaskLog.task_id == task.id).order_by(TaskLog.created_at.desc()).limit(300)
    if level:
        stmt = stmt.where(TaskLog.log_level == level)
    if log_type:
        stmt = stmt.where(TaskLog.log_type == log_type)
    logs = list(db.scalars(stmt).all())
    return _render(
        "task_logs.html",
        _base_context(request, task=task, logs=logs, level=level, log_type=log_type),
    )


@router.post("/tasks/{case_id}/writeback")
def writeback_action(
    case_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    task = load_task(db, case_id)
    review = latest_review(db, task)
    if review is None:
        return _redirect(f"/console/tasks/{case_id}/result", "尚无审查结果，无法回写")
    client = build_approval_client(settings)
    try:
        result = write_approval_comment(db, client, task, review.id, settings)
        db.commit()
        message = "评论回写成功" + ("（幂等重放）" if result.idempotent_replay else "")
    except AppError as exc:
        db.commit()
        message = f"评论回写失败：{exc.code} {exc.message}"
    finally:
        client.close()
    return _redirect(f"/console/tasks/{case_id}/result", message)


# --------------------------------------------------------------------------- #
# 管理页：规则 / 日志 / 故障
# --------------------------------------------------------------------------- #
@router.get("/admin/rules")
def admin_rules(request: Request, db: Session = Depends(get_db)):
    rules = list(db.scalars(select(ReviewRule).order_by(ReviewRule.priority, ReviewRule.id)).all())
    return _render("admin_rules.html", _base_context(request, rules=rules))


@router.post("/admin/rules/{rule_code}/toggle")
def admin_toggle_rule(rule_code: str, db: Session = Depends(get_db)):
    rule = db.scalar(select(ReviewRule).where(ReviewRule.rule_code == rule_code))
    if rule is None:
        return _redirect("/console/admin/rules", "规则不存在")
    rule.rule_status = "inactive" if rule.rule_status == "active" else "active"
    db.commit()
    return _redirect("/console/admin/rules", f"{rule_code} 已{'停用' if rule.rule_status == 'inactive' else '启用'}")


@router.post("/admin/rules/{rule_code}")
def admin_update_rule(
    rule_code: str,
    rule_name: str = Form(...),
    risk_level: str = Form(...),
    match_mode: str = Form(...),
    match_text: str = Form(...),
    suggestion_text: str = Form(...),
    priority: int = Form(100),
    rule_status: str = Form("active"),
    db: Session = Depends(get_db),
):
    rule = db.scalar(select(ReviewRule).where(ReviewRule.rule_code == rule_code))
    if rule is None:
        return _redirect("/console/admin/rules", "规则不存在")
    try:
        validate_match_text(match_mode, match_text)
    except AppError as exc:
        return _redirect("/console/admin/rules", f"保存失败：{exc.message}")
    rule.rule_name = rule_name
    rule.risk_level = risk_level
    rule.match_mode = match_mode
    rule.match_text = match_text
    rule.suggestion_text = suggestion_text
    rule.priority = priority
    rule.rule_status = rule_status
    db.commit()
    return _redirect("/console/admin/rules", f"{rule_code} 已保存")


@router.post("/admin/rules")
def admin_create_rule(
    rule_code: str = Form(...),
    rule_name: str = Form(...),
    risk_level: str = Form(...),
    match_mode: str = Form(...),
    match_text: str = Form(...),
    suggestion_text: str = Form(...),
    priority: int = Form(100),
    db: Session = Depends(get_db),
):
    try:
        payload = RulePayload(
            rule_code=rule_code,
            rule_name=rule_name,
            risk_level=risk_level,
            match_mode=match_mode,
            match_text=match_text,
            suggestion_text=suggestion_text,
            priority=priority,
        )
        validate_match_text(payload.match_mode, payload.match_text)
    except Exception as exc:  # noqa: BLE001 - 表单错误统一提示
        return _redirect("/console/admin/rules", f"新建失败：{exc}")
    if db.scalar(select(ReviewRule).where(ReviewRule.rule_code == rule_code)):
        return _redirect("/console/admin/rules", "规则编码已存在")
    db.add(ReviewRule(**payload.model_dump()))
    db.commit()
    return _redirect("/console/admin/rules", f"{rule_code} 已创建")


@router.get("/admin/logs")
def admin_logs(
    request: Request,
    level: str | None = None,
    log_type: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(TaskLog, ApprovalTask.approval_code).outerjoin(
        ApprovalTask, TaskLog.task_id == ApprovalTask.id
    ).order_by(TaskLog.created_at.desc()).limit(200)
    if level:
        stmt = stmt.where(TaskLog.log_level == level)
    if log_type:
        stmt = stmt.where(TaskLog.log_type == log_type)
    rows = db.execute(stmt).all()
    return _render(
        "admin_logs.html",
        _base_context(request, rows=rows, level=level, log_type=log_type),
    )


@router.get("/admin/faults")
def admin_faults(request: Request, settings: Settings = Depends(get_settings)):
    state: dict | None = None
    try:
        response = httpx.get(
            f"{settings.approval_system_base_url.rstrip('/')}/admin/state",
            timeout=settings.approval_system_timeout_seconds,
            trust_env=False,
        )
        if response.status_code == 200:
            state = response.json()
    except httpx.HTTPError:
        state = None
    return _render(
        "admin_faults.html",
        _base_context(request, state=state, mock_url=settings.approval_system_base_url),
    )


@router.post("/admin/faults")
def admin_set_fault(
    mode: str = Form(...),
    approval_code: str = Form(""),
    latency_ms: int = Form(0),
    settings: Settings = Depends(get_settings),
):
    try:
        response = httpx.post(
            f"{settings.approval_system_base_url.rstrip('/')}/admin/faults",
            json={
                "mode": mode,
                "approval_code": approval_code or None,
                "latency_ms": latency_ms,
            },
            timeout=settings.approval_system_timeout_seconds,
            trust_env=False,
        )
        message = f"故障已注入：{mode}" if response.status_code == 200 else f"注入失败：HTTP {response.status_code}"
    except httpx.HTTPError as exc:
        message = f"Mock 审批系统不可达：{exc}"
    return _redirect("/console/admin/faults", message)


@router.post("/admin/faults/clear")
def admin_clear_fault(settings: Settings = Depends(get_settings)):
    try:
        httpx.delete(
            f"{settings.approval_system_base_url.rstrip('/')}/admin/faults",
            timeout=settings.approval_system_timeout_seconds,
            trust_env=False,
        )
        message = "故障注入已清除"
    except httpx.HTTPError as exc:
        message = f"Mock 审批系统不可达：{exc}"
    return _redirect("/console/admin/faults", message)


@router.post("/admin/reset")
def admin_reset(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    try:
        httpx.post(
            f"{settings.approval_system_base_url.rstrip('/')}/admin/reset",
            timeout=settings.approval_system_timeout_seconds,
            trust_env=False,
        )
    except httpx.HTTPError:
        pass
    db.execute(
        text(
            "TRUNCATE task_logs, comment_logs, review_results, rule_hits, contract_parses, "
            "approval_attachments, approval_tasks RESTART IDENTITY CASCADE"
        )
    )
    db.commit()
    return _redirect("/console", "演示数据已重置")
