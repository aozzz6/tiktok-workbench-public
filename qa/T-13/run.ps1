# qa/T-13/run.ps1 —— T-13「启动脚本、测试、README 不再依赖旧仓库路径」的 QA 入口
# 用例本身在 cases.py（C-01 ~ C-10，清单见 .dsh-crew/jobs/fix-check-findings/T-13-plan.md）。
# 约定（与 qa/run-all.ps1 一致）：全通过退出码 0，任何一条不通过退出码非 0，原样透出。
# 本文件含非 ASCII 注释，必须存成 UTF-8 with BOM，否则 Windows PowerShell 5.1 按 GBK 读会乱码。

$ErrorActionPreference = "Continue"

$qaDir = $PSScriptRoot
$repo  = Split-Path -Parent (Split-Path -Parent $qaDir)
$py    = Join-Path $repo "webapp\.venv\Scripts\python.exe"
$cases = Join-Path $qaDir "cases.py"

if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "[FAIL] T-13：找不到解释器 $py"
    exit 2
}
if (-not (Test-Path -LiteralPath $cases)) {
    Write-Host "[FAIL] T-13：找不到用例文件 $cases"
    exit 2
}

# 子进程输出编码：用例里的中文是 UTF-8，本机控制台默认 GBK（与 qa/run-all.ps1 同做法）
try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = "utf-8"
} catch { }

Push-Location $repo
try {
    & $py $cases
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host "T-13 exit code = $code"
exit $code
