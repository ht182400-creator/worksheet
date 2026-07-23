$ErrorActionPreference = 'Continue'
$backendLog  = 'd:\Work_Area\Python\WorkSheet\work_order_system\frontend\backend_e2e.log'
$frontendLog = 'd:\Work_Area\Python\WorkSheet\work_order_system\frontend\frontend_e2e.log'
$ts = { "[$(Get-Date -Format HH:mm:ss)]" }

# 按端口精准清理（避免命令行正则匹配不到 python -m uvicorn / vite 子进程导致孤儿占用）
function Kill-Port($port) {
  $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if ($c) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host "$($ts.Invoke()) 清理占用端口 $port 的进程(PID $($c.OwningProcess))" }
}
# 启动前先清场，确保端口空闲
Kill-Port 8000
Kill-Port 5173
Start-Sleep -Seconds 1

Write-Host "$($ts.Invoke()) 启动后端 uvicorn :8000 ..."
Start-Process -FilePath "cmd.exe" -ArgumentList "/c","cd /d d:\Work_Area\Python\WorkSheet\work_order_system\backend && uvicorn app.main:app --port 8000 > $backendLog 2>&1" -NoNewWindow

# 轮询后端 /health（根路径，main.py:39），最多 20s
$ready = $false
for ($i = 1; $i -le 20; $i++) {
  try {
    $r = Invoke-WebRequest -Uri http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 1 -ErrorAction SilentlyContinue
    if ($r.StatusCode -eq 200) { $ready = $true; break }
  } catch {}
  Start-Sleep -Seconds 1
  if ($i % 3 -eq 0) { Write-Host "$($ts.Invoke()) 等待后端 ($i s)" }
}
Write-Host "$($ts.Invoke()) 后端就绪 ready=$ready"
if (-not $ready) { Write-Host "BACKEND_NOT_READY"; Kill-Port 8000; exit 1 }

Write-Host "$($ts.Invoke()) 启动前端 vite :5173 ..."
Start-Process -FilePath "cmd.exe" -ArgumentList "/c","cd /d d:\Work_Area\Python\WorkSheet\work_order_system\frontend && npm run dev > $frontendLog 2>&1" -NoNewWindow

# 轮询前端 :5173，最多 20s
$readyF = $false
for ($i = 1; $i -le 20; $i++) {
  try {
    $r = Invoke-WebRequest -Uri http://localhost:5173/ -UseBasicParsing -TimeoutSec 1 -ErrorAction SilentlyContinue
    if ($r.StatusCode -eq 200) { $readyF = $true; break }
  } catch {}
  Start-Sleep -Seconds 1
  if ($i % 3 -eq 0) { Write-Host "$($ts.Invoke()) 等待前端 ($i s)" }
}
Write-Host "$($ts.Invoke()) 前端就绪 ready=$readyF"
if (-not $readyF) { Write-Host "FRONTEND_NOT_READY"; Kill-Port 5173; exit 1 }

try {
  Set-Location d:\Work_Area\Python\WorkSheet\work_order_system\frontend
  Write-Host "$($ts.Invoke()) 运行 E2E 三连击（经 :5173 代理 → :8000）..."
  & node e2e_check.mjs
  Write-Host "E2E_NODE_EXIT=$LASTEXITCODE"
}
finally {
  Kill-Port 8000
  Kill-Port 5173
  Write-Host "$($ts.Invoke()) SERVERS_STOPPED"
}
