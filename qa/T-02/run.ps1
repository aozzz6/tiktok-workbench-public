# qa/T-02/run.ps1 —— T-02（抓取失败记下真实 statusCode/statusMsg）的 QA 用例入口
# 维护人：crew QA（T-02 用例）。总跑器 qa\run-all.ps1 由 PM 维护，本文件不碰它。
# 判定：退出码 0 = 全部用例 ok；非 0 = 有 FAIL 或根本跑不起来（不吞错误）。
# 解释器固定用 pipeline\.venv（本机实测 Python 3.13.14，见 PRD 第 3 节），不用系统 Python。
# 不联网：用例自己把 fetch_page / tiktok_fetch.fetch_html / socket 换成固定样例或抛异常，
# 浏览器兜底也一律 patch 掉（它本来会起 playwright 子进程真上网）。

$ErrorActionPreference = "Continue"

$repo  = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # qa\T-02 -> qa -> 仓库根
$py    = Join-Path $repo "pipeline\.venv\Scripts\python.exe"
$cases = Join-Path $PSScriptRoot "cases.py"

if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "[FAIL] 找不到 venv 解释器：$py" -ForegroundColor Red
    exit 2
}
if (-not (Test-Path -LiteralPath $cases)) {
    Write-Host "[FAIL] 找不到用例文件：$cases" -ForegroundColor Red
    exit 2
}

Push-Location $repo
& $py $cases
$code = $LASTEXITCODE
Pop-Location

if ($null -eq $code) { $code = 1 }
if ($code -eq 0) {
    Write-Host "[PASS] T-02 用例全部通过" -ForegroundColor Green
} else {
    Write-Host "[FAIL] T-02 有用例不通过 (exit=$code)" -ForegroundColor Red
}
exit $code
