"""冲突裁决路由（§25.2.7 / BR-06 / D4 / D7，限主管）。"""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import PERMISSION_DENY_CODE
from app.db import get_db
from app.logger import get_logger
from app.models import ConflictResolveRequest
from app.store import resolve_conflict, BusinessError

log = get_logger(__name__)
router = APIRouter()


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict_endpoint(conflict_id: str, payload: ConflictResolveRequest, db: Session = Depends(get_db)):
    """冲突裁决（限主管角色，工人无权限返回 403，D7）。"""
    try:
        result = resolve_conflict(db, conflict_id, payload)
        log.info("冲突裁决 %s strategy=%s", conflict_id, result["strategy"])
        return {"code": "0", "data": result, "traceId": _trace()}
    except BusinessError as be:
        return _fail(be.code, be.message, be.http_status)
    except Exception as exc:  # noqa: BLE001
        log.error("冲突裁决异常: %s", exc)
        return _fail("BIZ_CONFLICT_FAILED", "裁决失败", 500)


def _trace() -> str:
    return str(uuid.uuid4())


def _fail(code: str, message: str, status: int):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"code": code, "message": message, "traceId": _trace()})
