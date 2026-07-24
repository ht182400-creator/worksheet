/**
 * API 客户端封装（对应 V5.0 §27.6 共享模块）：
 * 统一注入 X-Tenant-Id / Idempotency-Key / 错误体映射到用户文案。
 */
const TENANT_ID = 'demo-tenant'
const BASE = '/api/v1'

export interface ApiError {
  code: string
  message: string
  traceId?: string
}

// 默认 any：骨架阶段未对每个响应建严格类型；生产应补 §25 schema 类型
async function request<T = any>(
  path: string,
  options: RequestInit & { idempotencyKey?: string } = {},
): Promise<T> {
  const { idempotencyKey, headers, ...rest } = options
  const hdrs: Record<string, string> = {
    'X-Tenant-Id': TENANT_ID,
    ...(headers as Record<string, string>),
  }
  // FormData（文件上传）不设 Content-Type，交由浏览器自动带 multipart boundary
  if (!(rest.body instanceof FormData)) {
    hdrs['Content-Type'] = 'application/json'
  }
  if (idempotencyKey) hdrs['Idempotency-Key'] = idempotencyKey

  const res = await fetch(`${BASE}${path}`, { ...rest, headers: hdrs })
  const body = await res.json()
  // 统一错误体（§25.1 {code,message,traceId}）
  if (body.code !== '0') {
    throw { code: body.code, message: body.message, traceId: body.traceId } as ApiError
  }
  return body.data as T
}

export const api = {
  getStateMachine: (orderId: string) =>
    request(`/work-orders/${orderId}/state-machine`),
  getWorkOrder: (orderId: string) => request(`/work-orders/${orderId}`),
  createWorkOrder: (payload: object, idem: string) =>
    request(`/work-orders`, {
      method: 'POST',
      body: JSON.stringify(payload),
      idempotencyKey: idem,
    }),
  submitReport: (orderId: string, payload: object, idem: string) =>
    request(`/work-orders/${orderId}/reports`, {
      method: 'POST',
      body: JSON.stringify(payload),
      idempotencyKey: idem,
    }),
  // OCR 文件上传（异步入队，§25.2.1 BR-17）：multipart + Idempotency-Key（MD5）
  uploadOcrFile: (file: File, idem: string) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<{ taskId: string; status: string; pollUrl: string }>(
      `/files/upload`,
      { method: 'POST', body: fd, idempotencyKey: idem },
    )
  },
  // 轮询 OCR 任务（实时进度 + 真实解析结果，PDF 文本层 / 后端原生 OCR 两条路径共用）
  getOcrTask: (taskId: string) =>
    request<{
      taskId: string
      status: string
      stage?: string      // 当前阶段（QUEUED/TEXT_LAYER/RENDER_OCR/PARSE_FIELDS/DONE/FAILED）
      progress?: number   // 进度百分比 0-100（前端进度条）
      message?: string    // 实时阶段说明（如"正在识别第 2/5 页"）
      result: OcrResult
    }>(`/ocr/tasks/${taskId}`),
  // 浏览器端 OCR（tesseract.js）识别出的原文 → 后端字段解析（图片路径）
  parseOcrText: (text: string) =>
    request<OcrResult>(`/ocr/parse-text`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
}

// OCR 解析结果（PDF 文本层与图片 OCR 两条路径共用）
export interface OcrResult {
  fields: { key: string; label: string; value: string; confidence: number }[]
  docConfidence: number
  needReview: boolean
  forceManual: boolean
  error?: string
  rawTextLen?: number
  rawText?: string
  engine?: string
}

// ApiError 已由上方 `export interface ApiError` 导出，无需重复 export type
