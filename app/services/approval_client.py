"""审批系统客户端：统一协议 + HTTP 实现（Mock 与真实系统共用）。

设计文档 §6.3–§6.4、§8.2。Mock 与真实系统都通过 HTTP 访问，仅 base_url 不同；
测试时可用 httpx.ASGITransport 把 Mock 服务直接注入，不占端口。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from urllib.parse import quote, urlparse

import httpx

from app.config import Settings
from app.errors import (
    AttachmentMissingError,
    DownloadFailedError,
    ExternalServiceError,
    NotFoundError,
)

_RETRYABLE_STATUS = {500, 502, 503, 504}
_RETRY_DELAYS = (0.5, 1.0)


# --------------------------------------------------------------------------- #
# DTO
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AttachmentMeta:
    attachment_id: str
    file_name: str
    file_type: str
    file_size: int | None = None


@dataclass(frozen=True)
class ApprovalSummary:
    approval_code: str
    title: str
    applicant_name: str
    apply_date: date | None
    attachment_count: int


@dataclass(frozen=True)
class ApprovalDetail:
    approval_code: str
    title: str
    applicant_name: str
    apply_date: date | None
    form_data: dict[str, str]
    status: str
    attachments: list[AttachmentMeta]


@dataclass(frozen=True)
class CommentResult:
    status: str
    response_text: str
    idempotent_replay: bool = False


class ApprovalSystemClient(Protocol):
    """审批系统访问协议：Mock 与真实系统都必须满足。"""

    def list_pending(self, limit: int) -> list[ApprovalSummary]: ...

    def get_approval(self, instance_id: str) -> ApprovalDetail: ...

    def download_attachment(self, instance_id: str, attachment_id: str, file_name: str) -> bytes: ...

    def post_comment(self, instance_id: str, content: str, idempotency_key: str) -> CommentResult: ...


# --------------------------------------------------------------------------- #
# HTTP 实现
# --------------------------------------------------------------------------- #
def _parse_date(value: object) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _is_loopback(base_url: str) -> bool:
    """本机回环地址必须绕过系统代理，否则会被代理转发（实测返回 502）。"""
    host = (urlparse(base_url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


class HttpApprovalClient:
    """基于 HTTP 的审批系统客户端；Mock 与真实系统通用。"""

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            trust_env=not _is_loopback(base_url),
        )

    # ---- 生命周期 ----
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpApprovalClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---- 内部 ----
    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                response = self._client.request(method, url, **kwargs)  # type: ignore[arg-type]
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < len(_RETRY_DELAYS):
                    time.sleep(_RETRY_DELAYS[attempt])
                    continue
                raise ExternalServiceError(f"审批系统请求失败：{exc}") from exc

            if response.status_code in _RETRYABLE_STATUS and attempt < len(_RETRY_DELAYS):
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            return response
        raise ExternalServiceError(f"审批系统请求失败：{last_error}")

    # ---- 协议实现 ----
    def list_pending(self, limit: int) -> list[ApprovalSummary]:
        response = self._request("GET", "/api/approvals/pending", params={"limit": limit})
        if response.status_code != 200:
            raise ExternalServiceError(f"拉取待办失败：HTTP {response.status_code}")
        payload = response.json()
        items = payload.get("items") or payload.get("approvals") or []
        return [
            ApprovalSummary(
                approval_code=str(item["approval_code"]),
                title=str(item.get("title", "")),
                applicant_name=str(item.get("applicant_name", "")),
                apply_date=_parse_date(item.get("apply_date")),
                attachment_count=int(item.get("attachment_count", 0)),
            )
            for item in items
        ]

    def get_approval(self, instance_id: str) -> ApprovalDetail:
        response = self._request("GET", f"/api/approvals/{quote(instance_id, safe='')}")
        if response.status_code == 404:
            raise NotFoundError(f"审批单不存在：{instance_id}")
        if response.status_code != 200:
            raise ExternalServiceError(f"查询审批详情失败：HTTP {response.status_code}")
        payload = response.json()
        info = payload.get("approval_info") or {}
        attachments = [
            AttachmentMeta(
                attachment_id=str(att["attachment_id"]),
                file_name=str(att.get("file_name", "")),
                file_type=str(att.get("file_type", "")).lower(),
                file_size=att.get("file_size"),
            )
            for att in (payload.get("attachments") or [])
        ]
        return ApprovalDetail(
            approval_code=str(info.get("approval_code", instance_id)),
            title=str(info.get("title", "")),
            applicant_name=str(info.get("applicant_name", "")),
            apply_date=_parse_date(info.get("apply_date")),
            form_data={str(k): str(v) for k, v in (payload.get("form_data") or {}).items()},
            status=str(payload.get("current_status") or info.get("status") or "pending"),
            attachments=attachments,
        )

    def download_attachment(self, instance_id: str, attachment_id: str, file_name: str) -> bytes:
        response = self._request(
            "GET",
            f"/api/approvals/{quote(instance_id, safe='')}/attachments/{quote(attachment_id, safe='')}",
        )
        if response.status_code == 404:
            raise AttachmentMissingError(f"合同附件缺失：{attachment_id}")
        if response.status_code != 200:
            raise DownloadFailedError(f"附件下载失败：HTTP {response.status_code}")

        content = response.content
        expected = response.headers.get("X-File-SHA256")
        if expected:
            actual = hashlib.sha256(content).hexdigest()
            if actual != expected:
                raise DownloadFailedError(
                    "附件校验失败：SHA256 不一致",
                    detail={"expected": expected, "actual": actual},
                )
        return content

    def post_comment(self, instance_id: str, content: str, idempotency_key: str) -> CommentResult:
        response = self._request(
            "POST",
            f"/api/approvals/{quote(instance_id, safe='')}/comments",
            json={"content": content},
            headers={"Idempotency-Key": idempotency_key},
        )
        if response.status_code != 200:
            raise ExternalServiceError(f"评论回写失败：HTTP {response.status_code}")
        payload = response.json()
        return CommentResult(
            status=str(payload.get("status", "success")),
            response_text=json.dumps(payload, ensure_ascii=False),
            idempotent_replay=bool(payload.get("idempotent_replay", False)),
        )


def build_approval_client(settings: Settings) -> ApprovalSystemClient:
    return HttpApprovalClient(
        base_url=settings.approval_system_base_url,
        timeout=settings.approval_system_timeout_seconds,
    )
