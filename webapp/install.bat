@echo off
chcp 65001 >nul
setlocal
set WEBAPP=%~dp0
set BASEPY=%~dp0..\pipeline\.venv\Scripts\python.exe
if not exist "%BASEPY%" set BASEPY=python

echo [1/2] 创建独立虚拟环境 .venv ...
"%BASEPY%" -m venv "%WEBAPP%.venv"

echo [2/2] 安装依赖 fastapi / uvicorn / openpyxl ...
"%WEBAPP%.venv\Scripts\python.exe" -m pip install -r "%WEBAPP%requirements.txt" -i https://pypi.org/simple
if errorlevel 1 (
  echo [提示] 官方源失败，回退清华镜像重试 ...
  "%WEBAPP%.venv\Scripts\python.exe" -m pip install -r "%WEBAPP%requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
)

echo.
echo 完成。双击 start.bat 启动工作台。
pause
