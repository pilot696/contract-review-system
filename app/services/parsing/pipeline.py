"""解析流水线：编排抽取、条款切分、字段抽取、可选 LLM 补抽与结果持久化。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ApprovalAttachment, ContractParse
from app.services.parsing import llm_extract
from app.services.parsing.fields import (
    BASIC_FIELDS,
    CLAUSE_FIELDS,
    FieldValue,
    extract_basic_info,
    extract_clauses,
)
from app.services.parsing.normalize import normalize_text
from app.services.parsing.ocr import OcrUnavailableError
from app.services.parsing.text import DocumentText, ParseFailure, extract_document

logger = logging.getLogger(__name__)

PARSER_VERSION = "deterministic-v1"


@dataclass
class ParseOutcome:
    file_name: str
    file_type: str
    page_count: int | None
    ocr_used: bool
    parse_status: str
    parse_error: str | None
    basic_info: dict[str, dict]
    clauses: dict[str, dict]
    parser_version: str = PARSER_VERSION
    full_text: str = ""
    text_blocks: list[dict] = field(default_factory=list)

    def to_tool_dict(self, document_id: int) -> dict:
        return {
            "document_id": document_id,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "page_count": self.page_count,
            "ocr_used": self.ocr_used,
            "parser_version": self.parser_version,
            "parse_status": self.parse_status,
            "parse_error": self.parse_error,
            "basic_info": self.basic_info,
            "clauses": self.clauses,
        }


def _empty_fields() -> tuple[dict[str, dict], dict[str, dict]]:
    basic = {name: FieldValue().to_dict() for name in BASIC_FIELDS}
    clauses = {name: FieldValue().to_dict() for name in CLAUSE_FIELDS}
    return basic, clauses


def _failed(
    file_name: str,
    file_type: str,
    error: str,
    *,
    page_count: int | None = None,
    ocr_used: bool = False,
) -> ParseOutcome:
    basic, clauses = _empty_fields()
    return ParseOutcome(
        file_name=file_name,
        file_type=file_type,
        page_count=page_count,
        ocr_used=ocr_used,
        parse_status="failed",
        parse_error=error,
        basic_info=basic,
        clauses=clauses,
    )


def _succeeded(document: DocumentText, file_name: str, file_type: str, settings: Settings) -> ParseOutcome:
    basic_values = extract_basic_info(document.blocks, file_name, document.ocr_used)
    clause_values = extract_clauses(document.blocks, document.ocr_used)

    full_text = normalize_text("\n".join(block.text for block in document.blocks))
    text_blocks = [
        {
            "page_no": block.page_no,
            "position": block.position,
            "text": block.text,
            "source": block.source,
            "confidence": block.confidence,
        }
        for block in document.blocks
    ]
    try:
        filled = llm_extract.enrich(basic_values, clause_values, full_text, settings)
        if filled:
            logger.info("LLM 补抽字段 %s 个：%s", filled, file_name)
    except Exception as exc:  # noqa: BLE001 - LLM 只是可选增强，失败不影响解析
        logger.warning("LLM 补抽异常，已回退确定性结果：%s", exc)

    return ParseOutcome(
        file_name=file_name,
        file_type=file_type,
        page_count=document.page_count,
        ocr_used=document.ocr_used,
        parse_status="success",
        parse_error=None,
        basic_info={name: value.to_dict() for name, value in basic_values.items()},
        clauses={name: value.to_dict() for name, value in clause_values.items()},
        full_text=full_text,
        text_blocks=text_blocks,
    )


def parse_file(path: Path, file_type: str, settings: Settings) -> ParseOutcome:
    file_name = path.name
    try:
        document = extract_document(path, file_type, settings)
    except ParseFailure as exc:
        logger.warning("解析失败 %s：%s", file_name, exc)
        return _failed(file_name, file_type, exc.code)
    except OcrUnavailableError as exc:
        logger.warning("OCR 失败 %s：%s", file_name, exc)
        return _failed(file_name, file_type, "OCR_FAILED")
    except Exception as exc:  # noqa: BLE001 - 兜底为解析失败，避免任务悬挂
        logger.exception("解析异常 %s：%s", file_name, exc)
        return _failed(file_name, file_type, "PARSE_FAILED")

    if not document.blocks:
        return _failed(
            file_name,
            file_type,
            "DOCUMENT_EMPTY",
            page_count=document.page_count,
            ocr_used=document.ocr_used,
        )

    return _succeeded(document, file_name, file_type, settings)


def parse_and_save(
    db: Session, attachment: ApprovalAttachment, settings: Settings
) -> tuple[ContractParse, ParseOutcome]:
    """解析附件并把结果 upsert 到 contract_parses（失败原因同样落库）。"""
    path = Path(attachment.file_path) if attachment.file_path else None
    if path is None or not path.exists():
        outcome = _failed(attachment.file_name, attachment.file_type, "PARSE_FAILED")
    else:
        outcome = parse_file(path, attachment.file_type, settings)

    record = db.scalar(
        select(ContractParse).where(
            ContractParse.task_id == attachment.task_id,
            ContractParse.attachment_id == attachment.id,
        )
    )
    if record is None:
        record = ContractParse(task_id=attachment.task_id, attachment_id=attachment.id)
        db.add(record)

    record.basic_info_json = outcome.basic_info
    record.clause_info_json = outcome.clauses
    record.full_text = outcome.full_text
    record.text_blocks_json = outcome.text_blocks
    record.parse_status = outcome.parse_status
    record.parse_error = outcome.parse_error
    record.ocr_used = outcome.ocr_used
    record.page_count = outcome.page_count
    record.parser_version = outcome.parser_version
    db.flush()
    return record, outcome


__all__ = ["ParseOutcome", "parse_and_save", "parse_file"]
