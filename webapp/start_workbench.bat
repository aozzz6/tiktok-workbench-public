@echo off
rem ============================================================
rem  TikTok Benchmark Workbench - One-click launcher (idempotent)
rem
rem  Usage: double-click this file, or run start_workbench.bat
rem
rem  Note: the built-in scheduler is DISABLED; daily updates are
rem        handled by a WorkBuddy automation. This script only
rem        starts the webapp service (port 8787) for viewing UI.
rem
rem  IMPORTANT: keep this file pure ASCII. cmd.exe parses .bat with
rem  the OEM codepage (GBK here), so non-ASCII bytes get garbled and
rem  the mangled text is treated as commands.
rem ============================================================
setlocal

set "APP_DIR=%~dp0"
set "PORT=8787"

rem --- already running? just open the browser ---
netstat -ano | findstr ":%PORT%" | findstr LISTENING >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Workbench already running on port %PORT%
    start "" "http://127.0.0.1:%PORT%"
    exit /b 0
)

cd /d "%APP_DIR%"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Not found: .venv\Scripts\python.exe
    echo         APP_DIR = %APP_DIR%
    pause
    exit /b 1
)

echo [INFO] Starting workbench ...
if not exist "logs" mkdir "logs"
start "workbench" /min ".venv\Scripts\python.exe" app.py

rem --- wait until port is listening (max 20s) ---
set /a WAIT=0
:waitloop
timeout /t 1 /nobreak >nul
netstat -ano | findstr ":%PORT%" | findstr LISTENING >nul 2>&1
if not errorlevel 1 goto ready
set /a WAIT+=1
if %WAIT% lss 20 goto waitloop
echo [WARN] Port %PORT% not ready after 20s - check the console window
pause
exit /b 1

:ready
echo [OK] Workbench ready: http://127.0.0.1:%PORT%
start "" "http://127.0.0.1:%PORT%"
exit /b 0
