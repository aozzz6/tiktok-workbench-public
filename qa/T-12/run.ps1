# qa/T-12/run.ps1 —— T-12「AI 接口地址不再重复拼接」的 QA 入口
# 用例本身在 cases.py（C-01 ~ C-07，清单见 .dsh-crew/jobs/fix-check-findings/T-12-plan.md）。
# 约定（与 qa/run-all.ps1 一致）：全通过退出码 0，任何一条不通过退出码非 0，原样透出。
# 解释器固定用 webapp\.venv（被测模块 webapp\llm.py 的依赖都在这个 venv 里）。

$ErrorActionPreference = "Continue"

# ── 递归护栏 ────────────────────────────────────────────────────────
# C-07 按计划要跑一遍全套 qa\run-all.ps1，而 run-all.ps1 会枚举 qa\ 下的目录、其中就有 qa/T-12，
# 于是又会调起本文件 —— 不设护栏就是无限递归。QA_T12_NESTED=1 表示「这一趟是 C-07 内部的全套重跑」，
# 此时 T-12 自己跳过（它已经在本入口顶层跑过一次了）。
if ($env:QA_T12_NESTED -eq "1") {
    Write-Host "[SKIP] T-12：这是 C-07 内部的全套重跑（QA_T12_NESTED=1），跳过 T-12 自己，避免无限递归。"
    exit 0
}

$qaDir = $PSScriptRoot
$repo  = Split-Path -Parent (Split-Path -Parent $qaDir)
$py    = Join-Path $repo "webapp\.venv\Scripts\python.exe"
$cases = Join-Path $qaDir "cases.py"

if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "[FAIL] T-12：找不到解释器 $py"
    exit 2
}
if (-not (Test-Path -LiteralPath $cases)) {
    Write-Host "[FAIL] T-12：找不到用例文件 $cases"
    exit 2
}

# 子进程输出编码：用例里的中文是 UTF-8 写出来的，本机控制台默认 GBK，不设会乱码。
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

if ($code -eq 0) {
    Write-Host "T-12 exit code = 0" -ForegroundColor Green
} else {
    Write-Host "T-12 有用例不通过 (exit=$code)" -ForegroundColor Red
}
exit $code
