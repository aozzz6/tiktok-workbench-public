# qa/T-04/run.ps1 —— T-04「修好两个因 API 变更早已失效的旧测试」的 QA 入口
# 用例本身在 cases.py（C-01 ~ C-10，清单见 .dsh-crew/jobs/s2-scrape-failure-fix/T-04-plan.md）。
# 约定（与 qa/run-all.ps1 一致）：全通过退出码 0，任何一条不通过退出码非 0，原样透出。

$ErrorActionPreference = "Continue"

$qaDir   = $PSScriptRoot
$repo    = Split-Path -Parent (Split-Path -Parent $qaDir)
$py      = Join-Path $repo "pipeline\.venv\Scripts\python.exe"
$cases   = Join-Path $qaDir "cases.py"

if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "[FAIL] T-04：找不到解释器 $py"
    exit 2
}
if (-not (Test-Path -LiteralPath $cases)) {
    Write-Host "[FAIL] T-04：找不到用例文件 $cases"
    exit 2
}

Push-Location $repo
try {
    & $py $cases
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host "T-04 exit code = $code"
exit $code
