"""二维码路由：生成 / 批量 / 打印确认（§25.2.6 / BR-15 / BR-21）。"""
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import QRCODE_BATCH_MAX, API_V1_PREFIX
from app.db import get_db
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
