"""存储层（DB 驱动，替换内存演示；对应 V5.0 §26）。

所有函数接收 `db` 会话（由 FastAPI 依赖 get_db 注入）。业务校验失败抛
BusinessError，由路由层转成统一 BIZ_* 错误响应（§25.1）。
"""
import os
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from app.logger import get_logger

log = get_logger(__name__)

from app.config import (
    DEFAULT_WITHDRAW_WINDOW_MINUTES,
    DEFAULT_DEMO_REQUIRED_QTY,
    OCR_UPLOAD_DIR,
    OCR_ENGINE_PDF_LAYER,
    OCR_ENGINE_SERVER,
    WORK_ORDER_DUPLICATE_CODE,
)
from app._field_parser import parse_work_order_fields
from app._pdf_extract import OcrNoTextLayerError, extract_text
from app.db import (
    WorkOrderORM,
    OrderProcessORM,
    ReportORM,
    ConflictLogORM,
    QrcodePrintTaskORM,
    OcrTaskORM,
)
from app.models import WorkOrder, ReportOut


class BusinessError(Exception):
    """可映射到 §25.1 统一错误体的业务异常。"""

    def __init__(self, code: str, message: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _to_work_order(orm: WorkOrderORM) -> WorkOrder:
    """ORM → Pydantic（API 输出复用 §26.2 模型）。"""
    return WorkOrder(
        order_uuid=orm.order_uuid,
        display_no=orm.display_no,
        tenant_id=orm.tenant_id,
        state=orm.state,
        version=orm.version,
        doc_confidence=orm.doc_confidence,
        need_review=orm.need_review,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def create_work_order(db, payload) -> WorkOrder:
    """创建工单（UUID 近似 v7；生产按 §26 用 UUID v7，PK=order_uuid）。

    去重：相同 ``display_no`` 视为同一张工单，已存在则抛 409 拒绝重复入库，
    避免 OCR 回填时同一工单被多次建单（脏数据）。
    """
    # 重复工单号拦截（按 display_no 判重，禁止重复入库回填）
    existing = db.scalars(
        select(WorkOrderORM).where(WorkOrderORM.display_no == payload.display_no)
    ).first()
    if existing is not None:
        log.warning("拒绝重复工单号 %s（已存在 order_uuid=%s）", payload.display_no, existing.order_uuid)
        raise BusinessError(
            WORK_ORDER_DUPLICATE_CODE,
            f"工单号已存在，请勿重复入库（已有 order_uuid={existing.order_uuid}）",
            409,
        )
    now = datetime.utcnow()
    orm = WorkOrderORM(
        order_uuid=str(uuid.uuid4()),
        display_no=payload.display_no,
        tenant_id=payload.tenant_id,
        doc_confidence=payload.doc_confidence,
        need_review=payload.need_review,
        created_at=now,
        updated_at=now,
    )
    db.add(orm)
    db.commit()
    db.refresh(orm)
    return _to_work_order(orm)


def get_work_order(db, order_id: str) -> Optional[WorkOrder]:
    """按主键获取工单。"""
    orm = db.get(WorkOrderORM, order_id)
    return _to_work_order(orm) if orm else None


def list_work_orders(db) -> List[WorkOrder]:
    """列出全部工单（演示用）。"""
    return [_to_work_order(o) for o in db.scalars(select(WorkOrderORM)).all()]


def apply_status_change(db, order_id: str, target_state: int, version: int) -> Optional[WorkOrder]:
    """乐观锁 + 状态变更（调用方须先校验 allowedTransitions，BR-18）。

    采用 SQL 层原子 ``UPDATE ... WHERE version=?`` 实现乐观锁，替代进程内
    ``orm.version != version`` 读-改-写，避免多事务并发时都读到旧版本并都提交、
    乐观锁失效（已知骨架缺陷）。影响行数为 0 视为版本冲突，返回 None 由路由层转
    409 VERSION_CONFLICT；order 不存在同样返回 None（由路由层先判 404）。
    """
    now = datetime.utcnow()
    result = db.execute(
        update(WorkOrderORM)
        .where(WorkOrderORM.order_uuid == order_id, WorkOrderORM.version == version)
        .values(state=target_state, version=WorkOrderORM.version + 1, updated_at=now)
    )
    if result.rowcount == 0:
        return None
    db.commit()
    # 重新加载以返回最新版本号（核心 UPDATE 不更新会话内已缓存的 ORM 对象）
    orm = db.get(WorkOrderORM, order_id)
    db.refresh(orm)
    return _to_work_order(orm)


def submit_report(db, order_id: str, payload) -> ReportOut:
    """报工提交：超报拦截(BR-05) + 在线自动合并(BR-22) + 撤回窗口(M5-12)。

    返回合并后的累计完成量与撤回截止时间。
    """
    wo = db.get(WorkOrderORM, order_id)
    if wo is None:
        raise BusinessError("BIZ_ORDER_NOT_FOUND", "工单不存在", 404)
    # 乐观锁：报工携带的 order.version 须与服务端一致
    if wo.version != payload.version:
        raise BusinessError("BIZ_VERSION_CONFLICT", "乐观锁失败，请拉取最新数据重试", 409)

    # 取或建工序进度（演示按 process_id 匹配；生产依 §26.3）
    proc = db.scalars(
        select(OrderProcessORM).where(
            OrderProcessORM.order_uuid == order_id,
            OrderProcessORM.process_code == payload.process_id,
        )
    ).first()
    if proc is None:
        proc = OrderProcessORM(
            order_uuid=order_id,
            process_code=payload.process_id,
            required_qty=DEFAULT_DEMO_REQUIRED_QTY,
            completed_qty=0,
            version=1,
            need_review=False,
        )
        db.add(proc)
        db.commit()
        db.refresh(proc)

    # 超报拦截（BR-05）
    if proc.completed_qty + payload.completed_qty > proc.required_qty:
        raise BusinessError(
            "BIZ_REPORT_OVERFLOW",
            f"超报：累计 {proc.completed_qty + payload.completed_qty} > 要求 {proc.required_qty}",
            422,
        )

    # 在线并发自动合并（BR-22：服务端累加，不弹二选一）
    # 使用 SQL 层原子 UPDATE ... WHERE version=? 防止读-改-写丢失：并发下后到
    # 事务读到旧 version 会命中 0 行，转 BIZ_VERSION_CONFLICT 由客户端拉取最新重试
    result = db.execute(
        update(OrderProcessORM)
        .where(
            OrderProcessORM.order_uuid == order_id,
            OrderProcessORM.process_code == payload.process_id,
            OrderProcessORM.version == proc.version,
        )
        .values(
            completed_qty=OrderProcessORM.completed_qty + payload.completed_qty,
            version=OrderProcessORM.version + 1,
        )
    )
    if result.rowcount == 0:
        db.rollback()
        raise BusinessError(
            "BIZ_VERSION_CONFLICT",
            "工序进度版本冲突，请拉取最新数据后重试",
            409,
        )
    db.refresh(proc)  # 重新加载累加后的累计量供返回
    report_id = str(uuid.uuid4())
    now = datetime.utcnow()
    rep = ReportORM(
        report_id=report_id,
        order_uuid=order_id,
        process_id=payload.process_id,
        operator_id=payload.operator_id,
        completed_qty=payload.completed_qty,
        client_created_at=payload.client_created_at or now,
        status="VALID",
        withdrawable_until=now + timedelta(minutes=DEFAULT_WITHDRAW_WINDOW_MINUTES),
    )
    db.add(rep)
    db.commit()
    return ReportOut(
        report_id=report_id,
        order_id=order_id,
        merged_completed=proc.completed_qty,
        need_review=proc.need_review,
        withdrawable_until=rep.withdrawable_until,
    )


def withdraw_report(db, report_id: str, operator_id: str) -> str:
    """报工撤回（M5-12 / §4.9.2 撤回窗口 + 工序门禁）。"""
    rep = db.get(ReportORM, report_id)
    if rep is None:
        raise BusinessError("BIZ_REPORT_NOT_FOUND", "报工记录不存在", 404)
    if rep.status == "WITHDRAWN":
        return "WITHDRAWN"
    if rep.withdrawable_until < datetime.utcnow():
        raise BusinessError("BIZ_WITHDRAW_EXPIRED", "撤回窗口已过期或已进入下工序", 409)
    rep.status = "WITHDRAWN"
    db.commit()
    return "WITHDRAWN"


def generate_qrcode(db, payload) -> str:
    """单张二维码生成（状态 3a_GENERATED，BR-21）。返回 print_task_id。"""
    task_id = str(uuid.uuid4())
    db.add(
        QrcodePrintTaskORM(
            print_task_id=task_id,
            order_uuid=payload.order_id,
            state="3a_GENERATED",
            dpi=payload.dpi,
            size_mm=payload.size_mm,
        )
    )
    db.commit()
    return task_id


def batch_qrcode(db, payload) -> str:
    """批量二维码生成（≤100，BR-15 / M3-02）。返回 batch_id（聚合任务标识）。"""
    batch_id = str(uuid.uuid4())
    for oid in payload.order_ids:
        db.add(
            QrcodePrintTaskORM(
                print_task_id=str(uuid.uuid4()),
                order_uuid=oid,
                state="3a_GENERATED",
                dpi=payload.dpi,
                size_mm=30,
            )
        )
    db.commit()
    return batch_id


def confirm_print(db, print_task_id: str) -> str:
    """打印确认（3a → 3b_已打印确认，BR-21）。"""
    orm = db.get(QrcodePrintTaskORM, print_task_id)
    if orm is None:
        raise BusinessError("BIZ_QRCODE_NOT_FOUND", "打印任务不存在", 404)
    orm.state = "3b_PRINTED"
    db.commit()
    return "3b_已打印确认"


def resolve_conflict(db, conflict_id: str, payload) -> dict:
    """冲突裁决（限主管角色，BR-06 / D4 / D7）。"""
    if payload.operator_role != "SUPERVISOR":
        raise BusinessError("BIZ_PERMISSION_DENY", "仅主管可裁决冲突", 403)
    strategy_map = {"keep_local": "LOCAL_PENDING", "keep_server": "SERVER", "merge": "MERGED"}
    strategy = strategy_map.get(payload.resolve_by, "LOCAL_PENDING")
    log_row = db.get(ConflictLogORM, conflict_id)
    if log_row is None:
        log_row = ConflictLogORM(conflict_id=conflict_id, entity_type="work_order", entity_id=conflict_id)
        db.add(log_row)
    log_row.strategy = strategy
    log_row.resolved_by = payload.operator_role
    db.commit()
    return {"status": "RESOLVED", "strategy": strategy}


def create_ocr_task(db: Session, filename: str, file_bytes: bytes) -> str:
    """创建 OCR 识别任务（异步：落盘文件 + 返回 QUEUED，轮询时执行真实解析）。"""
    task_id = f"OCR-{uuid.uuid4().hex[:12]}"
    try:
        OCR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        ext = os.path.splitext(filename)[1].lower() or ".pdf"
        save_path = OCR_UPLOAD_DIR / f"{task_id}{ext}"
        save_path.write_bytes(file_bytes)
    except Exception as exc:  # noqa: BLE001 - 落盘失败需明确抛出，前端据此重试
        log.error("OCR 文件落盘失败 %s: %s\n%s", task_id, exc, traceback.format_exc())
        raise BusinessError("OCR_SAVE_FAILED", 500, f"文件保存失败: {exc}")
    db.add(OcrTaskORM(task_id=task_id, status="QUEUED"))
    db.commit()
    log.info("创建 OCR 任务 %s 文件=%s 落盘=%s", task_id, filename, save_path)
    return task_id


def _find_ocr_file(task_id: str) -> Optional[Path]:
    """按任务号查找落盘的临时上传文件。"""
    if not OCR_UPLOAD_DIR.exists():
        return None
    matches = list(OCR_UPLOAD_DIR.glob(f"{task_id}*"))
    return matches[0] if matches else None


def get_ocr_task(db: Session, task_id: str) -> dict:
    """轮询 OCR 任务结果（§25.2.1 BR-17）。

    首次轮询（状态仍为 QUEUED）时执行真实 PDF 解析：提取文本 → 规则化抽取字段
    → 计算置信度。解析失败按 M1-09/M1-10 降级为 FAILED 并给出明确错误。
    """
    orm = db.get(OcrTaskORM, task_id)
    if orm is None:
        raise BusinessError("OCR_TASK_NOT_FOUND", "OCR 任务不存在", 404)
    if orm.status != "QUEUED":
        return {"taskId": task_id, "status": orm.status, "result": orm.result or {}}

    save_path = _find_ocr_file(task_id)
    try:
        if not save_path or not save_path.exists():
            raise OcrNoTextLayerError("未找到上传文件，请重新上传工单文件")
        suffix = save_path.suffix.lower()
        engine = ""
        # 图片与 PDF 统一走后端原生 OCR（方案 A）；PDF 若含文本层优先直抽（更快更准）
        if suffix in (".pdf",):
            text = ""
            try:
                text = extract_text(save_path.read_bytes())  # 先试文本层（pypdf）
            except OcrNoTextLayerError:
                text = ""  # 无文本层：交给后端 OCR
            if text and text.strip():
                engine = OCR_ENGINE_PDF_LAYER
            else:
                from app._ocr import ocr_bytes

                text = ocr_bytes(save_path.read_bytes(), suffix)
                engine = OCR_ENGINE_SERVER
        else:
            from app._ocr import ocr_bytes

            text = ocr_bytes(save_path.read_bytes(), suffix)
            engine = OCR_ENGINE_SERVER
        if not text or not text.strip():
            raise OcrNoTextLayerError("OCR 识别结果为空，请确认文件清晰或人工录入")
        result = parse_work_order_fields(text)
        result["rawText"] = text           # 回传原文供前端展示（方案 A 两条路径一致）
        result["engine"] = engine
        orm.status = "DONE"
        log.info("OCR 任务 %s 解析完成 engine=%s docConfidence=%s needReview=%s",
                 task_id, engine, result.get("docConfidence"), result.get("needReview"))
    except OcrNoTextLayerError as exc:
        result = {
            "error": str(exc),
            "fields": [],
            "docConfidence": 0.0,
            "needReview": True,
            "forceManual": True,
            "rawTextLen": 0,
            "engine": OCR_ENGINE_SERVER,
        }
        orm.status = "FAILED"
        log.warning("OCR 任务 %s 无文本层/空结果: %s", task_id, exc)
    except Exception as exc:  # noqa: BLE001 - 任何解析异常降级为 FAILED，不抛出
        result = {
            "error": f"PDF 解析失败: {exc}",
            "fields": [],
            "docConfidence": 0.0,
            "needReview": True,
            "forceManual": True,
            "rawTextLen": 0,
        }
        orm.status = "FAILED"
        log.error("OCR 任务 %s 解析异常: %s\n%s", task_id, exc, traceback.format_exc())

    try:
        orm.result = result
        db.commit()
        if save_path and save_path.exists():
            save_path.unlink(missing_ok=True)  # 解析后清理临时文件，防磁盘堆积
    except Exception as exc:  # noqa: BLE001 - 回写失败不影响已得结果
        log.error("OCR 结果回写失败 %s: %s\n%s", task_id, exc, traceback.format_exc())
    return {"taskId": task_id, "status": orm.status, "result": result}


def pending_tasks(db, operator_id: Optional[str] = None, state: Optional[int] = None) -> List[dict]:
    """工人待办任务（过滤参数，V4.1 落实）。返回工序级待报工列表。"""
    stmt = select(OrderProcessORM)
    rows = db.scalars(stmt).all()
    tasks = []
    for r in rows:
        tasks.append({
            "order_uuid": r.order_uuid,
            "process_code": r.process_code,
            "required_qty": r.required_qty,
            "completed_qty": r.completed_qty,
            "remaining_qty": r.required_qty - r.completed_qty,
        })
    return tasks
