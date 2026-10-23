"""日志初始化：控制台 + 轮转文件（设计文档 §7.7）。"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import Settings

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_CONFIGURED_FLAG = "_crs_logging_configured"

_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")


def redact_text(value: str, max_chars: int = 500) -> str:
    """日志脱敏：手机号/邮箱/身份证打码，正文片段截断（设计文档 §7.7）。"""
    text = _PHONE.sub("手机号***", value or "")
    text = _EMAIL.sub("***@***", text)
    text = _ID_CARD.sub("身份证***", text)
    return text[:max_chars]


def setup_logging(settings: Settings) -> None:
    """幂等初始化根日志（多次调用只生效一次）。"""
    root = logging.getLogger()
    if getattr(root, _CONFIGURED_FLAG, False):
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    setattr(root, _CONFIGURED_FLAG, True)
    # 压低第三方 HTTP 客户端的请求级 DEBUG/INFO 噪声（保留 warning 以上）
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger(__name__).info(
        "logging configured: level=%s dir=%s", settings.log_level, log_dir
    )
