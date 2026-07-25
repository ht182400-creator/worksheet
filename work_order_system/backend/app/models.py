"""Pydantic 数据模型（对应 V5.0 §25/§26）。"""
import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# 国内手机号规则：11 位且以 1 开头（工人管理面板「改」手机号校验，§工人管理面板）
PHONE_RE = re.compile(r"^1\d{10}$")


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
    """工人（小程序用户）注册/更新请求体（§新增推送 + getPhoneNumber 手机号）。"""
    openid: Optional[str] = None  # 直接提供 openid；或与 code 二选一
    code: Optional[str] = None    # wx.login 临时凭证，由后端换 openid
    name: Optional[str] = None
    tenant_id: str
    phone_code: Optional[str] = None  # 微信 getPhoneNumber 的 code，后端解密真实手机号（§新增）
    phone: Optional[str] = None       # 直接提供手机号（操作员补录/测试用；生产优先用 phone_code 解密）
    subscribe_quota: int = 0  # 一次性订阅剩余授权数（小程序侧授权后上报）


class WorkerUpdate(BaseModel):
    """工人信息更新请求体（操作员后台工人管理面板补填姓名，§工人管理面板）。

    ``name``/``phone``/``subscribe_quota`` 为 Optional：传 ``None``（缺省）表示不修改该字段；
    传显式值（含空串）则覆盖，便于操作员手动清空姓名。
    """
    name: Optional[str] = None   # 补填/修正姓名（可显式置空清空）
    phone: Optional[str] = None  # 可选补填手机号（一般无需，小程序已授权）
    subscribe_quota: Optional[int] = None  # 可选修正订阅授权余量（操作员后台调整）

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: Optional[str]) -> Optional[str]:
        """手机号校验：None 或空串（清空）放行；非空必须 11 位且以 1 开头（§工人管理面板）。"""
        if v is None or v == "":
            return v
        if not PHONE_RE.match(v):
            raise ValueError("手机号必须是 11 位数字且以 1 开头")
        return v


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
