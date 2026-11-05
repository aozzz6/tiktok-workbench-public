@echo off
cd /d "%~dp0"
set "WB_PORT=8789"
start "workbench" /min ".venv\Scripts\python.exe" _boot.py
