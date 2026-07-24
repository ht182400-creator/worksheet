"""DB 版端到端冒烟测试（TestClient + 临时 SQLite）。"""
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

# 测试库：导入 app 之前注入环境变量，使引擎指向临时库（Windows 路径用正斜杠）
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_db_path = _tmp.name.replace("\\", "/")
os.environ["WORK_ORDER_DB_URL"] = f"sqlite:///{_db_path}"

from fastapi.testclient import TestClient  # noqa: E402

import app.db  # noqa: E402
import app.main  # noqa: E402,F401

# 显式建表（新版 Starlette 下 TestClient 非 with 上下文不自动触发 startup）
app.db.init_db()

client = TestClient(app.main.app, raise_server_exceptions=True)


def _t(text):
    print(text, flush=True)


def _assert(cond, msg):
    if cond:
        print(f"[PASS] {msg}", flush=True)
    else:
        print(f"[FAIL] {msg}", flush=True)
        raise SystemExit(1)


TENANT = "t1"  # 测试租户（与 step2 创建工单保持一致）


# 1. 健康检查
r = client.get("/health")
_assert(r.status_code == 200 and r.json()["status"] == "ok", "health 200")

# 2. 创建工单
r = client.post("/api/v1/work-orders", json={"display_no": "WO-T1", "tenant_id": "t1", "doc_confidence": 0.95})
_assert(r.status_code == 200, f"create 200 (got {r.status_code})")
oid = r.json()["data"]["order_uuid"]
ver = r.json()["data"]["version"]

# 3. 状态机端点
r = client.get(f"/api/v1/work-orders/{oid}/state-machine")
_assert(r.status_code == 200, "state-machine 200")
sm = r.json()["data"]
_assert(sm["current_state"] == 2, f"current_state=2 (got {sm['current_state']})")
_assert(3 in sm["allowed_transitions"], "已分发(3)在 allowedTransitions")

# 4. 非法状态跳转 2->0 应 409（待分发不可回退）
r = client.patch(f"/api/v1/work-orders/{oid}/status", json={"target_state": 0, "version": ver})
_assert(r.status_code == 409 and r.json()["code"] == "BIZ_STATE_ILLEGAL", f"illegal 409 (got {r.status_code}/{r.json().get('code')})")

# 5. 合法跳转 2->3（待分发→已分发）
r = client.patch(f"/api/v1/work-orders/{oid}/status", json={"target_state": 3, "version": ver})
_assert(r.status_code == 200 and r.json()["data"]["current_state"] == 3, f"legal 200 (got {r.status_code})")
ver = r.json()["data"]["version"]

# 6. 报工正常（累计 50）
r = client.post(f"/api/v1/work-orders/{oid}/reports", json={"process_id": "p_01", "completed_qty": 50, "operator_id": "u1", "version": ver})
_assert(r.status_code == 200, f"report 200 (got {r.status_code})")
_assert(r.json()["data"]["merged_completed"] == 50, "merged 50")
_assert("withdrawable_until" in r.json()["data"], "含撤回窗口")

# 7. 超报拦截 50+60>100
r = client.post(f"/api/v1/work-orders/{oid}/reports", json={"process_id": "p_01", "completed_qty": 60, "operator_id": "u1", "version": ver})
_assert(r.status_code == 422 and r.json()["code"] == "BIZ_REPORT_OVERFLOW", f"overflow 422 (got {r.status_code})")

# 8. 报工撤回
rid = client.post(f"/api/v1/work-orders/{oid}/reports", json={"process_id": "p_02", "completed_qty": 10, "operator_id": "u1", "version": ver}).json()["data"]["report_id"]
r = client.request("DELETE", f"/api/v1/reports/{rid}", json={"operator_id": "u1"})
_assert(r.status_code == 200 and r.json()["data"]["status"] == "WITHDRAWN", f"withdraw 200 (got {r.status_code})")

# 9. 二维码批量 202 + Location
r = client.post("/api/v1/qrcode/batch", json={"order_ids": [oid]})
_assert(r.status_code == 202 and "Location" in r.headers, f"qrcode batch 202+Location (got {r.status_code})")
_assert(r.headers["Location"].endswith(r.json()["data"]["batchId"]), "Location 含 batchId")

# 10. 冲突裁决：工人 403，主管 200
r = client.post(f"/api/v1/conflicts/{oid}/resolve", json={"resolve_by": "keep_local", "operator_role": "CLERK"})
_assert(r.status_code == 403 and r.json()["code"] == "BIZ_PERMISSION_DENY", f"conflict clerk 403 (got {r.status_code})")
r = client.post(f"/api/v1/conflicts/{oid}/resolve", json={"resolve_by": "keep_local", "operator_role": "SUPERVISOR"})
_assert(r.status_code == 200 and r.json()["data"]["status"] == "RESOLVED", f"conflict supervisor 200 (got {r.status_code})")

# 11. OCR 上传 + 异步轮询（真实解析：样例工单 PDF → 字段抽取；进度条数据源）
from tests.sample_pdf import build_sample_wo_pdf
pdf_bytes = build_sample_wo_pdf()
r = client.post("/api/v1/files/upload", files={"file": ("wo.pdf", pdf_bytes, "application/pdf")})
_assert(r.status_code == 200 and r.json()["data"]["status"] == "QUEUED", "ocr upload 200")
tid = r.json()["data"]["taskId"]
# 后台线程异步解析：轮询直到终态（验证前端进度条数据可用，非同步阻塞）
_body = None
for _k in range(120):
    r = client.get(f"/api/v1/ocr/tasks/{tid}")
    _d = r.json()["data"]
    if _k == 0:
        _assert("stage" in _d and "progress" in _d and isinstance(_d.get("progress"), int),
                "ocr poll 含 stage/progress(int) 字段（进度条可用）")
    if _d["status"] in ("DONE", "FAILED"):
        _body = _d
        break
    time.sleep(0.3)
_assert(_body is not None, "ocr 任务到达终态(DONE/FAILED)")
_assert(_body["status"] == "DONE", f"ocr 异步解析 DONE (got {_body['status']})")
fmap = {f["key"]: f for f in _body["result"]["fields"]}
_assert(fmap.get("display_no", {}).get("value") == "WO-2026-00123", "ocr 解析出工单号")
_assert(fmap.get("plan_qty", {}).get("value", "").replace(",", "") == "1200", "ocr 解析出预计产量")
_assert(_body["result"]["docConfidence"] >= 0.7, "ocr 整单置信度达标")

# 11b. 非法/无文本层 PDF → FAILED 降级（M1-09 / M1-10，异步轮询验证）
r = client.post("/api/v1/files/upload", files={"file": ("bad.pdf", b"not-a-pdf", "application/pdf")})
tid_bad = r.json()["data"]["taskId"]
_bad = None
for _k in range(120):
    r = client.get(f"/api/v1/ocr/tasks/{tid_bad}")
    _d = r.json()["data"]
    if _d["status"] in ("DONE", "FAILED"):
        _bad = _d
        break
    time.sleep(0.3)
_assert(_bad is not None, "ocr 非法PDF 到达终态")
_assert(_bad["status"] == "FAILED", f"ocr 非法PDF FAILED 降级 (got {_bad['status']})")

# 12. 大屏 SSE（max_events=1 保证流必然结束，with 退出可正常关闭，不挂死）
with client.stream("GET", "/api/v1/bigscreen/metrics?lineId=l1&max_events=1") as resp:
    _assert(resp.status_code == 200, "bigscreen SSE 200")
    _assert("text/event-stream" in resp.headers.get("content-type", ""), "bigscreen content-type")
    _first = next(resp.iter_lines(), None)
    _assert(_first is not None and _first.startswith("event:"), "bigscreen 收到首条事件")

# 13. 工人待办
r = client.get("/api/v1/pending-tasks")
_assert(r.status_code == 200 and isinstance(r.json()["data"], list), "pending-tasks 200")

# ===== 以下为 TC-19~32（边界/异常/并发，对应 test_cases.json 与 docs/05） =====

# 14. 批量二维码超上限（TC-19）：101 个应被业务校验拦截为 BIZ_BATCH_OVERFLOW
_big_ids = [f"oid_{i}" for i in range(101)]
r = client.post("/api/v1/qrcode/batch", json={"order_ids": _big_ids})
_assert(r.status_code == 422 and r.json()["code"] == "BIZ_BATCH_OVERFLOW", f"batch overflow 422 (got {r.status_code}/{r.json().get('code')})")

# 15. 报工量为 0（TC-20，下界 ge=0）：200 且合并累计不变
r = client.post(f"/api/v1/work-orders/{oid}/reports", json={"process_id": "p_zero", "completed_qty": 0, "operator_id": "u1", "version": ver})
_assert(r.status_code == 200 and r.json()["data"]["merged_completed"] == 0, f"report qty=0 200 (got {r.status_code}, merged={r.json()['data'].get('merged_completed')})")

# 16. 报工恰好等于要求量（TC-21，边界不超报）：100 == required(100) 应放行
r = client.post(f"/api/v1/work-orders/{oid}/reports", json={"process_id": "p_edge", "completed_qty": 100, "operator_id": "u1", "version": ver})
_assert(r.status_code == 200 and r.json()["data"]["merged_completed"] == 100, f"report qty=100 200 (got {r.status_code}, merged={r.json()['data'].get('merged_completed')})")

# 17. 二维码 DPI 低于下限（TC-22，Pydantic ge=300）：422
r = client.post("/api/v1/qrcode/generate", json={"order_id": oid, "dpi": 200})
_assert(r.status_code == 422, f"qrcode dpi<300 422 (got {r.status_code})")

# 18. 二维码尺寸低于下限（TC-23，Pydantic ge=30）：422
r = client.post("/api/v1/qrcode/generate", json={"order_id": oid, "size_mm": 20})
_assert(r.status_code == 422, f"qrcode size<30 422 (got {r.status_code})")

# 19. 获取不存在工单（TC-24）：404 BIZ_ORDER_NOT_FOUND
r = client.get("/api/v1/work-orders/non-existent-uuid")
_assert(r.status_code == 404 and r.json()["code"] == "BIZ_ORDER_NOT_FOUND", f"get missing 404 (got {r.status_code}/{r.json().get('code')})")

# 20. 状态变更目标工单不存在（TC-25）：404 BIZ_ORDER_NOT_FOUND
r = client.patch("/api/v1/work-orders/non-existent-uuid/status", json={"target_state": 3, "version": 1})
_assert(r.status_code == 404 and r.json()["code"] == "BIZ_ORDER_NOT_FOUND", f"patch missing 404 (got {r.status_code}/{r.json().get('code')})")

# 21. 撤回不存在报工（TC-26）：404 BIZ_REPORT_NOT_FOUND
r = client.request("DELETE", "/api/v1/reports/non-existent-rid", json={"operator_id": "u1"})
_assert(r.status_code == 404 and r.json()["code"] == "BIZ_REPORT_NOT_FOUND", f"withdraw missing 404 (got {r.status_code}/{r.json().get('code')})")

# 22. 并发状态变更乐观锁冲突（TC-27）：新工单 2->3 成功后，用旧 version 重放 2->4（3 的合法转移）
#     触发版本不匹配 → 409 BIZ_VERSION_CONFLICT（而非被 STATE_ILLEGAL 抢先拦截）
_oid_lk = client.post("/api/v1/work-orders", json={"display_no": "WO-LK", "tenant_id": "t1"}).json()["data"]["order_uuid"]
r = client.patch(f"/api/v1/work-orders/{_oid_lk}/status", json={"target_state": 3, "version": 1})
_assert(r.status_code == 200, f"lock first 200 (got {r.status_code})")
r = client.patch(f"/api/v1/work-orders/{_oid_lk}/status", json={"target_state": 4, "version": 1})
_assert(r.status_code == 409 and r.json()["code"] == "BIZ_VERSION_CONFLICT", f"lock second 409 (got {r.status_code}/{r.json().get('code')})")

# 23. 撤回窗口过期（TC-28）：将 withdrawable_until 改到过去再撤回 → 409 BIZ_WITHDRAW_EXPIRED
_rid_exp = client.post(f"/api/v1/work-orders/{oid}/reports", json={"process_id": "p_exp", "completed_qty": 5, "operator_id": "u1", "version": ver}).json()["data"]["report_id"]
_sess = app.db.SessionLocal()
_rep = _sess.get(app.db.ReportORM, _rid_exp)
_rep.withdrawable_until = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
_sess.commit()
_sess.close()
r = client.request("DELETE", f"/api/v1/reports/{_rid_exp}", json={"operator_id": "u1"})
_assert(r.status_code == 409 and r.json()["code"] == "BIZ_WITHDRAW_EXPIRED", f"withdraw expired 409 (got {r.status_code}/{r.json().get('code')})")

# 24. 未知角色裁决（TC-29）：非 SUPERVISOR 一律 403 BIZ_PERMISSION_DENY
r = client.post(f"/api/v1/conflicts/{oid}/resolve", json={"resolve_by": "keep_server", "operator_role": "UNKNOWN"})
_assert(r.status_code == 403 and r.json()["code"] == "BIZ_PERMISSION_DENY", f"unknown role 403 (got {r.status_code}/{r.json().get('code')})")

# 25. 轮询不存在 OCR 任务（TC-30）：404 OCR_TASK_NOT_FOUND
r = client.get("/api/v1/ocr/tasks/non-existent-tid")
_assert(r.status_code == 404 and r.json()["code"] == "OCR_TASK_NOT_FOUND", f"ocr missing 404 (got {r.status_code}/{r.json().get('code')})")

# 26. 同工序多次报工在线合并无丢失（TC-31）：串行累加验证 BR-22（merged = Σ）
_oid_rep = client.post("/api/v1/work-orders", json={"display_no": "WO-RP", "tenant_id": "t1"}).json()["data"]["order_uuid"]
_merged = 0
for _i in range(5):
    r = client.post(f"/api/v1/work-orders/{_oid_rep}/reports", json={"process_id": "p_same", "completed_qty": 10, "operator_id": "u1", "version": 1})
    _assert(r.status_code == 200, f"merge step {_i} 200 (got {r.status_code})")
    _merged = r.json()["data"]["merged_completed"]
_assert(_merged == 50, f"merge no-loss = 50 (got {_merged})")

# 27. 并发状态竞态（TC-32）：2->3 成功后，旧 version 重放 2->1 必被拒（409）
_oid_race = client.post("/api/v1/work-orders", json={"display_no": "WO-RC", "tenant_id": "t1"}).json()["data"]["order_uuid"]
r = client.patch(f"/api/v1/work-orders/{_oid_race}/status", json={"target_state": 3, "version": 1})
_assert(r.status_code == 200, f"race first 200 (got {r.status_code})")
r = client.patch(f"/api/v1/work-orders/{_oid_race}/status", json={"target_state": 1, "version": 1})
_assert(r.status_code == 409, f"race second 409 (got {r.status_code})")

# 28. 图片 OCR 原文 → 字段解析（TC-34）：微信截图式单行文本，验证跨字段不误吞（M1-03 鲁棒性）
_img_text = "工单号：WO-2026-00999 客户：示例科技有限公司 预计产量：500 交货日期：2026-08-01"
r = client.post("/api/v1/ocr/parse-text", json={"text": _img_text})
_d = r.json()["data"]
_assert(r.status_code == 200 and r.json()["code"] == "0", f"parse-text 200 (got {r.status_code})")
_fm = {f["key"]: f for f in _d["fields"]}
_assert(_fm.get("display_no", {}).get("value") == "WO-2026-00999", "parse-text 解析出工单号(不含后续字段)")
_assert(_fm.get("customer", {}).get("value") == "示例科技有限公司", "parse-text 客户名不跨字段误吞")
_assert(_fm.get("plan_qty", {}).get("value") == "500", "parse-text 解析出预计产量")
_assert(_d.get("engine") == "external-text", "parse-text 标记外部文本引擎(方案 A)")

# 29. 空文本 → 400 OCR_TEXT_EMPTY（TC-35）
r = client.post("/api/v1/ocr/parse-text", json={"text": "   "})
_assert(r.status_code == 400 and r.json()["code"] == "OCR_TEXT_EMPTY", f"parse-text 空文本 400 (got {r.status_code}/{r.json().get('code')})")

# 30. 重复工单号拦截（TC-38）：同一 display_no 第二次创建 → 409 BIZ_WORK_ORDER_DUPLICATE
_dup_no = "WO-DUP-001"
_r_first = client.post("/api/v1/work-orders", json={"display_no": _dup_no, "tenant_id": "t1"})
_assert(_r_first.status_code == 200, f"dup first 200 (got {_r_first.status_code})")
_r_dup = client.post("/api/v1/work-orders", json={"display_no": _dup_no, "tenant_id": "t1"})
_assert(_r_dup.status_code == 409 and _r_dup.json()["code"] == "BIZ_WORK_ORDER_DUPLICATE",
        f"dup 409 (got {_r_dup.status_code}/{_r_dup.json().get('code')})")

# 31. OCR 异步进度条（TC-39）：用 PIL 生成图片上传，轮询验证 stage/progress 实时推进
def _make_png():
    from PIL import Image
    import io as _io
    _buf = _io.BytesIO()
    Image.new("RGB", (240, 80), "white").save(_buf, "PNG")
    return _buf.getvalue()


_png = _make_png()
_r39 = client.post("/api/v1/files/upload", files={"file": ("t.png", _png, "image/png")})
_assert(_r39.status_code == 200, f"tc39 upload 200 (got {_r39.status_code})")
_tid39 = _r39.json()["data"]["taskId"]
_seen_stages = set()
_seen_progress = set()
_final39 = None
for _i in range(120):
    _r = client.get(f"/api/v1/ocr/tasks/{_tid39}")
    _d = _r.json()["data"]
    _seen_stages.add(_d.get("stage"))
    if isinstance(_d.get("progress"), int):
        _seen_progress.add(_d["progress"])
    if _d["status"] in ("DONE", "FAILED"):
        _final39 = _d
        break
    time.sleep(0.3)
_assert(_final39 is not None, "TC-39 OCR 任务到达终态")
_assert(_final39["progress"] == 100, f"TC-39 终态进度=100 (got {_final39['progress']})")
_assert(len(_seen_stages) >= 2, f"TC-39 阶段有推进 (seen={sorted(_seen_stages)})")

# 32. 工单人微信推送骨架（TC-40）：assignee_openid 落库 + worker 注册/配额 + 关闭时 no-op
# 32a. 创建带 assignee_openid 的工单（推送默认关闭，应 no-op 不报错、字段正确回写）
_r40 = client.post("/api/v1/work-orders", json={"display_no": "WO-2026-PUSH01",
                                                "tenant_id": TENANT, "assignee_openid": "o_test_openid_123"})
_assert(_r40.status_code == 200, f"tc40 建单 200 (got {_r40.status_code})")
_assert(_r40.json()["data"].get("assignee_openid") == "o_test_openid_123", "tc40 assignee_openid 回写")
_r40_uuid = _r40.json()["data"]["order_uuid"]
_r40_ver = _r40.json()["data"]["version"]
# 32b. 工人注册（小程序上报 openid + 授权余量）
_r40w = client.post("/api/v1/workers", json={"openid": "o_test_openid_123",
                                             "name": "张三", "tenant_id": TENANT, "subscribe_quota": 3})
_assert(_r40w.status_code == 200 and _r40w.json()["data"]["subscribe_quota"] == 3, "tc40 worker 注册+余量")
# 32c. 配额查询
_r40q = client.get("/api/v1/workers/by-openid/o_test_openid_123")
_assert(_r40q.status_code == 200 and _r40q.json()["data"]["subscribe_quota"] == 3, "tc40 配额查询")
# 32d. 未注册工人查询 → 404
_r40nf = client.get("/api/v1/workers/by-openid/nope")
_assert(_r40nf.status_code == 404, f"tc40 未注册 404 (got {_r40nf.status_code})")
# 32e. 推送开关关闭时后台线程 no-op：触发已分发状态变更（PATCH /status，带 version）不应抛错
_r40d = client.patch(f"/api/v1/work-orders/{_r40_uuid}/status",
                     json={"target_state": 3, "version": _r40_ver, "operator_id": "op1"})
_assert(_r40d.status_code == 200, f"tc40 分发变更 200 (got {_r40d.status_code})")

_t("ALL_DB_SMOKE_PASS")
# 清理临时库：先释放连接池（Windows 文件锁），失败仅告警不阻碍结果
try:
    app.db.engine.dispose()
    os.unlink(_tmp.name)
except OSError as exc:  # noqa: BLE001
    print(f"[WARN] 清理临时库失败（可忽略）: {exc}", flush=True)
