"""文件 / OCR 路由：上传（异步 202）+ 任务轮询（§25.2.1 / BR-17）。"""
import traceback
import uuid

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session

from app._field_parser import parse_work_order_fields
from app.db import get_db
from app.logger import get_logger
from app.store import create_ocr_task, get_ocr_task, BusinessError

log = get_logger(__name__)
router = APIRouter()


@router.post("/files/upload")
async def upload_file(file: UploadFile = File(...), template_id: str = None, db: Session = Depends(get_db)):
    """OCR 文件上传（异步入队，返回 taskId，BR-17）。"""
    try:
        file_bytes = await file.read()
        if not file_bytes:
            return _fail("OCR_EMPTY_FILE", "上传文件为空", 400)
        task_id = create_ocr_task(db, file.filename or "upload.pdf", file_bytes)
        log.info("文件上传 task=%s name=%s size=%d", task_id, file.filename, len(file_bytes))
        return {
            "code": "0",
            "data": {
                "taskId": task_id,
                "status": "QUEUED",
                "pollUrl": f"/api/v1/ocr/tasks/{task_id}",
            },
            "traceId": _trace(),
        }
    except Exception as exc:  # noqa: BLE001
        log.error("文件上传异常: %s", exc)
        return _fail("OCR_TASK_FAILED", "上传失败", 500)


@router.get("/ocr/tasks/{task_id}")
def get_ocr_task_endpoint(task_id: str, db: Session = Depends(get_db)):
    """轮询 OCR 任务终态（演示返回 DONE 样例，含字段置信度，BR-20）。"""
    try:
        result = get_ocr_task(db, task_id)
        log.debug("轮询 OCR task=%s", task_id)
        return {"code": "0", "data": result, "traceId": _trace()}
    except BusinessError as be:
        return _fail(be.code, be.message, be.http_status)
    except Exception as exc:  # noqa: BLE001
        log.error("OCR 轮询异常: %s", exc)
        return _fail("OCR_TASK_FAILED", "轮询失败", 500)


def _trace() -> str:
    return str(uuid.uuid4())


@router.post("/ocr/parse-text")
def parse_text_endpoint(payload: dict, db: Session = Depends(get_db)):
    """外部已识别原文 → 字段解析（可选接口，M1-01/M1-03）。

    主识别路径是 `/files/upload`（后端原生 Tesseract OCR，方案 A）：图片/PDF 上传后
    由后端统一识别并解析。本接口用于"调用方已自行完成 OCR、仅需要字段解析回填"的场景，
    复用与 PDF 文本层相同的 `_field_parser`，保证字段语义一致。
    """
    try:
        text = (payload or {}).get("text", "")
        if not text or not text.strip():
            return _fail("OCR_TEXT_EMPTY", "识别文本为空", 400)
        parsed = parse_work_order_fields(text)
        return {
            "code": "0",
            "data": {
                "rawText": text,
                "fields": parsed["fields"],
                "docConfidence": parsed["docConfidence"],
                "needReview": parsed["needReview"],
                "forceManual": parsed["forceManual"],
                "engine": "external-text",
            },
            "traceId": _trace(),
        }
    except BusinessError as be:
        return _fail(be.code, be.message, be.http_status)
    except Exception as exc:  # noqa: BLE001
        log.error("文本字段解析异常: %s\n%s", exc, traceback.format_exc())
        return _fail("OCR_PARSE_ERROR", "字段解析失败", 500)


def _fail(code: str, message: str, status: int):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"code": code, "message": message, "traceId": _trace()})
