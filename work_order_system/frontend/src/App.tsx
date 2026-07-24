import { useRef, useState } from 'react'
import { api, ApiError, OcrResult } from './api/client'

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

// OCR 置信度分级阈值（与后端 M1-11 一致）
const OCR_AUTO_PASS = 0.95
const OCR_MANUAL_REVIEW = 0.70
const OCR_ENGINE_SERVER = 'server-tesseract' // 后端原生识别（方案 A）

// 工单 10 字段定义（M1-03，与后端 _field_parser.FIELD_SPECS 保持一致）。
// 纯扫描件无文本层时后端返回 fields=[]，前端用此常量渲染人工录入空表单。
const OCR_FIELDS: { key: string; label: string }[] = [
  { key: 'display_no', label: '工单号' },
  { key: 'customer_part_no', label: '客户料号' },
  { key: 'product_code', label: '产品编码' },
  { key: 'plan_qty', label: '预计产量' },
  { key: 'po_no', label: 'PO号' },
  { key: 'customer', label: '客户' },
  { key: 'delivery_date', label: '交货日期' },
  { key: 'batch_qty', label: '批次数量' },
  { key: 'order_date', label: '下单日期' },
  { key: 'plan_date', label: '计划日期' },
]

/** 依据置信度返回字段高亮样式（M1-06 高亮 + M1-11 分级）。 */
function confStyle(conf: number): React.CSSProperties {
  if (conf >= OCR_AUTO_PASS) return { borderColor: '#2e7d32', background: '#e8f5e9' }
  if (conf >= OCR_MANUAL_REVIEW) return { borderColor: '#ef6c00', background: '#fff3e0' }
  return { borderColor: '#c62828', background: '#ffebee' }
}

export default function App() {
  const [orderId, setOrderId] = useState('')
  const [displayNo, setDisplayNo] = useState('WO-2026-001')
  const [sm, setSm] = useState<StateMachine | null>(null)
  const [msg, setMsg] = useState('')
  const [reportQty, setReportQty] = useState('100')
  const [reportResult, setReportResult] = useState('')

  // ===== OCR 智能识别状态（M1-01/M1-03/M1-06/M1-07）=====
  const [ocrFile, setOcrFile] = useState<File | null>(null)
  const [ocrLoading, setOcrLoading] = useState(false)
  const [ocrError, setOcrError] = useState('')
  const [ocrResult, setOcrResult] = useState<OcrResult | null>(null)
  const [editFields, setEditFields] = useState<Record<string, string>>({})
  const pollRef = useRef<number | null>(null)

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

  /** 上传并识别（方案 A：图片/PDF 统一走后端原生 Tesseract OCR，M1-01/M1-09）。
   * 后端先解析（PDF 有文本层直抽、无文本层或图片走 OCR），前端仅轮询结果。
   */
  async function uploadAndRecognize() {
    if (!ocrFile) {
      setOcrError('请先选择工单文件（PDF 或图片截图）')
      return
    }
    setOcrError('')
    setOcrResult(null)
    setOcrLoading(true)
    const idem = crypto.randomUUID() // 演示幂等键；生产应为 MD5(文件内容)
    try {
      const up = await api.uploadOcrFile(ocrFile, idem)
      // 后端原生 OCR 较慢（图片/扫描件逐页识别），放宽超时：最多 50 次、间隔 600ms
      const poll = async (attempt: number) => {
        if (attempt > 50) {
          setOcrError('识别超时，请稍后重试')
          setOcrLoading(false)
          return
        }
        const task = await api.getOcrTask(up.taskId)
        if (task.status === 'QUEUED') {
          pollRef.current = window.setTimeout(() => poll(attempt + 1), 600)
          return
        }
        if (task.status === 'FAILED') {
          setOcrError(task.result?.error || '解析失败')
          setOcrResult(task.result)
          setOcrLoading(false)
          return
        }
        // DONE：回填字段 + 自动填充工单号输入框（M1-07 人工修正前置）
        const filled: Record<string, string> = {}
        task.result.fields.forEach((f) => { filled[f.key] = f.value })
        setEditFields(filled)
        setOcrResult(task.result)
        if (filled['display_no']) setDisplayNo(filled['display_no'])
        setOcrLoading(false)
      }
      poll(1)
    } catch (e) {
      setOcrError((e as ApiError).message)
      setOcrLoading(false)
    }
  }

  /** 人工修正字段（M1-07）后确认入库并回填（M1-03 回填目标）。 */
  async function confirmAndBackfill() {
    if (!ocrResult) return
    setMsg('')
    const display_no = editFields['display_no'] || displayNo
    try {
      const wo = await api.createWorkOrder(
        {
          display_no,
          tenant_id: 'demo-tenant',
          doc_confidence: ocrResult.docConfidence,
          need_review: ocrResult.needReview,
        },
        crypto.randomUUID(),
      )
      setOrderId(wo.order_uuid)
      setDisplayNo(display_no)
      if (pollRef.current) window.clearTimeout(pollRef.current)
      setMsg(`OCR 识别结果已入库：${wo.order_uuid}（置信度 ${ocrResult.docConfidence}）`)
    } catch (e) {
      setMsg((e as ApiError).message)
    }
  }

  return (
    <div className="app">
      <h1>工单智能识别与扫码分发系统</h1>

      {/* ===== 0. OCR 智能识别（PDF 上传 → 真实解析 → 预览/修正 → 回填） ===== */}
      <section>
        <h2>0. 上传工单文件（PDF / 图片截图，OCR 智能识别）</h2>
        <input
          type="file"
          accept="application/pdf,.pdf,image/png,image/jpeg,image/webp,image/gif"
          onChange={(e) => setOcrFile(e.target.files ? e.target.files[0] : null)}
        />
        <button onClick={uploadAndRecognize} disabled={ocrLoading}>
          {ocrLoading ? '识别中…' : '上传并识别'}
        </button>
        {ocrError && !ocrResult?.forceManual && <p className="err">{ocrError}</p>}

        {ocrResult && (ocrResult.fields.length > 0 || ocrResult.forceManual) && (
          <div className="ocr-panel">
            {ocrResult.rawText && (
              <details className="ocr-raw">
                <summary>
                  OCR 原文（{ocrResult.engine === OCR_ENGINE_SERVER ? '后端识别'
                    : ocrResult.engine === 'pdf-text-layer' ? 'PDF 文本层' : '识别'}）
                </summary>
                <pre>{ocrResult.rawText}</pre>
              </details>
            )}
            {ocrResult.forceManual ? (
                <div className="ocr-banner">
                  无法自动识别（{ocrResult.error || '识别结果为空或置信度过低'}）。
                  请人工录入以下字段后确认入库。
                </div>
            ) : (
              <p>
                整单置信度：
                <b style={{ color: ocrResult.docConfidence >= OCR_AUTO_PASS ? '#2e7d32'
                  : ocrResult.docConfidence >= OCR_MANUAL_REVIEW ? '#ef6c00' : '#c62828' }}>
                  {ocrResult.docConfidence}
                </b>
                {ocrResult.forceManual && <span className="badge red">需强制人工重录</span>}
                {ocrResult.needReview && !ocrResult.forceManual && <span className="badge orange">需人工审核</span>}
                {!ocrResult.needReview && <span className="badge green">自动通过</span>}
              </p>
            )}
            <table className="ocr-table">
              <thead>
                <tr><th>字段</th><th>识别值（可修正）</th><th>置信度</th></tr>
              </thead>
              <tbody>
                {(ocrResult.fields.length > 0
                  ? ocrResult.fields
                  : OCR_FIELDS.map((f) => ({
                      key: f.key,
                      label: f.label,
                      value: editFields[f.key] || '',
                      confidence: 0,
                    }))
                ).map((f) => (
                  <tr key={f.key}>
                    <td>{f.label}</td>
                    <td>
                      <input
                        value={editFields[f.key] ?? ''}
                        onChange={(e) =>
                          setEditFields((prev) => ({ ...prev, [f.key]: e.target.value }))
                        }
                        style={{ ...confStyle(f.confidence), width: '100%' }}
                      />
                    </td>
                    <td style={confStyle(f.confidence)}>{f.confidence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button onClick={confirmAndBackfill}>确认入库并回填</button>
          </div>
        )}
      </section>

      <section>
        <h2>1. 创建工单</h2>
        <input
          value={displayNo}
          onChange={(e) => setDisplayNo(e.target.value)}
          placeholder="业务单号（OCR 识别后自动回填）"
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
