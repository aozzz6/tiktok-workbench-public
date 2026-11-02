# qa/T-03/run.ps1 -- entry point for the T-03 QA cases (crew T-03)
# Maintainer of the file: crew_qa. Runner qa/run-all.ps1 belongs to the PM.
#
# What it does: runs cases.py with the repository virtualenv interpreter and
# passes its exit code straight through (0 = all cases pass, non-zero = fail).
# Not all-ASCII? Everything printed here is ASCII, so a GBK console cannot garble it.
#
# Run from anywhere:  powershell -NoProfile -ExecutionPolicy Bypass -File qa\T-03\run.ps1

$ErrorActionPreference = "Continue"

$qaDir = $PSScriptRoot
$repo  = Split-Path -Parent (Split-Path -Parent $qaDir)
$py    = Join-Path $repo "pipeline\.venv\Scripts\python.exe"
$cases = Join-Path $qaDir "cases.py"

if (-not (Test-Path -LiteralPath $py)) {
    Write-Host "[FAIL] interpreter not found: $py" -ForegroundColor Red
    exit 2
}
if (-not (Test-Path -LiteralPath $cases)) {
    Write-Host "[FAIL] case file not found: $cases" -ForegroundColor Red
    exit 2
}

Push-Location $repo
try {
    & $py $cases
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($null -eq $code) { $code = 1 }
Write-Host ""
Write-Host "[qa/T-03] exit=$code" -ForegroundColor Cyan
exit $code
