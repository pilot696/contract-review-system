"""8 张表的 ORM 模型（设计文档 §4.3 DDL 的 SQLAlchemy 映射）。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.enums import (
    BLOCKED_REASON_VALUES,
    DOWNLOAD_STATUS_VALUES,
    FAILED_STAGE_VALUES,
    HIT_STATUS_VALUES,
    LOG_LEVEL_VALUES,
    LOG_TYPE_VALUES,
    MATCH_MODE_VALUES,
    PARSE_STATUS_VALUES,
    RISK_LEVEL_VALUES,
    RULE_STATUS_VALUES,
    TASK_STATUS_VALUES,
    WRITE_STATUS_VALUES,
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


class ApprovalTask(Base):
    """审批任务表：按 approval_code 唯一去重。"""

    __tablename__ = "approval_tasks"
    __table_args__ = (
        CheckConstraint(_in_list("task_status", TASK_STATUS_VALUES), name="ck_tasks_task_status"),
        CheckConstraint(_in_list("write_status", WRITE_STATUS_VALUES), name="ck_tasks_write_status"),
        CheckConstraint(_in_list("failed_stage", FAILED_STAGE_VALUES), name="ck_tasks_failed_stage"),
        CheckConstraint(_in_list("blocked_reason", BLOCKED_REASON_VALUES), name="ck_tasks_blocked_reason"),
        Index("idx_tasks_status", "task_status"),
        Index("idx_tasks_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    approval_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    approval_title: Mapped[str] = mapped_column(String(512), nullable=False)
    applicant_name: Mapped[str | None] = mapped_column(String(128))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    task_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    write_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="not_written", server_default=text("'not_written'")
    )
    failed_stage: Mapped[str | None] = mapped_column(String(16))
    blocked_reason: Mapped[str | None] = mapped_column(String(32))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="mock", server_default=text("'mock'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ApprovalAttachment(Base):
    """审批附件表：下载状态与本地文件元数据。"""

    __tablename__ = "approval_attachments"
    __table_args__ = (
        CheckConstraint(_in_list("file_type", ("pdf", "docx", "png", "jpg", "jpeg")), name="ck_attachments_file_type"),
        CheckConstraint(_in_list("download_status", DOWNLOAD_STATUS_VALUES), name="ck_attachments_download_status"),
        UniqueConstraint("task_id", "external_attachment_id", name="uq_attachments_task_external"),
        Index("idx_attachments_task", "task_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("approval_tasks.id", ondelete="CASCADE"), nullable=False
    )
    external_attachment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1024))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_hash: Mapped[str | None] = mapped_column(String(64))
    download_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    download_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ContractParse(Base):
    """合同解析表：一份附件一条解析结果，含字段级证据。"""

    __tablename__ = "contract_parses"
    __table_args__ = (
        CheckConstraint(_in_list("parse_status", PARSE_STATUS_VALUES), name="ck_parses_parse_status"),
        UniqueConstraint("task_id", "attachment_id", name="uq_parses_task_attachment"),
        Index("idx_parses_task", "task_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("approval_tasks.id", ondelete="CASCADE"), nullable=False
    )
    attachment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("approval_attachments.id", ondelete="CASCADE"), nullable=False
    )
    basic_info_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    clause_info_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    full_text: Mapped[str | None] = mapped_column(Text)
    text_blocks_json: Mapped[list | None] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    parse_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    parse_error: Mapped[str | None] = mapped_column(Text)
    ocr_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    page_count: Mapped[int | None] = mapped_column(Integer)
    parser_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="deterministic-v1", server_default=text("'deterministic-v1'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ReviewRule(Base):
    """审查规则表。"""

    __tablename__ = "review_rules"
    __table_args__ = (
        CheckConstraint(_in_list("risk_level", RISK_LEVEL_VALUES), name="ck_rules_risk_level"),
        CheckConstraint(_in_list("rule_status", RULE_STATUS_VALUES), name="ck_rules_rule_status"),
        CheckConstraint(_in_list("match_mode", MATCH_MODE_VALUES), name="ck_rules_match_mode"),
        Index("idx_rules_status", "rule_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(8), nullable=False)
    rule_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default=text("'active'")
    )
    match_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    match_text: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion_text: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default=text("100"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RuleHit(Base):
    """规则命中表：只存命中/跳过记录，重跑时先删后插。"""

    __tablename__ = "rule_hits"
    __table_args__ = (
        CheckConstraint(_in_list("hit_status", HIT_STATUS_VALUES), name="ck_hits_hit_status"),
        CheckConstraint(
            "evidence_confidence IS NULL OR (evidence_confidence >= 0 AND evidence_confidence <= 1)",
            name="ck_hits_evidence_confidence",
        ),
        Index("idx_hits_task", "task_id"),
        Index("idx_hits_rule", "rule_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("approval_tasks.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("review_rules.id"), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_position: Mapped[str] = mapped_column(String(256), nullable=False)
    evidence_page: Mapped[int | None] = mapped_column(Integer)
    evidence_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    matched_field: Mapped[str | None] = mapped_column(String(64))
    hit_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="hit", server_default=text("'hit'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewResult(Base):
    """审查结果表：每任务一条，重跑覆盖。"""

    __tablename__ = "review_results"
    __table_args__ = (
        CheckConstraint(_in_list("overall_risk_level", RISK_LEVEL_VALUES), name="ck_results_overall_risk"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("approval_tasks.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    overall_risk_level: Mapped[str] = mapped_column(String(8), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    focus_points_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    comment_text: Mapped[str] = mapped_column(Text, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CommentLog(Base):
    """评论回写日志表：追加式。"""

    __tablename__ = "comment_logs"
    __table_args__ = (
        CheckConstraint(
            _in_list("write_status", ("writing", "success", "failed")), name="ck_comment_logs_write_status"
        ),
        Index("idx_comment_logs_task", "task_id", "created_at"),
        Index(
            "uq_comment_success",
            "idempotency_key",
            unique=True,
            postgresql_where=text("write_status = 'success'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("approval_tasks.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("review_results.id", ondelete="CASCADE"), nullable=False
    )
    write_status: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    write_response_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskLog(Base):
    """任务日志表：全链路。"""

    __tablename__ = "task_logs"
    __table_args__ = (
        CheckConstraint(_in_list("log_level", LOG_LEVEL_VALUES), name="ck_task_logs_log_level"),
        CheckConstraint(_in_list("log_type", LOG_TYPE_VALUES), name="ck_task_logs_log_type"),
        Index("idx_logs_task_time", "task_id", "created_at"),
        Index("idx_logs_level", "log_level"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("approval_tasks.id", ondelete="CASCADE")
    )
    log_level: Mapped[str] = mapped_column(String(8), nullable=False)
    log_type: Mapped[str] = mapped_column(String(16), nullable=False)
    log_content: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
