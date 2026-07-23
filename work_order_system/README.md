# 工单智能识别与扫码分发系统 — 最小可运行骨架

基于《需求与架构文档 V5.0》§25–§28 落地的 Phase 1 脚手架。设计稿与实现规格位于 `../docs/`。

## 目录结构

```
work_order_system/
├── backend/                 # FastAPI 后端
│   ├── openapi.yaml         # §25 接口契约导出（可直接导入 Apifox/Postman）
│   ├── requirements.txt
│   ├── logs/                # 运行期日志（TimedRotatingFileHandler）
│   ├── tests/
│   │   └── test_cases.json  # 数据驱动测试用例库（与 docs/05 对应）
│   ├── test_smoke_db.py     # 端到端冒烟测试（23 步全绿）
│   └── app/
│       ├── config.py        # 常量（禁止硬编码）
│       ├── logger.py        # 日志（控制台 + 文件轮转）
│       ├── db.py            # SQLAlchemy 数据库层（§26，6 张核心表）
│       ├── state_machine.py # BR-18 状态转移矩阵（权威源）
│       ├── models.py        # Pydantic 模型（§25/§26）
│       ├── store.py         # DB 存储层（SQLAlchemy，§26）+ BusinessError
│       ├── responses.py     # 统一响应 / BIZ_* 错误体
│       ├── main.py          # 入口（CORS + 路由挂载 + /health）
│       └── api/             # 路由：work_orders / reports / qrcode / files / conflicts / bigscreen / worker
└── frontend/                # React (Vite + TS)
    └── src/
        ├── api/client.ts    # api-client（tenant_id / Idempotency-Key / 错误映射）
        └── App.tsx          # 创建工单 / 状态机面板 / 报工
```

## 后端运行

```bash
cd backend
pip install -r requirements.txt
# 方式一
python app/main.py
# 方式二
uvicorn app.main:app --reload --port 8000
```

- Swagger 文档：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/health`

## 前端运行

```bash
cd frontend
npm install
npm run dev
# http://localhost:5173（Vite 已将 /api 代理到 :8000）
```

## 已落地能力（对应 V5.0）

| 能力 | 端点 / 模块 | 来源 |
|------|------------|------|
| 状态机校验（非法跳转→409） | `PATCH /work-orders/{id}/status` | BR-18 / D3 |
| 前端按钮驱动 | `GET /work-orders/{id}/state-machine` | §4.3 / §27.6 |
| 报工：超报拦截(422)+在线合并+撤回窗口 | `POST/DELETE reports` | BR-05/BR-22/M5-12 |
| 二维码：生成/批量(202+Location)/打印确认 | `POST /qrcode/*` | BR-15/BR-21/M3-02 |
| OCR：上传(202+taskId)+轮询 | `POST /files/upload` + `GET /ocr/tasks` | BR-17 |
| 统一错误体 + 日志 | `responses.py` / `logger.py` | §25.1 |

## 工程文档

实现层配套文档见 [`docs/`](./docs/00_文档导航.md)：数据库结构、数据结构、流程结构、API 契约、测试案例库、CI/CD 与部署。

## 注意事项

- 存储已落地 **SQLAlchemy + SQLite**（默认 `work_order_system.db`）；生产按 §26 用 Alembic 迁移并切换到 PG/MySQL（环境变量 `WORK_ORDER_DB_URL`）。
- CORS 当前放开（`*`），生产按 §13 安全设计收紧。
- 鉴权仅预留 `Bearer` + `X-Tenant-Id` 头，未实现 JWT 校验逻辑，Phase 1 联调用 `demo-tenant`。

## 下一步

1. 用 `openapi.yaml` 在 Apifox/Postman 生成 Mock 或契约测试。
2. 按 §28 任务表（T01–T14）逐模块替换内存实现为数据库 + 真实业务。
3. 前端补充 §27 四端组件树（工人 App 离线缓存 / 大屏 SSE 降级等）。
