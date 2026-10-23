"""M2 示例合同生成：真实可解析的 docx / 文本 PDF / 扫描 PNG / 空文本 PDF。

输出目录：mock_approval/files/
- sample_01_standard.docx   标准服务合同（预期低风险）
- sample_02_risky.pdf       高风险采购合同（预期 R001/R002/R003/R004/R005/R011）
- sample_03_scan.png        扫描件租赁合同（预期 R007/R008/R009/R010，OCR 路径）
- sample_04_empty.pdf       空文本 PDF（解析失败：DOCUMENT_EMPTY）

样本为合成数据，不含真实主体或个人信息。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FILES_DIR = Path(__file__).resolve().parent / "files"

_FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
]

# 低于该字节数视为 M1 占位文件，需要重新生成
_MIN_SIZES = {
    "sample_01_standard.docx": 5_000,
    "sample_02_risky.pdf": 1_500,
    "sample_03_scan.png": 20_000,
    "sample_04_empty.pdf": 500,
}

SAMPLE_01_STANDARD_DOCX = [
    "软件开发服务合同",
    "合同编号：HT-2026-0088",
    "甲方：启明科技有限公司",
    "乙方：云帆软件有限公司",
    "合同总金额为人民币伍拾万元整（¥500,000.00），币种为人民币。",
    "本合同自2026年11月1日起生效，有效期至2027年10月31日。",
    "一、付款条款：合同签订后5个工作日内支付合同总金额的30%作为预付款，验收合格后支付剩余70%。",
    "二、交付条款：乙方应于2027年3月31日前完成全部交付。",
    "三、验收条款：甲方应在收到交付物后15个工作日内完成验收，验收标准以双方确认的需求文档为准。",
    "四、违约责任：双方应各自承担相应的违约责任，任何一方违约的，应赔偿对方因此产生的直接损失。",
    "五、保密条款：双方对合作过程中知悉的商业秘密负有保密义务，保密期限为三年。",
    "六、数据条款：乙方仅可在本项目范围内处理甲方提供的业务数据，项目结束后应删除相关数据。",
    "七、知识产权条款：本项目产生的软件著作权归甲方所有，乙方享有署名权。",
    "八、争议解决：因本合同产生的争议，由甲方所在地人民法院管辖。",
]

SAMPLE_02_RISKY_PDF = [
    "服务器采购合同",
    "合同编号：HT-2026-0099",
    "甲方：启明科技有限公司",
    "乙方：星河数据设备有限公司",
    "合同总金额为人民币壹佰贰拾捌万元整（¥1,280,000.00），币种为人民币。",
    "本合同自2026年12月1日起生效，有效期至2027年11月30日。",
    "付款条款：合同签订后5个工作日内，甲方支付合同总金额的60%作为预付款。",
    "剩余40%的款项在验收后120天内支付。",
    "交付条款：乙方应于2026年12月31日前交付全部设备。",
    "续约条款：本合同到期后自动续约一年。",
    "违约责任：乙方承担全部违约责任，并赔偿甲方全部损失。",
    "保密条款：双方应对本合同内容保密，保密期限为五年。",
    "数据条款：乙方仅可在售后范围内处理设备运行数据。",
    "知识产权条款：乙方保证所供设备不侵犯第三方知识产权。",
    "争议解决：由乙方所在地人民法院管辖。",
]

SAMPLE_03_SCAN_PNG = [
    "设备租赁合同",
    "合同编号：HT-2026-0102",
    "甲方：启明科技有限公司",
    "乙方：恒达设备租赁有限公司",
    "本合同自2026年12月1日起生效，租赁期限至2027年11月30日止。",
    "付款条款：租金按季度支付，每季度首月10日前支付当季租金。",
    "交付条款：乙方应于2026年12月1日前交付全部租赁设备。",
    "验收条款：甲方应在设备到场后5个工作日内完成验收。",
    "违约责任：双方应各自承担相应的违约责任。",
    "数据条款：乙方有权将设备运行数据永久使用并共享给第三方。",
    "争议解决：由甲方所在地人民法院管辖。",
]


def find_font() -> Path:
    for candidate in _FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    raise RuntimeError(
        "未找到中文 TTF 字体（simhei.ttf / msyh.ttc / simsun.ttc）；"
        "可通过环境变量 SAMPLE_FONT_PATH 指定字体文件"
    )


def _write_docx(path: Path, lines: list[str]) -> None:
    import docx

    document = docx.Document()
    for line in lines:
        document.add_paragraph(line)
    document.save(str(path))


def _write_pdf(path: Path, lines: list[str]) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font_name = "SampleCN"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(find_font())))

    width, height = A4
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setFont(font_name, 10)
    y = height - 60
    for line in lines:
        if y < 60:
            pdf.showPage()
            pdf.setFont(font_name, 10)
            y = height - 60
        pdf.drawString(50, y, line)
        y -= 18
    if not lines:
        pdf.setFont(font_name, 10)
        pdf.drawString(50, height - 60, "")
    pdf.showPage()
    pdf.save()


def _write_scan_png(path: Path, lines: list[str]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1654, 2339  # A4 @ 200 DPI
    image = Image.new("RGB", (width, height), (248, 248, 246))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(find_font()), 26)
    y = 140
    for line in lines:
        draw.text((110, y), line, fill=(25, 25, 25), font=font)
        y += 46
    image.save(path, format="PNG", dpi=(200, 200))


def generate_all(files_dir: Path | None = None, force: bool = True) -> list[Path]:
    target_dir = files_dir or FILES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []

    docx_path = target_dir / "sample_01_standard.docx"
    pdf_path = target_dir / "sample_02_risky.pdf"
    png_path = target_dir / "sample_03_scan.png"
    empty_pdf_path = target_dir / "sample_04_empty.pdf"

    if force or not docx_path.exists():
        _write_docx(docx_path, SAMPLE_01_STANDARD_DOCX)
    if force or not pdf_path.exists():
        _write_pdf(pdf_path, SAMPLE_02_RISKY_PDF)
    if force or not png_path.exists():
        _write_scan_png(png_path, SAMPLE_03_SCAN_PNG)
    if force or not empty_pdf_path.exists():
        _write_pdf(empty_pdf_path, [])

    for path in (docx_path, pdf_path, png_path, empty_pdf_path):
        outputs.append(path)
        logger.info("sample generated: %s (%s bytes)", path.name, path.stat().st_size)
    return outputs


def samples_ready(files_dir: Path | None = None) -> bool:
    target_dir = files_dir or FILES_DIR
    for name, min_size in _MIN_SIZES.items():
        path = target_dir / name
        if not path.exists() or path.stat().st_size < min_size:
            return False
    return True


def ensure_samples(files_dir: Path | None = None) -> list[Path]:
    """幂等生成：文件缺失或仍为 M1 占位文件时才重新生成。"""
    target_dir = files_dir or FILES_DIR
    if samples_ready(target_dir):
        return [target_dir / name for name in _MIN_SIZES]
    return generate_all(target_dir, force=True)
