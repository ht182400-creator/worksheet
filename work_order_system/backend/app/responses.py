"""统一响应与业务错误工具（对应 V5.0 §25.1 错误体规范）。"""
import uuid

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def ok(data) -> JSONResponse:
    """成功响应：{"code":"0","data":...,"traceId":...}。"""
    # jsonable_encoder 处理 datetime/UUID/Pydantic 模型等不可直接 JSON 序列化的对象
    return JSONResponse({"code": "0", "data": jsonable_encoder(data), "traceId": str(uuid.uuid4())})


def fail(code: str, message: str, status_code: int = 400) -> JSONResponse:
    """业务错误响应：{"code":"BIZ_*","message":...,"traceId":...}。"""
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "traceId": str(uuid.uuid4())},
    )
