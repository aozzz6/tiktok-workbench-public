# qa/T-11/run.ps1 —— T-11「对标分析页空数据不再白屏」的 QA 入口
# 用例本身在 cases.py（C-01 ~ C-06，清单见 .dsh-crew/jobs/fix-check-findings/T-11-plan.md）。
# 约定（与 qa/run-all.ps1 一致）：全通过退出码 0，任何一条不通过退出码非 0，原样透出本文件的退出码。
#
# 为什么要这段「嵌套跳过」：
# C-06 按清单要求跑 qa\run-all.ps1，而 run-all 会遍历 qa\ 下每个目录、又回到本文件 ——
# 不掐断就会无限递归。cases.py 发起那一次 run-all 时会给子进程设 QA_T11_NESTED=1，
# 本目录于是只输出一行跳过、退出码 0（T-11 自己的用例此时正由外层这一次调用在跑）。
# 直接跑本文件、或由 run-all 正常调用本文件时，这个变量不存在，六条用例照常全跑。

$ErrorActionPreference = "Continue"

if ($env:QA_T11_NESTED -eq "1") {
    Write-Host "[skip] T-11：qa\run-all.ps1 的外层调用正在跑本目录，跳过这一次嵌套调用"
    exit 0
}

# 子进程输出编码：本机控制台默认 GBK，用例里的中文是 UTF-8 写出来的，不设会变乱码（同 qa\run-all.ps1）。
try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = "utf-8"
} catch { }

$qaDir = $PSScriptRoot
$repo  = Split-Path -Parent (Split-Path -Parent $qaDir)
$cases = Join-Path $qaDir "cases.py"

$py = $null
foreach ($cand in @(
        (Join-Path $repo "webapp\.venv\Scripts\python.exe"),
        (Join-Path $repo "pipeline\.venv\Scripts\python.exe"))) {
    if (Test-Path -LiteralPath $cand) { $py = $cand; break }
}
if (-not $py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source }
}

if (-not $py) {
    Write-Host "[FAIL] T-11：找不到 python 解释器（webapp\.venv 与 pipeline\.venv 都不存在，PATH 上也没有 python）"
    exit 2
}
if (-not (Test-Path -LiteralPath $cases)) {
    Write-Host "[FAIL] T-11：找不到用例文件 $cases"
    exit 2
}

Push-Location $repo
try {
    & $py $cases
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host "T-11 exit code = $code"
exit $code
