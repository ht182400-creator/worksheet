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
| 11 | GET | `/ocr/tasks/{task_id}` | 文员 | — | OCR 轮询（后端原生 OCR / PDF 文本层，方案 A） |
| 12 | POST | `/conflicts/{conflict_id}/resolve` | 主管 | 防竞态 | 冲突裁决 |
| 15 | POST | `/ocr/parse-text` | 文员 | — | 外部已识别原文 → 字段解析（可选；主路径见 #10/#11 后端原生 OCR，方案 A） |
| 13 | GET | `/bigscreen/metrics` | 大屏 | — | SSE 指标流 |
| 14 | GET | `/pending-tasks` | 工人 | — | 工人待办 |
| — | GET | `/health` | — | — | 健康检查（部署探针） |

## 关键接口契约

### 1. POST /work-orders
请求：`WorkOrderCreate{display_no, tenant_id, doc_confidence?, need_review?}`
响应：`200 {"code":"0","data":WorkOrder,"traceId":...}`
错误：`500 BIZ_CREATE_FAILED` / `409 BIZ_WORK_ORDER_DUPLICATE`（display_no 已存在，禁止重复入库回填）

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
- 实现：读取文件字节落盘 `data/uploads/{taskId}{ext}`，入队 QUEUED（**真实解析在轮询时执行**，非演示桩）。
错误：`400 OCR_EMPTY_FILE` / `500 OCR_SAVE_FAILED`

### 11. GET /ocr/tasks/{task_id}
响应：`200 {taskId, status, result}`
- `status`：`QUEUED`（解析中）/ `DONE`（识别成功）/ `FAILED`（无文本层或解析失败，M1-09 降级）
- `result`（DONE）：
  ```json
  {
    "fields": [{"key":"display_no","label":"工单号","value":"WO-2026-00123","confidence":0.9,"valueInferred":false}],
    "docConfidence": 0.9,
    "needReview": true,
    "forceManual": false,
    "rawTextLen": 154
  }
  ```
  - `fields`：M1-03 全部结构化字段（工单号/客户料号/产品编码/预计产量/PO号/客户/交货日期/批次数量/下单日期/计划日期），每项含中文 `label`、字段级 `confidence` 与 `valueInferred` 标志。
  - `valueInferred`（M1-03 鲁棒性）：`true` 表示该字段值并非由 OCR 标签匹配得到，而是标签被误识时由"值模式兜底"（工单号/产品编码/客户代码/日期等可读值）推断所得，置信度（0.5）低于标签命中（0.9），前端应明确标注"推断值"并保留人工复核。
  - 置信度分级（M1-11）：`docConfidence ≥ 0.95` 自动通过（needReview=false）；`0.70–0.95` 需审核；`< 0.70` 强制人工重录（forceManual=true）。
  - 解析链路：pypdf 提取文本层 / 后端 Tesseract OCR → `_field_parser` 规则化抽取（标签:值 + OCR 纠错库归一化 + 小字号标签误识时的值模式兜底）。
- `result`（FAILED）：`{error:"明确错误提示", fields:[], docConfidence:0.0, needReview:true, forceManual:true}`
错误：`404 OCR_TASK_NOT_FOUND`

### 15. POST /ocr/parse-text（外部原文 → 字段解析，可选 / M1-01）

> 背景（方案 A：后端原生 Tesseract）：图片（微信截图、拍照件）与 PDF **统一**走
> `POST /files/upload` + 轮询 `GET /ocr/tasks/{id}`，由**后端原生 Tesseract-OCR**
> 完成识别（含预处理：放大→灰度→自动对比度→二值化，识别率优于浏览器 tesseract.js）。
> `/ocr/parse-text` 仅作为**可选接口**存在：当调用方已自行完成 OCR、只需后端做字段解析回填时，
> 直接提交原文即可，实现"两者都要"（原文 + 工单字段）。

请求：`{text: string}`（调用方已识别的纯文本，UTF-8）
响应：`200 {code:"0", data:{rawText, fields, docConfidence, needReview, forceManual, engine:"external-text"}}`
- `rawText`：`text` 原样回显，供前端"OCR 原文"折叠区展示。
- `fields` / `docConfidence` / `needReview` / `forceManual`：与 §11 DONE 完全一致（复用同一 `_field_parser`）。
- 解析链路：外部原文 → 后端 `_field_parser` 规则化抽取。
- 主识别路径（图片/PDF）：见 §16 环境依赖 + 接口 #10/#11；`engine` 取值 `server-tesseract`（后端 OCR）/ `pdf-text-layer`（PDF 文本层）。
错误：`400 OCR_TEXT_EMPTY`（原文为空）/ `500 OCR_PARSE_ERROR`

## 16. 方案 A 环境依赖（原生 Tesseract-OCR 安装，M1-01/M1-09）
方案 A 依赖**系统层**原生 Tesseract-OCR 二进制（非浏览器），需额外安装：

1. 安装原生 Tesseract-OCR（含中文包 chi_sim）：
   - 方式一（推荐，需管理员）：`winget install -e --id UB-Mannheim.TesseractOCR`
     安装时在组件里勾选 **Additional script data** + **Chinese (chi_sim)** 语言包。
   - 方式二（手动）：从 https://github.com/UB-Mannheim/tesseract/wiki 下载安装包，
     安装目录默认 `C:\Program Files\Tesseract-OCR`，并勾选中文语言包。
   - 若安装时未勾选中文：手动下载 `chi_sim.traineddata`
     （https://github.com/tesseract-ocr/tessdata）放到
     `C:\Program Files\Tesseract-OCR\tessdata\`。
2. 验证：`tesseract --version` 可运行且 `tessdata` 目录含 `chi_sim.traineddata`。
3. Python 依赖（已加入 requirements.txt）：`pytesseract`、`Pillow`、`PyMuPDF`。
4. 装好后在后端目录执行 `pip install -r requirements.txt` 即可生效，无需改代码。

未安装时，上传图片 / PDF 会返回 FAILED，错误信息提示"未安装 / 未找到原生 Tesseract-OCR 二进制"。

### 本机已就绪记录（2026-07-24 实测）
- 原生 Tesseract 二进制已装于 `D:\Program Files (x86)\Tesseract-OCR\tesseract.exe`
  （**未加入 PATH**，故 `_ocr.py` 通过 `TESSERACT_CMD_CANDIDATES` 候选路径 / 环境变量
  `TESSERACT_CMD` 自动定位，无需手动加 PATH）。
- 该安装默认只含 `eng` + `osd`，**中文包 `chi_sim.traineddata` 已补全**：
  从 npm 包 `@tesseract.js-data/chi_sim` 取 `4.0.0/chi_sim.traineddata.gz` 解压后放入
  `D:\Program Files (x86)\Tesseract-OCR\tessdata\`（与原生引擎兼容，OCR 实测可正确识别中文）。
- Python 依赖：`pytesseract` / `Pillow` 已装，`PyMuPDF` 已 `pip install` 补齐。
- 端到端验证：用微软雅黑生成"工单号 WO-2026-00999 / 客户：示例科技有限公司 / 计划数量 500"
  测试图，原生引擎正确识别全部中英文（识别率显著高于原浏览器 tesseract.js 方案）。
- 注意：本机实测二进制版本为 `tesseract 4.00.00alpha`，与 chi_sim（LSTM）兼容，PSM=6/oem=1 正常。

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
