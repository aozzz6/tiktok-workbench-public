@echo off
rem T-18 portable entry: environment check for the portable package.
rem Runs first_run.py in portable, offline mode and always pauses before closing
rem (double-click users must be able to read the result).
rem Exit code: 0 = no blocking problem ([WARN] is not a failure), 1 = there is a [FAIL].
call "%~dp0_setenv.bat"
if not exist "%PY%" (
  echo [ERROR] portable python not found: %PY%
  pause
  exit /b 1
)
"%PY%" "%WB_ROOT%first_run.py" --portable --no-network
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
