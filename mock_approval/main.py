"""Mock 审批系统 HTTP 服务（默认 127.0.0.1:8100）。

对齐设计文档 §8.2：
- GET  /api/approvals/pending
- GET  /api/approvals/{instance_id}
- GET  /api/approvals/{instance_id}/attachments/{attachment_id}
- POST /api/approvals/{instance_id}/comments
- GET  /api/approvals/{instance_id}/comments
- POST/DELETE /admin/faults, POST /admin/reset, GET /healthz

启动：
    uv run uvicorn mock_approval.main:app --host 127.0.0.1 --port 8100
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from mock_approval import faults as fault_mod
from mock_approval.store import STORE, media_type_for

_TZ = timezone(timedelta(hours=8))

app = FastAPI(title="Mock Approval System", version="0.1.0")


class CommentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)


class FaultRequest(BaseModel):
    mode: str
    approval_code: str | None = None
    latency_ms: int = Field(0, ge=0, le=60000)


def _apply_latency(approval_code: str) -> None:
    if fault_mod.FAULTS.applies(approval_code) and fault_mod.FAULTS.latency_ms > 0:
        time.sleep(fault_mod.FAULTS.latency_ms / 1000)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "mock-approval-system"}


@app.get("/api/approvals/pending")
def list_pending(limit: int = 50) -> dict:
    items = STORE.pending(limit)
    return {"items": [item.summary() for item in items], "total": len(items)}


@app.get("/api/approvals/{instance_id}")
def get_approval(instance_id: str) -> dict:
    _apply_latency(instance_id)
    approval = STORE.get(instance_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"approval not found: {instance_id}")
    return approval.detail()


@app.get("/api/approvals/{instance_id}/attachments/{attachment_id}")
def download_attachment(instance_id: str, attachment_id: str) -> Response:
    _apply_latency(instance_id)
    if fault_mod.FAULTS.active(instance_id, "missing_attachment"):
        raise HTTPException(status_code=404, detail="attachment missing")
    if fault_mod.FAULTS.active(instance_id, "download_500"):
        raise HTTPException(status_code=500, detail="attachment storage unavailable")

    attachment = STORE.find_attachment(instance_id, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail=f"attachment not found: {attachment_id}")

    path = STORE.attachment_path(instance_id, attachment_id)
    if path is None:
        raise HTTPException(status_code=404, detail="attachment file missing")

    if fault_mod.FAULTS.active(instance_id, "empty_document"):
        content = b"%PDF-1.4\n% empty document (fault injection)\n%%EOF\n"
        file_name, file_type = "empty.pdf", "pdf"
    elif fault_mod.FAULTS.active(instance_id, "unreadable_image"):
        content = b"\x89PNG\r\n\x1a\n"  # 截断 PNG：无法识别
        file_name, file_type = "unreadable.png", "png"
    else:
        content = path.read_bytes()
        file_name, file_type = attachment.file_name, attachment.file_type

    return Response(
        content=content,
        media_type=media_type_for(file_type),
        headers={
            "X-File-SHA256": hashlib.sha256(content).hexdigest(),
            "X-File-Name": quote(file_name),
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}",
        },
    )


@app.post("/api/approvals/{instance_id}/comments")
def add_comment(
    instance_id: str,
    body: CommentRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    _apply_latency(instance_id)
    if fault_mod.FAULTS.active(instance_id, "comment_500"):
        raise HTTPException(status_code=500, detail="comment service unavailable")
    if STORE.get(instance_id) is None:
        raise HTTPException(status_code=404, detail=f"approval not found: {instance_id}")
    return STORE.add_comment(instance_id, body.content, idempotency_key or "")


@app.get("/api/approvals/{instance_id}/comments")
def list_comments(instance_id: str) -> dict:
    if STORE.get(instance_id) is None:
        raise HTTPException(status_code=404, detail=f"approval not found: {instance_id}")
    comments = STORE.list_comments(instance_id)
    return {"items": comments, "total": len(comments)}


@app.post("/admin/faults")
def set_fault(body: FaultRequest) -> dict:
    try:
        fault_mod.set_faults(body.mode, body.approval_code, body.latency_ms)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "modes": sorted(fault_mod.FAULTS.modes),
        "approval_code": fault_mod.FAULTS.approval_code,
        "latency_ms": fault_mod.FAULTS.latency_ms,
    }


@app.delete("/admin/faults")
def clear_faults() -> dict:
    fault_mod.clear_faults()
    return {"status": "ok", "modes": []}


@app.post("/admin/reset")
def reset() -> dict:
    STORE.reset()
    fault_mod.clear_faults()
    return {"status": "ok"}


@app.post("/admin/reload")
def reload_store() -> dict:
    """重新加载种子数据与附件哈希（示例合同重新生成后调用）。"""
    STORE.reset()
    return {"status": "ok", "approvals": len(STORE.approvals)}


@app.get("/admin/state")
def admin_state() -> dict:
    return {
        "approvals": len(STORE.approvals),
        "comments": {code: len(items) for code, items in STORE.comments.items()},
        "faults": {
            "modes": sorted(fault_mod.FAULTS.modes),
            "approval_code": fault_mod.FAULTS.approval_code,
            "latency_ms": fault_mod.FAULTS.latency_ms,
        },
        "time": datetime.now(_TZ).isoformat(timespec="seconds"),
    }
