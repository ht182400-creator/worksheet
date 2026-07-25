"""工人待办任务路由（§25.3 接口 14 / V4.1 过滤参数）+ 工人注册（§新增推送）。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.logger import get_logger
from app.models import WorkerRegister, WorkerUpdate
from app.store import list_workers, pending_tasks, upsert_worker, get_worker, search_workers, update_worker, delete_worker
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
    """工人（小程序用户）注册/更新：落库 openid + 订阅授权余量 + 手机号（§新增 getPhoneNumber）。

    支持两种入参：①直接传 ``openid``；②传 wx.login 的 ``code`` 由后端换 openid。
    手机号：优先用 ``phone_code`` 经微信解码；或直接传 ``phone``（操作员补录/测试）。
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
        # 解析手机号（非关键路径：解码失败仅告警并降级为不含手机号，不阻断注册）
        phone = None
        if payload.phone_code:
            try:
                phone = _wx_auth.phone_number_info(payload.phone_code)
            except _wx_auth.WxAuthError as e:
                log.warning("手机号解码失败，本次注册不含手机号: %s", e.message)
        if not phone and payload.phone:
            phone = payload.phone  # 直接提供（操作员补录/测试用）
        worker = upsert_worker(db, openid, payload.name, payload.tenant_id, payload.subscribe_quota, phone=phone)
        return {"code": "0", "data": {"openid": worker.openid, "subscribe_quota": worker.subscribe_quota, "phone": worker.phone or ""}, "traceId": _trace()}
    except Exception as exc:  # noqa: BLE001
        log.error("工人注册异常: %s", exc)
        return _fail("BIZ_WORKER_REGISTER_FAILED", "工人注册失败", 500)


@router.get("/workers")
def list_workers_endpoint(db: Session = Depends(get_db)):
    """列出所有工人（小程序姓名输入框「下拉选择」数据源，§新增）。

    返回 ``[{openid, name}]``；``name`` 为空时前端显示「未命名」。仅演示用途，
    生产应加鉴权避免泄露工人名单。
    """
    try:
        rows = list_workers(db)
        return {"code": "0", "data": rows, "traceId": _trace()}
    except Exception as exc:  # noqa: BLE001
        log.error("工人列表查询异常: %s", exc)
        return _fail("BIZ_WORKER_LIST_FAILED", "工人列表查询失败", 500)


@router.get("/workers/by-openid/{openid}")
def worker_quota_endpoint(openid: str, db: Session = Depends(get_db)):
    """查询单条工人完整信息（操作员后台工人管理面板「查」：openid/name/phone/subscribe_quota）。

    小程序同时用其判断能否继续推送 / 自阅手机。
    """
    worker = get_worker(db, openid)
    if worker is None:
        return _fail("BIZ_WORKER_NOT_FOUND", "工人不存在", 404)
    return {
        "code": "0",
        "data": {
            "openid": openid,
            "name": worker.name or "",
            "phone": worker.phone or "",
            "subscribe_quota": worker.subscribe_quota,
        },
        "traceId": _trace(),
    }


@router.get("/workers/search")
def search_workers_endpoint(q: str = Query("", description="手机号或 openid 后 N 位"), db: Session = Depends(get_db)):
    """搜索工人（操作员后台工人管理面板「找人」：按手机号模糊 + openid 后缀匹配）。"""
    try:
        log.info("工人搜索 q=%s", q)
        rows = search_workers(db, q)
        return {"code": "0", "data": rows, "traceId": _trace()}
    except Exception as exc:  # noqa: BLE001
        log.error("工人搜索异常: %s", exc)
        return _fail("BIZ_WORKER_SEARCH_FAILED", "工人搜索失败", 500)


@router.patch("/workers/{openid}")
def update_worker_endpoint(openid: str, payload: WorkerUpdate, db: Session = Depends(get_db)):
    """更新工人信息（操作员后台工人管理面板「改」：补填姓名/手机号/订阅余量）。"""
    try:
        log.info("工人更新 openid=%s name=%s quota=%s", openid, payload.name, payload.subscribe_quota)
        worker = update_worker(
            db,
            openid,
            name=payload.name,
            phone=payload.phone,
            subscribe_quota=payload.subscribe_quota,
        )
        if worker is None:
            return _fail("BIZ_WORKER_NOT_FOUND", "工人不存在", 404)
        return {
            "code": "0",
            "data": {
                "openid": worker.openid,
                "name": worker.name or "",
                "phone": worker.phone or "",
                "subscribe_quota": worker.subscribe_quota,
            },
            "traceId": _trace(),
        }
    except Exception as exc:  # noqa: BLE001
        log.error("工人更新异常: %s", exc)
        return _fail("BIZ_WORKER_UPDATE_FAILED", "工人更新失败", 500)


@router.delete("/workers/{openid}")
def delete_worker_endpoint(openid: str, db: Session = Depends(get_db)):
    """删除工人记录（操作员后台工人管理面板「删」）。"""
    try:
        log.info("工人删除 openid=%s", openid)
        worker = delete_worker(db, openid)
        if worker is None:
            return _fail("BIZ_WORKER_NOT_FOUND", "工人不存在", 404)
        return {
            "code": "0",
            "data": {"openid": worker.openid, "deleted": True},
            "traceId": _trace(),
        }
    except Exception as exc:  # noqa: BLE001
        log.error("工人删除异常: %s", exc)
        return _fail("BIZ_WORKER_DELETE_FAILED", "工人删除失败", 500)


def _trace() -> str:
    import uuid

    return str(uuid.uuid4())


def _fail(code: str, message: str, status: int):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"code": code, "message": message, "traceId": _trace()})
