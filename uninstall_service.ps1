# =============================================================================
#  Gelegram - Windows Service Uninstaller
# =============================================================================
#  Stops and removes the Gelegram Windows service.
#
#  Usage:
#    powershell -ExecutionPolicy Bypass -File uninstall_service.ps1
#    (Self-elevates to Administrator if needed)
# =============================================================================

$ErrorActionPreference = "Stop"

# -- Self-elevation: re-launch as Administrator if not already elevated --------
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Host "  [!] Not running as Administrator. Requesting elevation..." -ForegroundColor Yellow
    $scriptPath = $MyInvocation.MyCommand.Definition
    Start-Process powershell.exe -ArgumentList "-ExecutionPolicy Bypass -File `"$scriptPath`"" -Verb RunAs
    exit 0
}

$ServiceName = "Gelegram"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$NssmExe = Join-Path $ScriptDir "tools\nssm.exe"

Write-Host ""
Write-Host "  ========================================================" -ForegroundColor Magenta
Write-Host "    Gelegram Service Uninstaller" -ForegroundColor White
Write-Host "  ========================================================" -ForegroundColor Magenta
Write-Host ""

# Check if NSSM exists
if (-not (Test-Path $NssmExe)) {
    Write-Host "  [X] NSSM not found at: $NssmExe" -ForegroundColor Red
    Write-Host "  Trying system-level sc.exe fallback..." -ForegroundColor Yellow

    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        sc.exe delete $ServiceName
        Write-Host "  [OK] Service removed via sc.exe" -ForegroundColor Green
    } else {
        Write-Host "  [i] Service '$ServiceName' not found. Nothing to uninstall." -ForegroundColor Yellow
    }
    Read-Host "Press Enter to close"
    exit 0
}

# Check if service exists
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host "  [i] Service '$ServiceName' is not installed. Nothing to do." -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 0
}

Write-Host "  >> Stopping service '$ServiceName' ..." -ForegroundColor Cyan
try { & $NssmExe stop $ServiceName 2>&1 | Out-Null } catch {}
Start-Sleep -Seconds 3

Write-Host "  >> Removing service '$ServiceName' ..." -ForegroundColor Cyan
try { & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null } catch {}

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "  [OK] Service '$ServiceName' has been removed successfully." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Note: Log files in 'logs/' and 'gateway.log' were NOT deleted." -ForegroundColor Yellow
    Write-Host "  Delete them manually if no longer needed." -ForegroundColor DarkGray
} else {
    Write-Host ""
    Write-Host "  [X] Failed to remove service. Exit code: $LASTEXITCODE" -ForegroundColor Red
    Write-Host "  Try manually: sc.exe delete $ServiceName" -ForegroundColor Yellow
}

Write-Host ""
Read-Host "Press Enter to close"
