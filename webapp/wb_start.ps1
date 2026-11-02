# =============================================================================
#  wb_start.ps1 - start the workbench web service on port 8789 (idempotent)
#
#  Why this file exists (2026-10-22, acceptance item R1):
#  the old shortcut launched `.venv\Scripts\pythonw.exe _boot.py` directly. When the
#  launching console/session went away the child inherited dead stdio handles and
#  died on the next log write; the scheduled task's last result was 0xC000013A
#  (STATUS_CONTROL_C_EXIT). This launcher instead:
#    * exits 0 immediately if 8789 is already listening (idempotent, never two copies)
#    * starts pythonw.exe (no console) detached with _boot2.py (redirects its own stdio)
#    * passes the port to the child through the WB_PORT env var (2026-11-01): _boot2.py
#      takes no --port argument, app.py reads WB_PORT, so without this the child always
#      fell back to app.py's default port and the shortcut could open a different instance
#    * waits for /api/health 200 and reports PID / start time / health
#    * appends one line per run to logs\wb_start.log
#
#  NOTE: keep this file ASCII-only or save it as UTF-8 **with BOM**;
#        Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI and garbles non-ASCII.
# =============================================================================
param(
  [int]$Port = 8789,
  [int]$WaitSeconds = 150
)

$ErrorActionPreference = "Continue"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Base "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir "wb_start.log"
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  [System.IO.File]::AppendAllText($LogFile, $line + "`r`n", $Utf8)
}

function Get-Listener {
  try { return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop) } catch { return @() }
}

function Test-Health {
  try {
    $r = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/health" -f $Port) -UseBasicParsing -TimeoutSec 3
    return ($r.StatusCode -eq 200)
  } catch { return $false }
}

function Get-ServedBy([int]$procId) {
  try {
    $p = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $procId)
    return ("{0} | {1}" -f $p.ExecutablePath, $p.CommandLine)
  } catch { return "unknown" }
}

# pythonw.exe processes already running our boot wrapper (starting up but not yet bound?)
function Get-BootProcs {
  try {
    return @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
      Where-Object { $_.CommandLine -and $_.CommandLine -like "*_boot2.py*" })
  } catch { return @() }
}

# Wait until the port answers /api/health. Returns the owning pid, or 0 on timeout.
function Wait-Healthy([int]$seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 700
    # NOTE: @() wrap is required. In Windows PowerShell 5.1 a single CimInstance has no
    # Count property, so `$l.Count` is $null and `$null -gt 0` is false -> false "no listener".
    $l = @(Get-Listener)
    if ($l.Count -gt 0 -and (Test-Health)) { return [int]$l[0].OwningProcess }
  }
  return 0
}

# ---- 1) idempotent: already serving? ----------------------------------------
$listener = @(Get-Listener)
if ($listener.Count -gt 0) {
  $owner = [int]$listener[0].OwningProcess
  $ok = Test-Health
  Write-Log ("ALREADY port={0} pid={1} health={2} src={3}" -f $Port, $owner, $ok, (Get-ServedBy $owner))
  if ($ok) {
    Write-Host ("[OK] already running: http://127.0.0.1:{0}/  pid={1}" -f $Port, $owner)
    exit 0
  }
  Write-Host ("[WARN] port {0} is listening (pid={1}) but /api/health failed - not starting a second copy" -f $Port, $owner)
  exit 2
}

# ---- 1b) a boot wrapper is already starting (slow imports): just wait ---------
$booting = @(Get-BootProcs)
if ($booting.Count -gt 0) {
  $pids = ($booting | ForEach-Object { $_.ProcessId }) -join ","
  Write-Log ("WAIT existing boot wrapper pid={0} (no listener yet)" -f $pids)
  $owner = Wait-Healthy $WaitSeconds
  if ($owner -gt 0) {
    Write-Log ("READY port={0} pid={1} (adopted existing boot wrapper {2})" -f $Port, $owner, $pids)
    Write-Host ("[OK] running: http://127.0.0.1:{0}/  pid={1}  (waited for an already-starting instance)" -f $Port, $owner)
    exit 0
  }
  Write-Log ("FAIL existing boot wrapper pid={0} never became healthy" -f $pids)
  Write-Host "[FAIL] a boot wrapper was already running but never became healthy - check logs\_server_run.log"
  exit 1
}

# ---- 2) start detached ------------------------------------------------------
$pythonw = Join-Path $Base ".venv\Scripts\pythonw.exe"
$boot = Join-Path $Base "_boot2.py"
if (-not (Test-Path $pythonw)) { Write-Log ("ERROR missing {0}" -f $pythonw); Write-Host "[ERROR] missing $pythonw"; exit 1 }
if (-not (Test-Path $boot))    { Write-Log ("ERROR missing {0}" -f $boot);    Write-Host "[ERROR] missing $boot"; exit 1 }

# The child inherits this process' environment; app.py's _listen_port() reads WB_PORT
# first, so this is what decides which port _boot2.py -> app.py really binds.
$env:WB_PORT = "$Port"

Write-Log ("START port={0} pythonw={1} wb_port={2}" -f $Port, $pythonw, $env:WB_PORT)
Start-Process -FilePath $pythonw -ArgumentList "`"$boot`"" -WorkingDirectory $Base -WindowStyle Hidden

# ---- 3) wait for health -----------------------------------------------------
$owner = Wait-Healthy $WaitSeconds
if ($owner -gt 0) {
  $p = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $owner)
  $started = $p.CreationDate
  Write-Log ("READY port={0} pid={1} started={2} health=True src={3}" -f $Port, $owner, $started, (Get-ServedBy $owner))
  Write-Host ("[OK] started: http://127.0.0.1:{0}/  pid={1}  started={2}" -f $Port, $owner, $started)
  exit 0
}

Write-Log ("FAIL port={0} not healthy within {1}s" -f $Port, $WaitSeconds)
Write-Host ("[FAIL] port {0} did not become healthy in {1}s - see logs\_server_run.log" -f $Port, $WaitSeconds)
exit 1
