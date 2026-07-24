"""工人待办任务路由（§25.3 接口 14 / V4.1 过滤参数）+ 工人注册（§新增推送）。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.logger import get_logger
from app.models import WorkerRegister
from app.store import pending_tasks, upsert_worker, get_worker_quota
import app._wx_auth as _wx_auth

log = get_logger(__name__)
router = APIRouter()


@router.get("/pending-tasks")
def pending_tasks_endpoint(
    operator_id: Optional[str] = Query(None),
    state: Optional[int] = Query(None),
    assignee_openid: Optional[str] = Query(None, description="按工单人 openid 过滤（微信小程序拉取我的待办）"),
    db: Session = Depends(get_db),
):
    """工人待办任务（过滤参数）。返回工序级待报工列表。

    新增 ``assignee_openid``：仅返回指派给该工人的工单下工序（§新增推送，小程序用）。
    """
    tasks = pending_tasks(db, operator_id, state, assignee_openid=assignee_openid)
    return {"code": "0", "data": tasks, "traceId": _trace()}


@router.post("/workers")
def register_worker_endpoint(payload: WorkerRegister, db: Session = Depends(get_db)):
    """工人（小程序用户）注册/更新：落库 openid + 订阅授权余量（§新增推送）。

    支持两种入参：①直接传 ``openid``；②传 wx.login 的 ``code`` 由后端换 openid。
    """
    try:
        openid = payload.openid
        if not openid and payload.code:
            try:
                openid = _wx_auth.code2session(payload.code).get("openid")
            except _wx_auth.WxAuthError as e:
                return _fail(e.code, e.message, 502)
        if not openid:
            return _fail("BIZ_WORKER_OPENID_REQUIRED", "openid 与 code 至少提供一个", 400)
        worker = upsert_worker(db, openid, payload.name, payload.tenant_id, payload.subscribe_quota)
        return {"code": "0", "data": {"openid": worker.openid, "subscribe_quota": worker.subscribe_quota}, "traceId": _trace()}
    except Exception as exc:  # noqa: BLE001
        log.error("工人注册异常: %s", exc)
        return _fail("BIZ_WORKER_REGISTER_FAILED", "工人注册失败", 500)


@router.get("/workers/by-openid/{openid}")
def worker_quota_endpoint(openid: str, db: Session = Depends(get_db)):
    """查询工人订阅授权余量（小程序判断能否继续推送）。"""
    quota = get_worker_quota(db, openid)
    if quota is None:
        return _fail("BIZ_WORKER_NOT_FOUND", "工人不存在", 404)
    return {"code": "0", "data": {"openid": openid, "subscribe_quota": quota}, "traceId": _trace()}


def _trace() -> str:
    import uuid

    return str(uuid.uuid4())


def _fail(code: str, message: str, status: int):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"code": code, "message": message, "traceId": _trace()})
