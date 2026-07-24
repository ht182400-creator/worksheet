"""字段解析器单测（M1-03 鲁棒性）：覆盖 OCR 中文间插空格、换行不被吞等场景。

对应需求：Tesseract 常在相邻中文字符间插入空格（如 "工 单 号"），
导致与连写字段别名（"工单号"）匹配失败，整单置信度暴跌（线上曾出现 0.09）。
运行：pytest tests/test_field_parser.py -v
"""
import sys
import os
import logging

logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app._field_parser import (  # noqa: E402
    _normalize_ocr_text,
    parse_work_order_fields,
)


def test_normalize_removes_spaces_between_cjk() -> None:
    """中文之间的空格应被去除，使 '工 单 号' 归一成 '工单号'。"""
    raw = "工 单 号 : WO-2026-00999"
    assert _normalize_ocr_text(raw) == "工单号: WO-2026-00999"


def test_normalize_keeps_newlines() -> None:
    """归一化不得吞掉换行符，否则相邻两行会被误拼成一词。"""
    raw = "生产工单\n工单号：WO-2026-001\n客户：示例科技"
    out = _normalize_ocr_text(raw)
    assert "\n" in out, "换行符被误吞"
    assert "生产工单工单号" not in out, "两行被误拼成一词"


def test_parse_spaced_cjk_recovers_customer() -> None:
    """OCR 带空格文本：客户字段应正确提取，且整单置信度远高于 0.09。"""
    raw = (
        "生 产 工 单\n"
        "工 单 号 : WO-2026-00999\n"
        "客 户 : 示 例 科 技 有 限 公 司\n"
        "产 品 编 码 : PD-88231\n"
        "客 户 料 号 : CUST-7788\n"
        "预 计 产 量 : 500\n"
        "交 货 日 期 : 2026-08-01\n"
        "批 次 数 量 : 100\n"
        "下 单 日 期 : 2026-07-20\n"
        "计 划 日 期 : 2026-07-25\n"
    )
    res = parse_work_order_fields(raw)
    fields = {f["label"]: f for f in res["fields"]}
    assert fields["客户"]["value"] == "示例科技有限公司", fields["客户"]
    assert fields["工单号"]["value"] == "WO-2026-00999"
    assert fields["预计产量"]["value"] == "500"
    # 9/10 字段命中 -> 平均 0.81，远高于修复前的 0.09
    assert res["docConfidence"] >= 0.7, res["docConfidence"]


def test_parse_preserves_newline_separation() -> None:
    """多行 OCR 文本解析后不应因换行被吞而跨行串字段。"""
    raw = "客户：示例科技有限公司\n产品编码：PD-88231"
    res = parse_work_order_fields(raw)
    fields = {f["label"]: f for f in res["fields"]}
    assert fields["客户"]["value"] == "示例科技有限公司"
    assert "PD-88231" not in fields["客户"]["value"]


def test_parse_value_pattern_fallback_on_garbled_labels() -> None:
    """标签被 Tesseract 误识（小字号）时，按可读"值"兜底抽取，且不串味（M1-03 鲁棒性）。

    场景：工单流程卡截图，标签"产品编码/客户"被误识为近形错字（产取编吉/恭户材代），
    但工单号、产品编码、客户代码等值仍可读。验证：
      1) display_no / product_code / customer 通过值模式兜底命中（valueInferred=True）；
      2) customer 不会抢走 product_code 的 JQ011；
      3) customer_part_no 不会被工单号前缀 OD-1901050003 误吞；
      4) 密集表格含大量乱码日期时，日期兜底跳过（不填错）。
    """
    raw = (
        "工单流程卡(工单)\n"
        "产取编吉: JQ011-A-20CRMNTH008\n"      # 产品编码（标签误识）
        "恭户材代:, YAB0895 太绍钧\n"           # 客户（标签误识）
        "wO0-1991060003-1-7\n"                  # 工单号（小写 wO 前缀）
        "OD-1901050003-1-7\n"                   # 工单号第二处误读副本
        "2009-01-18 2010-01-14 2010-01-23 2018-01-24 2019-01-14 2019-01-23 2019-01-24\n"  # 大量日期
    )
    res = parse_work_order_fields(raw)
    fields = {f["label"]: f for f in res["fields"]}
    # 1) 三个关键字段经值模式兜底命中
    assert fields["工单号"]["value"] == "1991060003-1-7", fields["工单号"]
    assert fields["工单号"]["valueInferred"] is True
    assert fields["产品编码"]["value"] == "JQ011-A-20CRMNTH008", fields["产品编码"]
    assert fields["产品编码"]["valueInferred"] is True
    assert fields["客户"]["value"] == "YAB0895", fields["客户"]
    assert fields["客户"]["valueInferred"] is True
    # 2) 防串味：客户不应抢走 JQ011
    assert fields["客户"]["value"] != "JQ011", "customer 误抢 product_code 值"
    # 3) 防串味：客户料号不应吞工单号前缀
    assert fields["客户料号"]["value"] == "", "customer_part_no 误吞工单号"
    # 4) 日期兜底跳过（乱码日期过多）
    assert fields["下单日期"]["value"] == ""
    assert fields["交货日期"]["value"] == ""
    assert fields["计划日期"]["value"] == ""
    # 整单置信度 = 3*0.5/10 = 0.15，仍为低置信需人工复核
    assert res["docConfidence"] == 0.15, res["docConfidence"]
    assert res["needReview"] is True


if __name__ == "__main__":
    test_normalize_removes_spaces_between_cjk()
    test_normalize_keeps_newlines()
    test_parse_spaced_cjk_recovers_customer()
    test_parse_preserves_newline_separation()
    test_parse_value_pattern_fallback_on_garbled_labels()
    print("ALL_FIELD_PARSER_TESTS_PASS")
