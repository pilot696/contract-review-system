"""规则引擎单元测试（U1–U4）：数值解析、四类匹配器、风险汇总、关注点与渲染。"""

from __future__ import annotations

from app.services.parsing.fields import _chinese_to_number
from app.services.rules.matchers import (
    match_field_absent,
    match_keyword,
    match_numeric_threshold,
    match_regex,
)
from app.services.rules.numeric import compute_metric
from app.services.rules.report import build_comment_text, build_focus_points, build_summary_text
from app.services.rules.risk import aggregate_risk
from app.services.rules.seed import RULES_SEED
from app.services.rules.types import Hit, RuleContext

_RULES = {rule.rule_code: rule for rule in RULES_SEED}


def _ctx(
    text: str = "",
    fields: dict | None = None,
    statuses: dict | None = None,
    blocks: list[dict] | None = None,
) -> RuleContext:
    return RuleContext(
        case_id="AP-TEST",
        full_text=text,
        field_values=fields or {},
        field_status=statuses or {},
        blocks=blocks
        if blocks is not None
        else ([{"page_no": 1, "position": "第1页/第1段", "text": text, "source": "native", "confidence": 0.95}] if text else []),
    )


def _hit(code: str, status: str = "hit") -> Hit:
    rule = _RULES[code]
    return Hit(
        rule_code=rule.rule_code,
        rule_name=rule.rule_name,
        risk_level=rule.risk_level,
        evidence_text="证据",
        evidence_position="第1页/第1段",
        evidence_page=1,
        evidence_confidence=0.9,
        matched_field=None,
        hit_status=status,
        suggestion=rule.suggestion_text,
        priority=rule.priority,
    )


# --------------------------------------------------------------------------- #
# U1 数值解析
# --------------------------------------------------------------------------- #
def test_chinese_amount_parsing() -> None:
    assert _chinese_to_number("伍拾万") == 500000
    assert _chinese_to_number("壹佰贰拾捌万") == 1280000
    assert _chinese_to_number("叁千元") == 3000


def test_prepayment_ratio_metric() -> None:
    ctx = _ctx("合同签订后5个工作日内，甲方支付合同总金额的60%作为预付款。")
    metric = compute_metric("prepayment_ratio", ctx)
    assert metric.value == 0.6
    assert metric.evidence_text


def test_payment_period_metric() -> None:
    assert compute_metric("payment_period_days", _ctx("剩余40%的款项在验收后120天内支付。")).value == 120
    assert compute_metric("payment_period_days", _ctx("验收后3个月内支付尾款。")).value == 90
    assert compute_metric("prepayment_ratio", _ctx("租金按季度支付。")).value is None


# --------------------------------------------------------------------------- #
# U2 四类匹配器
# --------------------------------------------------------------------------- #
def test_keyword_matcher() -> None:
    rule = _RULES["R003"]
    hits = match_keyword(rule, _ctx("本合同到期后自动续约一年。"))
    assert len(hits) == 1 and hits[0].rule_code == "R003"
    assert match_keyword(rule, _ctx("本合同到期后双方协商续约。")) == []


def test_regex_matcher() -> None:
    rule = _RULES["R004"]
    hits = match_regex(rule, _ctx("违约责任：乙方承担全部违约责任，并赔偿甲方全部损失。"))
    assert len(hits) == 1
    assert match_regex(rule, _ctx("违约责任：双方应各自承担相应的违约责任。")) == []


def test_field_absent_matcher() -> None:
    rule = _RULES["R007"]
    missing_ctx = _ctx(fields={}, statuses={"amount": "missing"})
    hits = match_field_absent(rule, missing_ctx)
    assert len(hits) == 1 and hits[0].hit_status == "hit"
    present_ctx = _ctx(fields={"amount": 500000.0}, statuses={"amount": "extracted"})
    assert match_field_absent(rule, present_ctx) == []


def test_numeric_threshold_matcher() -> None:
    rule = _RULES["R001"]
    hit_ctx = _ctx("合同总金额的60%作为预付款。")
    hits = match_numeric_threshold(rule, hit_ctx)
    assert len(hits) == 1 and hits[0].rule_code == "R001"
    assert "0.6" in hits[0].evidence_text or "60.00%" in hits[0].evidence_text

    skipped = match_numeric_threshold(rule, _ctx("租金按季度支付。"))
    assert len(skipped) == 1 and skipped[0].hit_status == "skipped"


# --------------------------------------------------------------------------- #
# U3 风险汇总
# --------------------------------------------------------------------------- #
def test_aggregate_risk() -> None:
    assert aggregate_risk([]) == "low"
    assert aggregate_risk([_hit("R002"), _hit("R003")]) == "medium"
    assert aggregate_risk([_hit("R002"), _hit("R001")]) == "high"
    assert aggregate_risk([_hit("R001", status="skipped")]) == "low"


# --------------------------------------------------------------------------- #
# U4 关注点与渲染
# --------------------------------------------------------------------------- #
def test_focus_points_ordering_and_dedup() -> None:
    hits = [_hit("R002"), _hit("R001"), _hit("R001"), _hit("R008")]
    focus = build_focus_points(hits)
    assert focus[0]["code"] == "RULE:R001"  # high 优先
    assert len({point["code"] for point in focus}) == len(focus)  # 去重
    assert all(point["detail"] for point in focus)


def test_focus_points_fallback_when_no_hits() -> None:
    focus = build_focus_points([])
    assert focus[0]["code"] == "NONE"
    assert focus[0]["level"] == "low"


def test_summary_and_comment_rendering() -> None:
    hits = [_hit("R001"), _hit("R002")]
    summary = build_summary_text(hits, "high")
    assert "命中 2 条" in summary and "【高】" in summary

    focus = build_focus_points(hits)
    comment = build_comment_text(
        approval_code="AP20261021002",
        contract_title="服务器采购合同",
        hits=hits,
        focus_points=focus,
        overall_risk_level="high",
        generated_at="2026-10-22T16:40:00+08:00",
    )
    assert "【合同智能审查意见】" in comment
    assert "AP20261021002" in comment
    assert "总体风险等级：高" in comment
    assert "本意见由合同审查工具服务自动生成" in comment


def test_comment_text_respects_max_chars() -> None:
    hits = [_hit("R001"), _hit("R002"), _hit("R003")]
    focus = build_focus_points(hits)
    comment = build_comment_text(
        approval_code="AP20261021002",
        contract_title="服务器采购合同",
        hits=hits,
        focus_points=focus,
        overall_risk_level="high",
        generated_at="2026-10-22T16:40:00+08:00",
        max_chars=160,
    )
    assert len(comment) <= 160
    assert "截断" in comment
