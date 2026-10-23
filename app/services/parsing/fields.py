"""字段抽取：8 个基本信息 + 8 类条款，字段级证据（设计文档 §7.4.6–§7.4.7）。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.parsing.normalize import normalize_text
from app.services.parsing.segment import CLAUSE_FIELDS, ClauseSegment, segment_clauses
from app.services.parsing.text import TextBlock

logger = logging.getLogger(__name__)

BASIC_FIELDS: tuple[str, ...] = (
    "contract_title",
    "contract_no",
    "signing_entity",
    "counterparty_name",
    "amount",
    "currency",
    "effective_date",
    "expiration_date",
)

_EVIDENCE_MAX = 200


@dataclass
class FieldValue:
    value: str | float | None = None
    unit: str | None = None
    extract_status: str = "missing"
    confidence: float = 0.0
    evidence_text: str = ""
    page_no: int | None = None
    position: str = ""
    source: str = "deterministic"

    def to_dict(self) -> dict:
        payload: dict = {
            "value": self.value,
            "extract_status": self.extract_status,
            "confidence": round(self.confidence, 2),
            "evidence": {
                "text": self.evidence_text,
                "page_no": self.page_no,
                "position": self.position,
            },
        }
        if self.unit:
            payload["unit"] = self.unit
        if self.source != "deterministic":
            payload["source"] = self.source
        return payload


# --------------------------------------------------------------------------- #
# 公共工具
# --------------------------------------------------------------------------- #
def _normalized_blocks(blocks: list[TextBlock]) -> list[tuple[TextBlock, str]]:
    return [(block, normalize_text(block.text)) for block in blocks]


def _clip(text: str) -> str:
    return text[: _EVIDENCE_MAX]


def _field_from_block(
    value: str | float,
    block: TextBlock,
    confidence: float,
    ocr_used: bool,
    *,
    unit: str | None = None,
) -> FieldValue:
    adjusted = confidence - (0.1 if ocr_used and block.source == "ocr" else 0.0)
    return FieldValue(
        value=value,
        unit=unit,
        extract_status="extracted",
        confidence=max(0.05, min(0.99, adjusted)),
        evidence_text=_clip(normalize_text(block.text)),
        page_no=block.page_no,
        position=block.position,
    )


def _search(
    pairs: list[tuple[TextBlock, str]], pattern: str
) -> tuple[re.Match[str], TextBlock, str] | None:
    compiled = re.compile(pattern)
    for block, text in pairs:
        match = compiled.search(text)
        if match:
            return match, block, text
    return None


def _chinese_to_number(text: str) -> int | None:
    digits = {
        "零": 0, "〇": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
        "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
    }
    small_units = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
    big_units = {"万": 10_000, "亿": 100_000_000}

    total = 0
    section = 0
    number = 0
    seen = False
    for char in text:
        if char in digits:
            number = digits[char]
            seen = True
        elif char in small_units:
            unit = small_units[char]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
            seen = True
        elif char in big_units:
            section += number
            total += section * big_units[char]
            section = 0
            number = 0
            seen = True
    if not seen:
        return None
    return total + section + number


# --------------------------------------------------------------------------- #
# 基本信息
# --------------------------------------------------------------------------- #
def _extract_title(
    pairs: list[tuple[TextBlock, str]], file_name: str, ocr_used: bool
) -> FieldValue:
    for block, text in pairs[:3]:
        candidate = text.strip()
        if 3 <= len(candidate) <= 80 and not re.search(
            r"(合同编号|协议编号|甲方|乙方|金额|日期)", candidate
        ):
            return _field_from_block(candidate, block, 0.85, ocr_used)
    stem = Path(file_name).stem
    if stem:
        return FieldValue(
            value=stem,
            extract_status="extracted",
            confidence=0.6 - (0.1 if ocr_used else 0.0),
            evidence_text=f"来源：文件名 {file_name}",
            position="文件属性",
        )
    return FieldValue()


def _extract_contract_no(pairs: list[tuple[TextBlock, str]], ocr_used: bool) -> FieldValue:
    found = _search(
        pairs, r"(?:合同|协议)?(?:编号|编码)\s*[:：]\s*([A-Za-z0-9\-_/]{4,64})"
    )
    if not found:
        return FieldValue()
    match, block, _ = found
    return _field_from_block(match.group(1).strip(), block, 0.9, ocr_used)


def _extract_party(
    pairs: list[tuple[TextBlock, str]], label: str, ocr_used: bool
) -> FieldValue:
    found = _search(pairs, rf"{label}\s*[:：]\s*([^\s，,；;。]{{2,40}})")
    if not found:
        return FieldValue()
    match, block, _ = found
    return _field_from_block(match.group(1).strip(), block, 0.88, ocr_used)


def _extract_amount(pairs: list[tuple[TextBlock, str]], ocr_used: bool) -> FieldValue:
    chinese = _search(
        pairs,
        r"人民币\s*([零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]+)\s*元",
    )
    if chinese:
        match, block, _ = chinese
        value = _chinese_to_number(match.group(1))
        if value:
            return _field_from_block(float(value), block, 0.9, ocr_used, unit="CNY")

    symbol = _search(pairs, r"[¥￥]\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
    if symbol:
        match, block, _ = symbol
        try:
            return _field_from_block(
                float(match.group(1).replace(",", "")), block, 0.9, ocr_used, unit="CNY"
            )
        except ValueError:  # pragma: no cover
            pass

    general = _search(
        pairs, r"(?:金额|价款|总额|总价)[^0-9]{0,10}([0-9][0-9,]*(?:\.[0-9]+)?)\s*(万|亿)?\s*元?"
    )
    if general:
        match, block, _ = general
        try:
            value = float(match.group(1).replace(",", ""))
            unit = match.group(2)
            if unit == "万":
                value *= 10_000
            elif unit == "亿":
                value *= 100_000_000
            return _field_from_block(value, block, 0.85, ocr_used, unit="CNY")
        except ValueError:  # pragma: no cover
            pass
    return FieldValue()


def _extract_currency(
    pairs: list[tuple[TextBlock, str]], amount: FieldValue, ocr_used: bool
) -> FieldValue:
    for pattern, code in (
        (r"美元|USD", "USD"),
        (r"欧元|EUR", "EUR"),
        (r"人民币|CNY|RMB|[¥￥]", "CNY"),
    ):
        found = _search(pairs, pattern)
        if found:
            _, block, _ = found
            return _field_from_block(code, block, 0.9, ocr_used)
    if amount.extract_status == "extracted":
        return FieldValue(value="CNY", extract_status="extracted", confidence=0.5,
                          evidence_text="由金额推断为人民币", position="推断")
    return FieldValue()


_DATE_PATTERNS: dict[str, tuple[str, ...]] = {
    "effective_date": (
        r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*起\s*(?:生效|开始|执行)",
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(?:起)?\s*生效",
        r"(\d{4})-(\d{2})-(\d{2})\s*(?:起)?\s*生效",
    ),
    "expiration_date": (
        r"(?:有效期至|有效期到|有效期限至|租赁期限至|至|到)\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        r"(?:有效期至|至|到)\s*(\d{4})-(\d{2})-(\d{2})",
    ),
}


def _extract_date(
    pairs: list[tuple[TextBlock, str]], field: str, ocr_used: bool
) -> FieldValue:
    for pattern in _DATE_PATTERNS[field]:
        found = _search(pairs, pattern)
        if not found:
            continue
        match, block, _ = found
        try:
            year, month, day = (int(match.group(index)) for index in (1, 2, 3))
        except (TypeError, ValueError):  # pragma: no cover
            continue
        if not (1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
            continue
        return _field_from_block(f"{year:04d}-{month:02d}-{day:02d}", block, 0.9, ocr_used)
    return FieldValue()


def extract_basic_info(
    blocks: list[TextBlock], file_name: str, ocr_used: bool
) -> dict[str, FieldValue]:
    pairs = _normalized_blocks(blocks)
    amount = _extract_amount(pairs, ocr_used)
    values: dict[str, FieldValue] = {
        "contract_title": _extract_title(pairs, file_name, ocr_used),
        "contract_no": _extract_contract_no(pairs, ocr_used),
        "signing_entity": _extract_party(pairs, "甲方", ocr_used),
        "counterparty_name": _extract_party(pairs, "乙方", ocr_used),
        "amount": amount,
        "currency": _extract_currency(pairs, amount, ocr_used),
        "effective_date": _extract_date(pairs, "effective_date", ocr_used),
        "expiration_date": _extract_date(pairs, "expiration_date", ocr_used),
    }
    if amount.extract_status == "extracted" and amount.unit:
        values["amount"].unit = amount.unit
    return values


# --------------------------------------------------------------------------- #
# 条款
# --------------------------------------------------------------------------- #
def _clause_value(segment: ClauseSegment, ocr_used: bool) -> FieldValue:
    return FieldValue(
        value=segment.text,
        extract_status="extracted",
        confidence=max(0.05, min(0.99, segment.confidence - (0.1 if ocr_used else 0.0))),
        evidence_text=_clip(segment.text),
        page_no=segment.page_no,
        position=segment.position,
    )


def extract_clauses(blocks: list[TextBlock], ocr_used: bool) -> dict[str, FieldValue]:
    segments = segment_clauses(blocks)
    values: dict[str, FieldValue] = {}
    for clause in CLAUSE_FIELDS:
        segment = segments.get(clause)
        values[clause] = _clause_value(segment, ocr_used) if segment else FieldValue()
    return values


__all__ = [
    "BASIC_FIELDS",
    "CLAUSE_FIELDS",
    "FieldValue",
    "extract_basic_info",
    "extract_clauses",
]
