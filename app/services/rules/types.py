"""规则引擎公共类型：RuleContext / Hit（设计文档 §7.5.1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LEVEL_LABEL: dict[str, str] = {"low": "低", "medium": "中", "high": "高"}
LEVEL_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


@dataclass
class RuleContext:
    """一次审查所需的全部输入：全文、字段值/状态/证据、文本块。"""

    case_id: str
    full_text: str
    field_values: dict[str, Any] = field(default_factory=dict)
    field_status: dict[str, str] = field(default_factory=dict)
    field_evidence: dict[str, dict] = field(default_factory=dict)
    blocks: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class Hit:
    """一条规则命中（hit_status=hit）或跳过（skipped，仅审计）。"""

    rule_code: str
    rule_name: str
    risk_level: str
    evidence_text: str
    evidence_position: str
    evidence_page: int | None
    evidence_confidence: float | None
    matched_field: str | None
    hit_status: str
    suggestion: str
    priority: int = 100

    @property
    def is_hit(self) -> bool:
        return self.hit_status == "hit"

    def to_public_dict(self) -> dict:
        """工具 5 响应里的命中项（设计文档 §6.6）。"""
        return {
            "rule_code": self.rule_code,
            "rule_name": self.rule_name,
            "risk_level": self.risk_level,
            "evidence_text": self.evidence_text,
            "evidence_position": self.evidence_position,
            "evidence_page": self.evidence_page,
            "suggestion": self.suggestion,
        }


def locate_in_blocks(blocks: list[dict], needle: str, window: int = 40) -> tuple[str, str, int | None, float | None]:
    """在文本块中定位片段，返回 (证据片段, 位置, 页码, 置信度)。"""
    if needle:
        for block in blocks:
            text = str(block.get("text") or "")
            index = text.find(needle)
            if index >= 0:
                start = max(0, index - window)
                end = min(len(text), index + len(needle) + window)
                return (
                    text[start:end],
                    str(block.get("position") or ""),
                    block.get("page_no"),
                    block.get("confidence"),
                )
    return ("", "全文", None, None)


def context_from_parse_dicts(case_id: str, parses: list[dict]) -> RuleContext:
    """从解析结果字典构建上下文（测试与工具复用）。

    parses 每项至少包含 basic_info / clauses（字段对象带 value/extract_status/evidence），
    可包含 full_text 与 text_blocks。
    """
    full_texts: list[str] = []
    blocks: list[dict] = []
    field_values: dict[str, Any] = {}
    field_status: dict[str, str] = {}
    field_evidence: dict[str, dict] = {}
    for parse in parses:
        if parse.get("full_text"):
            full_texts.append(str(parse["full_text"]))
        blocks.extend(parse.get("text_blocks") or [])
        for section in ("basic_info", "clauses"):
            for name, field_data in (parse.get(section) or {}).items():
                field_values[name] = field_data.get("value")
                field_status[name] = str(field_data.get("extract_status", "missing"))
                field_evidence[name] = field_data.get("evidence") or {}
    return RuleContext(
        case_id=case_id,
        full_text="\n".join(full_texts),
        field_values=field_values,
        field_status=field_status,
        field_evidence=field_evidence,
        blocks=blocks,
    )
