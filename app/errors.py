"""统一异常层次（设计文档 §10.1）。"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """应用内可预期的业务异常，由 API 层映射为统一错误信封。"""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    message: str = "内部错误"

    def __init__(self, message: str | None = None, *, detail: dict[str, Any] | None = None) -> None:
        self.message = message or self.__class__.message
        self.detail = detail
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    http_status = 400
    message = "参数校验失败"


class NotFoundError(AppError):
    code = "NOT_FOUND"
    http_status = 404
    message = "资源不存在"


class ConflictError(AppError):
    code = "CONFLICT"
    http_status = 409
    message = "状态冲突"


class ParseRequiredError(AppError):
    code = "PARSE_REQUIRED"
    http_status = 409
    message = "合同尚未完成解析"


class ParseError(AppError):
    code = "PARSE_ERROR"
    http_status = 422
    message = "文档解析失败"


class ExternalServiceError(AppError):
    code = "APPROVAL_API_ERROR"
    http_status = 502
    message = "审批系统调用失败"


class BlockedError(Exception):
    """内部信号：任务需要进入 blocked 状态（不是对外异常）。"""

    def __init__(self, stage: str, reason: str, detail: str = "") -> None:
        self.stage = stage
        self.reason = reason
        self.detail = detail
        super().__init__(f"blocked at {stage}: {reason} {detail}".strip())


class AttachmentMissingError(AppError):
    code = "ATTACHMENT_MISSING"
    http_status = 404
    message = "合同附件缺失"


class DownloadFailedError(AppError):
    code = "DOWNLOAD_FAILED"
    http_status = 502
    message = "附件下载失败"


class WritebackFailedError(AppError):
    code = "WRITEBACK_FAILED"
    http_status = 502
    message = "评论回写失败"
