@echo off
rem T-18 portable entry: restart the workbench (optionally on one given port).
rem Usage: double-click this file, or give it one argument: the port to restart on.
rem One argument -> only that port is handled: our workbench there is restarted on the
rem SAME port, a foreign program there is left alone, a free port is started on.
rem No argument -> the launcher scans its candidate ports (old behaviour).
rem Exit code is the launcher exit code: 0 restarted, 1 failed, 2 could not kill / wrong state.
call "%~dp0_setenv.bat"
if not exist "%PY%" (
  echo [ERROR] portable python not found: %PY%
  pause
  exit /b 1
)
"%PY%" "%WB_ROOT%webapp\_launch.py" --restart %~1
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [ERROR] restart failed, launcher exit code %RC%
  pause
)
exit /b %RC%
