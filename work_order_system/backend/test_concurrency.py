"""真实并发测试（docs/05 §4 建议③）：验证 SQL 层原子乐观锁在多线程下真正生效。

与 `test_smoke_db.py` 的串行重放互补——那里用串行重放稳定覆盖分支与错误码，
这里用**真实多线程**同时提交同一旧版本，断言：
  - 状态变更（TC-27）：恰好 1 个成功，其余被 BIZ_VERSION_CONFLICT 拒绝；
  - 工序报工（TC-31）：恰好 1 个成功，其余被 BIZ_VERSION_CONFLICT 拒绝（无静默丢更新）。

之所以直接打 `store` 层而非 HTTP：FastAPI 同步路由经线程池执行，本质上仍是多线程，
但经过 TestClient/anyio 易触发 SQLite 文件锁抖动；直接多线程调用 store 既是真并发，
又能稳定断言 SQL 原子 `UPDATE ... WHERE version=?` 的不变量。

测试库启用 WAL + busy_timeout，避免并发写出现 `database is locked` 而误判。
"""
import os
import glob
import tempfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from sqlalchemy import event, text

# 必须在 import app.db 之前注入临时库地址（引擎在 import 时按环境变量创建）
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["WORK_ORDER_DB_URL"] = f"sqlite:///{_tmp.name.replace(chr(92), '/')}"

import app.db  # noqa: E402
import app.store as store  # noqa: E402

# 并发写稳健性：每个新连接设置 busy_timeout（connect 事件拿到的是原始 DBAPI 连接，直接用字符串）
event.listen(
    app.db.engine,
    "connect",
    lambda conn, _: conn.execute("PRAGMA busy_timeout=10000"),
)
with app.db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as _c:
    _c.execute(text("PRAGMA journal_mode=WAL"))
app.db.init_db()


def _assert(cond, msg):
    """断言：通过打印 PASS，失败打印 FAIL 并终止（退出码非零）。"""
    if cond:
        print(f"[PASS] {msg}", flush=True)
    else:
        print(f"[FAIL] {msg}", flush=True)
        raise SystemExit(1)


def _new_session():
    """每个线程独立会话（SQLAlchemy 会话非线程安全）。"""
    return app.db.SessionLocal()


def _status_worker(order_uuid, target, version):
    """并发状态变更 worker：返回 WorkOrder 或 None（None=被乐观锁拒绝）。"""
    s = _new_session()
    try:
        return store.apply_status_change(s, order_uuid, target, version)
    finally:
        s.close()


def _report_worker(order_uuid, payload):
    """并发报工 worker：成功返回 ReportOut，被乐观锁拒绝返回 BusinessError。"""
    s = _new_session()
    try:
        return store.submit_report(s, order_uuid, payload)
    except store.BusinessError as exc:
        return exc
    finally:
        s.close()


def _run_concurrent(worker, args_list, n):
    """线程池并发执行，收集结果列表。"""
    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(worker, *args_list))


# ===== TC-27 真实并发：状态变更乐观锁 =====
print("== TC-27 真实并发状态变更 ==")
_wo = store.create_work_order(_new_session(), SimpleNamespace(display_no="WO-C1", tenant_id="t1", doc_confidence=0.9, need_review=False))
_order_uuid = _wo.order_uuid
_version = _wo.version  # 初始 1
_n = 5
_results = _run_concurrent(
    _status_worker,
    [[_order_uuid] * _n, [3] * _n, [_version] * _n],
    _n,
)
_wins = [r for r in _results if r is not None]
_loses = [r for r in _results if r is None]
_assert(len(_wins) == 1, f"恰好 1 个成功 (got {len(_wins)})")
_assert(len(_loses) == _n - 1, f"其余 {_n - 1} 个被乐观锁拒绝 (got {len(_loses)})")
_assert(_wins[0].version == _version + 1, f"胜利者版本已自增为 {_version + 1} (got {_wins[0].version})")

# ===== TC-31 真实并发：工序报工在线合并无静默丢更新 =====
print("== TC-31 真实并发报工合并 ==")
_wo2 = store.create_work_order(_new_session(), SimpleNamespace(display_no="WO-C2", tenant_id="t1", doc_confidence=0.9, need_review=False))
_order_uuid2 = _wo2.order_uuid
# 串行先建工序进度（process version 1->2，completed=10），随后并发报工均读 version=2 竞争
_setup = store.submit_report(
    _new_session(),
    _order_uuid2,
    SimpleNamespace(process_id="p_conc", completed_qty=10, operator_id="u1", version=1, client_created_at=None),
)
_assert(_setup.merged_completed == 10, f"工序预建累计=10 (got {_setup.merged_completed})")

_n2 = 5
_payloads = [
    SimpleNamespace(process_id="p_conc", completed_qty=10, operator_id="u1", version=1, client_created_at=None)
    for _ in range(_n2)
]
_results2 = _run_concurrent(_report_worker, [[_order_uuid2] * _n2, _payloads], _n2)
_oks = [r for r in _results2 if not isinstance(r, store.BusinessError)]
_errs = [r for r in _results2 if isinstance(r, store.BusinessError)]
_assert(len(_oks) == 1, f"恰好 1 个报工成功 (got {len(_oks)})")
_assert(len(_errs) == _n2 - 1, f"其余 {_n2 - 1} 个被 BIZ_VERSION_CONFLICT 拒绝 (got {len(_errs)})")
_assert(all(e.code == "BIZ_VERSION_CONFLICT" for e in _errs), "被拒均为 BIZ_VERSION_CONFLICT")
# 胜利者累计 = 预建10 + 自身10 = 20；失败者被拒而非静默丢失（客户端重试）
_assert(_oks[0].merged_completed == 20, f"胜利者累计=20 无丢失 (got {_oks[0].merged_completed})")

print("ALL_CONCURRENCY_PASS")

# 清理：释放连接池后删除 WAL/SHM/DB 文件（Windows 文件锁失败仅告警）
try:
    app.db.engine.dispose()
    for _f in glob.glob(_tmp.name.replace(chr(92), "/") + "*"):
        try:
            os.unlink(_f)
        except OSError as _e:  # noqa: BLE001
            print(f"[WARN] 清理失败（可忽略）: {_e}", flush=True)
except OSError as _e:  # noqa: BLE001
    print(f"[WARN] 清理失败（可忽略）: {_e}", flush=True)
