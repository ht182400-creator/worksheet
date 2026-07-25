@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: ====================== 配置区 ======================
:: 工作目录（路径中请勿包含空格，否则需自行加引号）
set "BACKEND_DIR=D:\Work_Area\Python\WorkSheet\work_order_system\backend"
set "FRONTEND_DIR=D:\Work_Area\Python\WorkSheet\work_order_system\frontend"
:: 后端 uvicorn 端口 / 前端 vite 端口
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"
:: 后端健康检查接口
set "HEALTH_URL=http://127.0.0.1:%BACKEND_PORT%/health"
:: 后端就绪最大等待秒数
set "HEALTH_TIMEOUT=30"
:: ====================================================

echo ========================================
echo   工单系统 前后端重启脚本
echo ========================================

:: ---------- 1. 先杀掉占用端口的旧进程 ----------
echo [1/3] 停止旧进程 (端口 %BACKEND_PORT% / %FRONTEND_PORT%)...
call :kill_by_port %BACKEND_PORT%
call :kill_by_port %FRONTEND_PORT%
echo       等待端口释放...
timeout /t 2 /nobreak >nul

:: ---------- 2. 启动后端 ----------
echo [2/3] 启动后端 (uvicorn :%BACKEND_PORT%)...
start "work_order_backend" cmd /k "cd /d %BACKEND_DIR% && python -m uvicorn app.main:app --host 0.0.0.0 --port %BACKEND_PORT%"

:: ---------- 3. 等待后端就绪 ----------
echo       等待后端就绪 (%HEALTH_URL%)...
call :wait_health "%HEALTH_URL%" %HEALTH_TIMEOUT%

:: ---------- 4. 启动前端 ----------
echo [3/3] 启动前端 (vite :%FRONTEND_PORT%)...
start "work_order_frontend" cmd /k "cd /d %FRONTEND_DIR% && npm run dev"

echo.
echo 启动完成：
echo   后端窗口标题: work_order_backend   接口: http://localhost:%BACKEND_PORT%
echo   前端窗口标题: work_order_frontend   页面: http://localhost:%FRONTEND_PORT%/
echo   （两个窗口会保持打开以显示日志，关闭窗口即停止对应服务）
echo.
echo 按任意键关闭本启动器（不影响前后端窗口）...
pause >nul
goto :eof


:: ============ 子程序：按监听端口杀进程 ============
:kill_by_port
set "PORT=%~1"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%PORT%"') do (
    echo     杀掉端口 %PORT% 上的 PID %%a
    taskkill /F /PID %%a >nul 2>&1
)
goto :eof


:: ============ 子程序：轮询健康检查等待就绪 ============
:wait_health
set "URL=%~1"
set /a "TRIES=%~2"
:wait_loop
curl.exe -s -o NUL -w "%%{http_code}" "%URL%" 2>nul | findstr "200" >nul && (
    echo       后端已就绪。
    goto :eof
)
set /a TRIES-=1
if !TRIES!==0 (
    echo       警告: 后端在 %~2 秒内未就绪，请查看 work_order_backend 窗口日志。
    goto :eof
)
timeout /t 1 /nobreak >nul
goto :wait_loop
