# PowerShell 踩坑速查（本机 Windows 环境）

> 目的：把反复踩过的 PowerShell 雷点集中沉淀，避免每次临时试错浪费资源。
> 适用：本仓库在 Windows + PowerShell 下跑后端、造数、查端口、读日志等运维操作。
> 维护规则：每遇到新的 PowerShell 坑，追加到对应小节并更新「最近更新」日期，不要另开文件。

最近更新：2026-07-25

---

## 1. 变量名禁用 PowerShell 保留/只读变量

以下变量是 PowerShell 的只读或自动变量，**赋值会直接报错**，导致整段脚本中断（且报错信息隐蔽，容易误判为"命令没执行"）：

| 变量 | 类型 | 用途 |
|---|---|---|
| `$pid` | 只读 | 当前 PowerShell 进程 ID |
| `$PSItem`（即 `$_`） | 自动 | 管道当前对象 |

**错误示例**（今天踩过：`foreach ($pid in $pids)` 报"无法覆盖变量 PID，因为该变量为只读变量或常量"，导致 `taskkill` 没执行，旧进程没被杀）：

```powershell
# ❌ 错误：$pid 是只读变量
$pids = @(26620)
foreach ($pid in $pids) { taskkill /PID $pid /F }
```

**正确做法**：用自定义变量名（`$p`、`$procId`、`$x` 等）：

```powershell
# ✅ 正确
foreach ($p in $pids) { taskkill /PID $p /F }
```

**经验**：循环/函数参数里凡是要用 `id`/`pid`/`ps`/`host` 这类短名，先想一下是不是 PowerShell 保留字；不确定就用更长的前缀（`$targetPid`）。

---

## 2. `netstat` 查端口别用 `findstr ":8000"` 裸匹配

`findstr ":8000"` 会**误匹配 IPv6 地址里含 `:8000:` 的外部连接行**（例如 `[...:8000:...]:443`），把无关进程（如对外 443 的微信 API 连接）也选进来，误杀。

**错误示例**（今天踩过：把对外 443 的 PID 26508 误判为 8000 占用者）：

```powershell
netstat -ano | findstr ":8000"        # ❌ 会匹配 IPv6 外部地址里的 :8000:
```

**正确做法**：只匹配「本地监听 0.0.0.0:8000」的 LISTENING 行：

```powershell
# ✅ 精确匹配本机监听行
netstat -ano | findstr "0.0.0.0:8000" | findstr "LISTENING"
```

更稳妥（需管理员权限才能看到 PID 时可用）：

```powershell
# ✅ 用 NetTCPConnection 按 本地端口 + 状态 过滤
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess
```

**精确杀端口占用进程的标准模板**（重启 uvicorn 用）：

```powershell
$pids = netstat -ano | findstr "0.0.0.0:8000" | findstr "LISTENING" |
    ForEach-Object { ($_ -split '\s+')[-1] } | Where-Object { $_ -match '^\d+$' -and $_ -ne '4' } |
    Sort-Object -Unique
foreach ($p in $pids) { taskkill /PID $p /F }
Start-Sleep -Seconds 1
```

> `PID 4` 是系统进程（NT Kernel），永远不要杀；上面的 `Where-Object { $_ -ne '4' }` 是兜底。

---

## 3. `Get-Content` 必须显式指定 `-Encoding`

本机控制台是 GBK，`Get-Content` 不指定编码会按系统默认解码，遇到 UTF-8/BOM 文件会乱码，且 Craft 环境会**直接拦截**该命令（报错：reading file content without an explicit encoding）。

**错误示例**（今天被拦截）：

```powershell
Get-Content uvicorn_stderr.log -Tail 20      # ❌ 缺编码，被拦截
```

**正确做法**：显式传 `-Encoding utf8`，或直接用 IDE 的 `read_file` 工具读日志（更稳，不受编码影响）：

```powershell
Get-Content uvicorn_stderr.log -Encoding utf8 -Tail 20
```

> 经验：读自己项目里 Python 写的日志/文本，**优先用 read_file 工具**，别走 shell `Get-Content`。

---

## 4. `curl`/`head` 在 PowerShell 里的坑

- **`curl` 是 PowerShell 别名**，指向 `Invoke-WebRequest`，不是系统 `curl.exe`。要调用真正的 curl 必须写 `curl.exe`。
- **`head` 不是 PowerShell cmdlet**，直接写 `| head -c 300` 会报 `CommandNotFoundException`，并导致整条管道（含前面的 curl）不执行、拿不到任何输出。

**错误示例**（今天踩过：curl 状态码没打印，整条因 `head` 失败）：

```powershell
curl.exe ... | head -c 300     # ❌ head 不存在，管道失败
```

**正确做法**：

```powershell
# 看 HTTP 状态码 + Content-Type（推荐）
curl.exe -s -o NUL -w "HTTP=%{http_code} CT=%{content_type}\n" "http://127.0.0.1:8000/api/v1/qrcode/img?order_uuid=...&t=qr"

# 看响应体前 N 字节：用 Select-Object -First / -Last（PowerShell 原生），或重定向到文件再用 read_file
curl.exe -s "http://127.0.0.1:8000/health" | Select-Object -First 1
```

> 经验：在 PowerShell 里验证 HTTP 状态，**首选 `curl.exe -s -o NUL -w "%{http_code}"`**，比 `Invoke-WebRequest` 异常处理更干净（后者非 2xx 会抛异常，需 catch 里取 `StatusCode`）。

---

## 5. 启动后端并后台存活

- `Start-Process -NoNewWindow python -ArgumentList "..."` 会脱离当前 shell 独立运行，**适合拉起常驻 uvicorn**（当前 shell 退出后进程仍在）。
- 一定要 `-WorkingDirectory` 指到 `work_order_system/backend`，否则 `app.main:app` 找不到包；日志用 `-RedirectStandardOutput/-RedirectStandardError` 落到文件，便于事后排查（如 bind 失败的 `ERROR 10048`）。
- 重启前务必先按第 2 节精确杀掉旧 LISTENING 进程，否则新进程会因 `address already in use (10048)` 启动即退。

**标准重启模板**：

```powershell
cd d:\Work_Area\Python\WorkSheet\work_order_system\backend
# 1) 精确杀旧进程
$pids = netstat -ano | findstr "0.0.0.0:8000" | findstr "LISTENING" |
    ForEach-Object { ($_ -split '\s+')[-1] } | Where-Object { $_ -match '^\d+$' -and $_ -ne '4' } |
    Sort-Object -Unique
foreach ($p in $pids) { taskkill /PID $p /F }
Start-Sleep -Seconds 1
# 2) 起新进程（后台常驻）
Start-Process -NoNewWindow python -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000" `
    -WorkingDirectory "d:\Work_Area\Python\WorkSheet\work_order_system\backend" `
    -RedirectStandardOutput "uvicorn_stdout.log" -RedirectStandardError "uvicorn_stderr.log"
# 3) 等 health
$ok=$false
for ($i=1; $i -le 30; $i++) {
    try { Invoke-RestMethod -Uri http://127.0.0.1:8000/health -TimeoutSec 2; $ok=$true; break } catch { Start-Sleep -Seconds 1 }
}
echo "HEALTH=$ok"
```

---

## 6. 其他 Windows/PowerShell 提示

- 路径用反斜杠 `\` 或正斜杠 `/` 均可，但拼字符串时 PowerShell 变量里含 `$` 的路径要小心被解析；建议用单引号包裹路径字面量。
- 多行脚本里用反引号 `` ` `` 做换行续行（不是 `\`）。
- `Invoke-RestMethod` 默认把 JSON 反序列化成对象，取数组长度用 `$resp.data.Count`；空响应 `Count` 为 0 不报错。
- 非 2xx 用 `Invoke-RestMethod` 会抛异常，`$_.Exception.Response.StatusCode` 有时为 `$null`（如连接被拒），排查 HTTP 状态优先用第 4 节的 `curl.exe -w` 法。
