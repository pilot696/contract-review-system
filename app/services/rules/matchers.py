"""四类匹配器（设计文档 §7.5.2）。"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from app.services.rules.numeric import compute_metric
from app.services.rules.types import Hit, RuleContext, locate_in_blocks

MAX_HITS_PER_TEXT_RULE = 3


class RuleLike(Protocol):
    rule_code: str
    rule_name: str
    risk_level: str
    match_mode: str
    match_text: str
    suggestion_text: str
    priority: int


def _hit(rule: RuleLike, ctx: RuleContext, needle: str, evidence: str = "",
         position: str = "", page: int | None = None,
         confidence: float | None = None, matched_field: str | None = None,
         hit_status: str = "hit") -> Hit:
    located_text, located_position, located_page, located_confidence = locate_in_blocks(ctx.blocks, needle)
    return Hit(
        rule_code=rule.rule_code,
        rule_name=rule.rule_name,
        risk_level=rule.risk_level,
        evidence_text=evidence or located_text or needle,
        evidence_position=position or located_position or "全文",
        evidence_page=page if page is not None else located_page,
        evidence_confidence=confidence if confidence is not None else located_confidence,
        matched_field=matched_field,
        hit_status=hit_status,
        suggestion=rule.suggestion_text,
        priority=rule.priority,
    )


def match_keyword(rule: RuleLike, ctx: RuleContext) -> list[Hit]:
    keywords = [keyword for keyword in rule.match_text.split("|") if keyword]
    hits: list[Hit] = []
    for keyword in keywords:
        start = 0
        while len(hits) < MAX_HITS_PER_TEXT_RULE:
            index = ctx.full_text.find(keyword, start)
            if index < 0:
                break
            hits.append(_hit(rule, ctx, keyword))
            start = index + len(keyword)
    return hits


def match_regex(rule: RuleLike, ctx: RuleContext) -> list[Hit]:
    try:
        compiled = re.compile(rule.match_text)
    except re.error:  # pragma: no cover - 规则维护时已校验，这里兜底
        return []
    hits: list[Hit] = []
    for match in compiled.finditer(ctx.full_text):
        if len(hits) >= MAX_HITS_PER_TEXT_RULE:
            break
        hits.append(_hit(rule, ctx, match.group(0)))
    return hits


def match_field_absent(rule: RuleLike, ctx: RuleContext) -> list[Hit]:
    fields = [field.strip() for field in rule.match_text.split(",") if field.strip()]
    if not fields:
        return []
    if not all(ctx.field_status.get(field, "missing") == "missing" for field in fields):
        return []
    return [
        Hit(
            rule_code=rule.rule_code,
            rule_name=rule.rule_name,
            risk_level=rule.risk_level,
            evidence_text="全文未发现可用条款",
            evidence_position="全文未发现",
            evidence_page=None,
            evidence_confidence=None,
            matched_field=",".join(fields),
            hit_status="hit",
            suggestion=rule.suggestion_text,
            priority=rule.priority,
        )
    ]


def _compare(actual: float, op: str, threshold: float) -> bool:
    if op == ">":
        return actual > threshold
    if op == ">=":
        return actual >= threshold
    if op == "<":
        return actual < threshold
    if op == "<=":
        return actual <= threshold
    if op == "==":
        return actual == threshold
    return False


def match_numeric_threshold(rule: RuleLike, ctx: RuleContext) -> list[Hit]:
    try:
        spec = json.loads(rule.match_text)
        metric_name = str(spec["metric"])
        operator = str(spec.get("op", ">"))
        threshold = float(spec["value"])
    except (ValueError, KeyError, TypeError):  # pragma: no cover - 规则维护时已校验
        return []

    metric = compute_metric(metric_name, ctx)
    if metric.value is None:
        return [
            Hit(
                rule_code=rule.rule_code,
                rule_name=rule.rule_name,
                risk_level=rule.risk_level,
                evidence_text=f"指标 {metric_name} 不可得：{metric.note}",
                evidence_position="全文",
                evidence_page=None,
                evidence_confidence=None,
                matched_field=metric_name,
                hit_status="skipped",
                suggestion=rule.suggestion_text,
                priority=rule.priority,
            )
        ]

    if not _compare(metric.value, operator, threshold):
        return []

    formatted = f"{metric.value:.2%}" if metric_name == "prepayment_ratio" else f"{metric.value:.0f}"
    evidence = (
        f"{metric_name} 实测 {formatted}，阈值 {operator} {threshold:g}；"
        f"证据：{metric.evidence_text or metric.evidence_position}"
    )
    return [
        Hit(
            rule_code=rule.rule_code,
            rule_name=rule.rule_name,
            risk_level=rule.risk_level,
            evidence_text=evidence,
            evidence_position=metric.evidence_position or "全文",
            evidence_page=metric.evidence_page,
            evidence_confidence=None,
            matched_field=metric_name,
            hit_status="hit",
            suggestion=rule.suggestion_text,
            priority=rule.priority,
        )
    ]


MATCHERS: dict[str, Any] = {
    "keyword": match_keyword,
    "regex": match_regex,
    "field_absent": match_field_absent,
    "numeric_threshold": match_numeric_threshold,
}


def match_rule(rule: RuleLike, ctx: RuleContext) -> list[Hit]:
    matcher = MATCHERS.get(rule.match_mode)
    if matcher is None:  # pragma: no cover - DB CHECK 已约束
        return []
    return matcher(rule, ctx)
