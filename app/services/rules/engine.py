"""规则引擎：加载规则 → 构建上下文 → 匹配 → 汇总 → 落库 rule_hits（设计文档 §7.5、§6.6）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.errors import ConflictError, ParseRequiredError
from app.models import ApprovalTask, ContractParse, ReviewRule, RuleHit
from app.services.rules.matchers import RuleLike, match_rule
from app.services.rules.report import build_comment_text, build_focus_points, build_summary_text
from app.services.rules.risk import aggregate_risk
from app.services.rules.types import LEVEL_LABEL, Hit, RuleContext, context_from_parse_dicts

logger = logging.getLogger(__name__)

_TZ = timezone(timedelta(hours=8))


@dataclass
class RuleRunResult:
    case_id: str
    contract_title: str
    overall_risk_level: str
    hits: list[Hit] = field(default_factory=list)
    focus_points: list[dict] = field(default_factory=list)
    summary_text: str = ""
    comment_text: str = ""
    generated_at: str = ""

    @property
    def public_hits(self) -> list[dict]:
        return [hit.to_public_dict() for hit in self.hits if hit.is_hit]

    def to_tool_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "overall_risk_level": self.overall_risk_level,
            "risk_level_label": LEVEL_LABEL[self.overall_risk_level],
            "hit_count": len(self.public_hits),
            "hits": self.public_hits,
            "summary_text": self.summary_text,
            "focus_points": self.focus_points,
            "comment_text": self.comment_text,
            "generated_at": self.generated_at,
        }


def context_from_parse_rows(case_id: str, rows: list[ContractParse]) -> RuleContext:
    parses = [
        {
            "basic_info": row.basic_info_json or {},
            "clauses": row.clause_info_json or {},
            "full_text": row.full_text or "",
            "text_blocks": row.text_blocks_json or [],
        }
        for row in rows
    ]
    return context_from_parse_dicts(case_id, parses)


def load_active_rules(db: Session) -> list[ReviewRule]:
    return list(
        db.scalars(
            select(ReviewRule)
            .where(ReviewRule.rule_status == "active")
            .order_by(ReviewRule.priority, ReviewRule.id)
        ).all()
    )


def match_rules(rules: list[RuleLike], ctx: RuleContext) -> list[Hit]:
    hits: list[Hit] = []
    for rule in rules:
        hits.extend(match_rule(rule, ctx))
    return hits


def _contract_title(rows: list[ContractParse], fallback: str) -> str:
    for row in rows:
        title = ((row.basic_info_json or {}).get("contract_title") or {}).get("value")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return fallback


def _confidence(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(max(0.0, min(1.0, round(float(value), 4)))))


def run_rules(
    db: Session,
    task: ApprovalTask,
    *,
    now: datetime | None = None,
    comment_max_chars: int = 5000,
) -> RuleRunResult:
    """执行规则审查并写入 rule_hits；不写 review_results（由 save_review_result 负责）。"""
    rows = list(
        db.scalars(
            select(ContractParse).where(ContractParse.task_id == task.id)
        ).all()
    )
    if not rows:
        raise ParseRequiredError("合同尚未解析，请先调用 parse_contract_document")
    failed = [row for row in rows if row.parse_status != "success"]
    if failed:
        reasons = ", ".join(sorted({row.parse_error or "UNKNOWN" for row in failed}))
        raise ParseRequiredError(f"存在解析失败的附件，请先处理：{reasons}")

    rules = load_active_rules(db)
    if not rules:
        raise ConflictError("没有启用的审查规则，请先执行 scripts/seed_rules.py")

    ctx = context_from_parse_rows(task.approval_code, rows)
    hits = match_rules(rules, ctx)

    db.execute(delete(RuleHit).where(RuleHit.task_id == task.id))
    rule_ids = {rule.rule_code: rule.id for rule in rules}
    for hit in hits:
        db.add(
            RuleHit(
                task_id=task.id,
                rule_id=rule_ids[hit.rule_code],
                evidence_text=hit.evidence_text,
                evidence_position=hit.evidence_position[:256],
                evidence_page=hit.evidence_page,
                evidence_confidence=_confidence(hit.evidence_confidence),
                matched_field=hit.matched_field,
                hit_status=hit.hit_status,
            )
        )
    db.flush()

    overall = aggregate_risk(hits)
    focus_points = build_focus_points(hits)
    summary_text = build_summary_text(hits, overall)
    generated = (now or datetime.now(_TZ)).isoformat(timespec="seconds")
    contract_title = _contract_title(rows, task.approval_title)
    comment_text = build_comment_text(
        approval_code=task.approval_code,
        contract_title=contract_title,
        hits=hits,
        focus_points=focus_points,
        overall_risk_level=overall,
        generated_at=generated,
        max_chars=comment_max_chars,
    )

    logger.info(
        "规则审查完成 %s：命中 %s 条（总等级 %s）",
        task.approval_code,
        len([hit for hit in hits if hit.is_hit]),
        overall,
    )
    return RuleRunResult(
        case_id=task.approval_code,
        contract_title=contract_title,
        overall_risk_level=overall,
        hits=hits,
        focus_points=focus_points,
        summary_text=summary_text,
        comment_text=comment_text,
        generated_at=generated,
    )
