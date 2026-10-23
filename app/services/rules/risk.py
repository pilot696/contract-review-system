"""风险汇总（设计文档 §7.5.5）。"""

from __future__ import annotations

from app.services.rules.types import Hit


def aggregate_risk(hits: list[Hit]) -> str:
    """取命中项中的最高风险等级；无命中为 low。"""
    levels = {hit.risk_level for hit in hits if hit.is_hit}
    if "high" in levels:
        return "high"
    if "medium" in levels:
        return "medium"
    return "low"
