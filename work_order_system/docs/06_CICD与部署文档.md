# 06 CI/CD 与部署文档

> 对应 V5.0 §28 任务拆解、§26 数据模型、运维规范。覆盖：持续集成（CI）策略、部署方式、数据库迁移、环境变量。
>
> 工程文档导航见 [00_文档导航](./00_文档导航.md)；测试案例库见 [05_测试案例库文档](./05_测试案例库文档.md)。

## 1. CI/CD（持续集成）

本项目的持续集成目标是：**每次代码变更都能自动验证可运行性与契约一致性，且不允许破坏性变更合入主干。**

### 1.1 触发与阻断策略

- **触发时机**：每次向 `main`/`develop` 的 **Merge（合并）** 后，由 Jenkins 或 GitLab CI 自动触发流水线。
- **冒烟测试门禁**：流水线自动运行**测试集合**：
  - `backend/test_smoke_db.py`（覆盖 14 接口主链路 + 状态机 + 超报/撤回/裁决/权限/SSE 等 43 步断言，TC-01~TC-32）；
  - `backend/test_concurrency.py`（真实多线程并发，验证 SQL 层原子乐观锁 / 报工在线合并不变量，9 项断言）。
- **阻断规则**：**如果任一测试集合未通过，自动阻断合并请求（MR）**，不允许合入；需修复后重新触发，直至全绿。
- **附加门禁（建议）**：
  - `py_compile` 编译检查（所有 `app/*.py`、`app/api/*.py`）。
  - `import app.main` 导入自测。
  - `jsonschema` 契约校验（响应体结构与 `openapi.yaml` 对齐）。
  - 关键函数回归（创建/状态机/报工/撤回/裁决）。
  - **前端门禁**（2026-07-23 新增）：`frontend` 需先 `npm install && npm run build`（构建校验，CI 阻断）；`npm test`（vitest 单元测试，建议 CI）；前后端 E2E 冒烟为手动/可选。

> 一句话记录（便于评审/交接）：
> **每次代码合并（Merge）后，Jenkins/GitLab CI 自动运行冒烟测试集合。如果未通过，阻断合并请求（MR）。**

### 1.2 流水线示例（GitLab CI `.gitlab-ci.yml` 参考）

```yaml
stages:
  - test
  - build
  - deploy

smoke:
  stage: test
  image: python:3.11
  script:
    - cd work_order_system/backend
    - pip install -r requirements.txt
    - python -m py_compile app/*.py app/api/*.py
    - python -c "import app.main"
    - python -u test_smoke_db.py          # 未通过则非零退出 → 阻断 MR
    - python -u test_concurrency.py       # 真实并发断言，未通过同样阻断 MR
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'

frontend-build:
  stage: test
  image: node:20
  script:
    - cd work_order_system/frontend
    - npm ci
    - npm run build                       # tsc 类型检查 + vite build，未通过 → 阻断 MR
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'

frontend-test:
  stage: test
  image: node:20
  script:
    - cd work_order_system/frontend
    - npm ci
    - npm test -- --run                   # vitest 单元测试（建议 CI）
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
```

> 说明：测试使用 SQLite 内存/临时库（`WORK_ORDER_DB_URL` 覆盖），无需外部依赖即可运行；演示 `bigscreen` SSE 用 `max_events=1` 保证流可终止，不会挂死流水线。

### 1.3 质量红线

| 检查 | 不通过后果 |
| --- | --- |
| 冒烟测试集合 | 阻断 MR |
| 编译/导入 | 阻断 MR |
| 响应契约（jsonschema） | 阻断 MR（建议） |
| 覆盖率（建议） | 低于阈值告警 |

## 2. 部署

### 2.1 后端（FastAPI + Uvicorn）

```bash
cd work_order_system/backend
pip install -r requirements.txt
# 生产：由迁移工具建表（Alembic），而非 init_db()
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- `main.py` 启动事件 `init_db()` 仅骨架便利；**生产改用 Alembic 迁移**（§26 / §28 M0）。
- 健康检查：`GET /health`（部署探针）。
- CORS：骨架放开 `*`；生产按 §13 收紧为指定源。

### 2.2 前端（Vite + React + TS）

```bash
cd work_order_system/frontend
npm install
npm run dev        # 开发，代理 /api/v1 → :8000
npm run build      # 产物到 dist/（含 tsc 类型检查）
npm test           # vitest 单元测试（2026-07-23 新增，见 §05 §7）
```

### 2.3 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WORK_ORDER_DB_URL` | `sqlite:///./work_order_system.db` | 数据库地址；生产指向 PG/MySQL |
| `SERVICE_HOST` | `0.0.0.0` | 监听地址 |
| `SERVICE_PORT` | `8000` | 监听端口 |
| `API_V1_PREFIX` | `/api/v1` | 接口前缀 |
| `WX_APPID` | 空（必须从环境变量注入） | 小程序 AppID（非敏感）；由 backend/.env 或系统环境变量提供，详见 [07](./07_小程序占位符与配置处理手册.md) |
| `WX_APPSECRET` | 空（必须从环境变量注入） | 小程序密钥（【机密】）；仅 backend/.env / 系统环境变量，绝不入库、不写前端 |
| `WX_SUBSCRIBE_TEMPLATE_ID` | 空（必须从环境变量注入） | 订阅消息模板 ID（非敏感）；须与小程序端 TEMPLATE_ID、后端 WX_TEMPLATE_FIELDS 一致 |

### 2.4 数据库迁移（生产）

- 当前：`init_db()` 直接 `create_all`（骨架）。
- 生产：引入 Alembic，`env.py` 绑定 `engine`；`alembic revision --autogenerate` 生成迁移；`alembic upgrade head` 执行。
- 乐观锁列 `version`、索引 `tenant_id` 必须保留（§26）。
- 切换 PG/MySQL：`String(36)` 主键在 PG 用 `CHAR(36)` 或 `UUID`；`check_same_thread` 仅 SQLite 需要。

## 3. 运维要点（§20 / §14）

- 日志：`logging` + `TimedRotatingFileHandler` 午夜轮转，`backupCount=30`（见 `config.py` `LOG_BACKUP_DAYS`）。
- 监控（建议）：Prometheus + Grafana + Loki 全链路；大屏新鲜度告警（`BIGSCREEN_STALE_WARN_SECONDS=30`）。
- 备份：DB 每日备份并验证可恢复（§20）。
