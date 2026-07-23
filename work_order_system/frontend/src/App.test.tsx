import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from './App'

/**
 * 主流程冒烟（docs/05 §7-B）：mock fetch，验证
 * 创建工单 → 查询状态机 → 提交报工 三连击在 UI 上的正确渲染与错误兜底。
 * 状态标签来自 App.tsx 的 STATE_LABELS：2=待分发，3=已分发。
 */
function makeFetchSeq(responses: object[]) {
  const seq = responses.map((r) => ({ json: async () => r }))
  let i = 0
  return vi.fn(async () => seq[i++ % seq.length])
}

describe('App 主流程（创建 → 状态机 → 报工）', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  it('依次点击：创建工单 / 查询状态机 / 提交报工', async () => {
    const fetchMock = makeFetchSeq([
      { code: '0', data: { order_uuid: 'ORD-1', version: 1 }, traceId: 'a' },
      {
        code: '0',
        data: { current_state: 2, allowed_transitions: [3], visible_buttons: ['GEN_QRCODE'], version: 1 },
        traceId: 'b',
      },
      { code: '0', data: { merged_completed: 20, withdrawable_until: '2026-01-01T00:00:00Z' }, traceId: 'c' },
    ])
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    // 1. 创建工单
    fireEvent.click(screen.getByText('创建'))
    await waitFor(() => expect(screen.getByText(/已创建工单 ORD-1/)).toBeInTheDocument())

    // 2. 查询状态机
    fireEvent.click(screen.getByText('查询状态机'))
    await waitFor(() => expect(screen.getByText(/当前态：待分发/)).toBeInTheDocument())
    expect(screen.getByText(/可选跳转：已分发/)).toBeInTheDocument()

    // 3. 提交报工
    fireEvent.click(screen.getByText('提交报工'))
    await waitFor(() => expect(screen.getByText(/报工成功，累计 20/)).toBeInTheDocument())
  })

  it('创建失败时在 msg 区展示后端错误信息', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        json: async () => ({ code: 'BIZ_FAIL', message: '创建失败', traceId: 'e' }),
      })),
    )
    render(<App />)
    fireEvent.click(screen.getByText('创建'))
    await waitFor(() => expect(screen.getByText(/创建失败/)).toBeInTheDocument())
  })
})
