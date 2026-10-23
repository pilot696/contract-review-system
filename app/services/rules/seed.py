"""11 条种子规则（设计文档 §4.5）。DB 与测试共用同一份定义。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import ReviewRule


@dataclass(frozen=True)
class RuleDef:
    rule_code: str
    rule_name: str
    risk_level: str
    match_mode: str
    match_text: str
    suggestion_text: str
    priority: int = 100
    rule_status: str = "active"


RULES_SEED: tuple[RuleDef, ...] = (
    RuleDef(
        rule_code="R001",
        rule_name="预付款比例过高",
        risk_level="high",
        match_mode="numeric_threshold",
        match_text='{"metric":"prepayment_ratio","op":">","value":0.5}',
        suggestion_text="建议将预付款比例控制在30%–50%，并与交付节点挂钩。",
        priority=10,
    ),
    RuleDef(
        rule_code="R002",
        rule_name="付款周期过长",
        risk_level="medium",
        match_mode="numeric_threshold",
        match_text='{"metric":"payment_period_days","op":">","value":90}',
        suggestion_text="建议将付款周期压缩至90天以内，或增加分期与验收挂钩。",
        priority=110,
    ),
    RuleDef(
        rule_code="R003",
        rule_name="自动续约条款",
        risk_level="medium",
        match_mode="keyword",
        match_text="自动续约|自动延期|自动展期",
        suggestion_text="建议删除自动续约，或改为到期前双方书面确认后续约。",
        priority=120,
    ),
    RuleDef(
        rule_code="R004",
        rule_name="违约责任不对等",
        risk_level="high",
        match_mode="regex",
        match_text=r"(乙方|供方|卖方).{0,12}(承担全部|独自承担).{0,12}(违约|赔偿)",
        suggestion_text="建议补充双方对等违约责任，并约定责任上限。",
        priority=20,
    ),
    RuleDef(
        rule_code="R005",
        rule_name="管辖地不利",
        risk_level="medium",
        match_mode="regex",
        match_text=r"(由|向).{0,12}(乙方所在地|对方所在地|供方所在地).{0,12}(人民法院|法院|仲裁)",
        suggestion_text="建议将争议解决地约定为甲方所在地或双方认可的中立地。",
        priority=130,
    ),
    RuleDef(
        rule_code="R006",
        rule_name="主体信息缺失",
        risk_level="high",
        match_mode="field_absent",
        match_text="signing_entity,counterparty_name",
        suggestion_text="建议补充完整签约主体与对方主体信息（名称、统一社会信用代码）。",
        priority=30,
    ),
    RuleDef(
        rule_code="R007",
        rule_name="金额信息缺失",
        risk_level="high",
        match_mode="field_absent",
        match_text="amount",
        suggestion_text="建议明确合同总金额、币种及是否含税。",
        priority=40,
    ),
    RuleDef(
        rule_code="R008",
        rule_name="保密条款缺失",
        risk_level="medium",
        match_mode="field_absent",
        match_text="confidentiality_terms",
        suggestion_text="建议补充保密条款，明确保密范围、期限与违约责任。",
        priority=140,
    ),
    RuleDef(
        rule_code="R009",
        rule_name="数据处理条款风险",
        risk_level="high",
        match_mode="regex",
        match_text=r"(数据).{0,20}(任意使用|永久使用|无需授权|自由使用|共享给第三方|用于训练)",
        suggestion_text="建议限定数据使用目的、范围与保存期限，并要求删除/返还。",
        priority=50,
    ),
    RuleDef(
        rule_code="R010",
        rule_name="知识产权归属不明",
        risk_level="high",
        match_mode="field_absent",
        match_text="ip_terms",
        suggestion_text="建议明确知识产权归属、许可范围及侵权责任。",
        priority=60,
    ),
    RuleDef(
        rule_code="R011",
        rule_name="验收标准缺失",
        risk_level="medium",
        match_mode="field_absent",
        match_text="acceptance_terms",
        suggestion_text="建议补充验收标准、流程与不合格处理方式。",
        priority=150,
    ),
)


def upsert_seed_rules(db: Session) -> int:
    """幂等写入/更新 11 条种子规则（脚本与测试共用）。"""
    for rule in RULES_SEED:
        stmt = (
            pg_insert(ReviewRule)
            .values(
                rule_code=rule.rule_code,
                rule_name=rule.rule_name,
                risk_level=rule.risk_level,
                rule_status=rule.rule_status,
                match_mode=rule.match_mode,
                match_text=rule.match_text,
                suggestion_text=rule.suggestion_text,
                priority=rule.priority,
            )
            .on_conflict_do_update(
                index_elements=["rule_code"],
                set_={
                    "rule_name": rule.rule_name,
                    "risk_level": rule.risk_level,
                    "match_mode": rule.match_mode,
                    "match_text": rule.match_text,
                    "suggestion_text": rule.suggestion_text,
                    "priority": rule.priority,
                    "updated_at": func.now(),
                },
            )
        )
        db.execute(stmt)
    return len(RULES_SEED)
