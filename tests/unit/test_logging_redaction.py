"""日志脱敏单元测试（设计文档 §7.7）。"""

from __future__ import annotations

from app.logging import redact_text


def test_redact_masks_phone_email_and_id_card() -> None:
    text = "联系人：13800138000，邮箱 user@example.com，身份证 110101199001011234"
    masked = redact_text(text)
    assert "13800138000" not in masked
    assert "user@example.com" not in masked
    assert "110101199001011234" not in masked
    assert "手机号***" in masked
    assert "***@***" in masked
    assert "身份证***" in masked


def test_redact_truncates_long_text() -> None:
    assert len(redact_text("合同正文" * 500, max_chars=500)) == 500
    assert redact_text("短文本") == "短文本"
