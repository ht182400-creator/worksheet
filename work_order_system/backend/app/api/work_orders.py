"""工单路由：创建 / 获取 / 状态机端点 / 状态变更（§25.2.2/4/5）。"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import STATE_ILLEGAL_CODE, VERSION_CONFLICT_CODE, ORDER_NOT_FOUND_CODE
from app.db import get_db
from app.logger import get_logger
from app.models import WorkOrderCreate, StateMachineOut
from app.state_machine import (
    is_transition_allowed,
    get_allowed_transitions,
    get_visible_buttons,
)
from app.store import create_work_order, get_work_order, apply_status_change, BusinessError

log = get_logger(__name__)
router = APIRouter()


class StatusPatch(BaseModel):
    """PATCH /status 请求体（§25.2.4）。"""

    target_state: int
    version: int
    reason: Optional[str] = None


@router.post("/work-orders")
def create_work_order_endpoint(payload: WorkOrderCreate, db: Session = Depends(get_db)):
    """创建工单（§25.2.2，UUID v7 + tenant_id）。"""
    try:
        wo = create_work_order(db, payload)
        log.info("创建工单 %s tenant=%s", wo.order_uuid, wo.tenant_id)
        return {"code": "0", "data": wo.model_dump(), "traceId": _new_trace()}
    except Exception as exc:  # noqa: BLE001
        log.error("创建工单异常: %s", exc)
        return _fail("BIZ_CREATE_FAILED", "创建工单失败", 500)


@router.get("/work-orders/{order_id}")
def get_work_order_endpoint(order_id: str, db: Session = Depends(get_db)):
    """获取工单（§25 接口 4）。"""
    wo = get_work_order(db, order_id)
    if wo is None:
        return _fail(ORDER_NOT_FOUND_CODE, "工单不存在", 404)
    return {"code": "0", "data": wo.model_dump(), "traceId": _new_trace()}


@router.get("/work-orders/{order_id}/state-machine")
def state_machine_endpoint(order_id: str, db: Session = Depends(get_db)):
    """状态机端点（§25.2.5，前端按钮唯一数据源）。"""
    wo = get_work_order(db, order_id)
    if wo is None:
        return _fail(ORDER_NOT_FOUND_CODE, "工单不存在", 404)
    data = StateMachineOut(
        current_state=wo.state,
        allowed_transitions=get_allowed_transitions(wo.state),
        visible_buttons=get_visible_buttons(wo.state),
        version=wo.version,
    )
    return {"code": "0", "data": data.model_dump(), "traceId": _new_trace()}


@router.patch("/work-orders/{order_id}/status")
def patch_status_endpoint(order_id: str, body: StatusPatch, db: Session = Depends(get_db)):
    """状态变更（§25.2.4，校验 allowedTransitions + 乐观锁 version）。"""
    wo = get_work_order(db, order_id)
    if wo is None:
        return _fail(ORDER_NOT_FOUND_CODE, "工单不存在", 404)
    if not is_transition_allowed(wo.state, body.target_state):
        log.warning("非法状态跳转 %s->%s order=%s", wo.state, body.target_state, order_id)
        return _fail(STATE_ILLEGAL_CODE, "非法状态跳转（不在 allowedTransitions）", 409)
    old_state = wo.state
    try:
        updated = apply_status_change(db, order_id, body.target_state, body.version)
    except Exception as exc:  # noqa: BLE001
        log.error("状态变更异常: %s", exc)
        return _fail("BIZ_PATCH_FAILED", "状态变更失败", 500)
    if updated is None:
        return _fail(VERSION_CONFLICT_CODE, "乐观锁失败，请拉取最新数据重试", 409)
    log.info("工单 %s 状态 %s->%s", order_id, old_state, body.target_state)
    return {"code": "0", "data": {"current_state": updated.state, "version": updated.version}, "traceId": _new_trace()}


def _new_trace() -> str:
    import uuid

    return str(uuid.uuid4())


def _fail(code: str, message: str, status: int):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"code": code, "message": message, "traceId": _new_trace()})
