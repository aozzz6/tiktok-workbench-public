# qa/run-all.ps1 —— 仓库的 QA 用例总跑器（跨作业；各目录自带 run.ps1）
# 维护人：PM。QA 只负责写自己任务下的用例，**不要改这个文件**。
# 为什么是 .ps1 而不是 .sh：本机没有 bash（实测 Get-Command bash 为空）。
#
# 约定：每个用例放在 qa/<task-id>/ ，入口按顺序找 run.ps1 / check.ps1 / run.py / run.sh
# 每个入口必须：自己判 是/否，通过退出码 0，不通过退出码非 0，并打印一行可读的结论。
# 跑法： powershell -NoProfile -ExecutionPolicy Bypass -File qa\run-all.ps1   （在仓库根 D:\tiktok-workbench 下）
#       注意：本机没有 PowerShell 7，`pwsh` 不存在（Get-Command pwsh 为空），别用 pwsh。

$ErrorActionPreference = "Continue"
$qaRoot = $PSScriptRoot
$repo   = Split-Path -Parent $qaRoot
$py     = Join-Path $repo "pipeline\.venv\Scripts\python.exe"

# 子进程的输出编码：用例里的中文是 UTF-8 写出来的，本机控制台默认 GBK，
# 不设这一行时 T-02/T-03 的输出在汇总里会变成乱码（2026-11-02 实测）。
try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = "utf-8"
} catch { }

$dirs = @(Get-ChildItem -Path $qaRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name)

if ($dirs.Count -eq 0) {
    Write-Host "[FAIL] qa/ 下没有任何用例目录。'没有用例' 不等于 '通过'。" -ForegroundColor Red
    exit 2
}

$pass = 0
$fail = 0
$noEntry = @()
$failedNames = @()

foreach ($d in $dirs) {
    $entry = $null
    foreach ($name in @("run.ps1", "check.ps1", "run.py", "run.sh")) {
        $p = Join-Path $d.FullName $name
        if (Test-Path -LiteralPath $p) { $entry = $p; break }
    }
    if (-not $entry) {
        $noEntry += $d.Name
        Write-Host "[WARN] $($d.Name)：没有入口文件（run.ps1/check.ps1/run.py/run.sh），不算通过" -ForegroundColor Yellow
        continue
    }

    Write-Host ""
    Write-Host "=== $($d.Name) -> $(Split-Path -Leaf $entry) ===" -ForegroundColor Cyan
    Push-Location $repo
    $ext = [IO.Path]::GetExtension($entry).ToLower()
    if ($ext -eq ".ps1") {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $entry
    } elseif ($ext -eq ".py") {
        & $py $entry
    } elseif ($ext -eq ".sh") {
        & bash $entry
    } else {
        Write-Host "[WARN] 不认识的入口类型 $ext" -ForegroundColor Yellow
        $global:LASTEXITCODE = 99
    }
    $code = $LASTEXITCODE
    Pop-Location

    if ($code -eq 0) {
        $pass++
        Write-Host "[PASS] $($d.Name)" -ForegroundColor Green
    } else {
        $fail++
        $failedNames += "$($d.Name)(exit=$code)"
        Write-Host "[FAIL] $($d.Name) exit=$code" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "QA 用例：通过 $pass / 失败 $fail / 无用例入口 $($noEntry.Count)" -ForegroundColor Cyan
if ($noEntry.Count -gt 0) { Write-Host "  没有入口：$($noEntry -join ', ')" -ForegroundColor Yellow }
if ($fail -gt 0) {
    Write-Host "  失败：$($failedNames -join ', ')" -ForegroundColor Red
    exit 1
}
if ($noEntry.Count -gt 0) {
    # 有目录却没有可跑的入口，等于有一个任务没有被验证过 —— 不算绿
    exit 1
}
exit 0
