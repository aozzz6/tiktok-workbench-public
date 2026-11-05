@echo off
rem T-14 portable launcher environment: the ONLY place that writes WB_*/HF_*/PYTHON* variables.
rem Unconditional assignments on purpose: host or user-residual values are always overwritten.
set "WB_ROOT=%~dp0"
set "PY=%WB_ROOT%python\python.exe"
set "WB_PYTHON=%WB_ROOT%python\python.exe"
set "WB_PYTHONW=%WB_ROOT%python\pythonw.exe"
set "WB_PIPELINE_DIR=%WB_ROOT%pipeline"
set "WB_HOME=%WB_ROOT%workbuddy"
set "WB_LEDGER_DIR=%WB_ROOT%ledger"
set "WB_REVERSE_DIR=%WB_ROOT%reverse"
set "WB_LOG_DIR=%WB_ROOT%logs"
set "HF_HOME=%WB_ROOT%models\hf"
set "PATH=%WB_ROOT%python;%WB_ROOT%ffmpeg\bin;%PATH%"
set "PYTHONIOENCODING=utf-8"
rem Security: never import user site-packages (%APPDATA%\Python\...). A user "pip install --user"
rem could otherwise shadow or inject imports into the packaged dependencies.
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONUSERBASE="
set "HF_HUB_CACHE="
set "HUGGINGFACE_HUB_CACHE="
set "XDG_CACHE_HOME="
set "HF_HUB_OFFLINE="
if exist "%~dp0_setenv.local.bat" call "%~dp0_setenv.local.bat"
rem Always finish with errorlevel 0: "set" and "if exist" leave the caller's old errorlevel
rem untouched, so the entry bat would see a stale value and print a false load error.
exit /b 0
