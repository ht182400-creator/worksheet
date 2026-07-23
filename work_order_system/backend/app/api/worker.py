"""工人待办任务路由（§25.3 接口 14 / V4.1 过滤参数）。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.logger import get_logger
from app.store import pending_tasks

log = get_logger(__name__)
router = APIRouter()


@router.get("/pending-tasks")
def pending_tasks_endpoint(
    operator_id: Optional[str] = Query(None),
    state: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """工人待办任务（过滤参数）。返回工序级待报工列表。"""
    tasks = pending_tasks(db, operator_id, state)
    return {"code": "0", "data": tasks, "traceId": _trace()}


def _trace() -> str:
    import uuid

    return str(uuid.uuid4())
