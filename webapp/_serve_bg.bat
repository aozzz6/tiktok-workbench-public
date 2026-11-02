@echo off
cd /d "%~dp0"
start "workbench" /min ".venv\Scripts\python.exe" _boot.py
