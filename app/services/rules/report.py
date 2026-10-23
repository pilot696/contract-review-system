"""审查结论渲染：审批关注点、中文摘要、评论正文（设计文档 §7.5.6–§7.5.7、附录 C）。"""

from __future__ import annotations

from app.services.rules.types import LEVEL_LABEL, LEVEL_RANK, Hit

MAX_FOCUS_POINTS = 10


def build_focus_points(hits: list[Hit], limit: int = MAX_FOCUS_POINTS) -> list[dict]:
    """高风险在前 → 优先级 → 规则编码；无中高风险时才列低风险提示。"""
    actual = [hit for hit in hits if hit.is_hit]
    primary = [hit for hit in actual if hit.risk_level in {"high", "medium"}]
    selected = primary or [hit for hit in actual if hit.risk_level == "low"]
    selected = sorted(
        selected, key=lambda hit: (LEVEL_RANK[hit.risk_level], hit.priority, hit.rule_code)
    )

    focus_points: list[dict] = []
    seen: set[str] = set()
    for hit in selected:
        if hit.rule_code in seen:
            continue
        seen.add(hit.rule_code)
        focus_points.append(
            {
                "code": f"RULE:{hit.rule_code}",
                "level": hit.risk_level,
                "title": hit.rule_name,
                "detail": hit.suggestion,
                "evidence": hit.evidence_text,
            }
        )
        if len(focus_points) >= limit:
            break

    if not focus_points:
        focus_points.append(
            {
                "code": "NONE",
                "level": "low",
                "title": "未发现明显风险",
                "detail": "未命中风险规则，建议法务对关键条款做最终确认。",
                "evidence": "",
            }
        )
    return focus_points


def build_summary_text(hits: list[Hit], overall_risk_level: str) -> str:
    actual = [hit for hit in hits if hit.is_hit]
    if not actual:
        return "本次审查未命中风险规则，总体风险等级为【低】；建议法务对关键条款做最终确认。"

    high = sum(1 for hit in actual if hit.risk_level == "high")
    medium = sum(1 for hit in actual if hit.risk_level == "medium")
    low = sum(1 for hit in actual if hit.risk_level == "low")
    label = LEVEL_LABEL[overall_risk_level]
    focus = build_focus_points(hits)
    focus_text = "；".join(
        f"{index + 1}) {point['title']}：{point['detail']}"
        for index, point in enumerate(focus[:3])
    )
    return (
        f"本次审查共命中 {len(actual)} 条风险规则（高 {high} 条、中 {medium} 条、低 {low} 条），"
        f"总体风险等级为【{label}】。主要关注点：{focus_text}。"
        "建议法务复核上述条款后处理；本意见由规则引擎自动生成，仅供参考，不替代人工审批。"
    )


def build_comment_text(
    *,
    approval_code: str,
    contract_title: str,
    hits: list[Hit],
    focus_points: list[dict],
    overall_risk_level: str,
    generated_at: str,
    max_chars: int = 5000,
) -> str:
    actual = [hit for hit in hits if hit.is_hit]
    label = LEVEL_LABEL[overall_risk_level]
    lines: list[str] = [
        "【合同智能审查意见】",
        "",
        f"审批编号：{approval_code}",
        f"合同名称：{contract_title}",
        f"总体风险等级：{label}",
        "",
        f"一、命中规则（{len(actual)} 条）",
    ]
    if actual:
        for index, hit in enumerate(actual, start=1):
            lines.extend(
                [
                    f"{index}. [{LEVEL_LABEL[hit.risk_level]}] {hit.rule_name}",
                    f"   证据：{hit.evidence_text}",
                    f"   位置：{hit.evidence_position}",
                    f"   建议：{hit.suggestion}",
                ]
            )
    else:
        lines.append("未命中风险规则。")

    lines.extend(["", "二、审批关注点"])
    for index, point in enumerate(focus_points, start=1):
        lines.append(f"{index}. {point['title']}：{point['detail']}")

    lines.extend(
        [
            "",
            "三、说明",
            "本意见由合同审查工具服务自动生成，仅供审批参考，不替代人工审批。",
            f"生成时间：{generated_at}",
        ]
    )
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…（内容过长已截断，详见系统）"
    return text
