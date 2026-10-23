"""工具接口的请求模型（与设计文档 §6 的入参一致）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ListPendingRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)


class GetApprovalRequest(BaseModel):
    instance_id: str = Field(min_length=1, max_length=64)


class DownloadAttachmentRequest(BaseModel):
    instance_id: str = Field(min_length=1, max_length=64)
    attachment_id: str = Field(min_length=1, max_length=64)
    file_name: str = Field(min_length=1, max_length=255)


class ParseDocumentRequest(BaseModel):
    document_id: int = Field(ge=1)


class RunRulesRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)


class SaveReviewResultRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)
    overall_risk_level: str = Field(pattern="^(low|medium|high)$")
    summary_text: str = Field(min_length=1, max_length=10000)
    focus_points_json: str = Field(min_length=2, max_length=20000)
    comment_text: str = Field(min_length=1, max_length=20000)


class WriteCommentRequest(BaseModel):
    instance_id: str = Field(min_length=1, max_length=64)
    review_id: int = Field(ge=1)
