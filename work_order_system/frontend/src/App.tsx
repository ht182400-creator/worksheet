import { useRef, useState } from 'react'
import { api, ApiError, OcrResult, WorkerResult } from './api/client'

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

/** OCR 阶段中文标签（进度条展示，M1-01 体验优化）。 */
function ocrStageLabel(stage?: string): string {
  switch (stage) {
    case 'QUEUED': return '已入队，准备解析…'
    case 'TEXT_LAYER': return '正在提取 PDF 文本层…'
    case 'RENDER_OCR': return '正在渲染并逐页识别…'
    case 'PARSE_FIELDS': return '正在解析工单字段…'
    case 'DONE': return '解析完成'
    case 'FAILED': return '解析失败'
    default: return '解析中…'
  }
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
  const [ocrProgress, setOcrProgress] = useState(0)  // 解析进度百分比（真实进度条）
  const [ocrStage, setOcrStage] = useState('')        // 当前阶段（进度条中文文案映射）
  const [editFields, setEditFields] = useState<Record<string, string>>({})
  const pollRef = useRef<number | null>(null)

  // ===== 工人管理面板（操作员后台浏览/查/改/删，§工人管理面板）=====
  const [workerQuery, setWorkerQuery] = useState('')       // 搜索框：手机号或 openid 后 N 位
  const [workerResults, setWorkerResults] = useState<WorkerResult[]>([])  // 搜索结果
  const [workerMsg, setWorkerMsg] = useState('')           // 面板内提示/错误
  const [nameDrafts, setNameDrafts] = useState<Record<string, string>>({})  // 各工人姓名草稿
  const [allWorkers, setAllWorkers] = useState<WorkerResult[]>([])        // 浏览：所有工人记录
  const [selectedWorker, setSelectedWorker] = useState<WorkerResult | null>(null)  // 选中的单条记录
  const [editName, setEditName] = useState('')             // 选中记录的姓名编辑草稿
  const [editPhone, setEditPhone] = useState('')           // 选中记录的手机号编辑草稿
  const [editQuota, setEditQuota] = useState('')           // 选中记录的订阅余量编辑草稿

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
      // 后端异步后台解析（真实进度回写），前端轮询展示进度条；最多 120 次、间隔 600ms（约 72s）
      const poll = async (attempt: number) => {
        if (attempt > 120) {
          setOcrError('识别超时，请稍后重试')
          setOcrLoading(false)
          return
        }
        const task = await api.getOcrTask(up.taskId)
        setOcrProgress(task.progress ?? 0)
        setOcrStage(task.stage ?? '')
        if (task.status === 'QUEUED' || task.status === 'RUNNING') {
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
      const err = e as ApiError
      // 后端对已存在工单号返回 409 BIZ_WORK_ORDER_DUPLICATE，消息内含已有 order_uuid
      if (err.message.includes('已存在') || err.message.includes('order_uuid')) {
        setMsg(`该工单号已存在，未重复入库。${err.message}`)
      } else {
        setMsg(`确认入库失败：${err.message}`)
      }
    }
  }

  /** 搜索工人（按手机号模糊 + openid 后缀，§工人管理面板）。 */
  async function searchWorker() {
    if (!workerQuery.trim()) {
      setWorkerMsg('请输入手机号或 openid 后 N 位')
      return
    }
    setWorkerMsg('')
    try {
      const rows = (await api.searchWorkers(workerQuery.trim())) as WorkerResult[]
      setWorkerResults(rows)
      if (rows.length === 0) setWorkerMsg('未找到匹配的工人')
    } catch (e) {
      setWorkerMsg((e as ApiError).message)
    }
  }

  /** 保存某工人的姓名绑定（§工人管理面板）。 */
  async function saveWorkerName(openid: string) {
    const name = nameDrafts[openid]
    if (name === undefined) return
    try {
      const updated = await api.updateWorker(openid, { name })
      // 更新本地列表中的姓名并清除草稿
      setWorkerResults((prev) =>
        prev.map((w) => (w.openid === openid ? { ...w, name: updated.name } : w)),
      )
      setNameDrafts((prev) => {
        const next = { ...prev }
        delete next[openid]
        return next
      })
      setWorkerMsg(`已保存 ${updated.name || '(空)'} 的姓名绑定`)
    } catch (e) {
      setWorkerMsg((e as ApiError).message)
    }
  }

  /** 浏览所有工人记录（§工人管理面板：列表全量）。 */
  async function browseWorkers() {
    setWorkerMsg('')
    try {
      const rows = (await api.listWorkers()) as WorkerResult[]
      setAllWorkers(rows)
      setSelectedWorker(null)  // 切换列表时收起上一条详情，避免误改
      if (rows.length === 0) setWorkerMsg('暂无工人记录')
    } catch (e) {
      setWorkerMsg((e as ApiError).message)
    }
  }

  /** 查：点击列表某条记录，拉取完整信息并进入编辑态（§工人管理面板）。 */
  async function selectWorker(openid: string) {
    setWorkerMsg('')
    try {
      const detail = (await api.getWorker(openid)) as WorkerResult
      setSelectedWorker(detail)
      setEditName(detail.name)
      setEditPhone(detail.phone)
      setEditQuota(detail.subscribe_quota == null ? '' : String(detail.subscribe_quota))
    } catch (e) {
      setWorkerMsg((e as ApiError).message)
    }
  }

  /** 改：保存选中记录的姓名/手机号/订阅余量（§工人管理面板）。 */
  async function saveSelectedWorker() {
    if (!selectedWorker) return
    const openid = selectedWorker.openid
    setWorkerMsg('')
    try {
      const quotaNum = editQuota.trim() === '' ? undefined : Number(editQuota)
      if (editQuota.trim() !== '' && Number.isNaN(quotaNum)) {
        setWorkerMsg('订阅余量必须是数字')
        return
      }
      // 手机号校验：国内手机号 11 位且以 1 开头；留空视为不修改（沿用原值）
      const phoneTrim = editPhone.trim()
      const phoneChanged = phoneTrim !== selectedWorker.phone
      if (phoneChanged && phoneTrim !== '' && !/^1\d{10}$/.test(phoneTrim)) {
        setWorkerMsg('手机号必须是 11 位数字且以 1 开头')
        return
      }
      const payload: { name?: string; phone?: string; subscribe_quota?: number } = {}
      // 仅提交与数据库当前值不同的字段，减少不必要写入（§工人管理面板）
      if (editName !== selectedWorker.name) payload.name = editName
      if (editPhone !== selectedWorker.phone) payload.phone = editPhone
      if (quotaNum !== undefined && quotaNum !== selectedWorker.subscribe_quota) {
        payload.subscribe_quota = quotaNum
      }
      if (Object.keys(payload).length === 0) {
        setWorkerMsg('没有改动，无需保存')
        return
      }
      const updated = await api.updateWorker(openid, payload)
      setSelectedWorker(updated)
      // 同步刷新浏览列表中的该行
      setAllWorkers((prev) => prev.map((w) => (w.openid === openid ? updated : w)))
      setWorkerMsg(`已更新 ${updated.name || '(空)'} 的记录`)
    } catch (e) {
      setWorkerMsg((e as ApiError).message)
    }
  }

  /** 删：删除选中记录（§工人管理面板）。带二次确认防误删。 */
  async function deleteSelectedWorker() {
    if (!selectedWorker) return
    const openid = selectedWorker.openid
    if (!window.confirm(`确认删除工人 ${selectedWorker.name || openid}？此操作不可恢复。`)) return
    setWorkerMsg('')
    try {
      await api.deleteWorker(openid)
      setAllWorkers((prev) => prev.filter((w) => w.openid !== openid))
      if (selectedWorker.openid === openid) setSelectedWorker(null)
      setWorkerMsg('已删除该工人记录')
    } catch (e) {
      setWorkerMsg((e as ApiError).message)
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
        {ocrLoading && (
          <div className="ocr-progress">
            <progress max={100} value={ocrProgress} />
            <span className="ocr-pct">{ocrProgress}%</span>
            <div className="ocr-stage">{ocrStageLabel(ocrStage)}</div>
          </div>
        )}
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

      {/* ===== 4. 工人管理面板（操作员后台补填姓名，§工人管理面板） ===== */}
      <section>
        <h2>4. 工人管理（补填姓名）</h2>
        <input
          value={workerQuery}
          onChange={(e) => setWorkerQuery(e.target.value)}
          placeholder="输入手机号或微信用户 openid 后 6 位"
        />
        <button onClick={searchWorker}>搜索</button>
        {workerMsg && <p className="msg">{workerMsg}</p>}
        {workerResults?.length ? (
          <table className="worker-table">
            <thead>
              <tr><th>手机号</th><th>微信 openid</th><th>当前姓名</th><th>补填姓名</th><th>操作</th></tr>
            </thead>
            <tbody>
              {(workerResults || []).map((w) => (
                <tr key={w.openid}>
                  <td>{w.phone || '—'}</td>
                  <td style={{ wordBreak: 'break-all' }}>…{w.openid.slice(-6)}</td>
                  <td>{w.name || '未命名'}</td>
                  <td>
                    <input
                      value={nameDrafts[w.openid] ?? w.name}
                      onChange={(e) =>
                        setNameDrafts((prev) => ({ ...prev, [w.openid]: e.target.value }))
                      }
                      placeholder="输入姓名"
                    />
                  </td>
                  <td>
                    <button onClick={() => saveWorkerName(w.openid)}>保存</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}

        <hr className="panel-divider" />
        <h3>浏览所有记录</h3>
        <button onClick={browseWorkers}>浏览所有记录</button>
        {allWorkers.length ? (
          <table className="worker-table">
            <thead>
              <tr>
                <th>手机号</th>
                <th>微信 openid</th>
                <th>姓名</th>
                <th>订阅余量</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {allWorkers.map((w) => (
                <tr
                  key={w.openid}
                  className={selectedWorker?.openid === w.openid ? 'selected' : ''}
                  style={{ cursor: 'pointer' }}
                  onClick={() => selectWorker(w.openid)}
                >
                  <td>{w.phone || '—'}</td>
                  <td style={{ wordBreak: 'break-all' }}>…{w.openid.slice(-6)}</td>
                  <td>{w.name || '未命名'}</td>
                  <td>{w.subscribe_quota ?? '—'}</td>
                  <td>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        selectWorker(w.openid)
                      }}
                    >
                      查看 / 编辑
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}

        {selectedWorker && (
          <div className="worker-detail">
            <h3>记录详情（查 / 改 / 删）</h3>
            <p className="mono">微信 openid：{selectedWorker.openid}</p>
            <label className="field">
              姓名
              <input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="姓名" />
            </label>
            <label className="field">
              手机号
              <input value={editPhone} onChange={(e) => setEditPhone(e.target.value)} placeholder="手机号" />
            </label>
            <label className="field">
              订阅余量
              <input value={editQuota} onChange={(e) => setEditQuota(e.target.value)} placeholder="数字" />
            </label>
            <div className="worker-detail-actions">
              <button onClick={saveSelectedWorker}>保存修改</button>
              <button className="danger" onClick={deleteSelectedWorker}>删除记录</button>
            </div>
          </div>
        )}
      </section>

      {msg && <div className="msg">{msg}</div>}
    </div>
  )
}
