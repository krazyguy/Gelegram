# =============================================================================
#  Gelegram - Windows Service Installer (via NSSM)
# =============================================================================
#  This script:
#    1. Self-elevates to Administrator if not already running as admin
#    2. Downloads NSSM (Non-Sucking Service Manager) if not already present
#    3. Registers gateway.py as a Windows service named "Gelegram"
#    4. Configures auto-start on boot and auto-restart on failure
#    5. Starts the service immediately
#
#  Usage:
#    powershell -ExecutionPolicy Bypass -File install_service.ps1
#
#  To check status:   nssm status Gelegram
#  To stop/start:     nssm stop Gelegram / nssm start Gelegram
#  To uninstall:      powershell -File uninstall_service.ps1
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

# -- Configuration ------------------------------------------------------------
$ServiceName = "Gelegram"
$ServiceDisplayName = "Gelegram Telegram Bot"
$ServiceDescription = "Telegram to Gemini CLI ACP Bridge Bot with auto-restart watchdog"

# -- Get current user credentials to run service as THIS user -----------------
# Running as LocalSystem causes Gemini auth failures and permission conflicts.
# We run the service under the current rdp user account instead.
$RunAsUser = $env:USERDOMAIN + "\" + $env:USERNAME
Write-Host ""
Write-Host "  Service will run as: $RunAsUser" -ForegroundColor Cyan
$RunAsPassword = Read-Host "  Enter Windows password for '$RunAsUser'" -AsSecureString
$RunAsPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($RunAsPassword)
)

# -- Resolve the real USERPROFILE for the service account ---------------------
# When signed in with a Microsoft / email account, the service token may not
# carry the correct USERPROFILE.  We resolve it now (while running interactively
# as the actual user) and persist it so the service can restore it at runtime.
# NOTE: $ScriptDir and helper functions (Write-Ok etc.) are defined further below;
#       the file write is deferred until after they are available.
$RealUserProfile = $env:USERPROFILE
Write-Host "  Detected USERPROFILE : $RealUserProfile" -ForegroundColor Cyan

# Verify the .gemini folder exists so we catch mismatched accounts early
$GeminiDir = Join-Path $RealUserProfile ".gemini"
if (-not (Test-Path $GeminiDir)) {
    Write-Host "    [!] WARNING: .gemini directory not found at '$GeminiDir'." -ForegroundColor Yellow
    Write-Host "    [!] Gemini CLI auth may fail until you run 'gemini auth' as this user." -ForegroundColor Yellow
} else {
    Write-Host "    [OK] .gemini directory confirmed: $GeminiDir" -ForegroundColor Green
}

# Resolve paths relative to this script's location
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$GatewayScript = Join-Path $ScriptDir "gateway.py"
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$BatLauncher = Join-Path $ScriptDir "run_gateway.bat"
$NssmDir = Join-Path $ScriptDir "tools"
$NssmExe = Join-Path $NssmDir "nssm.exe"
$LogDir = Join-Path $ScriptDir "logs"

# Persist USERPROFILE hint now that $ScriptDir is available
# (run_gateway.bat and bot.py read this at service startup to restore env)
$ProfileHintFile = Join-Path $ScriptDir "service_userprofile.txt"
$RealUserProfile | Out-File -FilePath $ProfileHintFile -Encoding ascii -NoNewline
Write-Host "    [OK] Saved USERPROFILE hint: $ProfileHintFile" -ForegroundColor Green

# NSSM download URL (portable, no installer needed)
# Using the stable 2.24 release - widely tested and reliable
$NssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
$NssmZip = Join-Path $NssmDir "nssm.zip"

# -- Helper Functions ----------------------------------------------------------

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "  >> $Message" -ForegroundColor Cyan
    Write-Host "  $('-' * 60)" -ForegroundColor DarkGray
}

function Write-Ok {
    param([string]$Message)
    Write-Host "    [OK] $Message" -ForegroundColor Green
}

function Write-Notice {
    param([string]$Message)
    Write-Host "    [i] $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "    [X] $Message" -ForegroundColor Red
}

# -- Pre-flight Checks --------------------------------------------------------

Write-Host ""
Write-Host "  ========================================================" -ForegroundColor Magenta
Write-Host "    Gelegram Service Installer" -ForegroundColor White
Write-Host "  ========================================================" -ForegroundColor Magenta
Write-Host ""

Write-Step "Validating environment"

if (-not (Test-Path $VenvPython)) {
    Write-Err "Virtual environment Python not found at: $VenvPython"
    Write-Notice "Create it with: uv venv .venv"
    Write-Notice "Then install deps: uv pip install -r requirements.txt"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Ok "Python venv found: $VenvPython"

if (-not (Test-Path $GatewayScript)) {
    Write-Err "Gateway script not found at: $GatewayScript"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Ok "Gateway script found: $GatewayScript"

if (-not (Test-Path $BatLauncher)) {
    Write-Err "Batch launcher not found at: $BatLauncher"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Ok "Batch launcher found: $BatLauncher"

# -- Download NSSM if needed --------------------------------------------------

Write-Step "Checking for NSSM"

if (-not (Test-Path $NssmExe)) {
    Write-Notice "NSSM not found locally. Downloading..."

    # Create tools directory
    New-Item -ItemType Directory -Path $NssmDir -Force | Out-Null

    try {
        # Download NSSM zip
        Write-Notice "Downloading from $NssmUrl ..."
        Invoke-WebRequest -Uri $NssmUrl -OutFile $NssmZip -UseBasicParsing

        # Extract the correct architecture binary
        Write-Notice "Extracting NSSM..."
        Expand-Archive -Path $NssmZip -DestinationPath $NssmDir -Force

        # NSSM zip contains: nssm-2.24/win64/nssm.exe (and win32 variant)
        # Copy the correct architecture to our tools directory
        $arch = if ([System.Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
        $extractedNssm = Get-ChildItem -Path $NssmDir -Recurse -Filter "nssm.exe" |
            Where-Object { $_.Directory.Name -eq $arch } |
            Select-Object -First 1

        if ($extractedNssm) {
            Copy-Item $extractedNssm.FullName $NssmExe -Force
            Write-Ok "NSSM extracted: $NssmExe"
        } else {
            # Fallback: just find any nssm.exe
            $anyNssm = Get-ChildItem -Path $NssmDir -Recurse -Filter "nssm.exe" | Select-Object -First 1
            if ($anyNssm) {
                Copy-Item $anyNssm.FullName $NssmExe -Force
                Write-Ok "NSSM extracted (fallback): $NssmExe"
            } else {
                Write-Err "Could not find nssm.exe in the downloaded archive."
                Read-Host "Press Enter to exit"
                exit 1
            }
        }

        # Clean up extraction artifacts (keep only the final nssm.exe)
        Remove-Item $NssmZip -Force -ErrorAction SilentlyContinue
        Get-ChildItem -Path $NssmDir -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
    catch {
        Write-Err "Failed to download NSSM: $_"
        Write-Notice "You can manually download NSSM from https://nssm.cc/download"
        Write-Notice "Place nssm.exe in: $NssmDir"
        Read-Host "Press Enter to exit"
        exit 1
    }
} else {
    Write-Ok "NSSM found: $NssmExe"
}

# -- Remove existing service if present ---------------------------------------

Write-Step "Checking for existing service"

$existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Notice "Service '$ServiceName' already exists (Status: $($existingService.Status))"
    Write-Notice "Stopping and removing existing service..."

    # Use ErrorAction SilentlyContinue because $ErrorActionPreference = "Stop"
    # causes PowerShell to treat NSSM's benign stderr messages (e.g. "not started")
    # as terminating errors, aborting the entire script.
    try { & $NssmExe stop $ServiceName 2>&1 | Out-Null } catch {}
    Start-Sleep -Seconds 2
    try { & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null } catch {}
    Start-Sleep -Seconds 1

    Write-Ok "Existing service removed."
} else {
    Write-Ok "No existing service found. Clean install."
}

# -- Create log directory ------------------------------------------------------

Write-Step "Setting up log directory"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Write-Ok "Log directory: $LogDir"

# -- Install the service ------------------------------------------------------

Write-Step "Installing Windows service"

# Register the service using run_gateway.bat as the Application.
# The .bat wrapper uses %~dp0 expansion to resolve paths with spaces ("py 3.0")
# correctly, avoiding NSSM's known issues with spaces in Application/AppParameters.
# No AppParameters needed since the .bat handles everything internally.
& $NssmExe install $ServiceName $BatLauncher

if ($LASTEXITCODE -ne 0) {
    Write-Err "Failed to install service. Exit code: $LASTEXITCODE"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Ok "Service installed."

# -- Configure service parameters ---------------------------------------------

Write-Step "Configuring service parameters"

# Display name and description
& $NssmExe set $ServiceName DisplayName $ServiceDisplayName
& $NssmExe set $ServiceName Description $ServiceDescription

# Run as the rdp user, NOT LocalSystem.
# This ensures: correct Gemini credentials, correct env vars, user can manage their own process.
& $NssmExe set $ServiceName ObjectName $RunAsUser $RunAsPlain

# Working directory (bat file handles cd internally, but set for NSSM logs etc.)
& $NssmExe set $ServiceName AppDirectory "`"$ScriptDir`""

# Startup type: Automatic (starts on boot/login)
& $NssmExe set $ServiceName Start SERVICE_AUTO_START

# NSSM's own restart behavior (second safety net on top of gateway.py's watchdog)
# AppRestartDelay: milliseconds to wait before NSSM restarts the process
& $NssmExe set $ServiceName AppRestartDelay 10000

# AppThrottle: minimum milliseconds between restarts to prevent rapid looping
& $NssmExe set $ServiceName AppThrottle 5000

# AppExit Default: Restart - if gateway.py itself crashes, NSSM restarts it
& $NssmExe set $ServiceName AppExit Default Restart

# Log stdout/stderr to files (NSSM captures what gateway.py outputs)
$stdoutLog = Join-Path $LogDir "gelegram_stdout.log"
$stderrLog = Join-Path $LogDir "gelegram_stderr.log"
& $NssmExe set $ServiceName AppStdout "`"$stdoutLog`""
& $NssmExe set $ServiceName AppStderr "`"$stderrLog`""

# Log file rotation: rotate when file exceeds 10MB, keep appending
& $NssmExe set $ServiceName AppStdoutCreationDisposition 4
& $NssmExe set $ServiceName AppStderrCreationDisposition 4
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateOnline 1
& $NssmExe set $ServiceName AppRotateBytes 10485760

# Graceful shutdown: send Ctrl+C first, wait 10s, then kill
& $NssmExe set $ServiceName AppStopMethodSkip 0
& $NssmExe set $ServiceName AppStopMethodConsole 10000
& $NssmExe set $ServiceName AppStopMethodWindow 10000
& $NssmExe set $ServiceName AppStopMethodThreads 10000

# ── Inject correct env vars via NSSM AppEnvironmentExtra ─────────────────
# AppEnvironmentExtra adds variables to the service's env block at SCM start
# time, BEFORE the service process is created.  For MSA (Microsoft account)
# users the SCM may not load the user's registry hive, resulting in a broken
# USERPROFILE / APPDATA / HOME.  Setting them here is the earliest fix point.
$RealAppData     = Join-Path $RealUserProfile "AppData\Roaming"
$RealLocalAppData = Join-Path $RealUserProfile "AppData\Local"

& $NssmExe set $ServiceName AppEnvironmentExtra `
    "USERPROFILE=$RealUserProfile" `
    "HOME=$RealUserProfile" `
    "APPDATA=$RealAppData" `
    "LOCALAPPDATA=$RealLocalAppData"

Write-Ok "Environment fix injected (USERPROFILE=$RealUserProfile)."

Write-Ok "Service configured."

# -- Start the service --------------------------------------------------------

Write-Step "Starting service"

# NSSM may report SERVICE_START_PENDING/SERVICE_PAUSED to stderr during startup.
# These are intermediate states, not real errors. We ignore NSSM's exit code and
# instead wait then query the actual Windows service status directly.
try { & $NssmExe start $ServiceName 2>&1 | Out-Null } catch {}

Start-Sleep -Seconds 4

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $svc -or $svc.Status -ne "Running") {
    Write-Err "Service failed to start. Status: $($svc.Status). Check logs at: $LogDir"
    Write-Notice "Try: nssm status $ServiceName"
    Read-Host "Press Enter to exit"
    exit 1
}

Start-Sleep -Seconds 3

# Verify it's running
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Ok "Service is running!"
} else {
    Write-Notice "Service status: $($svc.Status) - it may still be starting up."
    Write-Notice "Check logs: $stdoutLog"
}

# -- Summary -------------------------------------------------------------------

Write-Host ""
Write-Host "  ========================================================" -ForegroundColor Magenta
Write-Host "    Installation Complete!" -ForegroundColor Green
Write-Host "  ========================================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "  Service Name : $ServiceName" -ForegroundColor White
Write-Host "  Status       : $($svc.Status)" -ForegroundColor White
Write-Host "  Startup      : Automatic (starts on boot)" -ForegroundColor White
Write-Host "  Auto-restart : Yes (double-layer: NSSM + gateway.py)" -ForegroundColor White
Write-Host ""
Write-Host "  Useful commands (run in normal PowerShell):" -ForegroundColor Yellow
Write-Host "    Get-Service Gelegram                          # Check status" -ForegroundColor DarkGray
Write-Host "    Stop-Service Gelegram                         # Stop service" -ForegroundColor DarkGray
Write-Host "    Start-Service Gelegram                        # Start service" -ForegroundColor DarkGray
Write-Host "    Restart-Service Gelegram                      # Restart service" -ForegroundColor DarkGray
Write-Host "    Get-Content '$stdoutLog' -Tail 50 -Wait       # Live log tail" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  NSSM commands (use full path, needs admin):" -ForegroundColor Yellow
Write-Host "    $NssmExe status $ServiceName" -ForegroundColor DarkGray
Write-Host "    $NssmExe stop $ServiceName" -ForegroundColor DarkGray
Write-Host "    $NssmExe start $ServiceName" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  To uninstall:" -ForegroundColor Yellow
Write-Host "    powershell -File uninstall_service.ps1" -ForegroundColor DarkGray
Write-Host ""

# Keep the elevated window open so the user can see the output
Read-Host "Press Enter to close"
