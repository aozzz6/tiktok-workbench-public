@echo off
rem Start workbench on an alternate port (2026-10-23).
rem Default 8789; pass a port to override, e.g.  _serve_port.bat 8801
setlocal
cd /d "%~dp0"
set "WB_PORT=%~1"
if "%WB_PORT%"=="" set "WB_PORT=8789"
echo Workbench: http://127.0.0.1:%WB_PORT%
start "workbench-%WB_PORT%" /min ".venv\Scripts\pythonw.exe" _boot.py --port %WB_PORT%
endlocal
