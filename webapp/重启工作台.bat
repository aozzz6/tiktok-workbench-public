@echo off
rem Restart the workbench so backend code changes take effect (2026-10-24).
rem Why: static js/css reload on browser refresh, but FastAPI routes need a process restart.
rem The launcher reuses an already-running instance, so double-clicking 启动工作台.bat
rem does NOT pick up new backend code. Use this script when /api/ai/* returns 404.
rem
rem Usage:  重启工作台.bat            -> restart on 8789 (default)
rem         重启工作台.bat 8791       -> restart on another port
rem Only python.exe / pythonw.exe listening on the given port are touched,
rem so reporter.js on 8787 (not python) is never killed.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PORT=8789"
if not "%~1"=="" set "PORT=%~1"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr LISTENING') do (
  for /f "tokens=1" %%N in ('tasklist /FI "PID eq %%P" /NH') do (
    if /i "%%N"=="python.exe"  ( echo [restart] killing python.exe pid %%P & taskkill /PID %%P /T /F >nul 2>&1 )
    if /i "%%N"=="pythonw.exe" ( echo [restart] killing pythonw.exe pid %%P & taskkill /PID %%P /T /F >nul 2>&1 )
  )
)

ping -n 3 127.0.0.1 >nul
echo [restart] starting workbench on port %PORT%
start "" ".venv\Scripts\pythonw.exe" _boot.py --port %PORT%
ping -n 7 127.0.0.1 >nul
start "" "http://127.0.0.1:%PORT%/"
endlocal
