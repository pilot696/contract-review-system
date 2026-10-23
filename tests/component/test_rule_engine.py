"""规则引擎组件测试：三份真实样本的命中集合与总风险等级（附录 B）。"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.services.parsing.pipeline import parse_file
from app.services.rules import aggregate_risk, context_from_parse_dicts, match_rules
from app.services.rules.seed import RULES_SEED

SAMPLES = Path("mock_approval/files")


def _context(name: str, file_type: str, case_id: str):
    settings = get_settings().model_copy(update={"llm_enabled": False})
    outcome = parse_file(SAMPLES / name, file_type, settings)
    assert outcome.parse_status == "success", outcome.parse_error
    return context_from_parse_dicts(
        case_id,
        [
            {
                "basic_info": outcome.basic_info,
                "clauses": outcome.clauses,
                "full_text": outcome.full_text,
                "text_blocks": outcome.text_blocks,
            }
        ],
    )


def _hits(name: str, file_type: str, case_id: str):
    ctx = _context(name, file_type, case_id)
    hits = match_rules(list(RULES_SEED), ctx)
    return ctx, hits


def test_sample1_standard_no_hits() -> None:
    _, hits = _hits("sample_01_standard.docx", "docx", "AP20261021001")
    actual = [hit for hit in hits if hit.is_hit]
    assert actual == [], [(hit.rule_code, hit.evidence_text) for hit in actual]
    assert aggregate_risk(hits) == "low"


def test_sample2_risky_expected_hits() -> None:
    _, hits = _hits("sample_02_risky.pdf", "pdf", "AP20261021002")
    actual = [hit for hit in hits if hit.is_hit]
    codes = {hit.rule_code for hit in actual}
    assert codes == {"R001", "R002", "R003", "R004", "R005", "R011"}, sorted(codes)
    assert aggregate_risk(hits) == "high"


def test_sample3_scan_expected_hits() -> None:
    ctx, hits = _hits("sample_03_scan.png", "png", "AP20261021003")
    actual = [hit for hit in hits if hit.is_hit]
    codes = {hit.rule_code for hit in actual}
    assert codes == {"R007", "R008", "R009", "R010"}, sorted(codes)
    assert aggregate_risk(hits) == "high"
    assert ctx.field_status["amount"] == "missing"
