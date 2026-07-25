# 工单系统（Work Order System）V0.1.0

> 基于《需求与架构文档 V5.0》§25–§28 落地的**首个可发布版本**。设计稿与实现规格位于 `docs/`。
> 二维码工单管理平台：文员建单 → 工人扫码/查待办 → 报工 → 主管裁决冲突 → OCR 自动结构化录入 → 大屏看板；含 React 后台、Vite 工人端、微信小程序三端 + FastAPI 后端。

## 技术栈
- 后端：FastAPI + SQLAlchemy + SQLite（可切 PG/MySQL，环境变量 `WORK_ORDER_DB_URL`），后端原生 Tesseract OCR（方案 A）
- 前端：React + TypeScript + Vite（后台 + 工人端）
- 小程序：微信小程序（扫码报工 + 订阅消息推送）
- 文档/部署：详见 [`docs/06_CICD与部署文档.md`](./docs/06_CICD与部署文档.md)

## 目录结构

```
work_order_system/
├── README.md
├── start_dev.bat            # 一键重启前后端（先按端口杀旧进程，再起后端+前端）
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── main.py          # 入口：CORS + 路由挂载 + /health
│   │   ├── db.py            # SQLAlchemy 数据库层（6 张核心表）
│   │   ├── models.py        # Pydantic 模型（含手机号校验 PHONE_RE）
│   │   ├── state_machine.py # BR-18 状态转移矩阵（权威源）
│   │   ├── store.py         # DB 存储层 + BusinessError
│   │   ├── config.py        # 常量（禁止硬编码）/ 错误码
│   │   ├── responses.py     # 统一响应 / BIZ_* 错误体
│   │   ├── logger.py        # 日志（控制台 + TimedRotatingFileHandler）
│   │   ├── _ocr.py          # 后端原生 Tesseract OCR（图片/PDF 预处理 + 识别，方案 A）
│   │   ├── _field_parser.py # 工单字段规则化解析（M1-03 + 值模式兜底）
│   │   ├── _pdf_extract.py  # PDF 文本层提取 / PyMuPDF 渲染
│   │   ├── _wx_auth.py      # 微信 code2session：wx.login code→openid（不回传 session_key）
│   │   ├── _wechat_push.py  # 订阅消息推送（工单状态变更推给工人）
│   │   ├── seed_workers.py  # 测试数据脚本（oSeed_* 记录）
│   │   ├── tests/           # 单测（test_smoke_db / test_field_parser / test_worker_admin + test_cases.json）
│   │   └── api/
│   │       ├── work_orders.py   # 工单创建/获取/状态机/状态变更
│   │       ├── reports.py       # 报工/撤回
│   │       ├── qrcode.py        # 二维码生成/批量/打印确认/图片直显
│   │       ├── files.py         # OCR 上传/轮询
│   │       ├── conflicts.py     # 冲突裁决
│   │       ├── bigscreen.py     # 大屏 SSE
│   │       ├── worker.py        # 工人待办/注册/管理面板（浏览·查·改·删·搜索）
│   │       └── wechat.py        # 微信小程序对接（code2session / 订阅配置）
│   ├── requirements.txt
│   ├── openapi.yaml         # §25 接口契约导出（可导入 Apifox/Postman）
│   └── data/uploads/        # OCR 上传目录（运行时生成，gitignored）
├── frontend/                # React (Vite + TS)
│   └── src/
│       ├── api/client.ts    # api-client（tenant_id / Idempotency-Key / 错误映射）
│       ├── App.tsx          # 创建工单 / 状态机面板 / 报工 / 工人管理 CRUD
│       └── styles.css
├── miniprogram/             # 微信小程序（扫码报工 + 订阅消息推送）
└── docs/                    # 文档中心（00~08 + PowerShell 踩坑速查）
```

## 后端运行

```bash
cd backend
pip install -r requirements.txt
# 方式一
python app/main.py
# 方式二（推荐）
uvicorn app.main:app --host 0.0.0.0 --port 8000
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

## 一键启动（推荐）

双击 `start_dev.bat`：先按端口（8000/5173）杀掉旧前后端进程，再启动后端（轮询 `/health` 就绪）与前台（Vite），两个服务各开独立日志窗口。

## 已落地能力（V0.1.0，对应 V5.0 §25–§28）

| 能力 | 端点 / 模块 | 来源 |
|------|------------|------|
| 工单全生命周期（创建/获取/状态机驱动/状态变更，乐观锁 409） | `POST /work-orders`、`GET /work-orders/{id}`、`GET .../state-machine`、`PATCH .../status` | BR-18 / D3 |
| 报工：超报拦截(422)+在线合并+撤回窗口 | `POST/DELETE /work-orders/{id}/reports`、`DELETE /reports/{id}` | BR-05/BR-22/M5-12 |
| 二维码：生成/批量(202+Location)/打印确认/图片直显 | `POST /qrcode/*`、`GET /qrcode/img` | BR-15/BR-21/M3-02 |
| OCR 自动录入：后端原生 Tesseract（方案 A）+ 字段规则化解析 + 值模式兜底 | `POST /files/upload`、`GET /ocr/tasks/{id}`、`POST /ocr/parse-text`、`_ocr.py`/`_field_parser.py` | BR-17 / M1-03 |
| 冲突裁决（防竞态） | `POST /conflicts/{id}/resolve` | BR-19 |
| 大屏看板（SSE 实时指标） | `GET /bigscreen/metrics` | BR-20 |
| 工人管理面板 CRUD（浏览/查/改[含手机号 11 位校验]/删） | `GET /workers`、`GET /workers/search`、`GET /workers/by-openid/{id}`、`PATCH /workers/{id}`、`DELETE /workers/{id}` | §新增 |
| 工人端待办（按 openid 过滤"我的待办"） | `GET /pending-tasks` | §新增 |
| 微信小程序对接（code2session 安全登录 + 订阅消息配置/推送） | `POST /wechat/code2session`、`GET /wechat/subscribe-config`、`_wx_auth.py`/`_wechat_push.py` | §新增 |
| 统一错误体 + 日志 | `responses.py` / `logger.py` | §25.1 |

> 已落地 **24 个 `/api/v1` 业务接口** + `/health` 探针，详见 [`docs/04_API接口契约文档.md`](./docs/04_API接口契约文档.md)。

## 工程文档

实现层配套文档见 [`docs/00_文档导航.md`](./docs/00_文档导航.md)：数据库结构、数据结构、流程结构、API 契约、测试案例库、CI/CD 与部署、小程序手册、发布说明。

Windows/PowerShell 运维踩坑（重启后端、查端口、读日志等）见 [`docs/00_PowerShell_踩坑速查.md`](../docs/00_PowerShell_踩坑速查.md)。

## 测试

```bash
cd backend
python -m pytest            # 或 python -m unittest discover
```

覆盖：14 接口主链路冒烟（`test_smoke_db`）+ OCR 字段解析（`test_field_parser`）+ 工人管理面板（`test_worker_admin`），共 **TC-01~TC-60 全部 PASS**，与 `tests/test_cases.json` 一一对应。详见 [`docs/05_测试案例库文档.md`](./docs/05_测试案例库文档.md)。

## 注意事项

- 存储已落地 **SQLAlchemy + SQLite**（默认 `work_order_system.db`）；生产按 §26 用 Alembic 迁移并切换到 PG/MySQL（环境变量 `WORK_ORDER_DB_URL`）。
- CORS 当前放开（`*`），生产按 §13 安全设计收紧。
- 鉴权仅预留 `Bearer` + `X-Tenant-Id` 头，未实现 JWT 校验逻辑；`DELETE /workers` 等管理接口当前无鉴权（演示用），生产需加操作员权限校验。
- 多租户 `tenant_id` 未强制注入，生产按 §13 拦截器实现。

## 版本历史

- **V0.1.0**（2026-07-26）：首个可发布版本。整合二维码工单全生命周期、OCR 自动录入（方案 A）、工人管理面板 CRUD、微信小程序 code2session 登录与订阅消息推送、大屏看板；24 个 `/api/v1` 接口；TC-01~TC-60 全绿。详见 [`docs/08_发布说明.md`](./docs/08_发布说明.md)。
