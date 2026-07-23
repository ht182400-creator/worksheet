"""状态机权威定义（BR-18 / §4.9.1 / 落实 D3：禁止非法回退）。

本模块是 `PATCH /status` 校验与 `state-machine` 接口的唯一数据源。
"""
from typing import Dict, List

# 主状态枚举（work_orders.state，0-6；3a/3b 子态见 qrcode_print_tasks 表）
STATE_PENDING_RECOGNIZE = 0   # 待识别
STATE_PENDING_REVIEW = 1      # 待审核
STATE_PENDING_DISTRIBUTE = 2  # 待分发
STATE_DISTRIBUTED = 3         # 已分发（含 3a 生成 / 3b 打印确认）
STATE_PRODUCING = 4           # 生产中
STATE_COMPLETED = 5           # 已完成
STATE_CLOSED = 6              # 已关闭（终态，不可变）

# 合法跳转矩阵（唯一权威数据源，对应 §4.9.1 转移表）
STATE_TRANSITIONS: Dict[int, List[int]] = {
    STATE_PENDING_RECOGNIZE: [STATE_PENDING_REVIEW],
    STATE_PENDING_REVIEW: [STATE_PENDING_RECOGNIZE, STATE_PENDING_DISTRIBUTE],
    STATE_PENDING_DISTRIBUTE: [STATE_PENDING_REVIEW, STATE_DISTRIBUTED],
    STATE_DISTRIBUTED: [STATE_PRODUCING],
    STATE_PRODUCING: [STATE_COMPLETED, STATE_CLOSED],
    STATE_COMPLETED: [STATE_CLOSED],
    STATE_CLOSED: [],  # 终态不可变，关闭后纠错走红冲工单（§4.9.1）
}

# 前端可见按钮（由 state-machine 接口驱动，禁止硬编码，见 §4.3/§27.6）
STATE_VISIBLE_BUTTONS: Dict[int, List[str]] = {
    STATE_PENDING_RECOGNIZE: ["OCR_RETRY"],
    STATE_PENDING_REVIEW: ["APPROVE", "REJECT"],
    STATE_PENDING_DISTRIBUTE: ["BACK_REVIEW", "GEN_QRCODE"],
    STATE_DISTRIBUTED: ["CONFIRM_PRINT", "REPRINT"],
    STATE_PRODUCING: ["REPORT", "CLOSE"],
    STATE_COMPLETED: ["CLOSE"],
    STATE_CLOSED: [],
}


def is_transition_allowed(current: int, target: int) -> bool:
    """校验目标态是否在当前态合法跳转边内（BR-18 / 落实 D3）。"""
    return target in STATE_TRANSITIONS.get(current, [])


def get_allowed_transitions(current: int) -> List[int]:
    """获取当前态的合法跳转列表。"""
    return list(STATE_TRANSITIONS.get(current, []))


def get_visible_buttons(current: int) -> List[str]:
    """获取当前态前端可见按钮。"""
    return list(STATE_VISIBLE_BUTTONS.get(current, []))
