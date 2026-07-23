import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api } from './client'

/**
 * 验证 api client 的契约映射（docs/04 统一响应体 + docs/05 §7-B）：
 * 1) 成功体 code==='0' 返回 data；
 * 2) 错误体 code!=='0' 抛出 ApiError（保留 code/message）；
 * 3) 请求注入 X-Tenant-Id / Idempotency-Key / Content-Type 头。
 */
describe('api client 请求封装', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  it('成功体 code==="0" 返回 data', async () => {
    const body = { code: '0', data: { hello: 'world' }, traceId: 't1' }
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => body })))
    const data = await api.getWorkOrder('o1')
    expect(data).toEqual({ hello: 'world' })
  })

  it('错误体 code!=="0" 抛出 ApiError 并保留 code/message', async () => {
    const body = { code: 'ORDER_NOT_FOUND', message: 'not found', traceId: 't2' }
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => body })))
    await expect(api.getWorkOrder('o1')).rejects.toMatchObject({
      code: 'ORDER_NOT_FOUND',
      message: 'not found',
    })
  })

  it('创建工单注入 X-Tenant-Id 与 Idempotency-Key 头', async () => {
    const body = { code: '0', data: { order_uuid: 'o1' }, traceId: 't3' }
    const fetchMock = vi.fn(async () => ({ json: async () => body }))
    vi.stubGlobal('fetch', fetchMock)
    await api.createWorkOrder({ display_no: 'WO-1', tenant_id: 'demo-tenant' }, 'idem-1')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    const headers = init.headers as Record<string, string>
    expect(headers['X-Tenant-Id']).toBe('demo-tenant')
    expect(headers['Idempotency-Key']).toBe('idem-1')
    expect(headers['Content-Type']).toBe('application/json')
  })
})
