"""应用配置：全部来自环境变量或 .env（设计文档 §11.1）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 应用 ----
    app_env: str = "dev"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    # ---- 数据库 ----
    # 占位默认值：真实连接串请通过 .env / 环境变量 DATABASE_URL 注入
    database_url: str = (
        "postgresql+psycopg://crs:change_me@127.0.0.1:5432/contract_review"
    )
    db_pool_size: int = 5
    db_echo: bool = False

    # ---- 附件与解析 ----
    attachment_root: Path = Path("data/attachments")
    max_file_mb: int = 50
    max_pages: int = 200
    parse_timeout_seconds: int = 120
    ocr_engine: str = "rapidocr"

    # ---- 审批系统 ----
    approval_system_base_url: str = "http://127.0.0.1:8100"
    approval_system_timeout_seconds: float = 10.0
    mock_host: str = "127.0.0.1"
    mock_port: int = 8100

    # ---- 任务编排 ----
    pipeline_concurrency: int = 5
    max_retry: int = 5
    stale_task_seconds: int = 600
    writeback_blocking: bool = True
    comment_max_chars: int = 5000

    # ---- LLM（可选）----
    llm_enabled: bool = False
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 20.0
    llm_session_id: str = ""
    llm_user_agent: str = "contract-review-system/0.1.0"

    # ---- 日志 ----
    log_level: str = "INFO"
    log_dir: Path = Path("logs")

    @property
    def is_dev(self) -> bool:
        return self.app_env.strip().lower() == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
