"""文本规范化（设计文档 §7.4.4）。"""

from __future__ import annotations

_FULLWIDTH_MAP: dict[int, int] = {}
for _code in range(0xFF10, 0xFF1A):  # ０-９
    _FULLWIDTH_MAP[_code] = _code - 0xFEE0
for _code in range(0xFF21, 0xFF3B):  # Ａ-Ｚ
    _FULLWIDTH_MAP[_code] = _code - 0xFEE0
for _code in range(0xFF41, 0xFF5B):  # ａ-ｚ
    _FULLWIDTH_MAP[_code] = _code - 0xFEE0
_FULLWIDTH_MAP[0x3000] = 0x20  # 全角空格

_TRANS = str.maketrans(_FULLWIDTH_MAP)


def normalize_text(text: str) -> str:
    """全角数字/字母转半角，压缩空白；保留中文标点与换行。"""
    if not text:
        return ""
    translated = text.translate(_TRANS)
    lines = []
    for line in translated.split("\n"):
        compact = " ".join(part for part in line.replace("\t", " ").split(" ") if part)
        if compact:
            lines.append(compact)
    return "\n".join(lines)
