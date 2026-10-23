"""Mock 审批系统的故障注入开关（内存态，进程内生效）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

ALLOWED_MODES = {
    "missing_attachment",  # 附件下载返回 404
    "download_500",        # 附件下载返回 500
    "empty_document",      # 返回无文本的空文档
    "unreadable_image",    # 返回纯白图（OCR 无结果）
    "comment_500",         # 评论回写返回 500
}


@dataclass
class FaultState:
    lock: Lock = field(default_factory=Lock)
    modes: set[str] = field(default_factory=set)
    approval_code: str | None = None  # 为空表示对所有审批单生效
    latency_ms: int = 0

    def applies_to(self, code: str) -> bool:
        return self.approval_code is None or self.approval_code == code

    def active(self, code: str, mode: str) -> bool:
        with self.lock:
            return mode in self.modes and self.applies_to(code)

    def applies(self, code: str) -> bool:
        with self.lock:
            return self.applies_to(code)

    def reset(self) -> None:
        with self.lock:
            self.modes.clear()
            self.approval_code = None
            self.latency_ms = 0


FAULTS = FaultState()


def set_faults(mode: str, approval_code: str | None = None, latency_ms: int = 0) -> None:
    if mode not in ALLOWED_MODES:
        raise ValueError(f"unknown fault mode: {mode}; allowed={sorted(ALLOWED_MODES)}")
    with FAULTS.lock:
        FAULTS.modes = {mode}
        FAULTS.approval_code = approval_code
        FAULTS.latency_ms = latency_ms


def clear_faults() -> None:
    FAULTS.reset()
