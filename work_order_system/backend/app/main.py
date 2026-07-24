"""FastAPI 应用入口（最小可运行骨架，对应 V5.0 §25 接口契约 + §26 DB）。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import SERVICE_HOST, SERVICE_PORT, API_V1_PREFIX
from app.logger import get_logger
from app.api import work_orders, reports, qrcode, files, conflicts, bigscreen, worker, wechat
from app.db import init_db, DB_URL

log = get_logger(__name__)

app = FastAPI(title="工单智能识别与扫码分发系统", version="V5.0")

# CORS（演示放开；生产按 §13 收紧为指定源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(work_orders.router, prefix=API_V1_PREFIX)
app.include_router(reports.router, prefix=API_V1_PREFIX)
app.include_router(qrcode.router, prefix=API_V1_PREFIX)
app.include_router(files.router, prefix=API_V1_PREFIX)
app.include_router(conflicts.router, prefix=API_V1_PREFIX)
app.include_router(bigscreen.router, prefix=API_V1_PREFIX)
app.include_router(worker.router, prefix=API_V1_PREFIX)
app.include_router(wechat.router, prefix=API_V1_PREFIX)


@app.on_event("startup")
def on_startup():
    """启动时建表（生产应由迁移工具执行，§26）。"""
    init_db()
    log.info("数据库初始化完成（%s）", DB_URL)


@app.get("/health")
def health_check():
    """健康检查（部署探针用）。"""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    log.info("启动服务 %s:%s", SERVICE_HOST, SERVICE_PORT)
    uvicorn.run("app.main:app", host=SERVICE_HOST, port=SERVICE_PORT, reload=True)
