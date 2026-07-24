"""微信小程序订阅消息推送（工单 → 工人，§新增）。

仅依赖标准库 ``urllib``（不引入 requests），与项目其它重依赖解耦。

订阅消息机制（已与用户确认）：
- **一次性订阅**：用户在小程序交互中授权一次 → 可下发一条，发掉即消耗。
- ``access_token`` 有效期 2h，需缓存并在临近过期时刷新。
- ``43101``=用户拒绝/未授权 → 仅告警不阻断；推送作为"增强"，失败由小程序轮询 ``pending-tasks`` 兜底。
- ``WX_PUSH_ENABLED=False``（默认）时所有推送为 no-op，不影响主流程（测试/未配置环境安全）。
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import (
    WX_APPID,
    WX_APPSECRET,
    WX_HTTP_TIMEOUT,
    WX_PUSH_ENABLED,
    WX_ACCESS_TOKEN_TTL_SECONDS,
    WX_TOKEN_REFRESH_THRESHOLD,
    WX_SUBSCRIBE_PAGE,
    WX_SUBSCRIBE_TEMPLATE_ID,
    WX_TEMPLATE_FIELDS,
)
from app.logger import get_logger

log = get_logger(__name__)

_WX_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
_WX_SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send"

# 模块级 access_token 缓存（单进程内存；uvicorn --reload 重启会丢，自动重取，无害）
_token_cache = {"token": None, "expire_at": 0.0}
_token_lock = threading.Lock()


def is_push_enabled() -> bool:
    """推送总开关：未配置 appid/secret 或显式关闭时为 no-op，不影响主流程。"""
    return bool(WX_PUSH_ENABLED) and bool(WX_APPID) and bool(WX_APPSECRET)


def get_access_token() -> str:
    """获取/复用 access_token（缓存，距过期 <阈值时刷新）。"""
    now = time.time()
    with _token_lock:
        if _token_cache["token"] and now < _token_cache["expire_at"]:
            return _token_cache["token"]
        token, expires_in = _fetch_token()
        # 提前阈值刷新，避免临界时刻 token 失效导致推送失败
        _token_cache["token"] = token
        _token_cache["expire_at"] = now + max(0, expires_in - WX_TOKEN_REFRESH_THRESHOLD)
        return token


def _refresh_token_force() -> str:
    """强制清空缓存并重新获取 token（token 失效重试场景）。"""
    with _token_lock:
        _token_cache["token"] = None
        _token_cache["expire_at"] = 0.0
    return get_access_token()


def _fetch_token():
    """向微信换取 access_token（环境变量注入的 appid/secret）。"""
    qs = urllib.parse.urlencode(
        {"grant_type": "client_credential", "appid": WX_APPID, "secret": WX_APPSECRET}
    )
    req = urllib.request.Request(f"{_WX_TOKEN_URL}?{qs}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=WX_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - 网络异常需上抛，由调用方降级
        log.error("获取微信 access_token 网络异常: %s", exc)
        raise
    j = json.loads(raw)
    if "access_token" not in j:
        raise RuntimeError(f"微信 token 接口异常: {raw}")
    return j["access_token"], int(j.get("expires_in", WX_ACCESS_TOKEN_TTL_SECONDS))


def push_work_order_event(openid: str, event: str, ctx: dict, page: str = None) -> dict:
    """推送工单事件订阅消息（入库 CREATE / 分发 DISPATCH）。

    返回结果 dict：``ok`` 成功；``skipped`` 开关关闭；``declined`` 用户未授权(43101)；
    其余为微信 errcode。永不抛异常，确保不阻断工单主流程。
    """
    if not is_push_enabled():
        return {"ok": False, "skipped": True, "reason": "disabled"}
    try:
        token = get_access_token()
    except Exception as exc:  # noqa: BLE001
        log.error("推送中止：获取 access_token 失败: %s", exc)
        return {"ok": False, "reason": "token_failed"}

    # 按模板字段映射构建 data（单字段值上限 20 字，微信强制）
    data = {}
    for kw, fmt in WX_TEMPLATE_FIELDS.items():
        try:
            val = fmt.format(**ctx)
        except (KeyError, IndexError):
            val = ""
        data[kw] = {"value": val[:20]}
    body = {
        "touser": openid,
        "template_id": WX_SUBSCRIBE_TEMPLATE_ID,
        "data": data,
    }
    if page:
        body["page"] = page
    return _send(token, body)


def _send(token: str, body: dict) -> dict:
    """发送订阅消息并处理微信错误码（token 失效刷新重试 / 43101 静默降级）。"""
    url = f"{_WX_SEND_URL}?access_token={token}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=WX_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        log.error("订阅消息 HTTP 异常: %s", exc)
        return {"ok": False, "reason": "http_error", "errmsg": str(exc)}
    try:
        j = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": "bad_json", "raw": raw}

    errcode = j.get("errcode", 0)
    if errcode == 0:
        return {"ok": True, "errcode": 0}
    # token 失效/非法 → 强制刷新并重试一次
    if errcode in (40001, 40014, 42001):
        log.warning("access_token 失效(errcode=%s)，刷新后重试", errcode)
        try:
            new_token = _refresh_token_force()
        except Exception as exc:  # noqa: BLE001
            log.error("刷新 token 失败: %s", exc)
            return {"ok": False, "errcode": errcode, "errmsg": j.get("errmsg")}
        return _send(new_token, body)
    # 43101：用户拒绝/未授权 → 静默降级（依赖小程序轮询兜底）
    if errcode == 43101:
        return {"ok": False, "declined": True, "errcode": 43101, "errmsg": j.get("errmsg")}
    log.warning("订阅消息推送返回 errcode=%s errmsg=%s", errcode, j.get("errmsg"))
    return {"ok": False, "errcode": errcode, "errmsg": j.get("errmsg")}
