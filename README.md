# Worksheet 工作区

> 个人工作区仓库，当前主项目为 **工单系统（Work Order System）**——基于二维码的工单管理平台。

## 仓库组成

| 目录 / 文件 | 说明 |
|------------|------|
| [`work_order_system/`](./work_order_system) | **主项目**：二维码工单系统（FastAPI 后端 + React/Vite 前后台 + 微信小程序）。详见其 [`README.md`](./work_order_system/README.md) |
| `work_order_system/docs/` | 文档中心（00 文档导航、API 契约、测试案例库、CI/CD 部署、小程序手册、发布说明、PowerShell 踩坑速查） |
| `Data/` | OCR 测试样本（gitignored） |
| `_sync_via_api.py` | GitHub 同步工具（仅在 `github.com` 不可达时走 Git Data REST API 同步，正常用 `git push`） |

## 主项目速览（工单系统 V0.1.0）

二维码工单管理平台：文员建单 → 工人扫码 / 查待办 → 报工 → 主管裁决冲突 → OCR 自动结构化录入 → 大屏看板；含 React 后台、Vite 工人端、微信小程序三端 + FastAPI 后端。

- **已落地 24 个 `/api/v1` 业务接口** + `/health` 探针
- **测试**：TC-01~TC-60 全部 PASS（冒烟 + OCR 字段解析 + 工人管理面板）
- **一键启动**：双击 `work_order_system/start_dev.bat` 重启前后端

### 技术栈
- 后端：FastAPI + SQLAlchemy + SQLite（可切 PG/MySQL）/ 后端原生 Tesseract OCR
- 前端：React + TypeScript + Vite
- 小程序：微信小程序（扫码报工 + 订阅消息推送）

### 快速开始
```bash
# 后端
cd work_order_system/backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000   # http://localhost:8000/docs

# 前端
cd work_order_system/frontend
npm install
npm run dev                                        # http://localhost:5173

# 或一键启动（推荐）：双击 work_order_system/start_dev.bat
```

完整能力矩阵、接口契约、部署与已知限制见 [`work_order_system/README.md`](./work_order_system/README.md) 与 [`work_order_system/docs/`](./work_order_system/docs)。

## 版本与发布
- 工单系统当前版本 **V0.1.0**（2026-07-26），git tag `v0.1.0`，[GitHub Release](https://github.com/ht182400-creator/worksheet/releases/tag/v0.1.0)。
- 更新日志见 [`work_order_system/CHANGELOG.md`](./work_order_system/CHANGELOG.md)。
