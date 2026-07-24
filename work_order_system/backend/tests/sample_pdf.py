"""测试样例工单 PDF 生成（含中文，使用 reportlab 内置 CID 字体）。

仅在测试中使用，不进生产依赖。生成的 PDF 带文本层，供 _pdf_extract /
_field_parser 做真实解析断言（M1-03 / TC-13 / TC-14）。
"""
import io

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas


def build_sample_wo_pdf() -> bytes:
    """生成一张含全部 M1-03 字段的电子工单 PDF（文本层可提取）。"""
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setFont("STSong-Light", 12)
    lines = [
        "工单号：WO-2026-00123",
        "客户料号：CUST-PART-7788",
        "产品编码：PROD-ABC-001",
        "预计产量：1,200",
        "PO号：PO-2026-55021",
        "客户：示例科技有限公司",
        "交货日期：2026-08-15",
        "批次数量：300",
        "下单日期：2026-07-10",
        "计划日期：2026-07-20",
    ]
    y = 800
    for line in lines:
        c.drawString(80, y, line)
        y -= 22
    c.showPage()
    c.save()
    return buf.getvalue()
