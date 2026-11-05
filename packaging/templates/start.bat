@echo off
rem T-14 portable entry: environment -> best-effort first run -> launcher.
rem Exit code is the launcher exit code (0 ready, 1 failed, 2 old instance / restart failed).
call "%~dp0_setenv.bat"
if errorlevel 1 (
  echo [ERROR] failed to load _setenv.bat
  pause
  exit /b 1
)
"%PY%" "%WB_ROOT%first_run.py" --portable --init-only
"%PY%" "%WB_ROOT%webapp\_launch.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [ERROR] workbench did not start, launcher exit code %RC%
  pause
)
exit /b %RC%
