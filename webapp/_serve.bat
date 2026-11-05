@echo off
cd /d "%~dp0"
set "WB_PORT=8789"
.venv\Scripts\python.exe app.py > _server_run.log 2>&1
