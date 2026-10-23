"""OCR 封装：RapidOCR（rapidocr-onnxruntime，离线中文模型，纯 CPU）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_engine = None


class OcrUnavailableError(RuntimeError):
    """OCR 引擎不可用或识别过程异常，映射为 blocked_reason=OCR_FAILED。"""


def get_engine():
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:  # noqa: BLE001 - 导入失败统一视为不可用
            raise OcrUnavailableError(f"RapidOCR 不可用：{exc}") from exc
        try:
            _engine = RapidOCR()
        except Exception as exc:  # noqa: BLE001 - 模型加载失败
            raise OcrUnavailableError(f"RapidOCR 初始化失败：{exc}") from exc
    return _engine


def ocr_pil_image(image) -> tuple[str, float]:
    """对 PIL.Image 做 OCR，返回 (按行拼接文本, 平均置信度)。"""
    import numpy as np

    array = np.array(image.convert("RGB"))
    # RapidOCR/OpenCV 约定 BGR 通道
    bgr = array[:, :, ::-1].copy()

    engine = get_engine()
    try:
        result, _ = engine(bgr)
    except Exception as exc:  # noqa: BLE001 - 识别异常归为 OCR 失败
        raise OcrUnavailableError(f"OCR 识别失败：{exc}") from exc

    if not result:
        return "", 0.0

    lines: list[str] = []
    scores: list[float] = []
    for item in result:
        if len(item) < 3:
            continue
        text = str(item[1]).strip()
        if not text:
            continue
        lines.append(text)
        try:
            scores.append(float(item[2]))
        except (TypeError, ValueError):
            continue

    confidence = sum(scores) / len(scores) if scores else 0.0
    return "\n".join(lines), confidence
