"""文件 / OCR 路由：上传（异步 202）+ 任务轮询（§25.2.1 / BR-17）。"""
import uuid

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session

from app.db import get_db
from app.logger import get_logger
from app.store import create_ocr_task, get_ocr_task, BusinessError

log = get_logger(__name__)
router = APIRouter()


@router.post("/files/upload")
def upload_file(file: UploadFile = File(...), template_id: str = None, db: Session = Depends(get_db)):
    """OCR 文件上传（异步入队，返回 taskId，BR-17）。"""
    try:
        task_id = create_ocr_task(db, file.filename)
        log.info("文件上传 task=%s name=%s", task_id, file.filename)
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


def _fail(code: str, message: str, status: int):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"code": code, "message": message, "traceId": _trace()})
