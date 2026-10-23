"""可选 LLM 补抽：仅填补确定性抽取失败的字段（设计文档 §7.4.9）。

约束：
- 默认关闭（LLM_ENABLED=false）；未配置 base/key 时直接跳过；
- 只补 missing 字段，绝不覆盖确定性高置信结果；
- LLM 输出按不可信数据处理，异常/超时/格式错误一律回退；
- 置信度上限 0.7，并标记 source=llm。
"""

from __future__ import annotations

import json
import logging
import re
import uuid

import httpx

from app.config import Settings
from app.services.parsing.fields import BASIC_FIELDS, CLAUSE_FIELDS, FieldValue, _chinese_to_number

logger = logging.getLogger(__name__)

_SESSION_ID = f"contract-review-{uuid.uuid4().hex[:12]}"

_BASIC_LABELS: dict[str, str] = {
    "contract_title": "合同标题（字符串）",
    "contract_no": "合同编号（字符串）",
    "signing_entity": "甲方/签约主体名称（字符串）",
    "counterparty_name": "乙方/对方名称（字符串）",
    "amount": "合同金额（数字，单位元，不带千分位）",
    "currency": "币种（CNY/USD/EUR）",
    "effective_date": "生效日期（YYYY-MM-DD）",
    "expiration_date": "到期日期（YYYY-MM-DD）",
}

_CLAUSE_LABELS: dict[str, str] = {
    "payment_terms": "付款条款原文（字符串）",
    "delivery_terms": "交付条款原文（字符串）",
    "acceptance_terms": "验收条款原文（字符串）",
    "breach_terms": "违约条款原文（字符串）",
    "confidentiality_terms": "保密条款原文（字符串）",
    "data_terms": "数据处理条款原文（字符串）",
    "ip_terms": "知识产权条款原文（字符串）",
    "dispute_resolution_terms": "争议解决条款原文（字符串）",
}


def enrich(
    basic: dict[str, FieldValue],
    clauses: dict[str, FieldValue],
    full_text: str,
    settings: Settings,
) -> int:
    """尝试用 LLM 补抽缺失字段，返回实际填补数量；任何异常都被吞掉。"""
    if not (settings.llm_enabled and settings.llm_base_url and settings.llm_api_key):
        return 0

    missing_basic = [name for name in BASIC_FIELDS if basic[name].extract_status == "missing"]
    missing_clauses = [name for name in CLAUSE_FIELDS if clauses[name].extract_status == "missing"]
    if not missing_basic and not missing_clauses:
        return 0

    prompt = _build_prompt(full_text, missing_basic, missing_clauses)
    try:
        content = _chat(settings, prompt)
        if not content.strip():
            logger.info("LLM 未返回可抽取内容（视为无补充字段）")
            return 0
        payload = _parse_json_object(content)
    except Exception as exc:  # noqa: BLE001 - 可选增强，失败即回退
        logger.warning("LLM 补抽失败（保持确定性结果）：%s", exc)
        return 0

    filled = 0
    for name in missing_basic:
        raw = payload.get(name)
        value = _coerce(name, raw)
        if value is None:
            continue
        basic[name] = FieldValue(
            value=value,
            unit="CNY" if name == "amount" else None,
            extract_status="extracted",
            confidence=0.7,
            evidence_text=f"LLM 补抽：{str(value)[:120]}",
            position="LLM 补抽",
            source="llm",
        )
        filled += 1

    for name in missing_clauses:
        raw = payload.get(name)
        if not isinstance(raw, str) or not raw.strip():
            continue
        clauses[name] = FieldValue(
            value=raw.strip()[:2000],
            extract_status="extracted",
            confidence=0.7,
            evidence_text=f"LLM 补抽：{raw.strip()[:120]}",
            position="LLM 补抽",
            source="llm",
        )
        filled += 1
    return filled


def _build_prompt(full_text: str, missing_basic: list[str], missing_clauses: list[str]) -> str:
    lines = [
        "你是企业合同信息抽取器。只输出一个 JSON 对象，不要输出解释、Markdown 代码块或多余文本。",
        "只能依据 <合同文本> 中的原文抽取；找不到的字段请省略，不要编造。",
        "需要抽取的字段：",
    ]
    for name in missing_basic:
        lines.append(f"- {name}：{_BASIC_LABELS[name]}")
    for name in missing_clauses:
        lines.append(f"- {name}：{_CLAUSE_LABELS[name]}")
    lines.append("输出示例：{\"amount\": 500000, \"effective_date\": \"2026-11-01\"}")
    lines.append("<合同文本>")
    lines.append(full_text[:15000])
    lines.append("</合同文本>")
    return "\n".join(lines)


def _chat(settings: Settings, prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.llm_user_agent,
        "x-opencode-session": settings.llm_session_id or _SESSION_ID,
    }
    with httpx.Client(
        base_url=settings.llm_base_url.rstrip("/"), timeout=settings.llm_timeout_seconds
    ) as client:
        response = client.post(
            "/chat/completions",
            headers=headers,
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500,
                "temperature": 0,
            },
        )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("LLM 响应缺少 choices")
    content = (choices[0].get("message") or {}).get("content") or ""
    return content


def _parse_json_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text.strip())
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError("LLM 输出不是 JSON 对象") from None
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("LLM 输出不是 JSON 对象")
    return payload


def _coerce(field: str, raw: object) -> str | float | None:
    if raw is None:
        return None
    if field == "amount":
        if isinstance(raw, (int, float)):
            return float(raw) if raw > 0 else None
        text = str(raw).replace(",", "").replace("¥", "").replace("￥", "").strip()
        try:
            return float(text)
        except ValueError:
            number = _chinese_to_number(text)
            return float(number) if number else None
    if field in {"effective_date", "expiration_date"}:
        match = re.search(r"(\d{4})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})", str(raw))
        if not match:
            return None
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    if field == "currency":
        text = str(raw).upper()
        for code in ("CNY", "USD", "EUR"):
            if code in text:
                return code
        return None
    text = str(raw).strip()
    return text or None


__all__ = ["enrich"]
