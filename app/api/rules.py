"""规则维护接口（设计文档 §6.9、§7.5.8）。"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import ReviewRule, RuleHit
from app.services.parsing.fields import BASIC_FIELDS
from app.services.parsing.segment import CLAUSE_FIELDS

router = APIRouter(prefix="/api/rules", tags=["rules"])

_ALLOWED_FIELDS = set(BASIC_FIELDS) | set(CLAUSE_FIELDS)
_MATCH_MODES = {"keyword", "regex", "field_absent", "numeric_threshold"}
_RISK_LEVELS = {"low", "medium", "high"}


class RulePayload(BaseModel):
    rule_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,31}$")
    rule_name: str = Field(min_length=2, max_length=128)
    risk_level: str = Field(pattern="^(low|medium|high)$")
    match_mode: str = Field(pattern="^(keyword|regex|field_absent|numeric_threshold)$")
    match_text: str = Field(min_length=1, max_length=2000)
    suggestion_text: str = Field(min_length=1, max_length=2000)
    priority: int = Field(default=100, ge=0, le=10000)
    rule_status: str = Field(default="active", pattern="^(active|inactive)$")


def validate_match_text(mode: str, match_text: str) -> None:
    if mode == "regex":
        try:
            re.compile(match_text)
        except re.error as exc:
            raise ValidationError(f"正则表达式无效：{exc}") from exc
    elif mode == "field_absent":
        fields = [field.strip() for field in match_text.split(",") if field.strip()]
        if not fields or any(field not in _ALLOWED_FIELDS for field in fields):
            raise ValidationError(
                f"field_absent 字段必须来自解析字段：{', '.join(sorted(_ALLOWED_FIELDS))}"
            )
    elif mode == "numeric_threshold":
        try:
            spec = json.loads(match_text)
        except ValueError as exc:
            raise ValidationError("numeric_threshold 的 match_text 必须是 JSON") from exc
        if not {"metric", "op", "value"} <= set(spec):
            raise ValidationError("numeric_threshold 需要 metric/op/value")
        if spec["metric"] not in {"prepayment_ratio", "payment_period_days"}:
            raise ValidationError("不支持的 metric")
        if spec.get("op") not in {">", ">=", "<", "<=", "=="}:
            raise ValidationError("op 必须是 > >= < <= ==")


def _serialize(rule: ReviewRule) -> dict:
    return {
        "id": rule.id,
        "rule_code": rule.rule_code,
        "rule_name": rule.rule_name,
        "risk_level": rule.risk_level,
        "rule_status": rule.rule_status,
        "match_mode": rule.match_mode,
        "match_text": rule.match_text,
        "suggestion_text": rule.suggestion_text,
        "priority": rule.priority,
        "updated_at": rule.updated_at.isoformat(timespec="seconds") if rule.updated_at else None,
    }


@router.get("")
def list_rules(db: Session = Depends(get_db)) -> dict:
    rules = list(
        db.scalars(select(ReviewRule).order_by(ReviewRule.priority, ReviewRule.id)).all()
    )
    return {"ok": True, "data": {"items": [_serialize(rule) for rule in rules], "total": len(rules)}}


@router.post("", status_code=201)
def create_rule(payload: RulePayload, db: Session = Depends(get_db)) -> dict:
    validate_match_text(payload.match_mode, payload.match_text)
    exists = db.scalar(select(ReviewRule).where(ReviewRule.rule_code == payload.rule_code))
    if exists:
        raise ConflictError(f"规则编码已存在：{payload.rule_code}")
    rule = ReviewRule(**payload.model_dump())
    db.add(rule)
    db.commit()
    return {"ok": True, "data": _serialize(rule)}


@router.put("/{rule_code}")
def update_rule(rule_code: str, payload: RulePayload, db: Session = Depends(get_db)) -> dict:
    rule = db.scalar(select(ReviewRule).where(ReviewRule.rule_code == rule_code))
    if rule is None:
        raise NotFoundError(f"规则不存在：{rule_code}")
    if payload.rule_code != rule_code:
        raise ValidationError("不允许修改 rule_code")
    validate_match_text(payload.match_mode, payload.match_text)
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    db.commit()
    return {"ok": True, "data": _serialize(rule)}


@router.post("/{rule_code}/toggle")
def toggle_rule(rule_code: str, db: Session = Depends(get_db)) -> dict:
    rule = db.scalar(select(ReviewRule).where(ReviewRule.rule_code == rule_code))
    if rule is None:
        raise NotFoundError(f"规则不存在：{rule_code}")
    rule.rule_status = "inactive" if rule.rule_status == "active" else "active"
    db.commit()
    return {"ok": True, "data": _serialize(rule)}


@router.delete("/{rule_code}")
def delete_rule(rule_code: str, db: Session = Depends(get_db)) -> dict:
    rule = db.scalar(select(ReviewRule).where(ReviewRule.rule_code == rule_code))
    if rule is None:
        raise NotFoundError(f"规则不存在：{rule_code}")
    hit_count = int(
        db.scalar(select(func.count()).select_from(RuleHit).where(RuleHit.rule_id == rule.id)) or 0
    )
    if hit_count:
        raise ConflictError("该规则已有历史命中，请改为停用")
    db.delete(rule)
    db.commit()
    return {"ok": True, "data": {"rule_code": rule_code, "deleted": True}}
