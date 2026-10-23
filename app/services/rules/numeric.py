"""数值型规则指标：预付款比例、付款周期（设计文档 §7.5.3）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.rules.types import RuleContext, locate_in_blocks

_PREPAY_PATTERNS = (
    r"预付款?\s*比例\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%",
    r"合同(?:总)?金额的\s*(\d+(?:\.\d+)?)\s*%\s*作为预付款",
    r"支付(?:合同)?(?:总)?金额的\s*(\d+(?:\.\d+)?)\s*%\s*作为预付款",
)
_PREPAY_AMOUNT_PATTERN = r"(?:预付(?:款)?金额|预付款)\s*(?:为|[:：])?\s*(?:人民币)?\s*([0-9][0-9,]*(?:\.\d+)?)\s*元"

_PERIOD_PATTERNS = (
    r"(\d+)\s*(?:个)?(?:天|日)\s*内\s*(?:付款|支付)",
    r"月结\s*(\d+)\s*天",
    r"(\d+)\s*个月(?:内)?\s*(?:付款|支付)",
    r"(?:账期|付款周期)\s*(?:为|[:：])?\s*(\d+)\s*(?:天|日)",
)


@dataclass(frozen=True)
class Metric:
    name: str
    value: float | None
    evidence_text: str = ""
    evidence_position: str = "全文"
    evidence_page: int | None = None
    note: str = ""


def _text_candidates(ctx: RuleContext) -> list[str]:
    payment = str(ctx.field_values.get("payment_terms") or "")
    candidates = [payment] if payment else []
    if ctx.full_text:
        candidates.append(ctx.full_text)
    return candidates


def _snippet(ctx: RuleContext, text: str, start: int, end: int, window: int = 40) -> str:
    return text[max(0, start - window) : min(len(text), end + window)]


def prepayment_ratio(ctx: RuleContext) -> Metric:
    for text in _text_candidates(ctx):
        for pattern in _PREPAY_PATTERNS:
            match = re.search(pattern, text)
            if match:
                ratio = float(match.group(1)) / 100
                snippet = _snippet(ctx, text, match.start(), match.end())
                ev_text, position, page, _ = locate_in_blocks(ctx.blocks, match.group(0))
                return Metric(
                    name="prepayment_ratio",
                    value=ratio,
                    evidence_text=ev_text or snippet,
                    evidence_position=position,
                    evidence_page=page,
                )

    total = ctx.field_values.get("amount")
    for text in _text_candidates(ctx):
        match = re.search(_PREPAY_AMOUNT_PATTERN, text)
        if not match:
            continue
        try:
            prepay = float(match.group(1).replace(",", ""))
        except ValueError:  # pragma: no cover
            continue
        if isinstance(total, (int, float)) and total > 0:
            ev_text, position, page, _ = locate_in_blocks(ctx.blocks, match.group(0))
            return Metric(
                name="prepayment_ratio",
                value=prepay / float(total),
                evidence_text=ev_text or match.group(0),
                evidence_position=position,
                evidence_page=page,
            )
    return Metric(name="prepayment_ratio", value=None, note="预付款比例不可计算")


def payment_period_days(ctx: RuleContext) -> Metric:
    best: tuple[int, str, int | None] | None = None
    for text in _text_candidates(ctx):
        for pattern in _PERIOD_PATTERNS:
            for match in re.finditer(pattern, text):
                raw = int(match.group(1))
                days = raw * 30 if "个月" in match.group(0) and "月结" not in match.group(0) else raw
                if best is None or days > best[0]:
                    best = (days, match.group(0), match.start())
                break
        if best is not None:
            ev_text, position, page, _ = locate_in_blocks(ctx.blocks, best[1])
            return Metric(
                name="payment_period_days",
                value=float(best[0]),
                evidence_text=ev_text,
                evidence_position=position,
                evidence_page=page,
            )
    return Metric(name="payment_period_days", value=None, note="付款周期不可计算")


_METRICS = {
    "prepayment_ratio": prepayment_ratio,
    "payment_period_days": payment_period_days,
}


def compute_metric(name: str, ctx: RuleContext) -> Metric:
    compute = _METRICS.get(name)
    if compute is None:
        return Metric(name=name, value=None, note=f"未知指标：{name}")
    return compute(ctx)
