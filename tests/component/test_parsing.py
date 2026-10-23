"""解析模块测试（C1–C3）：docx / 文本 PDF / 扫描件的字段、证据与失败映射。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.services.parsing.fields import BASIC_FIELDS, CLAUSE_FIELDS
from app.services.parsing.pipeline import ParseOutcome, parse_file

SAMPLES = Path("mock_approval/files")


def _settings():
    # 解析测试保持确定性/离线：不触发可选 LLM 补抽
    return get_settings().model_copy(update={"llm_enabled": False})


def _parse(name: str, file_type: str) -> ParseOutcome:
    return parse_file(SAMPLES / name, file_type, _settings())


def test_parse_standard_docx() -> None:
    outcome = _parse("sample_01_standard.docx", "docx")
    assert outcome.parse_status == "success", outcome.parse_error
    assert outcome.ocr_used is False
    assert outcome.page_count is None  # docx 无真实分页

    basic = outcome.basic_info
    assert basic["contract_title"]["value"] == "软件开发服务合同"
    assert basic["contract_no"]["value"] == "HT-2026-0088"
    assert basic["signing_entity"]["value"] == "启明科技有限公司"
    assert basic["counterparty_name"]["value"] == "云帆软件有限公司"
    assert basic["amount"]["value"] == 500000.0
    assert basic["currency"]["value"] == "CNY"
    assert basic["effective_date"]["value"] == "2026-11-01"
    assert basic["expiration_date"]["value"] == "2027-10-31"

    # 字段级证据：每个已抽取字段都有原文片段 + 位置
    for name in BASIC_FIELDS:
        field = basic[name]
        assert field["extract_status"] == "extracted", name
        assert field["evidence"]["text"], name
        assert field["evidence"]["position"], name

    for clause in CLAUSE_FIELDS:
        assert outcome.clauses[clause]["extract_status"] == "extracted", clause


def test_parse_risky_pdf() -> None:
    outcome = _parse("sample_02_risky.pdf", "pdf")
    assert outcome.parse_status == "success", outcome.parse_error
    assert outcome.page_count is not None and outcome.page_count >= 1
    assert outcome.ocr_used is False

    basic = outcome.basic_info
    assert basic["contract_title"]["value"] == "服务器采购合同"
    assert basic["amount"]["value"] == 1280000.0
    assert basic["effective_date"]["value"] == "2026-12-01"
    assert basic["expiration_date"]["value"] == "2027-11-30"

    clauses = outcome.clauses
    assert "60%" in clauses["payment_terms"]["value"]
    assert "120天" in clauses["payment_terms"]["value"]  # 段落归并
    assert clauses["acceptance_terms"]["extract_status"] == "missing"


def test_parse_scan_png_uses_ocr() -> None:
    outcome = _parse("sample_03_scan.png", "png")
    assert outcome.parse_status == "success", outcome.parse_error
    assert outcome.ocr_used is True
    assert outcome.page_count == 1

    basic = outcome.basic_info
    assert basic["signing_entity"]["value"] == "启明科技有限公司"
    assert basic["amount"]["extract_status"] == "missing"

    clauses = outcome.clauses
    assert clauses["data_terms"]["extract_status"] == "extracted"
    assert "永久使用" in clauses["data_terms"]["value"]
    assert clauses["confidentiality_terms"]["extract_status"] == "missing"
    assert clauses["ip_terms"]["extract_status"] == "missing"


def test_parse_empty_pdf_document_empty() -> None:
    outcome = _parse("sample_04_empty.pdf", "pdf")
    assert outcome.parse_status == "failed"
    assert outcome.parse_error == "DOCUMENT_EMPTY"


def test_parse_corrupt_file_fails_with_reason(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nthis is not a real pdf")
    outcome = parse_file(broken, "pdf", _settings())
    assert outcome.parse_status == "failed"
    assert outcome.parse_error in {"PARSE_FAILED", "OCR_FAILED", "DOCUMENT_EMPTY"}


def test_parse_deadline_guard() -> None:
    import time

    from app.services.parsing.text import ParseFailure, _ensure_deadline

    _ensure_deadline(time.monotonic() + 5)  # 未超时：不抛异常
    with pytest.raises(ParseFailure) as excinfo:
        _ensure_deadline(time.monotonic() - 1)
    assert "解析超时" in str(excinfo.value)
