"""工单字段规则化解析（内部模块，对应 M1-03/M1-11）。

从 PDF 提取文本中按"标签:值"模式抽取结构化字段，计算字段级置信度，
并依据 M1-11 置信度分级给出整单置信度与是否需人工审核/强制重录。
"""
import re
import traceback
from typing import Dict, List

from app.config import (
    OCR_AUTO_PASS_CONFIDENCE,
    OCR_FIELD_EMPTY_CONFIDENCE,
    OCR_FIELD_FOUND_CONFIDENCE,
    OCR_FIELD_NUMERIC_MISMATCH,
    OCR_FIELD_VALUE_PATTERN_CONFIDENCE,
    OCR_MANUAL_REVIEW_CONFIDENCE,
)
from app.logger import get_logger

log = get_logger(__name__)

# OCR 常见噪声：Tesseract 常在相邻中文字符间插入空格（如 "工 单 号"），
# 导致与连写的字段别名（"工单号"）匹配失败。解析前需归一化去除这类空格。
# 注意：仅匹配水平空白 [ \t]，绝不能吞掉换行符（否则会把相邻两行中文误拼成一个词）。
_CJK = r"\u4e00-\u9fff"
_SPACE_BETWEEN_CJK = re.compile(rf"([{_CJK}])[ \t]+([{_CJK}:：])")
_SPACE_AFTER_CJK_COLON = re.compile(rf"([{_CJK}:：])[ \t]+([{_CJK}])")


def _normalize_ocr_text(text: str) -> str:
    """OCR 文本归一化：去除中文字符之间、中文与冒号之间的多余空格（M1-03 鲁棒性）。

    Tesseract 常在中文间插空格使字段标签（"工单号"）与 OCR 输出（"工 单 号"）失配；
    仅去除 CJK↔CJK / CJK↔冒号 之间的水平空格，不影响英文/数字/编号（如 "WO-2026-00999"），
    也不会吞掉换行符（否则两行会被误拼成一词）。因正则非重叠替换，需迭代至稳定。
    """
    if not text:
        return text
    prev = None
    cur = text
    for _ in range(4):  # 迭代到稳定，处理 "客 户 :" 这类需多轮合并的情况
        cur = _SPACE_BETWEEN_CJK.sub(r"\1\2", cur)
        cur = _SPACE_AFTER_CJK_COLON.sub(r"\1\2", cur)
        if cur == prev:
            break
        prev = cur
    return cur

# 工单结构化字段定义（M1-03）。按"特异性"从高到低排列，避免短标签误命中。
# - aliases：中英文标签别名，正则忽略大小写
# - val_pattern：值捕获正则（默认取到行尾/逗号前）
# - numeric：期望数值字段（非数值则降级置信度）
# - code：参与 OCR 纠错库归一化的编码类字段
FIELD_SPECS: List[dict] = [
    {
        "key": "display_no", "label": "工单号",
        "aliases": ["工单号", "工单编号", "订单号", "单号", "工单No", "WO-", "Work Order"],
        "val_pattern": r"[^\n，；;]{1,40}", "numeric": False, "code": True,
    },
    {
        "key": "customer_part_no", "label": "客户料号",
        "aliases": ["客户料号", "客料号", "客户零件号", "Customer Part", "Part No"],
        "val_pattern": r"[^\n，；;]{1,40}", "numeric": False, "code": True,
    },
    {
        "key": "product_code", "label": "产品编码",
        "aliases": ["产品编码", "产品代码", "物料编码", "产品型号", "Item Code", "Material"],
        "val_pattern": r"[^\n，；;]{1,40}", "numeric": False, "code": True,
    },
    {
        "key": "plan_qty", "label": "预计产量",
        "aliases": ["预计产量", "计划产量", "生产数量", "订单数量", "Quantity", "Qty"],
        "val_pattern": r"[\d,，]{1,20}", "numeric": True, "code": False,
    },
    {
        "key": "po_no", "label": "PO号",
        "aliases": ["PO号", "采购单号", "PO No", "P.O"],
        "val_pattern": r"[^\n，；;]{1,40}", "numeric": False, "code": True,
    },
    {
        "key": "customer", "label": "客户",
        "aliases": ["客户：", "客户:", "客户名称", "Customer", "Client"],
        "val_pattern": r"[^\n，；;]{1,40}", "numeric": False, "code": False,
    },
    {
        "key": "delivery_date", "label": "交货日期",
        "aliases": ["交货日期", "交期", "Delivery", "Due Date"],
        "val_pattern": r"[^\n，；;]{1,30}", "numeric": False, "code": False,
    },
    {
        "key": "batch_qty", "label": "批次数量",
        "aliases": ["批次数量", "Batch Qty", "批量"],
        "val_pattern": r"[\d,，]{1,20}", "numeric": True, "code": False,
    },
    {
        "key": "order_date", "label": "下单日期",
        "aliases": ["下单日期", "开单日期", "Order Date"],
        "val_pattern": r"[^\n，；;]{1,30}", "numeric": False, "code": False,
    },
    {
        "key": "plan_date", "label": "计划日期",
        "aliases": ["计划日期", "计划开工", "Plan Date"],
        "val_pattern": r"[^\n，；;]{1,30}", "numeric": False, "code": False,
    },
]

# OCR 纠错库（M1-03）：常见易混字符映射，仅对编码类字段应用
_OCR_CONFUSION_MAP = str.maketrans({"Ｏ": "0", "ｏ": "0", "Ｏ": "0", "l": "1", "Ｌ": "1", "Ｉ": "1", "｜": "1"})


def _normalize_code(value: str) -> str:
    """编码类字段 OCR 纠错：全角转半角 + 易混字符归一（M1-03）。"""
    normalized = value.translate(_OCR_CONFUSION_MAP)
    # 全角数字转半角
    normalized = normalized.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return normalized


# --- 值模式兜底抽取（M1-03 鲁棒性）---
# 小字号中文标签常被 Tesseract 误识为近形错字（如 "产函编矿"≈产品编码、"弯户材"≈客户），
# 导致按标签匹配 10 个字段全 0。但字段"值"（编码/客户代码/日期/长工单号）通常可读。
# 当按标签匹配完全失败（conf==0.0）时，直接从可读值中还原字段。仅在真实工单流程卡类
# 图像上增益明显；置信度低于标签命中，提示前端该值为"推断值"需人工确认。
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_VALUE_FALLBACK_PATTERNS = {
    "display_no": r"\b(?:WO-?)?\d{8,}(?:-\d+)+",          # 1901050003-1-7 / WO-...（≥8 位连字符数字，避开 4-2-2 日期）
    "product_code": r"\b[A-Z]{2,4}\d{2,}-[A-Z0-9.]+(?:[-.][A-Z0-9.]+)+",  # JQ011-A.20CRMNTH008（OCR 常把 - 误识为 .）
    "customer": r"\b[A-Z]{2,4}\d{3,}",                    # YAB065（客户代码，≥3 位数字）
    "customer_part_no": r"\b[A-Z]{2,4}-\d{3,7}(?![\d-])",  # CUST-7788（负向预查避免截断吞掉工单号 OD-1901050003）
    "po_no": r"\bPO[ -]?[A-Z0-9][A-Z0-9-]*",              # PO12345
}

# 兜底抽取顺序：特异性/最易误抢的字段先处理并占用文本区间，避免后续字段串味。
# 例如 product_code 必须先于 customer（否则 customer 会抢走 JQ011），
# customer_part_no 必须最后（其 X-123 形态易与工单号前缀 OD-xxxx 冲突）。
_FALLBACK_PRIORITY = ["display_no", "product_code", "customer", "po_no", "customer_part_no"]
# 日期兜底仅在去重后日期数较少时启用（≤3），避免密集表格中大量乱码日期被错误分配。
_DATE_FALLBACK_MAX = 3


def _value_pattern_match(key: str, text: str, used_spans: List[tuple]) -> "object":
    """按字段的值模式在文本中查找首个不与已占用区间重叠的命中（避免重复抽取）。"""
    pat = _VALUE_FALLBACK_PATTERNS.get(key)
    if not pat:
        return None
    rx = re.compile(pat)
    for m in rx.finditer(text):
        s, e = m.span()
        if any(not (e <= us or s >= ue) for (us, ue) in used_spans):
            continue  # 与已抽取字段区间重叠，跳过
        return m
    return None


def _fill_dates_by_order(fields: List[dict], text: str) -> None:
    """日期字段兜底：收集全部 YYYY-MM-DD，按时间排序分配给空日期字段。

    约定：最早=下单日期，最晚=交货日期，中间=计划日期（符合工单业务语义）。
    仅对标签完全未匹配（conf==0.0）的日期字段生效。
    """
    date_fields = [
        f for f in fields
        if f["key"] in ("order_date", "plan_date", "delivery_date")
        and f["confidence"] == 0.0 and not f["value"]
    ]
    if not date_fields:
        return
    dates = sorted(set(_DATE_RE.findall(text)))
    # 仅当去重后日期数较少（≤_DATE_FALLBACK_MAX）时启用：密集表格常含大量乱码日期，
    # 此时按位置分配极易出错，宁可留空交由人工填写。
    if not dates or len(dates) > _DATE_FALLBACK_MAX:
        return
    used: List[str] = []

    def _take(preferred_idx: int) -> "str | None":
        if 0 <= preferred_idx < len(dates):
            d = dates[preferred_idx]
            if d not in used:
                used.append(d)
                return d
        for d in dates:
            if d not in used:
                used.append(d)
                return d
        return None

    keys = [f["key"] for f in date_fields]
    mapping: Dict[str, "str | None"] = {}
    if "order_date" in keys:
        mapping["order_date"] = _take(0)
    if "delivery_date" in keys:
        mapping["delivery_date"] = _take(-1)
    if "plan_date" in keys:
        mapping["plan_date"] = _take(len(dates) // 2)
    for f in date_fields:
        val = mapping.get(f["key"])
        if val:
            f["value"] = val
            f["confidence"] = round(OCR_FIELD_VALUE_PATTERN_CONFIDENCE, 2)
            f["valueInferred"] = True


# 每个字段的"截止符"集合：除自身外所有字段标签，用于文本字段惰性捕获时遇到
# 其它字段标签即止，避免单行文本（图片 OCR 常见）跨字段误吞（M1-03 鲁棒性）。
_STOP_ALIASES = {
    spec["key"]: "|".join(
        re.escape(a)
        for other in FIELD_SPECS
        if other["key"] != spec["key"]
        for a in other["aliases"]
    )
    for spec in FIELD_SPECS
}


def _extract_field(text: str, spec: dict) -> tuple:
    """抽取单个字段，返回 (value, confidence)。"""
    alt = "|".join(re.escape(a) for a in spec["aliases"])
    if spec.get("numeric"):
        # 数值字段：仅捕获数字/逗号，天然在首个非数字处截止
        vp = spec.get("val_pattern", r"[\d,，]{1,20}")
        rx = re.compile(rf"(?:{alt})\s*[:：]?\s*({vp})", re.IGNORECASE)
    else:
        # 文本字段：惰性捕获，遇到其它字段标签 / 换行 / 中文逗号即止
        vp = spec.get("val_pattern", r"[^\n，；;]{1,40}")
        stop = _STOP_ALIASES[spec["key"]]
        rx = re.compile(
            rf"(?:{alt})\s*[:：]?\s*?({vp}?)(?=\s*(?:{stop})|\n|，|；|$)",
            re.IGNORECASE,
        )
    match = rx.search(text)
    if not match:
        return "", 0.0
    value = match.group(1).strip().strip("：:").strip()
    if not value or re.fullmatch(r"[\s\-/]+", value):
        return "", OCR_FIELD_EMPTY_CONFIDENCE  # 命中标签但值缺失
    if spec.get("numeric") and not re.search(r"\d", value):
        return value, OCR_FIELD_NUMERIC_MISMATCH  # 期望数值却非数值
    if spec.get("code"):
        value = _normalize_code(value)
    return value, OCR_FIELD_FOUND_CONFIDENCE


def parse_work_order_fields(text: str) -> dict:
    """解析工单结构化字段，返回与接口契约一致的 result 字典。

    返回结构：
        {
          "fields": [{"key","label","value","confidence"}, ...],
          "docConfidence": float,
          "needReview": bool,   # 置信度 < 自动通过阈值
          "forceManual": bool,  # 置信度 < 强制重录阈值
          "rawTextLen": int,
        }
    """
    try:
        # OCR 文本归一化：去除中文间/中文与冒号间空格，恢复与连写别名的匹配（M1-03）
        norm_text = _normalize_ocr_text(text)
        fields: List[dict] = []
        for spec in FIELD_SPECS:
            value, conf = _extract_field(norm_text, spec)
            fields.append({
                "key": spec["key"],
                "label": spec["label"],
                "value": value,
                "confidence": round(conf, 2),
                "valueInferred": False,  # 该字段值是否由值模式兜底推断（非标签命中）
            })

        # --- 第二遍：值模式兜底抽取（M1-03 鲁棒性）---
        # 仅对"标签完全未匹配"(conf==0.0 且无值)的字段启用，避免覆盖已正确识别的字段。
        # 小字号标签被误识时，字段"值"（编码/客户代码/长工单号/日期）通常仍可读。
        # 按 _FALLBACK_PRIORITY 顺序处理：先处理特异性高/易误抢的字段并占用文本区间，
        # 后续字段用 _value_pattern_match 的区间去重避免串味（如 customer 抢 product_code）。
        used_spans: List[tuple] = []
        for f in fields:
            if f["value"]:
                idx = norm_text.find(f["value"])
                if idx >= 0:
                    used_spans.append((idx, idx + len(f["value"])))
        for key in _FALLBACK_PRIORITY:
            f = next((x for x in fields if x["key"] == key), None)
            if not f or f["confidence"] != 0.0 or f["value"]:
                continue
            m = _value_pattern_match(key, norm_text, used_spans)
            if not m:
                continue
            val = m.group(0).strip()
            if f.get("code"):
                val = _normalize_code(val)
            f["value"] = val
            f["confidence"] = round(OCR_FIELD_VALUE_PATTERN_CONFIDENCE, 2)
            f["valueInferred"] = True
            s, e = m.span()
            used_spans.append((s, e))
        # 日期字段兜底：按时间排序分配（最早=下单，最晚=交货，中间=计划），仅当日期数较少时
        _fill_dates_by_order(fields, norm_text)

        doc_confidence = round(sum(f["confidence"] for f in fields) / len(fields), 2) if fields else 0.0
        need_review = doc_confidence < OCR_AUTO_PASS_CONFIDENCE
        force_manual = doc_confidence < OCR_MANUAL_REVIEW_CONFIDENCE
        return {
            "fields": fields,
            "docConfidence": doc_confidence,
            "needReview": need_review,
            "forceManual": force_manual,
            "rawTextLen": len(text),
        }
    except Exception as exc:  # noqa: BLE001 - 解析失败不应导致任务崩溃
        log.error("工单字段解析异常: %s\n%s", exc, traceback.format_exc())
        return {
            "fields": [],
            "docConfidence": 0.0,
            "needReview": True,
            "forceManual": True,
            "rawTextLen": len(text),
        }
