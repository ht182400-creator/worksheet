"""临时脚本：向 workers 表插入若干测试工人数据，便于前端工人管理面板实测（浏览/查/改/删）。

- 复用真实后端引擎与 WorkerORM，确保字段/表结构与运行库完全一致。
- 仅演示用，生产不应存在；用完可删除本文件（数据保留在 work_order_system.db）。
- 运行：python seed_workers.py（在 backend 目录下）
"""
import os
import sys
import uuid

# 让脚本能 import 到 app 包（backend 为工作目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base, WorkerORM  # noqa: E402

# 与前端 client.ts 一致的演示租户
DEMO_TENANT = "demo-tenant"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work_order_system.db")
DB_URL = os.getenv("WORK_ORDER_DB_URL", f"sqlite:///{DB_PATH}")

engine = create_engine(DB_URL, connect_args={"check_same_thread": False, "timeout": 30})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _seed():
    # 用后端已有的 Tenant 常量（若存在），否则回退演示租户
    try:
        from app.config import TENANT_ID as CFG_TENANT  # type: ignore
        tenant = CFG_TENANT
    except Exception:
        tenant = DEMO_TENANT

    samples = [
        ("oSeed_alpha_001", "张伟", "13800001111", 10),
        ("oSeed_beta_002", "李娜", "13800002222", 5),
        ("oSeed_gamma_003", "王强", "13800003333", 0),
        ("oSeed_delta_004", "刘洋", "13800004444", 3),
        ("oSeed_epsilon_005", "", "13800005555", 8),   # 无姓名（测「未命名」展示）
        ("oSeed_zeta_006", "陈静", "13800006666", 1),
    ]

    with SessionLocal() as db:
        for openid, name, phone, quota in samples:
            existing = db.scalars(
                select(WorkerORM).where(WorkerORM.openid == openid)
            ).first()
            if existing is not None:
                print(f"跳过已存在: {openid}")
                continue
            w = WorkerORM(
                worker_id=str(uuid.uuid4()),
                openid=openid,
                name=name or None,
                phone=phone,
                tenant_id=tenant,
                subscribe_quota=quota,
            )
            db.add(w)
            print(f"已插入: {openid} name={name or '(空)'} phone={phone} quota={quota}")
        db.commit()
    print("done")


if __name__ == "__main__":
    _seed()
