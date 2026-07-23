// 前后端 E2E 冒烟（docs/05 §7-C）：经前端 dev 代理 :5173 → 后端 :8000，
// 验证代理打通 + 联调契约（成功体 code==='0'）。Node 24 全局 fetch。
const BASE = 'http://localhost:5173/api/v1'

function assertOk(label, res, body) {
  const ok = res.status === 200 && body && body.code === '0'
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${label} status=${res.status} code=${body?.code}`)
  if (!ok) {
    console.log('  body=', JSON.stringify(body))
    process.exitCode = 1
  }
  return ok
}

async function main() {
  // 1) 创建工单（幂等键 + 租户头，模拟前端 client.ts）
  const createRes = await fetch(`${BASE}/work-orders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': 'demo-tenant', 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ display_no: 'WO-E2E-001', tenant_id: 'demo-tenant' }),
  })
  const createBody = await createRes.json()
  assertOk('CREATE work-order', createRes, createBody)
  const orderId = createBody?.data?.order_uuid
  if (!orderId) throw new Error('缺少 order_uuid，无法继续 E2E')

  // 2) 查询状态机（按钮由后端驱动）
  const smRes = await fetch(`${BASE}/work-orders/${orderId}/state-machine`, {
    headers: { 'X-Tenant-Id': 'demo-tenant' },
  })
  const smBody = await smRes.json()
  assertOk('GET state-machine', smRes, smBody)

  // 3) 提交报工（BR-22 在线合并）
  const repRes = await fetch(`${BASE}/work-orders/${orderId}/reports`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': 'demo-tenant', 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ process_id: 'p_02', completed_qty: 10, operator_id: 'u_emp_01', version: 1 }),
  })
  const repBody = await repRes.json()
  assertOk('POST report', repRes, repBody)

  console.log('E2E_SUMMARY: 代理打通 + 三连击均 code===0 →', process.exitCode ? 'FAIL' : 'ALL_PASS')
}

main().catch((e) => {
  console.error('E2E_ERROR', e)
  process.exit(1)
})
