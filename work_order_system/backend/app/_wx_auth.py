"""微信小程序登录态换取（jscode2session）。

仅用标准库 urllib，遵循项目"单文件后端模块"约定（参考 _wechat_push.py）。
WX_APPID / WX_APPSECRET 来自 app.config（环境变量注入）；未配置时明确报错，
不静默降级（登录态换取属于关键路径，必须让调用方感知）。
"""
import json
import traceback
import urllib.error
import urllib.request

from app.config import WX_APPID, WX_APPSECRET, WX_HTTP_TIMEOUT
from app.logger import get_logger

log = get_logger(__name__)

# 微信官方 jscode2session 接口：用临时 code 换 openid + session_key + unionid
_WX_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"

# 业务错误码（与 §25.1 统一错误体对齐）
WX_ERR_NOT_CONFIGURED = "BIZ_WX_NOT_CONFIGURED"
WX_ERR_CODE2SESSION_FAILED = "BIZ_WX_CODE2SESSION_FAILED"


class WxAuthError(Exception):
    """微信登录态换取失败（由路由层转成统一错误响应）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def code2session(code: str) -> dict:
    """用 wx.login 的临时 code 换取 openid / session_key。

    入参:
        code: 小程序 ``wx.login()`` 返回的临时登录凭证（5 分钟有效、一次性）
    返回:
        dict: 微信原始返回 {openid, session_key, unionid?}
    异常:
        WxAuthError: 配置缺失 / 网络失败 / 微信返回错误码
    """
    if not WX_APPID or not WX_APPSECRET:
        raise WxAuthError(
            WX_ERR_NOT_CONFIGURED,
            "微信小程序 AppID/AppSecret 未配置（请设置环境变量 WX_APPID、WX_APPSECRET）",
        )
    url = (
        f"{_WX_CODE2SESSION_URL}?appid={WX_APPID}"
        f"&secret={WX_APPSECRET}&js_code={code}&grant_type=authorization_code"
    )
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "work-order-backend")
    try:
        with urllib.request.urlopen(req, timeout=WX_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except urllib.error.URLError as exc:
        log.error("调用微信 jscode2session 网络异常: %s", exc)
        raise WxAuthError(WX_ERR_CODE2SESSION_FAILED, "换取微信登录态网络失败") from exc
    except Exception as exc:  # noqa: BLE001 - 解析失败也算换取失败
        log.error("调用微信 jscode2session 异常: %s\n%s", exc, traceback.format_exc())
        raise WxAuthError(WX_ERR_CODE2SESSION_FAILED, "换取微信登录态失败") from exc

    if data.get("errcode"):
        # 40029=code 无效；40163=code 已被使用；45011=频率限制；等
        log.warning(
            "微信 jscode2session 返回错误 errcode=%s msg=%s",
            data.get("errcode"),
            data.get("errmsg"),
        )
        raise WxAuthError(WX_ERR_CODE2SESSION_FAILED, f"微信登录态换取失败: {data.get('errmsg')}")
    if not data.get("openid"):
        raise WxAuthError(WX_ERR_CODE2SESSION_FAILED, "微信未返回 openid")
    log.info("jscode2session 成功 openid=%s", data.get("openid"))
    return data


# 获取 access_token（client_credential），用于手机号解码等需 token 的微信接口（§新增）
_WX_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
_WX_PHONE_URL = "https://api.weixin.qq.com/wxa/business/getuserphonenumber"

_WX_TOKEN_CACHE = {"token": None, "expire_at": 0.0}

WX_ERR_PHONE_FAILED = "BIZ_WX_PHONE_FAILED"


def get_access_token() -> str:
    """获取/复用 access_token（client_credential，2h 有效，临近过期自动刷新）。"""
    import time

    now = time.time()
    if _WX_TOKEN_CACHE["token"] and now < _WX_TOKEN_CACHE["expire_at"]:
        return _WX_TOKEN_CACHE["token"]
    if not WX_APPID or not WX_APPSECRET:
        raise WxAuthError(WX_ERR_NOT_CONFIGURED, "微信 AppID/AppSecret 未配置")
    url = f"{_WX_TOKEN_URL}?grant_type=client_credential&appid={WX_APPID}&secret={WX_APPSECRET}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "work-order-backend")
    try:
        with urllib.request.urlopen(req, timeout=WX_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.error("获取 access_token 异常: %s", exc)
        raise WxAuthError(WX_ERR_PHONE_FAILED, "获取 access_token 失败") from exc
    if data.get("errcode"):
        raise WxAuthError(WX_ERR_PHONE_FAILED, f"获取 access_token 失败: {data.get('errmsg')}")
    token = data["access_token"]
    _WX_TOKEN_CACHE["token"] = token
    _WX_TOKEN_CACHE["expire_at"] = now + max(0, int(data.get("expires_in", 7200)) - 300)
    return token


def phone_number_info(code: str) -> str:
    """用 getPhoneNumber 的 code 换取真实手机号（§新增）。

    入参: code = 小程序 ``bindgetphonenumber`` 事件 ``e.detail.code``
    返回: 纯手机号字符串（phone_info.pure_phone_number）
    异常: WxAuthError（未配置 / 网络失败 / 微信返回错误码）
    """
    if not code:
        raise WxAuthError(WX_ERR_PHONE_FAILED, "phone code 为空")
    token = get_access_token()
    url = f"{_WX_PHONE_URL}?access_token={token}"
    body = json.dumps({"code": code}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    req.add_header("User-Agent", "work-order-backend")
    try:
        with urllib.request.urlopen(req, timeout=WX_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.error("解码手机号网络异常: %s", exc)
        raise WxAuthError(WX_ERR_PHONE_FAILED, "解码手机号网络失败") from exc
    if data.get("errcode"):
        raise WxAuthError(WX_ERR_PHONE_FAILED, f"解码手机号失败: {data.get('errmsg')}")
    phone_info = data.get("phone_info") or {}
    phone = phone_info.get("purePhoneNumber") or phone_info.get("phoneNumber")
    if not phone:
        raise WxAuthError(WX_ERR_PHONE_FAILED, "微信未返回手机号")
    log.info("解码手机号成功（openid 侧关联）")
    return phone
