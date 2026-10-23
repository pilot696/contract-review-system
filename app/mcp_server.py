"""MCP stdio 服务器：把 7 个工具暴露给 AI 客户端（设计文档 §6.10）。

启动：
    uv run python -m app.mcp_server
"""

from __future__ import annotations

import asyncio
from functools import wraps

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from app.errors import AppError

from app.config import get_settings
from app.db import SessionLocal
from app.services.approval_client import build_approval_client
from app.services.attachment_service import ensure_attachment
from app.services.parsing import parse_and_save
from app.services.pull_service import get_and_upsert_detail, pull_and_upsert
from app.services.rules import run_rules
from app.services.state import now_iso
from app.services.task_service import load_task
from app.services.writeback_service import save_review_result, write_approval_comment
from app.models import ApprovalAttachment, ApprovalTask

TOOL_NAMES: tuple[str, ...] = (
    "list_pending_contract_approvals",
    "get_contract_approval",
    "download_contract_attachment",
    "parse_contract_document",
    "run_contract_rules",
    "save_review_result",
    "write_approval_comment",
)

mcp = FastMCP("contract-review")


def _tool_guard(fn):
    """把服务层 AppError 转成 MCP ToolError，message 统一为 "<CODE>: <中文说明>"（§6.1）。"""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AppError as exc:
            raise ToolError(f"{exc.code}: {exc.message}") from exc

    return wrapper


@mcp.tool()
@_tool_guard
def list_pending_contract_approvals(limit: int = 50) -> dict:
    """拉取待处理审批单列表，并按 approval_code 去重入库。"""
    settings = get_settings()
    client = build_approval_client(settings)
    db = SessionLocal()
    try:
        result = pull_and_upsert(db, client, limit)
        db.commit()
        return {
            "approvals": [
                {
                    "approval_code": task.approval_code,
                    "title": task.approval_title,
                    "applicant_name": task.applicant_name,
                    "apply_date": task.applied_at.date().isoformat() if task.applied_at else None,
                    "attachment_count": task.attachment_count,
                    "task_status": task.task_status,
                }
                for task in result.tasks
            ],
            "total": result.total,
            "pulled_at": now_iso(),
        }
    finally:
        db.close()
        client.close()


@mcp.tool()
@_tool_guard
def get_contract_approval(instance_id: str) -> dict:
    """查询单个审批单详情（审批信息、表单数据、附件、当前状态）。"""
    settings = get_settings()
    client = build_approval_client(settings)
    db = SessionLocal()
    try:
        task, detail = get_and_upsert_detail(db, client, instance_id)
        db.commit()
        records = {
            record.external_attachment_id: record
            for record in db.query(ApprovalAttachment).filter(
                ApprovalAttachment.task_id == task.id
            )
        }
        return {
            "approval_info": {
                "approval_code": task.approval_code,
                "title": task.approval_title,
                "applicant_name": task.applicant_name,
                "apply_date": task.applied_at.date().isoformat() if task.applied_at else None,
                "status": detail.status,
            },
            "form_data": detail.form_data,
            "attachments": [
                {
                    "attachment_id": meta.attachment_id,
                    "file_name": meta.file_name,
                    "file_type": meta.file_type,
                    "file_size": meta.file_size,
                    "download_status": records[meta.attachment_id].download_status
                    if meta.attachment_id in records
                    else "pending",
                    "local_path": records[meta.attachment_id].file_path
                    if meta.attachment_id in records
                    else None,
                }
                for meta in detail.attachments
            ],
            "current_status": {
                "task_status": task.task_status,
                "write_status": task.write_status,
                "failed_stage": task.failed_stage,
                "blocked_reason": task.blocked_reason,
            },
        }
    finally:
        db.close()
        client.close()


@mcp.tool()
@_tool_guard
def download_contract_attachment(instance_id: str, attachment_id: str, file_name: str) -> dict:
    """下载合同附件到本地并返回路径与 SHA256 校验信息。"""
    settings = get_settings()
    client = build_approval_client(settings)
    db = SessionLocal()
    try:
        task = db.query(ApprovalTask).filter(ApprovalTask.approval_code == instance_id).one_or_none()
        if task is None:
            raise ValueError("任务不存在，请先调用 list_pending_contract_approvals")
        record = (
            db.query(ApprovalAttachment)
            .filter(
                ApprovalAttachment.task_id == task.id,
                ApprovalAttachment.external_attachment_id == attachment_id,
            )
            .one_or_none()
        )
        if record is None:
            raise ValueError("附件元数据不存在，请先调用 get_contract_approval")
        from app.services.approval_client import AttachmentMeta

        meta = AttachmentMeta(
            attachment_id=record.external_attachment_id,
            file_name=file_name or record.file_name,
            file_type=record.file_type,
            file_size=record.file_size,
        )
        saved = ensure_attachment(db, client, task, meta, settings)
        db.commit()
        return {
            "attachment_id": saved.external_attachment_id,
            "document_id": saved.id,
            "file_name": saved.file_name,
            "file_type": saved.file_type,
            "file_path": saved.file_path,
            "file_size": saved.file_size,
            "file_hash": saved.file_hash,
            "download_status": saved.download_status,
        }
    finally:
        db.close()
        client.close()


@mcp.tool()
@_tool_guard
def parse_contract_document(document_id: int) -> dict:
    """解析已下载的合同附件，返回 8+8 字段与字段级证据。"""
    settings = get_settings()
    db = SessionLocal()
    try:
        attachment = db.get(ApprovalAttachment, document_id)
        if attachment is None:
            raise ValueError(f"附件记录不存在：document_id={document_id}")
        if not attachment.file_path:
            raise ValueError("附件尚未下载，请先调用 download_contract_attachment")
        _record, outcome = parse_and_save(db, attachment, settings)
        db.commit()
        return outcome.to_tool_dict(attachment.id)
    finally:
        db.close()


@mcp.tool()
@_tool_guard
def run_contract_rules(case_id: str) -> dict:
    """对已解析合同执行规则审查，返回命中与风险结论（命中写入 rule_hits）。"""
    db = SessionLocal()
    try:
        task = load_task(db, case_id)
        result = run_rules(db, task)
        db.commit()
        return result.to_tool_dict()
    finally:
        db.close()


@mcp.tool()
@_tool_guard
def save_review_result(
    case_id: str,
    overall_risk_level: str,
    summary_text: str,
    focus_points_json: str,
    comment_text: str,
) -> dict:
    """保存审查结果（按任务幂等 upsert）。"""
    db = SessionLocal()
    try:
        task = load_task(db, case_id)
        result = save_review_result(
            db,
            task,
            overall_risk_level=overall_risk_level,
            summary_text=summary_text,
            focus_points_json=focus_points_json,
            comment_text=comment_text,
        )
        db.commit()
        return result.to_tool_dict()
    finally:
        db.close()


@mcp.tool()
@_tool_guard
def write_approval_comment(instance_id: str, review_id: int) -> dict:
    """把审查意见写回审批评论区（幂等键去重）。"""
    settings = get_settings()
    client = build_approval_client(settings)
    db = SessionLocal()
    try:
        task = load_task(db, instance_id)
        result = write_approval_comment(db, client, task, review_id, settings)
        db.commit()
        return result.to_tool_dict()
    finally:
        db.close()
        client.close()


async def list_tool_names() -> list[str]:
    """测试/诊断用：列出注册的 MCP 工具名。"""
    tools = await mcp.list_tools()
    return sorted(tool.name for tool in tools)


if __name__ == "__main__":
    mcp.run()
