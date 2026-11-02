@echo off
set "BASE=D:\workbuddy_project\tiktok\webapp"
set "PY=%BASE%\.venv\Scripts\pythonw.exe"
set "PORT=8787"
netstat -ano | findstr ":%PORT%" | findstr LISTENING >nul 2>&1
if not errorlevel 1 (
  echo [serve2] already listening on %PORT%
  exit /b 0
)
if not exist "%PY%" (
  echo [serve2] ERROR pythonw not found: %PY%
  exit /b 1
)
echo [serve2] starting pythonw _boot2.py
start "" "%PY%" "%BASE%\_boot2.py"
