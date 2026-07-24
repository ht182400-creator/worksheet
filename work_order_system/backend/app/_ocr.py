"""后端原生 Tesseract OCR（方案 A：识别率优于浏览器 tesseract.js，M1-01/M1-09）。

与浏览器 tesseract.js（方案 B）相比，方案 A 的识别率提升来自三点：
  1. 原生引擎：服务端直接调用系统 Tesseract-OCR（LSTM），无 WASM 性能墙，
     可跑高分辨率输入与完整预处理；
  2. 图像预处理：放大 → 灰度 → 自动对比度 → 二值化，显著提升中文小字/截图召回；
  3. 显式 PSM + 最新中文语言包，避免浏览器 CDN 语言包版本受限问题。

依赖（需在系统层安装/装包）：
  - 系统原生 Tesseract-OCR 二进制（含 chi_sim 中文包），由 pytesseract 调用；
  - Python 包：pytesseract（图片 OCR）、Pillow（图像处理）、PyMuPDF（PDF 转图）。

为便于"模块导入测试"在不具备上述依赖的环境也能通过，所有重依赖均延迟导入到函数内。
"""
import io
import re
import traceback
from typing import List

from app.config import (
    OCR_BINARIZE_METHOD,
    OCR_BINARIZE_THRESHOLD,
    OCR_DESPECKLE_AREA,
    OCR_DPI,
    OCR_ENGINE_SERVER,
    OCR_GRID_LINE_RATIO,
    OCR_LANG,
    OCR_MAX_PAGES,
    OCR_PSM,
    OCR_PSM_CANDIDATES,
    OCR_REMOVE_GRID_LINES,
    OCR_SHARPEN,
    OCR_UPSCALE,
    OCR_UPSCALE_MIN_WIDTH,
    OCR_STAGE_RENDER_OCR,
    OCR_PCT_RENDER_OCR_MIN,
    OCR_PCT_RENDER_OCR_MAX,
    TESSERACT_CMD_CANDIDATES,
)
from app.logger import get_logger

log = get_logger(__name__)

# 图片类扩展名（与 store 层文件类型判断保持一致）
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
PDF_SUFFIXES = (".pdf",)

# 已解析的 tesseract 二进制路径缓存（首次成功解析后复用，避免重复探测）
_TESS_CMD_CACHE = None


def _preprocess(img) -> "object":
    """图像预处理：放大 → 灰度 → 自动对比度 → 二值化（白底黑字）。

    工单截图/扫描件常见小字、低对比度、灰底，预处理是识别率提升的关键。
    返回 PIL.Image（灰度二值化后的单通道图像）。
    """
    width = img.width
    # 小图放大：输入像素密度越高，tesseract 中文召回越好（工单流程卡标签字号小，2x 仍易误识）
    scale = OCR_UPSCALE
    if width < OCR_UPSCALE_MIN_WIDTH:
        scale = max(scale, OCR_UPSCALE_MIN_WIDTH / width)
    if scale > 1:
        from PIL import Image as _PILImage

        img = img.resize((int(width * scale), int(img.height * scale)), _PILImage.LANCZOS)
    img = img.convert("L")  # 转灰度
    from PIL import ImageOps

    img = ImageOps.autocontrast(img)  # 自动对比度拉伸，拉开文字/背景
    # 工单表单常见网格线/边框，会被 Tesseract 误识为字符（M1-01）；先行去除
    if OCR_REMOVE_GRID_LINES:
        img = _remove_grid_lines(img)
    # 二值化前轻度锐化：增强细小笔画，提升小字号中文召回（依赖缺失则降级跳过）
    if OCR_SHARPEN:
        img = _sharpen_gray(img)
    # 二值化：otsu 按图像自适应阈值（适配不同光照/截图），fixed 用固定阈值
    img = _binarize(img)
    return img


def _sharpen_gray(img) -> "object":
    """灰度图轻度锐化（3x3 浮雕核），增强细小笔画以提升小字号中文召回。

    cv2 对 uint8 输出自动裁剪到 [0,255]；依赖缺失时原样返回（降级不中断）。
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except Exception as exc:  # numpy/cv2 缺失时降级为不锐化
        log.warning("锐化依赖缺失，跳过（%s）", exc)
        return img
    try:
        arr = np.array(img, dtype=np.float32)
        kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]], dtype=np.float32)
        out = cv2.filter2D(arr, -1, kernel)
        return Image.fromarray(out.clip(0, 255).astype(np.uint8), mode="L")
    except Exception as exc:  # noqa: BLE001
        log.warning("锐化异常，降级为不锐化: %s", exc)
        return img


def _binarize(img) -> "object":
    """灰度图转二值（黑字白底）。优先 Otsu 自动阈值，依赖缺失或 fixed 模式用固定阈值。"""
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except Exception as exc:  # numpy/cv2 缺失时降级为 fixed 阈值
        log.warning("二值化依赖缺失，用固定阈值（%s）", exc)
        return img.point(lambda p: 0 if p < OCR_BINARIZE_THRESHOLD else 255)
    try:
        arr = np.array(img)
        if OCR_BINARIZE_METHOD == "otsu":
            _, thr = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            arr = 255 - thr  # 反相：黑字白底
        else:
            arr = np.where(arr < OCR_BINARIZE_THRESHOLD, 0, 255).astype(np.uint8)
        return Image.fromarray(arr, mode="L")
    except Exception as exc:  # noqa: BLE001
        log.warning("二值化异常，降级为固定阈值: %s", exc)
        return img.point(lambda p: 0 if p < OCR_BINARIZE_THRESHOLD else 255)


def _remove_grid_lines(img) -> "object":
    """去除工单表格的网格线/边框，避免 Tesseract 把线条误识为字符（M1-01）。

    用 OpenCV 形态学开运算分别检测长水平/垂直线，再掩膜涂白；并对线掩膜做连通域
    面积过滤去除小噪点，避免误删细笔画文字。依赖缺失或异常时原样返回（降级不中断）。
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except Exception as exc:  # numpy/cv2 缺失时降级为不做网格去除
        log.warning("网格线去除依赖缺失，跳过（%s）", exc)
        return img
    try:
        arr = np.array(img)
        h, w = arr.shape
        # Otsu 二值化（黑=文字/线，白=背景），用于形态学检测
        _, binary = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        # 水平长线：结构宽度覆盖画面大部分
        h_len = max(1, int(w * OCR_GRID_LINE_RATIO))
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1))
        h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel, iterations=1)
        # 垂直线：结构高度覆盖画面大部分
        v_len = max(1, int(h * OCR_GRID_LINE_RATIO))
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len))
        v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel, iterations=1)
        lines = cv2.bitwise_or(h_lines, v_lines)
        # 连通域面积过滤：仅保留较大的线，去除小噪点（防误删细笔画）
        num, labels, stats, _ = cv2.connectedComponentsWithStats(lines, 8)
        mask = np.zeros_like(lines)
        for i in range(1, num):
            if stats[i, cv2.CC_STAT_AREA] >= OCR_DESPECKLE_AREA:
                mask[labels == i] = 255
        # 在灰图上把线区域涂白（当作背景）
        out = arr.copy()
        out[mask > 0] = 255
        return Image.fromarray(out, mode="L")
    except Exception as exc:  # noqa: BLE001 - 任何异常都降级，保证 OCR 主流程不中断
        log.warning("网格线去除异常，降级为不去除: %s", exc)
        return img


# --- 自适应多 PSM 识别（M1-01）：按质量分择优，提升表单/表格类图片识别率 ---
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")  # 基础汉字区间（覆盖常用工单字段）
# 结构化值模式：标签可能被误识，但字段"值"（日期/编码/长数字）通常可读；
# 优先选取含这些结构化信息的读，配合字段解析器的值模式兜底抽取（M1-03）。
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CODE_RE = re.compile(r"[A-Z]{2,4}\d{2,}(?:-[A-Z0-9]+)+")
_LONGNUM_RE = re.compile(r"\d{8,}")
_FIELD_KEYWORDS = None  # 懒加载：来自 _field_parser.FIELD_SPECS 的中文标签


def _field_keywords() -> List[str]:
    """懒加载字段标签关键词（来自 _field_parser.FIELD_SPECS），用于质量打分。"""
    global _FIELD_KEYWORDS
    if _FIELD_KEYWORDS is None:
        try:
            from app._field_parser import FIELD_SPECS

            kws = set()
            for spec in FIELD_SPECS:
                for alias in spec["aliases"]:
                    if re.search(r"[\u4e00-\u9fff]", alias):
                        kws.add(alias)
            _FIELD_KEYWORDS = list(kws)
        except Exception as exc:  # noqa: BLE001 - 取不到关键词则不计关键词分
            log.warning("加载字段关键词失败，质量分仅按中文占比: %s", exc)
            _FIELD_KEYWORDS = []
    return _FIELD_KEYWORDS


def _ocr_quality_score(text: str) -> int:
    """OCR 结果质量分：中文越多、命中的字段标签越多、含结构化值（日期/编码/长数字）越多分越高。

    标签小字号易被误识导致 kws 偏低，故额外奖励可读的结构化值，引导选出含真实字段值的读。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    kws = sum(1 for kw in _field_keywords() if kw in text)
    structured = (
        len(_DATE_RE.findall(text))
        + len(_CODE_RE.findall(text))
        + len(_LONGNUM_RE.findall(text))
    )
    return cjk + 10 * kws + 5 * structured


def _ocr_pil_multi(img, lang: str = OCR_LANG) -> str:
    """自适应多 PSM 识别：遍历候选 PSM，按质量分择优返回最佳文本（M1-01）。

    表单/表格截图用单一 PSM（如 6）常整页乱码；多 PSM 择优能显著提升召回。
    """
    best_text, best_score = "", -1
    for psm in OCR_PSM_CANDIDATES:
        try:
            text = _ocr_pil(img, psm=psm)
        except Exception as exc:  # noqa: BLE001 - 单个 PSM 失败不影响其他候选
            log.warning("PSM=%d OCR 失败，跳过: %s", psm, exc)
            continue
        score = _ocr_quality_score(text)
        if score > best_score:
            best_score, best_text = score, text
    log.debug("自适应 PSM 择优 best_score=%d best_len=%d", best_score, len(best_text))
    return best_text


def _resolve_tesseract_cmd() -> str:
    """定位系统原生 Tesseract 二进制路径（不在 PATH 时也能找到）。

    优先级：环境变量 TESSERACT_CMD > config 候选路径 > PATH 查找。
    返回可用二进制路径；全部失败抛出 RuntimeError，由上层给出安装指引。
    """
    import os
    import shutil

    global _TESS_CMD_CACHE
    if _TESS_CMD_CACHE and os.path.exists(_TESS_CMD_CACHE):
        return _TESS_CMD_CACHE
    env = os.environ.get("TESSERACT_CMD")
    if env and os.path.exists(env):
        _TESS_CMD_CACHE = env
        return env
    for cand in TESSERACT_CMD_CANDIDATES:
        if os.path.exists(cand):
            _TESS_CMD_CACHE = cand
            return cand
    found = shutil.which("tesseract")
    if found:
        _TESS_CMD_CACHE = found
        return found
    raise RuntimeError(
        "未找到原生 Tesseract-OCR 二进制（方案 A 环境依赖）：请安装 Tesseract-OCR 并加入 PATH，"
        "或用环境变量 TESSERACT_CMD 指向 tesseract.exe；详见 API 契约文档 §16"
    )


def _ocr_pil(img, lang: str = OCR_LANG, psm: int = OCR_PSM) -> str:
    """对单张 PIL 图像做 OCR，返回识别文本（内部工具）。"""
    import pytesseract

    # 让 pytesseract 找到不在 PATH 的原生二进制（方案 A 本机实测装在 D 盘）
    try:
        pytesseract.pytesseract.tesseract_cmd = _resolve_tesseract_cmd()
    except RuntimeError as exc:
        log.error("Tesseract 二进制定位失败: %s", exc)
        raise
    config = f"--psm {psm} --oem 1"  # oem=1 强制 LSTM 神经网络模型
    try:
        return pytesseract.image_to_string(img, lang=lang, config=config) or ""
    except Exception as exc:  # noqa: BLE001 - 未安装原生 tesseract 时给出明确安装指引
        msg = str(exc)
        if "tesseract" in msg.lower():
            msg = ("未安装/未找到原生 Tesseract-OCR 二进制（方案 A 环境依赖）：请先安装 "
                   "Tesseract-OCR 并加入 PATH，且安装中文包 chi_sim；详见 API 契约文档 §16")
        log.error("Tesseract OCR 调用失败: %s\n%s", msg, traceback.format_exc())
        raise RuntimeError(msg) from exc


def ocr_image_bytes(data: bytes, on_progress=None) -> str:
    """对图片字节（微信截图/扫描件）做 OCR，返回识别文本。

    包含预处理，识别率优于浏览器端直接识别。on_progress 用于上报进度。
    """
    from PIL import Image

    try:
        if on_progress:
            on_progress(OCR_STAGE_RENDER_OCR, OCR_PCT_RENDER_OCR_MIN, "正在识别图片…")
        img = Image.open(io.BytesIO(data))
        img = _preprocess(img)
        text = _ocr_pil_multi(img)
        if on_progress:
            on_progress(OCR_STAGE_RENDER_OCR, OCR_PCT_RENDER_OCR_MAX, "图片识别完成")
        log.info("图片 OCR 完成 len=%d", len(text))
        return text
    except Exception as exc:  # noqa: BLE001 - OCR 失败需抛出让上层降级
        log.error("图片 OCR 异常: %s\n%s", exc, __import__("traceback").format_exc())
        raise


def ocr_pdf_bytes(data: bytes, on_progress=None) -> str:
    """PDF 逐页渲染为图片（PyMuPDF，无需外部 poppler）后 OCR，返回拼接文本。

    用于纯扫描件（无文本层）场景，解决 M1-09 无文本层无法解析的问题。
    on_progress 在每页渲染+OCR 后回调，上报逐页进度（真实进度条数据源）。
    """
    import fitz  # PyMuPDF
    from PIL import Image

    try:
        doc = fitz.open(stream=data, filetype="pdf")
        total = min(len(doc), OCR_MAX_PAGES)
        pages: List[str] = []
        for i, page in enumerate(doc):
            if i >= OCR_MAX_PAGES:
                log.warning("PDF 超出最大页数 %d，剩余页跳过", OCR_MAX_PAGES)
                break
            if on_progress:
                # 渲染+OCR 阶段进度随页数线性推进（MIN→MAX 区间）
                _pct = int(OCR_PCT_RENDER_OCR_MIN
                           + (i / total) * (OCR_PCT_RENDER_OCR_MAX - OCR_PCT_RENDER_OCR_MIN)) \
                    if total > 0 else OCR_PCT_RENDER_OCR_MIN
                on_progress(OCR_STAGE_RENDER_OCR, _pct, f"正在渲染并识别第 {i + 1}/{total} 页")
            pix = page.get_pixmap(dpi=OCR_DPI)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            img = _preprocess(img)
            pages.append(_ocr_pil_multi(img))
            if on_progress:
                _pct = int(OCR_PCT_RENDER_OCR_MIN
                           + ((i + 1) / total) * (OCR_PCT_RENDER_OCR_MAX - OCR_PCT_RENDER_OCR_MIN)) \
                    if total > 0 else OCR_PCT_RENDER_OCR_MAX
                on_progress(OCR_STAGE_RENDER_OCR, _pct, f"已识别第 {i + 1}/{total} 页")
        doc.close()
        text = "\n".join(p for p in pages if p)
        log.info("PDF OCR 完成 pages=%d len=%d", len(pages), len(text))
        return text
    except Exception as exc:  # noqa: BLE001
        log.error("PDF OCR 异常: %s\n%s", exc, __import__("traceback").format_exc())
        raise


def ocr_bytes(data: bytes, suffix: str, on_progress=None) -> str:
    """按文件类型分流 OCR：图片直接识别，PDF 渲染后识别。返回识别文本。"""
    s = (suffix or "").lower()
    if s in PDF_SUFFIXES:
        return ocr_pdf_bytes(data, on_progress=on_progress)
    if s in IMAGE_SUFFIXES:
        return ocr_image_bytes(data, on_progress=on_progress)
    # 未知类型兜底按图片尝试
    log.warning("未知扩展名 %s，按图片 OCR 兜底", suffix)
    return ocr_image_bytes(data, on_progress=on_progress)


def engine_name() -> str:
    """返回当前 OCR 引擎标识（供接口回传前端展示）。"""
    return OCR_ENGINE_SERVER
