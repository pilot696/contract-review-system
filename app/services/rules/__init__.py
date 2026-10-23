"""规则引擎子包：四类匹配器、风险汇总、关注点与评论生成。"""

from app.services.rules.engine import RuleRunResult, context_from_parse_rows, match_rules, run_rules
from app.services.rules.risk import aggregate_risk
from app.services.rules.types import (
    LEVEL_LABEL,
    LEVEL_RANK,
    Hit,
    RuleContext,
    context_from_parse_dicts,
)

__all__ = [
    "LEVEL_LABEL",
    "LEVEL_RANK",
    "Hit",
    "RuleContext",
    "RuleRunResult",
    "aggregate_risk",
    "context_from_parse_dicts",
    "context_from_parse_rows",
    "match_rules",
    "run_rules",
]
