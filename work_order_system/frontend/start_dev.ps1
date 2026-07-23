$ErrorActionPreference = 'Continue'
$PROJ = "d:\Work_Area\Python\WorkSheet\work_order_system"
$BACKEND = "$PROJ\backend"
$FRONTEND = "$PROJ\frontend"

# 按端口精准清理残留，避免孤儿进程占端口（[Errno 10048]）
foreach ($p in @(8000, 5173)) {
  $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
  if ($c) {
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "KILLED port $p (PID $($c.OwningProcess))"
  } else {
    Write-Host "PORT $p FREE"
  }
}

# 启动后端：uvicorn 仅监听 IPv4 127.0.0.1:8000
Start-Process -FilePath "python" -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
  -WorkingDirectory $BACKEND `
  -RedirectStandardOutput "$FRONTEND\backend_dev.log" `
  -RedirectStandardError "$FRONTEND\backend_dev.err" -NoNewWindow
Write-Host "BACKEND_LAUNCHED"

# 启动前端：vite dev 走 localhost:5173，代理 /api/v1 -> :8000
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev > frontend_dev.log 2>&1" `
  -WorkingDirectory $FRONTEND -NoNewWindow
Write-Host "FRONTEND_LAUNCHED"

# 轮询后端健康（IPv4 显式）
$b = $false
for ($i = 0; $i -lt 15; $i++) {
  try {
    $r = Invoke-WebRequest -Uri http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 1
    if ($r.StatusCode -eq 200) { $b = $true; break }
  } catch {}
  Start-Sleep 1
}
Write-Host "BACKEND_HEALTHY=$b"

# 轮询前端（vite 监听 localhost）
$f = $false
for ($i = 0; $i -lt 15; $i++) {
  try {
    $r = Invoke-WebRequest -Uri http://localhost:5173/ -UseBasicParsing -TimeoutSec 1
    if ($r.StatusCode -eq 200) { $f = $true; break }
  } catch {}
  Start-Sleep 1
}
Write-Host "FRONTEND_HEALTHY=$f"
Write-Host "ACCESS_URL=http://localhost:5173"
Write-Host "DONE"
