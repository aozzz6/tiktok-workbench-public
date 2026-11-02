@echo off
REM 2026-10-21 一次性重启脚本（方案 A/B 后端需重载）。
REM 停掉占用 8787 的 _boot.py 实例，再用同一条 pythonw 方式拉起；失败时用户双击 lnk 即可。
cd /d "%~dp0"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr "127.0.0.1:8787" ^| findstr LISTENING') do (
  echo [restart] killing pid %%P
  taskkill /PID %%P /T /F >nul 2>&1
)

timeout /t 2 /nobreak >nul
echo [restart] starting pythonw _boot.py
start "" ".venv\Scripts\pythonw.exe" _boot.py
