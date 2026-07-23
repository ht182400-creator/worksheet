"""报工路由：提交 + 撤回（§25.2.2 / §25.2.3 / M5-12 / BR-05 / BR-22）。"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.logger import get_logger
from app.models import ReportRequest
from app.store import submit_report, withdraw_report, BusinessError

log = get_logger(__name__)
router = APIRouter()


class WithdrawBody(BaseModel):
    """DELETE /reports/{id} 请求体（§25.2.3）。"""

    operator_id: str


@router.post("/work-orders/{order_id}/reports")
def submit_report_endpoint(order_id: str, payload: ReportRequest, db: Session = Depends(get_db)):
    """报工提交：超报拦截 + 在线自动合并 + 撤回窗口（§25.2.2）。"""
    try:
        out = submit_report(db, order_id, payload)
        log.info("报工合并：工单 %s 工序 %s 累计 %d", order_id, payload.process_id, out.merged_completed)
        return {"code": "0", "data": out.model_dump(), "traceId": _trace()}
    except BusinessError as be:
        return _fail(be.code, be.message, be.http_status)
    except Exception as exc:  # noqa: BLE001
        log.error("报工异常: %s", exc)
        return _fail("BIZ_REPORT_FAILED", "报工失败", 500)


@router.delete("/reports/{report_id}")
def withdraw_report_endpoint(report_id: str, body: WithdrawBody, db: Session = Depends(get_db)):
    """报工撤回（M5-12 / §4.9.2 撤回窗口）。"""
    try:
        status = withdraw_report(db, report_id, body.operator_id)
        log.info("报工撤回 %s by %s", report_id, body.operator_id)
        return {"code": "0", "data": {"status": status}, "traceId": _trace()}
    except BusinessError as be:
        return _fail(be.code, be.message, be.http_status)
    except Exception as exc:  # noqa: BLE001
        log.error("报工撤回异常: %s", exc)
        return _fail("BIZ_WITHDRAW_FAILED", "撤回失败", 500)


def _trace() -> str:
    import uuid

    return str(uuid.uuid4())


def _fail(code: str, message: str, status: int):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"code": code, "message": message, "traceId": _trace()})
