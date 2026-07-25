# Changelog

本仓库所有重要变更均记录于此文件。格式参考 [Keep a Changelog](https://keepachangelog.com/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-07-26

首个带 git tag 的对外发布版本，基于《需求与架构文档 V5.0》§25–§28 落地。

### Added（新增）
- **工单全生命周期**：创建 / 获取 / 状态机驱动按钮 / 状态变更（乐观锁，冲突返回 `409 BIZ_VERSION_CONFLICT`）。
- **报工与撤回**：超报拦截（`422`）、在线合并、撤回窗口。
- **二维码**：单张 / 批量生成（≤100，返回 `202 + Location`）、打印确认（`3a→3b`）、图片直显 `GET /qrcode/img`（`t=qr` 扫码报工深链 / `t=bar` 工单号 Code128，PNG 按参数哈希缓存）。
- **OCR 自动录入（方案 A，后端原生 Tesseract）**：
  - 图片 / PDF 统一走后端原生 OCR，识别前预处理（放大→灰度→自动对比度→二值化）提升中文召回；
  - 字段解析按标签匹配 + 值模式兜底（修复 M1-03 的 `0.0 / 0.09` 置信度问题）；
  - PDF 先试文本层（快/准），无文本层用 PyMuPDF 逐页 300 DPI 渲染 OCR。
- **冲突裁决**：主管防竞态裁决并发报工。
- **大屏看板**：SSE 实时指标流 `GET /bigscreen/metrics`。
- **工人管理面板（后台 CRUD）**：浏览所有记录、查单条、改（姓名 / 手机号 11 位校验 / 订阅余量）、删。
- **工人端待办**：按 `assignee_openid` 过滤"我的待办" `GET /pending-tasks`。
- **微信小程序对接**：`POST /wechat/code2session` 安全登录（不回传 `session_key`）、订阅消息配置 `GET /wechat/subscribe-config` 与推送（工单状态变更推送给工人）。
- **健康检查探针** `GET /health`（部署就绪检查）。
- **一键启动脚本** `start_dev.bat`：先按端口精确杀旧进程，再启动后端（uvicorn :8000）与前台（vite :5173），后端就绪后自动拉起前端。
- **运维速查** `docs/00_PowerShell_踩坑速查.md`。

### Changed（变更）
- 接口契约总数校正为 **24 个 `/api/v1` 业务接口 + `/health`**，并重排编号（原有两个 `#15`、`#13/#14` 错位，现按模块连续 1–24）。
- 文档导航（00）、测试案例库（05）同步更新：接口数 16→24，用例区间 TC-01~TC-41 → TC-01~TC-60。
- 前端 `api/client.ts` 类型收敛，新增工人管理面板 `frontend/src/App.tsx`。
- README 重写为 V0.1.0 对齐版（目录 / 能力矩阵 / 一键启动 / 版本历史）。

### Fixed（修复）
- OCR 整单置信度 `0.09`：Tesseract 在相邻中文间插空格导致标签失配 → 解析层去除 CJK↔CJK / CJK↔冒号 间水平空格，合成表单 `0.09→0.81`。
- OCR 整单置信度 `0.0`：小字号中文标签被系统性误识 → 引入值模式兜底抽取（按可读值还原关键字段，置信度 `0.5` 并标 `valueInferred`），真实样例恢复工单号 / 产品编码 / 客户 3 关键字段。
- `.gitignore` 路径需带 `work_order_system/` 前缀才对子目录生效，已修正并排除 `smoke_*.txt` 调试产物与小程序私有配置。

### 关键新增文件（相对 v0.0.x）
- `backend/app/api/wechat.py`、`backend/app/_wx_auth.py`、`backend/app/_wechat_push.py`（微信对接）
- `backend/app/api/worker.py` 工人管理 CRUD 全量（含 `DELETE`）
- `backend/seed_workers.py`（测试数据）、`backend/tests/test_worker_admin.py`
- `frontend/src/App.tsx` 工人管理面板、`frontend/src/api/client.ts` 类型收敛
- `start_dev.bat`（一键重启）、`docs/08_发布说明.md`、`docs/00_PowerShell_踩踩速查.md`

### 部署要点
- **OCR 依赖**：后端原生 Tesseract 需预装（`D:\Program Files (x86)\Tesseract-OCR`），`_ocr.py` 经 `TESSERACT_CMD_CANDIDATES` / 环境变量 `TESSERACT_CMD` 自动定位；中文包 `chi_sim.traineddata` 必备。
- **微信小程序**：需配置 AppID 与订阅消息模板，`.env` 含密钥，禁止入库。
- **数据库**：默认 SQLite（运行时生成 `work_order_system.db`，gitignored）；生产切 PG/MySQL 用 `WORK_ORDER_DB_URL` + Alembic。
- **前端生产构建**：`cd frontend && npm run build`，产物自行托管；本版以 dev 模式交付为主。

### Known Limitations（已知限制）
- 管理接口（如 `DELETE /workers`）当前无鉴权（演示用），生产需加操作员权限校验。
- 多租户 `tenant_id` 未强制注入；JWT 鉴权未实现（仅预留头）。
- 大屏 SSE 降级、工人 App 离线缓存等 §27 高级项待后续版本。

## [0.0.3] - 2026-07（内部里程碑）
内部开发里程碑（v0.0.1~v0.0.3），未对外发布，仅作为 V0.1.0 基线。

[0.1.0]: https://github.com/ht182400-creator/worksheet/releases/tag/v0.1.0
[0.0.3]: https://github.com/ht182400-creator/worksheet/releases/tag/v0.0.3
