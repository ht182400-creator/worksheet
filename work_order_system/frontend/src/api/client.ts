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
    'Content-Type': 'application/json',
    'X-Tenant-Id': TENANT_ID,
    ...(headers as Record<string, string>),
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
}

// ApiError 已由上方 `export interface ApiError` 导出，无需重复 export type
