"""数据库层（替换内存存储，对应 V5.0 §26 数据模型）。

使用 SQLAlchemy + SQLite（骨架默认）；生产可改环境变量 WORK_ORDER_DB_URL 指向
PostgreSQL/MySQL（§26 索引与乐观锁约定不变）。
"""
import os
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

# 数据库地址：默认本地 SQLite 文件；测试通过环境变量覆盖为内存/临时库
DB_URL = os.getenv("WORK_ORDER_DB_URL", "sqlite:///./work_order_system.db")
_CONNECT_ARGS = (
    {"check_same_thread": False, "timeout": 30}
    if DB_URL.startswith("sqlite") else {}
)

engine = create_engine(DB_URL, connect_args=_CONNECT_ARGS)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def _now() -> datetime:
    """统一 UTC 时间戳（§25.1 时间约定）。"""
    return datetime.utcnow()


class WorkOrderORM(Base):
    """工单主表（§26.2）。"""

    __tablename__ = "work_orders"

    order_uuid = Column(String(36), primary_key=True)
    display_no = Column(String(32), nullable=False)
    tenant_id = Column(String(36), nullable=False, index=True)  # 多租户隔离（BR-16）
    state = Column(Integer, nullable=False, default=2)
    version = Column(Integer, nullable=False, default=1)  # 乐观锁
    doc_confidence = Column(Float, nullable=True)
    need_review = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now)

    # 业务唯一性：同一工单号（display_no）全局唯一，禁止重复入库（代码层已拦截，此约束为 DB 级兜底）
    __table_args__ = (UniqueConstraint("display_no", name="uq_work_orders_display_no"),)


class OrderProcessORM(Base):
    """工序进度（§26.3，并发累加 + 乐观锁）。"""

    __tablename__ = "order_process"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_uuid = Column(String(36), nullable=False, index=True)
    process_code = Column(String(20), nullable=False)
    required_qty = Column(Integer, nullable=False, default=0)
    completed_qty = Column(Integer, nullable=False, default=0)  # 并发累加（BR-22）
    version = Column(Integer, nullable=False, default=1)  # 乐观锁
    need_review = Column(Boolean, nullable=False, default=False)


class ReportORM(Base):
    """报工记录（§26.4，撤回态标记）。"""

    __tablename__ = "reports"

    report_id = Column(String(36), primary_key=True)
    order_uuid = Column(String(36), nullable=False, index=True)
    process_id = Column(String(20), nullable=False)
    operator_id = Column(String(36), nullable=False)
    completed_qty = Column(Integer, nullable=False)
    client_created_at = Column(DateTime, nullable=True)
    status = Column(String(16), nullable=False, default="VALID")  # VALID/WITHDRAWN
    withdrawable_until = Column(DateTime, nullable=True)


class ConflictLogORM(Base):
    """离线冲突记录（§26.5，默认本地优先待复核）。"""

    __tablename__ = "conflict_logs"

    conflict_id = Column(String(36), primary_key=True)
    entity_type = Column(String(32), nullable=True)
    entity_id = Column(String(36), nullable=True)
    local_version = Column(Integer, nullable=True)
    server_version = Column(Integer, nullable=True)
    strategy = Column(String(16), nullable=False, default="LOCAL_PENDING")
    resolved_by = Column(String(36), nullable=True)
    audit = Column(JSON, nullable=True)


class QrcodePrintTaskORM(Base):
    """二维码打印任务（§26.6，3a/3b 拆分）。"""

    __tablename__ = "qrcode_print_tasks"

    print_task_id = Column(String(36), primary_key=True)
    order_uuid = Column(String(36), nullable=False, index=True)
    state = Column(String(16), nullable=False, default="3a_GENERATED")  # 3a/3b
    dpi = Column(Integer, nullable=False, default=300)
    size_mm = Column(Integer, nullable=False, default=30)
    reprint_req_id = Column(String(36), nullable=True)


class OcrTaskORM(Base):
    """OCR 任务（§26.7 状态机 + 死信）。

    解析改为后台线程异步执行，stage/progress/message 实时反映进度，
    前端轮询即可展示真实进度条（避免长阻塞且不可见，M1-01 体验优化）。
    """

    __tablename__ = "ocr_tasks"

    task_id = Column(String(36), primary_key=True)
    status = Column(String(16), nullable=False, default="QUEUED")  # QUEUED/RUNNING/DONE/FAILED
    stage = Column(String(24), nullable=False, default="QUEUED")   # 当前阶段（OCR_STAGE_*）
    progress = Column(Integer, nullable=False, default=0)           # 进度百分比 0-100（前端进度条）
    message = Column(String(120), nullable=True)                    # 实时阶段说明（如"正在识别第 2/5 页"）
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


def _migrate_ocr_task_columns() -> None:
    """旧库兼容迁移：为 ocr_tasks 补加 stage/progress/message 列（首次部署新列时执行）。

    生产库若已存在旧 schema 表，``create_all`` 不会自动加列，需 ALTER 补齐，
    否则后台解析写入新列会报 ``no such column``。
    """
    from sqlalchemy import inspect

    try:
        insp = inspect(engine)
        if not insp.has_table("ocr_tasks"):
            return
        existing = {c["name"] for c in insp.get_columns("ocr_tasks")}
        needed = {
            "stage": "VARCHAR(24) NOT NULL DEFAULT 'QUEUED'",
            "progress": "INTEGER NOT NULL DEFAULT 0",
            "message": "VARCHAR(120)",
        }
        with engine.begin() as conn:
            for col, ddl in needed.items():
                if col not in existing:
                    log.info("OCR 任务表迁移：新增列 %s", col)
                    conn.execute(text(f"ALTER TABLE ocr_tasks ADD COLUMN {col} {ddl}"))
    except Exception as exc:  # noqa: BLE001 - 迁移失败不应阻断启动
        log.error("OCR 任务表迁移异常: %s\n%s", exc, traceback.format_exc())


def init_db() -> None:
    """创建全部表（生产应在迁移工具中执行，此处仅骨架便利）。"""
    Base.metadata.create_all(bind=engine)
    _migrate_ocr_task_columns()


def get_db():
    """FastAPI 依赖：提供会话并在请求结束后关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
