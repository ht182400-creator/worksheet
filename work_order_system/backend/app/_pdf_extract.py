"""PDF 文本提取（内部模块，对应 M1-01/M1-02）。

使用 pypdf 提取含文本层电子工单的文字；纯图片扫描件无文本层时抛
OcrNoTextLayerError，由调用方按 M1-09/M1-10 降级为人工录入提示。
"""
import io
import traceback
from typing import List

import pypdf
from pypdf.errors import PdfReadError

from app.config import OCR_MAX_PAGES
from app.logger import get_logger

log = get_logger(__name__)


class OcrNoTextLayerError(Exception):
    """PDF 无文本层（纯扫描件），需 OCR 引擎或人工录入（M1-09）。"""


def extract_text(file_bytes: bytes) -> str:
    """从 PDF 字节提取全文（合并各页）。

    无文本层或解析失败时抛 OcrNoTextLayerError / PdfReadError，由上层映射为
    OCR 任务 FAILED 终态并给出明确提示（M1-09）。
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    except PdfReadError as exc:
        log.error("PDF 解析失败（非合法 PDF）: %s\n%s", exc, traceback.format_exc())
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf 可能抛多种异常
        log.error("PDF 读取异常: %s\n%s", exc, traceback.format_exc())
        raise OcrNoTextLayerError(f"PDF 读取异常: {exc}")

    if not reader.pages:
        raise OcrNoTextLayerError("PDF 无页面内容")

    parts: List[str] = []
    for idx, page in enumerate(reader.pages):
        if idx >= OCR_MAX_PAGES:
            log.warning("PDF 页数超过上限 %d，仅解析前 %d 页", OCR_MAX_PAGES, OCR_MAX_PAGES)
            break
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - 单页提取失败不应中断整体
            log.warning("第 %d 页文本提取异常（跳过）: %s", idx, exc)
            text = ""
        parts.append(text)

    full = "\n".join(parts)
    if not full.strip():
        raise OcrNoTextLayerError("PDF 未检测到可提取的文本层（疑似纯扫描件）")
    return full
