"""大屏指标流路由（SSE + 新鲜度，§25.2.8 / BR-19 / E16 / D5）。"""
import asyncio
import json
from datetime import datetime
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.config import BIGSCREEN_PUSH_INTERVAL_SECONDS
from app.logger import get_logger

log = get_logger(__name__)
router = APIRouter()


async def _metric_stream(request: Request, max_events: int = None) -> AsyncGenerator[str, None]:
    """向大屏推送指标；每 ≤5s 一次，附带 server_ts 供前端算新鲜度。

    - 循环检测客户端断开（http.disconnect），及时退出避免服务端资源泄漏；
    - max_events 限制推送条数（默认无限），便于调试/轮询场景优雅结束流。
    """
    sent = 0
    while True:
        if max_events is not None and sent >= max_events:
            break
        # 非阻塞检测断开：无消息时 wait_for 立即超时，不阻塞推送节奏
        try:
            message = await asyncio.wait_for(request.receive(), timeout=0)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            message = None
        if message and message.get("type") == "http.disconnect":
            log.info("大屏 SSE 客户端断开，结束推送")
            break
        payload = {
            "kpi": {
                "producing": 12,
                "completed_today": 348,
                "exception": 2,
                "online_workers": 26,
            },
            "server_ts": datetime.utcnow().isoformat() + "Z",
        }
        yield f"event: metrics\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        sent += 1
        await asyncio.sleep(BIGSCREEN_PUSH_INTERVAL_SECONDS)


@router.get("/bigscreen/metrics")
async def bigscreen_metrics(request: Request, line_id: str = None, max_events: int = None):
    """大屏指标 SSE 流（text/event-stream）。前端据 server_ts 计算新鲜度。"""
    return StreamingResponse(_metric_stream(request, max_events), media_type="text/event-stream")
