"""二维码路由：生成 / 批量 / 打印确认（§25.2.6 / BR-15 / BR-21）。"""
import hashlib
import io
import traceback
from pathlib import Path

import qrcode
import barcode
from barcode.writer import ImageWriter
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import QRCODE_BATCH_MAX, API_V1_PREFIX, QRCODE_IMG_DIR, QRCODE_DEEPLINK_SCHEME
from app.db import get_db, WorkOrderORM
from app.logger import get_logger
from app.models import QrcodeGenerateRequest, QrcodeBatchRequest
from app.store import generate_qrcode, batch_qrcode, confirm_print, BusinessError

log = get_logger(__name__)
router = APIRouter()


class PrintConfirm(BaseModel):
    """打印确认请求体（BR-21：3a → 3b）。"""

    print_task_id: str


@router.post("/qrcode/generate")
def generate_qrcode_endpoint(payload: QrcodeGenerateRequest, db: Session = Depends(get_db)):
    """单张二维码生成（状态 3a_GENERATED）。"""
    try:
        task_id = generate_qrcode(db, payload)
        log.info("生成二维码 task=%s order=%s dpi=%s", task_id, payload.order_id, payload.dpi)
        return {"code": "0", "data": {"print_task_id": task_id, "state": "3a_已生成"}, "traceId": _trace()}
    except Exception as exc:  # noqa: BLE001
        log.error("生成二维码异常: %s", exc)
        return _fail("BIZ_QRCODE_FAILED", "生成失败", 500)


@router.post("/qrcode/batch")
def batch_qrcode_endpoint(payload: QrcodeBatchRequest, db: Session = Depends(get_db)):
    """批量二维码生成（≤100，202 + Location，§25.2.6）。"""
    if len(payload.order_ids) > QRCODE_BATCH_MAX:
        return _fail("BIZ_BATCH_OVERFLOW", f"批量上限 {QRCODE_BATCH_MAX}", 422)
    try:
        batch_id = batch_qrcode(db, payload)
        location = f"{API_V1_PREFIX}/qrcode/batch/tasks/{batch_id}"
        log.info("批量二维码 batch=%s 数量=%d", batch_id, len(payload.order_ids))
        # Location 必须设在返回的 JSONResponse 上，而非注入的 Response
        return JSONResponse(
            status_code=202,
            content={"code": "0", "data": {"batchId": batch_id}},
            headers={"Location": location},
        )
    except Exception as exc:  # noqa: BLE001
        log.error("批量二维码异常: %s", exc)
        return _fail("BIZ_QRCODE_FAILED", "批量生成失败", 500)


@router.post("/qrcode/print/confirm")
def confirm_print_endpoint(payload: PrintConfirm, db: Session = Depends(get_db)):
    """打印确认（3a → 3b 已打印确认）。"""
    try:
        state = confirm_print(db, payload.print_task_id)
        log.info("打印确认 task=%s", payload.print_task_id)
        return {"code": "0", "data": {"state": state}, "traceId": _trace()}
    except BusinessError as be:
        return _fail(be.code, be.message, be.http_status)
    except Exception as exc:  # noqa: BLE001
        log.error("打印确认异常: %s", exc)
        return _fail("BIZ_QRCODE_FAILED", "打印确认失败", 500)


def _trace() -> str:
    return str(uuid.uuid4())


def _fail(code: str, message: str, status: int):
    return JSONResponse(status_code=status, content={"code": code, "message": message, "traceId": _trace()})


@router.get("/qrcode/img")
def qrcode_image_endpoint(
    order_uuid: str,
    process_code: str = "",
    t: str = "qr",
    db: Session = Depends(get_db),
):
    """返回工单/工序二维码或条形码 PNG（供小程序 <image> 直接显示，零前端依赖）。

    - t=qr：内容=扫码报工深链 `{QRCODE_DEEPLINK_SCHEME}?order_uuid=&process_code=`，
      工人用小程序内 `wx.scanCode` 扫描后解析并跳转报工页；
    - t=bar：内容=工单号 display_no（Code128 条码，供扫码枪读取）。
    按下参数哈希缓存到 QRCODE_IMG_DIR，命中直接返回，避免重复绘制。
    """
    try:
        cache_dir = QRCODE_IMG_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key = hashlib.md5(f"{order_uuid}|{process_code}|{t}".encode("utf-8")).hexdigest()
        cache_file = cache_dir / f"{cache_key}.png"
        if cache_file.exists():
            return _png(cache_file.read_bytes())
        if t == "bar":
            wo = db.get(WorkOrderORM, order_uuid)
            text = wo.display_no if wo else order_uuid
            img_bytes = _gen_barcode(text)
        else:
            deep = f"{QRCODE_DEEPLINK_SCHEME}?order_uuid={order_uuid}&process_code={process_code}"
            img_bytes = _gen_qrcode(deep)
        cache_file.write_bytes(img_bytes)
        return _png(img_bytes)
    except Exception as exc:  # noqa: BLE001
        log.error("生成二维码/条形码图片异常: %s\n%s", exc, traceback.format_exc())
        return _fail("BIZ_QRCODE_FAILED", "生成图片失败", 500)


def _png(b: bytes) -> Response:
    """将 PNG 字节包装为图片响应。"""
    return Response(content=b, media_type="image/png")


def _gen_qrcode(text: str) -> bytes:
    """生成二维码 PNG 字节（纠错级 M，黑底白字）。"""
    buf = io.BytesIO()
    qr = qrcode.QRCode(box_size=8, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(buf, format="PNG")
    return buf.getvalue()


def _gen_barcode(text: str) -> bytes:
    """生成 Code128 条形码 PNG 字节（不绘文字，规避字体依赖）。"""
    buf = io.BytesIO()
    writer = ImageWriter()
    code128 = barcode.get("Code128", text, writer=writer)
    code128.write(buf, options={"write_text": False, "module_height": 12, "module_width": 0.3, "quiet_zone": 4})
    return buf.getvalue()
