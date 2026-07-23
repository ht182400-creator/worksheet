"""API 路由聚合（供 main.py 统一 include）。"""
from app.api import work_orders, reports, qrcode, files, conflicts, bigscreen, worker

__all__ = ["work_orders", "reports", "qrcode", "files", "conflicts", "bigscreen", "worker"]
