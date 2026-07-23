# 04 API 接口契约文档

> 对应 V5.0 §25 接口契约。实现文件：`backend/app/api/*.py`。前缀统一 `API_V1_PREFIX=/api/v1`。
>
> 已落地 **14 个接口**（与设计稿 §25 全量清单一致）。每个接口标注：方法、路径、角色、幂等、请求、响应、错误码。

## 通用约定（§25.1）

- **版本化**：路径前缀 `/api/v1`；生产支持 `X-API-Version` / `Client-Version`（§24.5）。
- **租户**：`X-Tenant-Id` 头（BR-16，生产拦截器注入）。
- **幂等**：创建工单 / 文件上传(MD5) / 二维码生成(工单互斥) / 冲突裁决(防竞态) 需 `Idempotency-Key`。
- **乐观锁**：`PATCH /status`、报工 `version` 字段。
- **响应体**：成功 `{"code":"0","data":...,"traceId":...}`；错误 `{"code":"BIZ_*","message":...,"traceId":...}`。

## 接口清单（已落地 14 个）

| # | 方法 | 路径 | 角色 | 幂等 | 说明 |
| --- | --- | --- | --- | --- | --- |
| 1 | POST | `/work-orders` | 文员 | Idempotency-Key | 创建工单 |
| 2 | GET | `/work-orders/{order_id}` | 全部 | — | 获取工单 |
| 3 | GET | `/work-orders/{order_id}/state-machine` | 全部 | — | 状态机端点（按钮数据源） |
| 4 | PATCH | `/work-orders/{order_id}/status` | 文员/主管 | — | 状态变更（校验 + 乐观锁） |
| 5 | POST | `/work-orders/{order_id}/reports` | 工人 | — | 报工（超报拦截+合并+撤回窗口） |
| 6 | DELETE | `/reports/{report_id}` | 工人 | — | 报工撤回 |
| 7 | POST | `/qrcode/generate` | 文员 | 工单互斥 | 单张二维码生成 |
| 8 | POST | `/qrcode/batch` | 文员 | — | 批量生成（≤100，202+Location） |
| 9 | POST | `/qrcode/print/confirm` | 文员 | — | 打印确认 3a→3b |
| 10 | POST | `/files/upload` | 文员 | MD5 | OCR 上传（202+taskId） |
| 11 | GET | `/ocr/tasks/{task_id}` | 文员 | — | OCR 轮询 |
| 12 | POST | `/conflicts/{conflict_id}/resolve` | 主管 | 防竞态 | 冲突裁决 |
| 13 | GET | `/bigscreen/metrics` | 大屏 | — | SSE 指标流 |
| 14 | GET | `/pending-tasks` | 工人 | — | 工人待办 |
| — | GET | `/health` | — | — | 健康检查（部署探针） |

## 关键接口契约

### 1. POST /work-orders
请求：`WorkOrderCreate{display_no, tenant_id, doc_confidence?, need_review?}`
响应：`200 {"code":"0","data":WorkOrder,"traceId":...}`
错误：`500 BIZ_CREATE_FAILED`

### 3. GET /work-orders/{id}/state-machine
响应：`200 {current_state, allowed_transitions:[int], visible_buttons:[str], version}`
错误：`404 BIZ_ORDER_NOT_FOUND`

### 4. PATCH /work-orders/{id}/status
请求：`{target_state:int, version:int, reason?:str}`
- 校验 `target_state ∈ allowedTransitions`，否则 `409 BIZ_STATE_ILLEGAL`（D3）
- `version` 不一致 → `409 BIZ_VERSION_CONFLICT`（BR-18）
响应：`200 {current_state, version}`
错误：`404 BIZ_ORDER_NOT_FOUND` / `409 BIZ_STATE_ILLEGAL` / `409 BIZ_VERSION_CONFLICT` / `500 BIZ_PATCH_FAILED`

### 5. POST /work-orders/{id}/reports
请求：`ReportRequest{process_id, completed_qty(≥0), operator_id, client_created_at?, version}`
- `version` 不一致 → `409 BIZ_VERSION_CONFLICT`
- 超报（累计 > 要求量）→ `422 BIZ_REPORT_OVERFLOW`（BR-05）
响应：`200 ReportOut{report_id, order_id, merged_completed, need_review, withdrawable_until}`
错误：`404 BIZ_ORDER_NOT_FOUND` / `409 BIZ_VERSION_CONFLICT` / `422 BIZ_REPORT_OVERFLOW` / `500 BIZ_REPORT_FAILED`

### 6. DELETE /reports/{id}
请求体：`{operator_id:str}`
- 已撤回 → `200 {status:"WITHDRAWN"}`
- 超窗口 → `409 BIZ_WITHDRAW_EXPIRED`
响应：`200 {status:"WITHDRAWN"}`
错误：`404 BIZ_REPORT_NOT_FOUND` / `409 BIZ_WITHDRAW_EXPIRED`

### 7. POST /qrcode/generate
请求：`QrcodeGenerateRequest{order_id, process_id?, dpi(≥300), size_mm(≥30)}`
响应：`200 {print_task_id, state:"3a_已生成"}`
错误：`500 BIZ_QRCODE_FAILED`

### 8. POST /qrcode/batch
请求：`QrcodeBatchRequest{order_ids:[str](≤100), dpi(≥300)}`
- 超 100 → `422 BIZ_BATCH_OVERFLOW`
响应：`202 {"code":"0","data":{"batchId":...}}`，头 `Location:/api/v1/qrcode/batch/tasks/{batchId}`

### 9. POST /qrcode/print/confirm
请求：`{print_task_id:str}`
响应：`200 {state:"3b_已打印确认"}`
错误：`404 BIZ_QRCODE_NOT_FOUND`

### 10. POST /files/upload
请求：multipart `file` + `template_id?`
响应：`202 {taskId, status:"QUEUED", pollUrl:"/api/v1/ocr/tasks/{taskId}"}`
错误：`500 OCR_TASK_FAILED`

### 11. GET /ocr/tasks/{task_id}
响应：`200 {taskId, status:"DONE", result:{fields:[{key,value,confidence}], docConfidence, needReview}}`（演示固定 DONE）
错误：`404 OCR_TASK_NOT_FOUND`

### 12. POST /conflicts/{id}/resolve
请求：`ConflictResolveRequest{resolve_by:"keep_local"|"keep_server"|"merge", resolved_qty?, operator_role:"SUPERVISOR"|"CLERK"}`
- `operator_role != SUPERVISOR` → `403 BIZ_PERMISSION_DENY`（D7）
响应：`200 {status:"RESOLVED", strategy:"LOCAL_PENDING"|"SERVER"|"MERGED"}`
错误：`403 BIZ_PERMISSION_DENY` / `500 BIZ_CONFLICT_FAILED`

### 13. GET /bigscreen/metrics
查询：`lineId?`, `max_events?`（默认无限；测试/调试传 `max_events=1` 优雅结束）
响应：`text/event-stream`，事件 `event: metrics\ndata: {...}\n\n`（每 3s 一帧）
- 检测 `http.disconnect` 退出；`server_ts` 供新鲜度计算（BR-19/D5）

### 14. GET /pending-tasks
查询：`operator_id?`, `state?`
响应：`200 {data:[{order_uuid, process_code, required_qty, completed_qty, remaining_qty}]}`

## 契约一致性检查

- OpenAPI 定义见 `backend/openapi.yaml`（14 接口，可导入 Apifox/Postman）。
- 后端 `responses.ok/fail` 与路由返回结构一致（成功 `code=0`，错误 `BIZ_*`）。
- 状态机校验、乐观锁、超报拦截、权限控制均与 §25 / §4.9 一致。
- **契约测试建议**：用 `jsonschema` 校验响应体（已在骨架环境安装），纳入 CI 门禁（见 [06_CICD与部署文档](./06_CICD与部署文档.md)）。
