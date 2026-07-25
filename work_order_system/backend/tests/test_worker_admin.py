"""工人管理面板后端接口单测（§工人管理面板）。

覆盖对象：
  - GET  /api/v1/workers/search  按手机号模糊 + openid 后缀搜索
  - PATCH /api/v1/workers/{openid} 补填/修正工人姓名

测试策略：沿用 tests.test_wechat 范式 —— 注入临时 SQLite，真实调用业务层，
仅对「微信网络层」做 mock（本测试不触发 jscode2session / 手机号解密，故无需 mock）。
所有断言针对真实返回值；测试数据统一为模块级命名常量。

运行：
  cd work_order_system/backend
  python -m unittest tests.test_worker_admin -v
"""
import os
import sys
import tempfile
import logging
import unittest

logging.disable(logging.CRITICAL)

# ===== 测试数据命名常量（禁止裸字面量 / 硬编码魔法值）=====
TENANT_ID = "demo-tenant"
TEST_OPENID_A = "oAdminTest_openidA987654"
TEST_OPENID_B = "oAdminTest_openidB123456"
TEST_PHONE_A = "13800001001"
TEST_PHONE_B = "13900002002"
TEST_NAME_A = "张三"
TEST_NAME_B = "李四"
TEST_NAME_UPDATED = "王五"
EMPTY_QUERY = ""
EXPECTED_SEARCH_OK = "0"
# 业务错误码（取自生产常量，避免断言里写死字符串）
EXPECTED_NOT_FOUND_CODE = "BIZ_WORKER_NOT_FOUND"
EXPECTED_NOT_FOUND_HTTP = 404

# 注入临时 SQLite，确保导入 app.main 时不会连接真实库
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["WORK_ORDER_DB_URL"] = "sqlite:///" + _tmp.name.replace(os.sep, "/")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db  # noqa: E402
import app.main  # noqa: E402,F401
from fastapi.testclient import TestClient  # noqa: E402

app.db.init_db()  # 确保表（含 phone 列）已就绪
client = TestClient(app.main.app)


def _register(openid: str, phone: str, name=None) -> dict:
    """通过已有注册接口写入一个带手机号的工人（测试夹具）。"""
    payload = {"openid": openid, "tenant_id": TENANT_ID, "phone": phone}
    if name is not None:
        payload["name"] = name
    resp = client.post("/api/v1/workers", json=payload)
    return resp


class TestWorkerSearch(unittest.TestCase):
    """GET /workers/search 搜索工人（按手机号 / openid 后 N 位）。"""

    @classmethod
    def setUpClass(cls):
        # 写入两个不同工人，保证搜索可区分
        _register(TEST_OPENID_A, TEST_PHONE_A, TEST_NAME_A)
        _register(TEST_OPENID_B, TEST_PHONE_B, TEST_NAME_B)

    def test_search_by_phone_suffix(self):
        """TC-S1：按手机号后 4 位模糊搜索命中对应工人。"""
        suffix = TEST_PHONE_A[-4:]
        resp = client.get(f"/api/v1/workers/search?q={suffix}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], EXPECTED_SEARCH_OK)
        data = body["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["openid"], TEST_OPENID_A)
        self.assertEqual(data[0]["phone"], TEST_PHONE_A)

    def test_search_by_openid_suffix(self):
        """TC-S2：按 openid 后 6 位后缀搜索命中对应工人。"""
        suffix = TEST_OPENID_B[-6:]
        resp = client.get(f"/api/v1/workers/search?q={suffix}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["openid"], TEST_OPENID_B)

    def test_search_empty_query_returns_empty(self):
        """TC-S3：空查询返回空列表（不报错、不泄全量）。"""
        resp = client.get("/api/v1/workers/search?q=")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], [])

    def test_search_no_match(self):
        """TC-S4：无匹配返回空列表。"""
        resp = client.get("/api/v1/workers/search?q=no_such_worker_999")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], [])

    def test_search_response_shape(self):
        """TC-S5：返回项含 openid/name/phone/subscribe_quota 字段。"""
        resp = client.get(f"/api/v1/workers/search?q={TEST_PHONE_A[-4:]}")
        data = resp.json()["data"][0]
        for key in ("openid", "name", "phone", "subscribe_quota"):
            self.assertIn(key, data)


class TestWorkerUpdate(unittest.TestCase):
    """PATCH /workers/{openid} 补填姓名。"""

    @classmethod
    def setUpClass(cls):
        _register("oPatchTest_openidX654321", "13700003003", "待改")

    def test_update_name_success(self):
        """TC-U1：PATCH 姓名成功，返回值与库中一致。"""
        openid = "oPatchTest_openidX654321"
        resp = client.patch(f"/api/v1/workers/{openid}", json={"name": TEST_NAME_UPDATED})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], EXPECTED_SEARCH_OK)
        self.assertEqual(body["data"]["name"], TEST_NAME_UPDATED)
        # 再次搜索确认落库
        s = client.get(f"/api/v1/workers/search?q={openid[-6:]}")
        self.assertEqual(s.json()["data"][0]["name"], TEST_NAME_UPDATED)

    def test_update_not_found(self):
        """TC-U2：PATCH 不存在的 openid 返回 404 + BIZ_WORKER_NOT_FOUND。"""
        resp = client.patch("/api/v1/workers/ghost_openid_noexist", json={"name": "x"})
        self.assertEqual(resp.status_code, EXPECTED_NOT_FOUND_HTTP)
        self.assertEqual(resp.json()["code"], EXPECTED_NOT_FOUND_CODE)

    def test_update_empty_name_clears(self):
        """TC-U3：显式传空串清空姓名（区别于不传字段）。"""
        openid = "oPatchTest_openidX654321"
        resp = client.patch(f"/api/v1/workers/{openid}", json={"name": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["name"], "")
        # 复原，避免影响其他用例
        client.patch(f"/api/v1/workers/{openid}", json={"name": TEST_NAME_UPDATED})


class TestWorkerList(unittest.TestCase):
    """GET /workers 浏览所有工人记录（操作员后台「浏览所有记录」）。"""

    @classmethod
    def setUpClass(cls):
        _register("oListTest_openidL111111", "13600004004", TEST_NAME_A)

    def test_list_returns_full_shape(self):
        """TC-L1：返回列表含全部字段，且含已注册工人。"""
        resp = client.get("/api/v1/workers")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], EXPECTED_SEARCH_OK)
        data = body["data"]
        self.assertIsInstance(data, list)
        # 找到我们刚注册的工人
        hit = next((w for w in data if w["openid"] == "oListTest_openidL111111"), None)
        self.assertIsNotNone(hit)
        for key in ("openid", "name", "phone", "subscribe_quota"):
            self.assertIn(key, hit)


class TestWorkerGetByOpenid(unittest.TestCase):
    """GET /workers/by-openid/{openid} 查单条完整信息（操作员后台「查」）。"""

    @classmethod
    def setUpClass(cls):
        _register("oGetTest_openidG222222", "13500005005", TEST_NAME_B)

    def test_get_by_openid_success(self):
        """TC-G1：查存在的工人返回完整信息（含 name/phone/subscribe_quota）。"""
        resp = client.get("/api/v1/workers/by-openid/oGetTest_openidG222222")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["openid"], "oGetTest_openidG222222")
        self.assertEqual(data["name"], TEST_NAME_B)
        self.assertEqual(data["phone"], "13500005005")
        self.assertIn("subscribe_quota", data)

    def test_get_by_openid_not_found(self):
        """TC-G2：查不存在的 openid 返回 404 + BIZ_WORKER_NOT_FOUND。"""
        resp = client.get("/api/v1/workers/by-openid/ghost_openid_noexist")
        self.assertEqual(resp.status_code, EXPECTED_NOT_FOUND_HTTP)
        self.assertEqual(resp.json()["code"], EXPECTED_NOT_FOUND_CODE)


class TestWorkerUpdateQuota(unittest.TestCase):
    """PATCH /workers/{openid} 修正订阅余量（操作员后台「改」扩展）。"""

    @classmethod
    def setUpClass(cls):
        _register("oQuotaTest_openidQ333333", "13400006006", "待调余量")

    def test_update_quota_success(self):
        """TC-Q1：PATCH subscribe_quota 成功，返回值与库中一致。"""
        openid = "oQuotaTest_openidQ333333"
        resp = client.patch(f"/api/v1/workers/{openid}", json={"subscribe_quota": 7})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["subscribe_quota"], 7)
        # 再次查询确认落库
        g = client.get(f"/api/v1/workers/by-openid/{openid}")
        self.assertEqual(g.json()["data"]["subscribe_quota"], 7)


class TestWorkerUpdatePhoneValidation(unittest.TestCase):
    """PATCH /workers/{openid} 手机号格式校验（操作员后台「改」，§工人管理面板）。"""

    @classmethod
    def setUpClass(cls):
        _register("oPhoneTest_openidP555555", "13200008008", "待校验")

    def test_update_phone_invalid_rejected(self):
        """TC-P1：手机号非 11 位/不以 1 开头 → 422 校验失败。"""
        openid = "oPhoneTest_openidP555555"
        for bad in ("123", "1380000111", "23800001111", "138000011111"):
            resp = client.patch(f"/api/v1/workers/{openid}", json={"phone": bad})
            self.assertEqual(resp.status_code, 422, f"phone={bad} 应被拒")
            self.assertIn("手机号", resp.json()["detail"][0]["msg"])

    def test_update_phone_valid_accepted(self):
        """TC-P2：合法 11 位手机号 → 200 并通过 by-openid 落库。"""
        openid = "oPhoneTest_openidP555555"
        resp = client.patch(f"/api/v1/workers/{openid}", json={"phone": "13800009999"})
        self.assertEqual(resp.status_code, 200)
        g = client.get(f"/api/v1/workers/by-openid/{openid}")
        self.assertEqual(g.json()["data"]["phone"], "13800009999")
        # 复原
        client.patch(f"/api/v1/workers/{openid}", json={"phone": "13200008008"})

    def test_update_phone_empty_clears(self):
        """TC-P3：手机号传空串 → 放行并清空（区别于不传字段）。"""
        openid = "oPhoneTest_openidP555555"
        resp = client.patch(f"/api/v1/workers/{openid}", json={"phone": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["phone"], "")
        # 复原
        client.patch(f"/api/v1/workers/{openid}", json={"phone": "13200008008"})


class TestWorkerDelete(unittest.TestCase):
    """DELETE /workers/{openid} 删除工人（操作员后台「删」）。"""

    @classmethod
    def setUpClass(cls):
        _register("oDelTest_openidD444444", "13300007007", "待删")

    def test_delete_success(self):
        """TC-D1：删除成功返回 code=0 + deleted=true，且后续查询转 404。"""
        openid = "oDelTest_openidD444444"
        resp = client.delete(f"/api/v1/workers/{openid}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], EXPECTED_SEARCH_OK)
        self.assertEqual(body["data"]["deleted"], True)
        # 删除后该记录不复存在
        g = client.get(f"/api/v1/workers/by-openid/{openid}")
        self.assertEqual(g.status_code, EXPECTED_NOT_FOUND_HTTP)
        self.assertEqual(g.json()["code"], EXPECTED_NOT_FOUND_CODE)

    def test_delete_not_found(self):
        """TC-D2：删除不存在的 openid 返回 404 + BIZ_WORKER_NOT_FOUND。"""
        resp = client.delete("/api/v1/workers/ghost_openid_noexist")
        self.assertEqual(resp.status_code, EXPECTED_NOT_FOUND_HTTP)
        self.assertEqual(resp.json()["code"], EXPECTED_NOT_FOUND_CODE)


if __name__ == "__main__":
    import unittest

    unittest.main()
