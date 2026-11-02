@echo off
chcp 65001 >nul
setlocal
set WEBAPP=%~dp0
rem 清理宿主注入的 PYTHONPATH（避免加载外部 shim）
set PYTHONPATH=
set PYTHONHOME=
set PY=%WEBAPP%.venv\Scripts\python.exe
if not exist "%PY%" (
  echo [错误] 未找到虚拟环境，请先双击 install.bat 安装依赖。
  pause
  exit /b 1
)
rem 端口统一到 8789（与桌面快捷方式 / 重启工作台.bat / _launch.py 的既有优先顺序一致）
set WB_PORT=8789
echo 工作台启动中：http://127.0.0.1:8789
start "" http://127.0.0.1:8789
"%PY%" "%WEBAPP%app.py"
pause
