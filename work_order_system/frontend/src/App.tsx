import { useState } from 'react'
import { api, ApiError } from './api/client'

interface StateMachine {
  current_state: number
  allowed_transitions: number[]
  visible_buttons: string[]
  version: number
}

// 状态枚举标签（演示用；生产应由 state-machine 接口的 visibleButtons 驱动渲染）
const STATE_LABELS: Record<number, string> = {
  0: '待识别',
  1: '待审核',
  2: '待分发',
  3: '已分发',
  4: '生产中',
  5: '已完成',
  6: '已关闭',
}

export default function App() {
  const [orderId, setOrderId] = useState('')
  const [displayNo, setDisplayNo] = useState('WO-2026-001')
  const [sm, setSm] = useState<StateMachine | null>(null)
  const [msg, setMsg] = useState('')
  const [reportQty, setReportQty] = useState('100')
  const [reportResult, setReportResult] = useState('')

  /** 创建工单（幂等键防重试）。 */
  async function createOrder() {
    setMsg('')
    try {
      const wo = await api.createWorkOrder(
        { display_no: displayNo, tenant_id: 'demo-tenant' },
        crypto.randomUUID(),
      )
      setOrderId(wo.order_uuid)
      setMsg(`已创建工单 ${wo.order_uuid}`)
    } catch (e) {
      setMsg((e as ApiError).message)
    }
  }

  /** 查询状态机（按钮由后端 allowedTransitions 驱动，禁止硬编码，§4.3/§27.6）。 */
  async function loadSm() {
    if (!orderId) {
      setMsg('请先创建或填入工单ID')
      return
    }
    try {
      const data = await api.getStateMachine(orderId)
      setSm(data)
      setMsg('')
    } catch (e) {
      setMsg((e as ApiError).message)
    }
  }

  /** 提交报工（超报拦截 + 在线合并，§25.2.2 / BR-05 / BR-22）。 */
  async function submitReport() {
    if (!orderId) {
      setMsg('请先创建工单')
      return
    }
    try {
      const r = await api.submitReport(
        orderId,
        {
          process_id: 'p_02',
          completed_qty: Number(reportQty),
          operator_id: 'u_emp_01',
          version: 1,
        },
        crypto.randomUUID(),
      )
      setReportResult(
        `报工成功，累计 ${r.merged_completed}，撤回截止 ${r.withdrawable_until}`,
      )
    } catch (e) {
      setReportResult((e as ApiError).message)
    }
  }

  return (
    <div className="app">
      <h1>工单智能识别与扫码分发系统</h1>

      <section>
        <h2>1. 创建工单</h2>
        <input
          value={displayNo}
          onChange={(e) => setDisplayNo(e.target.value)}
          placeholder="业务单号"
        />
        <button onClick={createOrder}>创建</button>
      </section>

      <section>
        <h2>2. 状态机（按钮由后端驱动）</h2>
        <input
          value={orderId}
          onChange={(e) => setOrderId(e.target.value)}
          placeholder="工单ID（创建后自动填充）"
        />
        <button onClick={loadSm}>查询状态机</button>
        {sm && (
          <div className="sm">
            <p>当前态：{STATE_LABELS[sm.current_state]} ({sm.current_state})</p>
            <p>可选跳转：{sm.allowed_transitions.map((s) => STATE_LABELS[s]).join('、') || '无（终态）'}</p>
            <p>可见按钮：{sm.visible_buttons.join('、') || '无'}</p>
            <p>version：{sm.version}</p>
          </div>
        )}
      </section>

      <section>
        <h2>3. 报工提交（超报拦截 + 自动合并）</h2>
        <input
          value={reportQty}
          onChange={(e) => setReportQty(e.target.value)}
          placeholder="完成数量"
        />
        <button onClick={submitReport}>提交报工</button>
        {reportResult && <p className="result">{reportResult}</p>}
      </section>

      {msg && <div className="msg">{msg}</div>}
    </div>
  )
}
