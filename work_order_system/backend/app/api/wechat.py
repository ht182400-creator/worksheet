"""微信小程序相关接口（登录态换取 + 订阅消息配置下发，§新增推送）。"""
from fastapi import APIRouter
from pydantic import BaseModel

from app._wx_auth import WxAuthError, code2session
from app.api.worker import _fail, _trace
from app.config import WX_PUSH_ENABLED, WX_SUBSCRIBE_PAGE, WX_SUBSCRIBE_TEMPLATE_ID
from app.logger import get_logger

log = get_logger(__name__)
router = APIRouter()


class Code2SessionIn(BaseModel):
    """wx.login 临时 code 换取 openid 的请求体。"""

    code: str


@router.post("/wechat/code2session")
def code2session_endpoint(payload: Code2SessionIn):
    """小程序 wx.login 的临时 code → openid（§新增推送）。

    小程序无法直接拿到 openid，必须经后端用 code 调微信 jscode2session 换取。
    出于安全，仅回传 openid，不返回 session_key（session_key 属敏感凭证，不应下发前端）。
    """
    try:
        sess = code2session(payload.code)
        return {"code": "0", "data": {"openid": sess.get("openid")}, "traceId": _trace()}
    except WxAuthError as e:
        return _fail(e.code, e.message, 502)
    except Exception as exc:  # noqa: BLE001
        log.error("code2session 接口异常: %s", exc)
        return _fail("BIZ_WX_CODE2SESSION_FAILED", "登录态换取失败", 500)


@router.get("/wechat/subscribe-config")
def subscribe_config_endpoint():
    """下发订阅消息配置（模板 id / 跳转页 / 是否启用），供小程序 requestSubscribeMessage 使用。

    小程序侧不必硬编码模板 id，启动时拉一次即可；模板 id 与 WX_TEMPLATE_FIELDS 需在小程序
    后台「订阅消息」中与后端 config.py 保持关键字一致。
    """
    return {
        "code": "0",
        "data": {
            "enabled": WX_PUSH_ENABLED,
            "template_id": WX_SUBSCRIBE_TEMPLATE_ID,
            "page": WX_SUBSCRIBE_PAGE,
        },
        "traceId": _trace(),
    }
