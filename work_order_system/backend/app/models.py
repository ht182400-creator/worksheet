"""Pydantic 数据模型（对应 V5.0 §25/§26）。"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class WorkOrderCreate(BaseModel):
    """创建工单请求体。"""
    display_no: str
    tenant_id: str
    doc_confidence: Optional[float] = None
    need_review: bool = False
    assignee_openid: Optional[str] = None  # 指定工单人的微信 openid（订阅消息推送目标，§新增）


class WorkOrder(BaseModel):
    """工单实体（对应 §26.2）。"""
    order_uuid: str
    display_no: str
    tenant_id: str
    state: int = 2  # 默认待分发（已通过审核）
    version: int = 1
    doc_confidence: Optional[float] = None
    need_review: bool = False
    assignee_openid: Optional[str] = None  # 指定工单人的微信 openid（§新增推送）
    created_at: datetime
    updated_at: datetime


class WorkerRegister(BaseModel):
    """工人（小程序用户）注册/更新请求体（§新增推送）。"""
    openid: str
    name: Optional[str] = None
    tenant_id: str
    subscribe_quota: int = 0  # 一次性订阅剩余授权数（小程序侧授权后上报）


class StateMachineOut(BaseModel):
    """状态机端点输出（§25.2.5）。"""
    current_state: int
    allowed_transitions: List[int]
    visible_buttons: List[str]
    version: int


class ReportRequest(BaseModel):
    """报工请求体（§25.2.2 / BR-05 / BR-22）。"""
    process_id: str
    completed_qty: int = Field(ge=0)
    operator_id: str
    client_created_at: Optional[datetime] = None
    version: int


class ReportOut(BaseModel):
    """报工响应（含合并结果与撤回窗口，§25.2.2）。"""
    report_id: str
    order_id: str
    merged_completed: int
    need_review: bool
    withdrawable_until: datetime


class QrcodeGenerateRequest(BaseModel):
    """单张二维码生成请求（BR-15 / BR-21）。"""
    order_id: str
    process_id: Optional[str] = None
    dpi: int = Field(default=300, ge=300)
    size_mm: int = Field(default=30, ge=30)


class QrcodeBatchRequest(BaseModel):
    """批量二维码生成请求（M3-02）。

    上限校验交由路由层按 QRCODE_BATCH_MAX 业务规则返回 BIZ_BATCH_OVERFLOW，
    此处不挂 Pydantic max_length，避免 Pydantic 抢先拦截导致业务错误码失效（死代码）。
    """
    order_ids: List[str]
    dpi: int = Field(default=300, ge=300)


class ConflictResolveRequest(BaseModel):
    """冲突裁决请求（BR-06 / D4 / D7，限主管）。"""
    resolve_by: str  # keep_local | keep_server | merge
    resolved_qty: Optional[int] = None
    operator_role: str  # SUPERVISOR | CLERK


class ErrorResponse(BaseModel):
    """统一错误响应体（§25.1）。"""
    code: str
    message: str
    traceId: Optional[str] = None
