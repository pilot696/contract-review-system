"""条款切分：按条款标题归并后续段落（设计文档 §7.4.5）。"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.parsing.text import TextBlock

# 每类条款的标题关键词（命中任一即视为该条款开始）
CLAUSE_HEADINGS: dict[str, tuple[str, ...]] = {
    "payment_terms": ("付款条款", "支付条款", "付款方式", "结算方式", "租金支付"),
    "delivery_terms": ("交付条款", "交货条款", "交付时间", "交付地点"),
    "acceptance_terms": ("验收条款", "验收标准", "验收方式", "验收流程"),
    "breach_terms": ("违约责任", "违约条款"),
    "confidentiality_terms": ("保密条款", "保密义务"),
    "data_terms": ("数据条款", "数据保护", "数据处理"),
    "ip_terms": ("知识产权条款", "知识产权归属", "知识产权"),
    "dispute_resolution_terms": ("争议解决", "管辖", "仲裁", "诉讼"),
}

CLAUSE_FIELDS: tuple[str, ...] = tuple(CLAUSE_HEADINGS)

CLAUSE_LABELS: dict[str, str] = {
    "payment_terms": "付款条款",
    "delivery_terms": "交付条款",
    "acceptance_terms": "验收条款",
    "breach_terms": "违约条款",
    "confidentiality_terms": "保密条款",
    "data_terms": "数据条款",
    "ip_terms": "知识产权条款",
    "dispute_resolution_terms": "争议解决条款",
}


@dataclass(frozen=True)
class ClauseSegment:
    text: str
    page_no: int | None
    position: str
    confidence: float


def heading_for(text: str) -> str | None:
    for clause, headings in CLAUSE_HEADINGS.items():
        for heading in headings:
            if heading in text:
                return clause
    return None


def segment_clauses(blocks: list[TextBlock]) -> dict[str, ClauseSegment | None]:
    """按块顺序归并：标题块开启条款，后续无标题块并入当前条款。"""
    grouped: dict[str, list[TextBlock]] = {}
    current: str | None = None
    for block in blocks:
        clause = heading_for(block.text)
        if clause is not None:
            current = clause
            grouped.setdefault(clause, []).append(block)
        elif current is not None:
            grouped[current].append(block)

    result: dict[str, ClauseSegment | None] = {}
    for clause in CLAUSE_FIELDS:
        parts = grouped.get(clause, [])
        if not parts:
            result[clause] = None
            continue
        result[clause] = ClauseSegment(
            text="\n".join(part.text for part in parts).strip(),
            page_no=parts[0].page_no,
            position=parts[0].position,
            confidence=min(part.confidence for part in parts),
        )
    return result
