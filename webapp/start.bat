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
rem 可迁移的默认路径：已设置同名环境变量时不覆盖（换机后双击即可用，不必改代码）
if not defined WB_PIPELINE_DIR set "WB_PIPELINE_DIR=%WEBAPP%..\pipeline"
if not defined WB_PYTHON set "WB_PYTHON=%WEBAPP%..\pipeline\.venv\Scripts\python.exe"
if not defined WB_REVERSE_DIR set "WB_REVERSE_DIR=%WEBAPP%..\reverse"
if not defined WB_HOME set "WB_HOME=%USERPROFILE%\.workbuddy"
rem 台账目录：本机若已有 D:\1\对标账号 就沿用，否则用仓库内 ledger\
if not defined WB_LEDGER_DIR if exist "D:\1\对标账号" set "WB_LEDGER_DIR=D:\1\对标账号"
if not defined WB_LEDGER_DIR set "WB_LEDGER_DIR=%WEBAPP%..\ledger"
echo 工作台启动中：http://127.0.0.1:8789
start "" http://127.0.0.1:8789
"%PY%" "%WEBAPP%app.py"
pause
