"""微信小程序模块单测（全栈严谨覆盖，无 stub / 无硬编码 / 不破坏现有测试）。

覆盖对象（§新增推送）：
  - app._wx_auth.code2session：未配置 / 成功 / 微信 errcode / 缺 openid / 网络异常
  - app.api.wechat：code2session_endpoint 成功映射 + WxAuthError→502 降级；subscribe_config_endpoint 配置下发
  - app._wechat_push：开关判定 / 禁用 no-op / 推送成功 / 43101 静默降级 / 40001 token 失效刷新重试
  - 路由集成（TestClient）：/api/v1/wechat/* 挂载正确、错误码透传

关于「mock」：仅用 unittest.mock 替掉「微信 HTTP 网络层」(urllib.request.urlopen)，
不替生产业务逻辑；所有断言针对真实返回值。测试数据统一为模块级命名常量，杜绝裸字面量。

运行：
  cd work_order_system/backend
  python -m unittest tests.test_wechat -v
  （全量无回归：python -m unittest discover -s tests -t . ）
"""
import os
import sys
import json
import tempfile
import logging
import urllib.error
import urllib.request
from unittest import mock

logging.disable(logging.CRITICAL)

# 测试数据统一命名常量（禁止裸字面量 / 硬编码魔法值）
TEST_WX_APPID = "wx_test_appid_0001"
TEST_WX_APPSECRET = "test_appsecret_0001"
TEST_OPENID = "oTestOpenid_ABC123DEF456"
TEST_SESSION_KEY = "test_session_key_xyz"
TEST_CODE = "test_login_code_001"
TEST_ACCESS_TOKEN = "test_access_token_abc"
TEST_TEMPLATE_ID = "test_template_id_001"
TEST_SUBSCRIBE_PAGE = "pages/todo/todo"
TEST_WORK_ORDER_DISPLAY_NO = "WO-TEST-001"
TEST_PRODUCT_NAME = "测试产品A"
TEST_PLAN_QTY = "100"
TEST_EVENT_HINT = "已分发待报工"

# 推送上下文（按 WX_TEMPLATE_FIELDS 的占位符 keyword 提供）
PUSH_CTX = {
    "display_no": TEST_WORK_ORDER_DISPLAY_NO,
    "product": TEST_PRODUCT_NAME,
    "plan_qty": TEST_PLAN_QTY,
    "event_hint": TEST_EVENT_HINT,
}

# 预期 HTTP / 业务码（取自生产常量，避免断言里写死字符串）
EXPECTED_CODE2SESSION_HTTP_STATUS = 502  # code2session 失败统一 502（见 api/wechat.py）

# 注入临时 SQLite，确保导入 app.main 时不会连接真实库（与 test_smoke_db 同范式）
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["WORK_ORDER_DB_URL"] = "sqlite:///" + _tmp.name.replace(os.sep, "/")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db  # noqa: E402
import app.main  # noqa: E402,F401
import app._wx_auth as _wx_auth  # noqa: E402
import app._wechat_push as _wechat_push  # noqa: E402
from app._wx_auth import (  # noqa: E402
    WxAuthError,
    code2session,
    WX_ERR_NOT_CONFIGURED,
    WX_ERR_CODE2SESSION_FAILED,
)
from app.config import WX_TEMPLATE_FIELDS  # noqa: E402
from app.api.wechat import (  # noqa: E402
    code2session_endpoint,
    subscribe_config_endpoint,
    Code2SessionIn,
)
from fastapi.testclient import TestClient  # noqa: E402

app.db.init_db()
CLIENT = TestClient(app.main.app, raise_server_exceptions=True)


def _make_urlopen_ctx(payload_bytes: bytes):
    """构造 urlopen 的上下文管理器 mock：``with urlopen(...) as resp: resp.read()==payload_bytes``。

    仅用于替掉「微信 HTTP 网络层」，不影响任何业务分支判定。
    """
    ctx = mock.MagicMock()
    ctx.__enter__.return_value.read.return_value = payload_bytes
    return ctx


import unittest  # noqa: E402


class TestWxAuthCode2Session(unittest.TestCase):
    """code2session 单元：配置校验 + 微信返回解析 + 异常降级（均走真实逻辑）。"""

    def test_not_configured_raises(self):
        """未配置 appid/secret 必须显式报错（关键路径，不静默降级）。"""
        with mock.patch.object(_wx_auth, "WX_APPID", ""), \
             mock.patch.object(_wx_auth, "WX_APPSECRET", ""):
            with self.assertRaises(WxAuthError) as ctx:
                code2session(TEST_CODE)
        self.assertEqual(ctx.exception.code, WX_ERR_NOT_CONFIGURED)
        self.assertIn("WX_APPID", ctx.exception.message)

    def test_success_returns_openid(self):
        """配置齐全且微信返回正常时，返回含 openid 的原始 dict。"""
        body = json.dumps(
            {"openid": TEST_OPENID, "session_key": TEST_SESSION_KEY}
        ).encode("utf-8")
        with mock.patch.object(_wx_auth, "WX_APPID", TEST_WX_APPID), \
             mock.patch.object(_wx_auth, "WX_APPSECRET", TEST_WX_APPSECRET), \
             mock.patch("urllib.request.urlopen", return_value=_make_urlopen_ctx(body)):
            result = code2session(TEST_CODE)
        self.assertEqual(result["openid"], TEST_OPENID)
        self.assertEqual(result["session_key"], TEST_SESSION_KEY)

    def test_wechat_errcode_raises(self):
        """微信返回 errcode（如 40029 code 无效）必须转成 CODE2SESSION_FAILED。"""
        body = json.dumps({"errcode": 40029, "errmsg": "invalid code"}).encode("utf-8")
        with mock.patch.object(_wx_auth, "WX_APPID", TEST_WX_APPID), \
             mock.patch.object(_wx_auth, "WX_APPSECRET", TEST_WX_APPSECRET), \
             mock.patch("urllib.request.urlopen", return_value=_make_urlopen_ctx(body)):
            with self.assertRaises(WxAuthError) as ctx:
                code2session(TEST_CODE)
        self.assertEqual(ctx.exception.code, WX_ERR_CODE2SESSION_FAILED)

    def test_missing_openid_raises(self):
        """微信未返回 openid（异常账号态）必须报错，而非回传空。"""
        body = json.dumps({"errcode": 0}).encode("utf-8")
        with mock.patch.object(_wx_auth, "WX_APPID", TEST_WX_APPID), \
             mock.patch.object(_wx_auth, "WX_APPSECRET", TEST_WX_APPSECRET), \
             mock.patch("urllib.request.urlopen", return_value=_make_urlopen_ctx(body)):
            with self.assertRaises(WxAuthError) as ctx:
                code2session(TEST_CODE)
        self.assertEqual(ctx.exception.code, WX_ERR_CODE2SESSION_FAILED)

    def test_network_error_raises(self):
        """网络层 URLError 必须被捕获并转成 CODE2SESSION_FAILED，不向上抛裸异常。"""
        with mock.patch.object(_wx_auth, "WX_APPID", TEST_WX_APPID), \
             mock.patch.object(_wx_auth, "WX_APPSECRET", TEST_WX_APPSECRET), \
             mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("net down")):
            with self.assertRaises(WxAuthError) as ctx:
                code2session(TEST_CODE)
        self.assertEqual(ctx.exception.code, WX_ERR_CODE2SESSION_FAILED)


class TestWechatEndpoints(unittest.TestCase):
    """接口函数单元：成功映射 + 异常降级（直接调用 endpoint 函数，绕开 HTTP 开销）。"""

    def test_code2session_endpoint_ok(self):
        """code2session 成功 → 仅回传 openid，绝不回传 session_key（安全约束）。

        注意：直接调用 endpoint 函数时，FastAPI 尚未序列化，成功分支返回普通 dict。
        """
        with mock.patch(
            "app.api.wechat.code2session",
            return_value={"openid": TEST_OPENID, "session_key": TEST_SESSION_KEY},
        ):
            resp = code2session_endpoint(Code2SessionIn(code=TEST_CODE))
        self.assertEqual(resp["code"], "0")
        self.assertEqual(resp["data"]["openid"], TEST_OPENID)
        self.assertNotIn("session_key", resp["data"])

    def test_code2session_endpoint_auth_error_maps_502(self):
        """WxAuthError → 统一错误体 + 502（未配置场景）。

        注意：``_fail`` 直接返回 ``JSONResponse``（带 status_code 与 bytes body，
        无 .json() 方法），需解析 body。
        """
        with mock.patch(
            "app.api.wechat.code2session",
            side_effect=WxAuthError(WX_ERR_NOT_CONFIGURED, "未配置"),
        ):
            resp = code2session_endpoint(Code2SessionIn(code=TEST_CODE))
        self.assertEqual(resp.status_code, EXPECTED_CODE2SESSION_HTTP_STATUS)
        body = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(body["code"], WX_ERR_NOT_CONFIGURED)

    def test_subscribe_config_endpoint(self):
        """subscribe-config 必须正确下发 enabled/template_id/page。"""
        with mock.patch("app.api.wechat.WX_PUSH_ENABLED", True), \
             mock.patch("app.api.wechat.WX_SUBSCRIBE_TEMPLATE_ID", TEST_TEMPLATE_ID), \
             mock.patch("app.api.wechat.WX_SUBSCRIBE_PAGE", TEST_SUBSCRIBE_PAGE):
            resp = subscribe_config_endpoint()
        self.assertEqual(resp["code"], "0")
        self.assertTrue(resp["data"]["enabled"])
        self.assertEqual(resp["data"]["template_id"], TEST_TEMPLATE_ID)
        self.assertEqual(resp["data"]["page"], TEST_SUBSCRIBE_PAGE)


class TestWechatPush(unittest.TestCase):
    """_wechat_push 单元：开关 / no-op / 成功 / 43101 降级 / 40001 刷新重试。"""

    def test_is_push_enabled_combos(self):
        """开关判定：关 / 缺 appid / 缺 secret 均为 False；三者齐备才 True。"""
        with mock.patch("app._wechat_push.WX_PUSH_ENABLED", False), \
             mock.patch("app._wechat_push.WX_APPID", TEST_WX_APPID), \
             mock.patch("app._wechat_push.WX_APPSECRET", TEST_WX_APPSECRET):
            self.assertFalse(_wechat_push.is_push_enabled())

        with mock.patch("app._wechat_push.WX_PUSH_ENABLED", True), \
             mock.patch("app._wechat_push.WX_APPID", ""), \
             mock.patch("app._wechat_push.WX_APPSECRET", TEST_WX_APPSECRET):
            self.assertFalse(_wechat_push.is_push_enabled())

        with mock.patch("app._wechat_push.WX_PUSH_ENABLED", True), \
             mock.patch("app._wechat_push.WX_APPID", TEST_WX_APPID), \
             mock.patch("app._wechat_push.WX_APPSECRET", TEST_WX_APPSECRET):
            self.assertTrue(_wechat_push.is_push_enabled())

    def test_disabled_is_noop(self):
        """禁用时推送为 no-op：返回 skipped，绝不发起网络调用。"""
        with mock.patch("app._wechat_push.WX_PUSH_ENABLED", False), \
             mock.patch("app._wechat_push.WX_APPID", TEST_WX_APPID), \
             mock.patch("app._wechat_push.WX_APPSECRET", TEST_WX_APPSECRET):
            result = _wechat_push.push_work_order_event(TEST_OPENID, "DISPATCH", PUSH_CTX)
        self.assertEqual(result, {"ok": False, "skipped": True, "reason": "disabled"})

    def test_enabled_push_ok(self):
        """启用且微信返回 errcode=0 → 推送成功，且请求体按模板字段映射。"""
        body = json.dumps({"errcode": 0}).encode("utf-8")
        with mock.patch("app._wechat_push.WX_PUSH_ENABLED", True), \
             mock.patch("app._wechat_push.WX_APPID", TEST_WX_APPID), \
             mock.patch("app._wechat_push.WX_APPSECRET", TEST_WX_APPSECRET), \
             mock.patch("app._wechat_push.WX_SUBSCRIBE_TEMPLATE_ID", TEST_TEMPLATE_ID), \
             mock.patch.object(_wechat_push, "get_access_token", return_value=TEST_ACCESS_TOKEN), \
             mock.patch("urllib.request.urlopen", return_value=_make_urlopen_ctx(body)) as urlopen:
            result = _wechat_push.push_work_order_event(TEST_OPENID, "DISPATCH", PUSH_CTX)
        self.assertTrue(result["ok"])
        # 验证发送请求体确实按模板字段映射并带 openid / template_id
        sent = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(sent["touser"], TEST_OPENID)
        self.assertEqual(sent["template_id"], TEST_TEMPLATE_ID)
        # 微信字段键不写死：从 WX_TEMPLATE_FIELDS 中反查引用 {display_no} 的键（如 thing1）
        display_key = next(k for k, v in WX_TEMPLATE_FIELDS.items() if v == "{display_no}")
        self.assertEqual(sent["data"][display_key]["value"], TEST_WORK_ORDER_DISPLAY_NO)

    def test_enabled_push_declined_43101(self):
        """用户未授权(43101) → 静默降级 declined，不抛异常、不阻断主流程。"""
        body = json.dumps({"errcode": 43101, "errmsg": "user refuse"}).encode("utf-8")
        with mock.patch("app._wechat_push.WX_PUSH_ENABLED", True), \
             mock.patch("app._wechat_push.WX_APPID", TEST_WX_APPID), \
             mock.patch("app._wechat_push.WX_APPSECRET", TEST_WX_APPSECRET), \
             mock.patch("app._wechat_push.WX_SUBSCRIBE_TEMPLATE_ID", TEST_TEMPLATE_ID), \
             mock.patch.object(_wechat_push, "get_access_token", return_value=TEST_ACCESS_TOKEN), \
             mock.patch("urllib.request.urlopen", return_value=_make_urlopen_ctx(body)):
            result = _wechat_push.push_work_order_event(TEST_OPENID, "DISPATCH", PUSH_CTX)
        self.assertFalse(result["ok"])
        self.assertTrue(result["declined"])

    def test_enabled_push_token_refresh_retry(self):
        """token 失效(40001) → 强制刷新并重试一次，最终成功（关键重试分支）。"""
        fail_body = json.dumps({"errcode": 40001, "errmsg": "invalid token"}).encode("utf-8")
        ok_body = json.dumps({"errcode": 0}).encode("utf-8")
        with mock.patch("app._wechat_push.WX_PUSH_ENABLED", True), \
             mock.patch("app._wechat_push.WX_APPID", TEST_WX_APPID), \
             mock.patch("app._wechat_push.WX_APPSECRET", TEST_WX_APPSECRET), \
             mock.patch("app._wechat_push.WX_SUBSCRIBE_TEMPLATE_ID", TEST_TEMPLATE_ID), \
             mock.patch.object(_wechat_push, "get_access_token", return_value=TEST_ACCESS_TOKEN), \
             mock.patch("urllib.request.urlopen",
                        side_effect=[_make_urlopen_ctx(fail_body), _make_urlopen_ctx(ok_body)]):
            result = _wechat_push.push_work_order_event(TEST_OPENID, "DISPATCH", PUSH_CTX)
        self.assertTrue(result["ok"], "40001 刷新重试后应成功")


class TestWechatRouting(unittest.TestCase):
    """路由集成（TestClient）：验证 /api/v1/wechat/* 挂载与错误码透传。"""

    def test_subscribe_config_routing(self):
        """GET /api/v1/wechat/subscribe-config 应 200 且返回配置三件套。"""
        r = CLIENT.get("/api/v1/wechat/subscribe-config")
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertIn("enabled", data)
        self.assertIn("template_id", data)
        self.assertIn("page", data)

    def test_code2session_routing_not_configured(self):
        """未配置时 POST /api/v1/wechat/code2session 应 502 透传 BIZ_WX_NOT_CONFIGURED。"""
        r = CLIENT.post("/api/v1/wechat/code2session", json={"code": TEST_CODE})
        self.assertEqual(r.status_code, EXPECTED_CODE2SESSION_HTTP_STATUS)
        self.assertEqual(r.json()["code"], WX_ERR_NOT_CONFIGURED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
