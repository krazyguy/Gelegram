# =============================================================================
#  Gelegram – Onboarding Setup Script
# =============================================================================
#  This script automates the full installation pipeline for Gelegram:
#    1. Checks/installs Node.js (via winget, or MSI fallback)
#    2. Checks/installs uv (Python package manager)
#    3. Checks/installs Gemini CLI (npm global)
#    4. Creates Python venv and installs dependencies
#    5. Configures .env (bot token, password, workspace path, gemini CLI path)
#    6. Runs gemini auth for Google OAuth
#    7. Optionally installs the Windows service (default: Yes)
#
#  Usage:
#    powershell -ExecutionPolicy Bypass -File setup.ps1
#
#  The script is idempotent — safe to re-run. Already-completed steps are
#  skipped with a green [OK] message.
# =============================================================================

$ErrorActionPreference = "Stop"

# ─────────────────────────────────────────────────────────────────────────────
# Resolve project root from the script's own location
# ─────────────────────────────────────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions – consistent with install_service.ps1 colour scheme
# ─────────────────────────────────────────────────────────────────────────────

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

function Write-Info {
    param([string]$Message)
    Write-Host "    $Message" -ForegroundColor White
}

# ─────────────────────────────────────────────────────────────────────────────
# Utility: Refresh PATH from registry so newly installed tools are visible
# in the current session without restarting the terminal.
# ─────────────────────────────────────────────────────────────────────────────
function Refresh-Path {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

# ─────────────────────────────────────────────────────────────────────────────
# Utility: Test if a command exists on the current PATH
# ─────────────────────────────────────────────────────────────────────────────
function Test-Command {
    param([string]$Name)
    try {
        $null = Get-Command $Name -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

# =============================================================================
#  BANNER
# =============================================================================

Write-Host ""
Write-Host "  ========================================================" -ForegroundColor Magenta
Write-Host "    Gelegram – Onboarding Setup" -ForegroundColor White
Write-Host "  ========================================================" -ForegroundColor Magenta
Write-Host ""

# =============================================================================
#  PHASE 1: Pre-flight & Detection
# =============================================================================

Write-Step "Detecting environment"

$CurrentUser    = $env:USERNAME
$CurrentDomain  = $env:USERDOMAIN
$UserProfile    = $env:USERPROFILE
$GeminiDir      = Join-Path $UserProfile ".gemini"

Write-Info "User       : $CurrentDomain\$CurrentUser"
Write-Info "Profile    : $UserProfile"
Write-Info "Project    : $ScriptDir"

if (Test-Path $GeminiDir) {
    Write-Ok ".gemini directory found: $GeminiDir"
} else {
    Write-Notice ".gemini directory not found — will be created during gemini auth"
}

# =============================================================================
#  PHASE 2: Dependency Installation
# =============================================================================

# ── 2.1  Node.js ─────────────────────────────────────────────────────────────

Write-Step "Checking Node.js"

if (Test-Command "node") {
    $nodeVersion = & node --version 2>$null
    Write-Ok "Node.js already installed: $nodeVersion"
} else {
    Write-Notice "Node.js not found. Installing..."

    $wingetAvailable = Test-Command "winget"

    if ($wingetAvailable) {
        # ── winget is available — use it directly ────────────────────────
        Write-Notice "Installing Node.js LTS via winget..."
        try {
            & winget install OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements
            Refresh-Path

            if (Test-Command "node") {
                $nodeVersion = & node --version 2>$null
                Write-Ok "Node.js installed successfully: $nodeVersion"
            } else {
                Write-Err "Node.js was installed but 'node' is not on PATH yet."
                Write-Notice "You may need to restart your terminal after setup completes."
            }
        } catch {
            Write-Err "winget install failed: $_"
            Write-Notice "Falling back to MSI download..."
            $wingetAvailable = $false  # trigger MSI fallback below
        }
    }

    if (-not $wingetAvailable) {
        # ── winget not available — ask user ──────────────────────────────
        Write-Notice "winget is not available on this system."
        $installWinget = Read-Host "    Install winget first? (Y/n)"

        if ($installWinget -eq "" -or $installWinget -match "^[Yy]") {
            # Download and install winget (App Installer from Microsoft)
            Write-Notice "Downloading winget (App Installer)..."
            $wingetUrl = "https://aka.ms/getwinget"
            $wingetMsix = Join-Path $ScriptDir "tools\Microsoft.DesktopAppInstaller.msixbundle"
            New-Item -ItemType Directory -Path (Join-Path $ScriptDir "tools") -Force | Out-Null

            try {
                Invoke-WebRequest -Uri $wingetUrl -OutFile $wingetMsix -UseBasicParsing
                Write-Notice "Installing winget..."
                Add-AppxPackage -Path $wingetMsix
                Remove-Item $wingetMsix -Force -ErrorAction SilentlyContinue
                Refresh-Path

                if (Test-Command "winget") {
                    Write-Ok "winget installed successfully."
                    Write-Notice "Installing Node.js LTS via winget..."
                    & winget install OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements
                    Refresh-Path
                } else {
                    Write-Err "winget installed but not detected. Falling back to MSI."
                }
            } catch {
                Write-Err "Failed to install winget: $_"
                Write-Notice "Falling back to Node.js MSI download..."
            }
        }

        # ── MSI fallback (if winget didn't work or user declined) ────────
        if (-not (Test-Command "node")) {
            Write-Notice "Downloading Node.js LTS installer from nodejs.org..."
            $nodeUrl = "https://nodejs.org/dist/v22.15.0/node-v22.15.0-x64.msi"
            $nodeMsi = Join-Path $ScriptDir "tools\node_installer.msi"
            New-Item -ItemType Directory -Path (Join-Path $ScriptDir "tools") -Force | Out-Null

            try {
                Invoke-WebRequest -Uri $nodeUrl -OutFile $nodeMsi -UseBasicParsing
                Write-Notice "Running Node.js installer (silent mode)..."
                Start-Process msiexec.exe -ArgumentList "/i `"$nodeMsi`" /qn /norestart" -Wait -NoNewWindow
                Remove-Item $nodeMsi -Force -ErrorAction SilentlyContinue
                Refresh-Path

                if (Test-Command "node") {
                    $nodeVersion = & node --version 2>$null
                    Write-Ok "Node.js installed via MSI: $nodeVersion"
                } else {
                    Write-Err "Node.js MSI installed but 'node' not on PATH."
                    Write-Notice "You may need to restart your terminal."
                }
            } catch {
                Write-Err "Failed to download/install Node.js: $_"
                Write-Err "Please install Node.js manually from https://nodejs.org"
                Read-Host "Press Enter to exit"
                exit 1
            }
        }
    }
}

# ── 2.2  uv ──────────────────────────────────────────────────────────────────

Write-Step "Checking uv (Python package manager)"

if (Test-Command "uv") {
    $uvVersion = & uv --version 2>$null
    Write-Ok "uv already installed: $uvVersion"
} else {
    Write-Notice "uv not found. Installing..."
    try {
        # Official uv installer for Windows
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        Refresh-Path

        if (Test-Command "uv") {
            $uvVersion = & uv --version 2>$null
            Write-Ok "uv installed successfully: $uvVersion"
        } else {
            # uv installs to ~/.local/bin or CARGO_HOME — add common location
            $uvLocalBin = Join-Path $UserProfile ".local\bin"
            if (Test-Path (Join-Path $uvLocalBin "uv.exe")) {
                $env:Path = "$uvLocalBin;$env:Path"
                Write-Ok "uv installed at $uvLocalBin"
            } else {
                Write-Err "uv was installed but not found on PATH."
                Write-Notice "You may need to restart your terminal."
            }
        }
    } catch {
        Write-Err "Failed to install uv: $_"
        Write-Notice "Install manually: https://github.com/astral-sh/uv"
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# ── 2.3  Gemini CLI ──────────────────────────────────────────────────────────

Write-Step "Checking Gemini CLI"

# Check if gemini CLI is installed via npm global list
$geminiInstalled = $false
try {
    $npmListOutput = & npm list -g @google/gemini-cli 2>$null
    if ($npmListOutput -match "gemini-cli") {
        $geminiInstalled = $true
    }
} catch {}

# Also try the command directly (handles manual installs)
if (-not $geminiInstalled -and (Test-Command "gemini")) {
    $geminiInstalled = $true
}

if ($geminiInstalled) {
    Write-Ok "Gemini CLI already installed."
} else {
    Write-Notice "Gemini CLI not found. Installing via npm..."
    try {
        & npm install -g @google/gemini-cli
        Refresh-Path
        Write-Ok "Gemini CLI installed."
    } catch {
        Write-Err "Failed to install Gemini CLI: $_"
        Write-Notice "Install manually: npm install -g @google/gemini-cli"
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# =============================================================================
#  PHASE 3: Python Environment
# =============================================================================

Write-Step "Setting up Python virtual environment"

$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
    Write-Ok "Virtual environment already exists: .venv"
} else {
    Write-Notice "Creating virtual environment with uv..."
    try {
        Push-Location $ScriptDir
        & uv venv .venv
        Pop-Location
        Write-Ok "Virtual environment created."
    } catch {
        Write-Err "Failed to create venv: $_"
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# ── Install Python dependencies ──────────────────────────────────────────────

Write-Step "Installing Python dependencies"

$RequirementsFile = Join-Path $ScriptDir "requirements.txt"

if (-not (Test-Path $RequirementsFile)) {
    Write-Err "requirements.txt not found at: $RequirementsFile"
    Read-Host "Press Enter to exit"
    exit 1
}

try {
    Write-Notice "Running: uv pip install -r requirements.txt"
    Push-Location $ScriptDir
    & uv pip install -r requirements.txt --python "$VenvPython"
    Pop-Location
    Write-Ok "Dependencies installed."
} catch {
    Write-Err "Failed to install dependencies: $_"
    Read-Host "Press Enter to exit"
    exit 1
}

# =============================================================================
#  PHASE 4: Configuration (.env)
# =============================================================================

Write-Step "Configuring environment (.env)"

$EnvFile     = Join-Path $ScriptDir ".env"
$EnvExample  = Join-Path $ScriptDir ".env.example"

if (Test-Path $EnvFile) {
    Write-Ok ".env file already exists."
    Write-Notice "Skipping configuration prompts (edit .env manually if needed)."
} else {
    if (-not (Test-Path $EnvExample)) {
        Write-Err ".env.example template not found!"
        Read-Host "Press Enter to exit"
        exit 1
    }

    # Read the template
    $envContent = Get-Content $EnvExample -Raw

    # ── Prompt: Telegram Bot Token ───────────────────────────────────────
    Write-Host ""
    Write-Host "    Please provide the following configuration values." -ForegroundColor White
    Write-Host "    (Get a bot token from @BotFather on Telegram)" -ForegroundColor DarkGray
    Write-Host ""

    $botToken = Read-Host "    Enter your Telegram Bot Token"
    if ($botToken) {
        $envContent = $envContent -replace "TELEGRAM_BOT_TOKEN=.*", "TELEGRAM_BOT_TOKEN=$botToken"
    } else {
        Write-Notice "No token entered — you'll need to edit .env manually."
    }

    # ── Prompt: Bot Password ─────────────────────────────────────────────
    $botPassword = Read-Host "    Set a Telegram password (or press Enter to skip)"
    if ($botPassword) {
        $envContent = $envContent -replace "BOT_PASSWORD=.*", "BOT_PASSWORD=$botPassword"
    }

    # ── Prompt: Workspace Location ───────────────────────────────────────
    $defaultWorkspace = ".\workdir"
    $workspace = Read-Host "    Workspace directory (default: $defaultWorkspace)"
    if (-not $workspace) {
        $workspace = $defaultWorkspace
    }
    $envContent = $envContent -replace "GEMINI_WORKING_DIR=.*", "GEMINI_WORKING_DIR=$workspace"

    # ── Detect Gemini CLI Path ───────────────────────────────────────────
    # Try where.exe first (works if gemini was already on PATH before this session).
    # Fall back to the standard npm global install location on Windows.
    $geminiCliPath = $null

    try {
        $whereResult = & where.exe gemini 2>$null | Select-Object -First 1
        if ($whereResult -and (Test-Path $whereResult)) {
            $geminiCliPath = $whereResult
            Write-Ok "Gemini CLI found via where: $geminiCliPath"
        }
    } catch {}

    if (-not $geminiCliPath) {
        # Fallback: standard npm global install path on Windows
        $defaultGeminiPath = Join-Path $env:APPDATA "npm\gemini.cmd"
        if (Test-Path $defaultGeminiPath) {
            $geminiCliPath = $defaultGeminiPath
            Write-Ok "Gemini CLI found at default location: $geminiCliPath"
        } else {
            Write-Notice "Gemini CLI path could not be auto-detected."
            Write-Notice "Setting to 'gemini' (assumes it will be on PATH)."
            $geminiCliPath = "gemini"
        }
    }

    $envContent = $envContent -replace "GEMINI_CLI_PATH=.*", "GEMINI_CLI_PATH=$geminiCliPath"

    # ── Write the .env file ──────────────────────────────────────────────
    $envContent | Set-Content -Path $EnvFile -Encoding UTF8 -NoNewline
    Write-Ok ".env file created successfully."
}

# =============================================================================
#  PHASE 5: Gemini Auth
# =============================================================================

Write-Step "Gemini CLI Authentication"

Write-Host ""
Write-Host "    Gemini CLI needs Google OAuth to function." -ForegroundColor White
Write-Host "    A browser window will open — log in with your Google account." -ForegroundColor White
Write-Host "    Close the gemini session after authentication completes." -ForegroundColor DarkGray
Write-Host ""

# Resolve gemini CLI path for auth (same logic as .env detection)
$geminiAuthCmd = $null
try {
    $whereResult = & where.exe gemini 2>$null | Select-Object -First 1
    if ($whereResult -and (Test-Path $whereResult)) {
        $geminiAuthCmd = $whereResult
    }
} catch {}

if (-not $geminiAuthCmd) {
    $defaultGeminiPath = Join-Path $env:APPDATA "npm\gemini.cmd"
    if (Test-Path $defaultGeminiPath) {
        $geminiAuthCmd = $defaultGeminiPath
    }
}

if ($geminiAuthCmd) {
    try {
        Write-Notice "Running: gemini auth"
        # Run gemini auth — this opens a browser for Google OAuth
        & cmd /c "$geminiAuthCmd" auth
        Write-Ok "Gemini authentication completed."
    } catch {
        Write-Notice "gemini auth exited (this may be normal): $_"
    }
} else {
    Write-Notice "Could not locate gemini CLI to run auth."
    Write-Notice "After setup, run 'gemini auth' manually to authenticate."
}

Write-Host ""
Write-Host "    REMINDER: Make sure you logged in with your Google account!" -ForegroundColor Yellow
Write-Host "    If the browser didn't open, run 'gemini auth' manually." -ForegroundColor Yellow
Write-Host ""

# =============================================================================
#  PHASE 6: Service Installation
# =============================================================================

Write-Step "Windows Service Installation"

Write-Host ""
Write-Host "    Install Gelegram as a Windows background service?" -ForegroundColor White
Write-Host "    This makes the bot start automatically on boot and" -ForegroundColor White
Write-Host "    survive crashes with automatic restarts." -ForegroundColor White
Write-Host ""

$installService = Read-Host "    Install as service? [Y/n] (default: Y)"

if ($installService -eq "" -or $installService -match "^[Yy]") {
    Write-Notice "Launching service installer (will request Administrator elevation)..."
    $installScript = Join-Path $ScriptDir "install_service.ps1"

    if (Test-Path $installScript) {
        try {
            & powershell -ExecutionPolicy Bypass -File "$installScript"
        } catch {
            Write-Err "Service installation failed: $_"
            Write-Notice "You can run install_service.ps1 manually later."
        }
    } else {
        Write-Err "install_service.ps1 not found at: $installScript"
    }
} else {
    Write-Ok "Skipping service installation."
    Write-Host ""
    Write-Host "    To run the bot manually:" -ForegroundColor White
    Write-Host "      .venv\Scripts\activate" -ForegroundColor DarkGray
    Write-Host "      python gateway.py            # with watchdog (recommended)" -ForegroundColor DarkGray
    Write-Host "      python bot.py                # without watchdog" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "    To install as a service later:" -ForegroundColor White
    Write-Host "      powershell -File install_service.ps1" -ForegroundColor DarkGray
    Write-Host ""
}

# =============================================================================
#  SUMMARY
# =============================================================================

Write-Host ""
Write-Host "  ========================================================" -ForegroundColor Magenta
Write-Host "    Gelegram Setup Complete!" -ForegroundColor Green
Write-Host "  ========================================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "  User         : $CurrentDomain\$CurrentUser" -ForegroundColor White
Write-Host "  Profile      : $UserProfile" -ForegroundColor White
Write-Host "  Project      : $ScriptDir" -ForegroundColor White
Write-Host "  .gemini      : $GeminiDir" -ForegroundColor White
Write-Host ""
Write-Host "  IMPORTANT REMINDERS:" -ForegroundColor Yellow
Write-Host "    1. If gemini auth didn't complete, run: gemini auth" -ForegroundColor Yellow
Write-Host "    2. Message your bot on Telegram — it will guide you" -ForegroundColor Yellow
Write-Host "       through identity setup on the first message." -ForegroundColor Yellow
Write-Host ""

Read-Host "Press Enter to close"
