#!/usr/bin/env bash
# =============================================================================
#  Gelegram - Onboarding Setup Script (Linux / macOS)
# =============================================================================
#  This script automates the full installation pipeline for Gelegram:
#    1. Checks/installs Node.js (via nvm, brew, or system package manager)
#    2. Checks/installs uv (Python package manager)
#    3. Checks/installs Gemini CLI (npm global)
#    4. Creates Python venv and installs dependencies
#    5. Configures .env (bot token, password, workspace path, gemini CLI path)
#    6. Optionally installs as a background service
#         Linux  : systemd user service
#         macOS  : launchd LaunchAgent plist
#    7. Runs gemini auth for Google OAuth (final step)
#
#  Usage:
#    chmod +x setup.sh && ./setup.sh
#
#  The script is idempotent -- safe to re-run. Already-completed steps are
#  skipped with a green [OK] message.
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# Resolve script directory (follows symlinks)
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -----------------------------------------------------------------------------
# Detect OS
# -----------------------------------------------------------------------------
OS="$(uname -s)"
case "$OS" in
    Linux*)   PLATFORM="linux" ;;
    Darwin*)  PLATFORM="macos" ;;
    *)        echo "[X] Unsupported platform: $OS. Use setup.ps1 on Windows."; exit 1 ;;
esac

# -----------------------------------------------------------------------------
# Colour helpers
# -----------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
BOLD='\033[1m'
NC='\033[0m' # No Colour

write_step()   { echo ""; echo -e "  ${CYAN}>> $1${NC}"; echo -e "  ${GRAY}$(printf '%0.s-' {1..60})${NC}"; }
write_ok()     { echo -e "    ${GREEN}[OK]${NC} $1"; }
write_notice() { echo -e "    ${YELLOW}[i]${NC} $1"; }
write_err()    { echo -e "    ${RED}[X]${NC} $1"; }
write_info()   { echo -e "    ${BOLD}$1${NC}"; }

# Prompt helper (compatible with bash and zsh)
ask() {
    local prompt="$1"
    local default="${2:-}"
    local answer
    if [ -n "$default" ]; then
        read -r -p "    $prompt (default: $default): " answer
        echo "${answer:-$default}"
    else
        read -r -p "    $prompt: " answer
        echo "$answer"
    fi
}

# =============================================================================
#  BANNER
# =============================================================================

echo ""
echo -e "  ${BOLD}========================================================"
echo -e "    Gelegram - Onboarding Setup (${PLATFORM})"
echo -e "  ========================================================${NC}"
echo ""

# =============================================================================
#  PHASE 1: Pre-flight & Detection
# =============================================================================

write_step "Detecting environment"

CURRENT_USER="$(whoami)"
USER_HOME="$HOME"
GEMINI_DIR="$HOME/.gemini"

write_info "User       : $CURRENT_USER"
write_info "Home       : $USER_HOME"
write_info "Project    : $SCRIPT_DIR"
write_info "Platform   : $PLATFORM"

if [ -d "$GEMINI_DIR" ]; then
    write_ok ".gemini directory found: $GEMINI_DIR"
else
    write_notice ".gemini directory not found -- will be created during gemini auth"
fi

# =============================================================================
#  PHASE 2: Dependency Installation
# =============================================================================

# -- 2.1  Node.js --------------------------------------------------------------

write_step "Checking Node.js"

if command -v node &>/dev/null; then
    NODE_VER="$(node --version)"
    write_ok "Node.js already installed: $NODE_VER"
else
    write_notice "Node.js not found. Attempting installation..."

    # -- Strategy 1: nvm (most portable, user-level)
    NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [ -s "$NVM_DIR/nvm.sh" ]; then
        # shellcheck source=/dev/null
        source "$NVM_DIR/nvm.sh"
        write_notice "nvm found -- installing Node.js LTS..."
        nvm install --lts
        nvm use --lts
        NODE_VER="$(node --version)"
        write_ok "Node.js installed via nvm: $NODE_VER"
    elif command -v brew &>/dev/null; then
        # -- Strategy 2: Homebrew (macOS / Linux with brew)
        write_notice "Installing Node.js via Homebrew..."
        brew install node
        NODE_VER="$(node --version)"
        write_ok "Node.js installed via brew: $NODE_VER"
    elif [ "$PLATFORM" = "linux" ]; then
        # -- Strategy 3: System package manager
        if command -v apt-get &>/dev/null; then
            write_notice "Installing Node.js via apt (NodeSource LTS)..."
            curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
            sudo apt-get install -y nodejs
        elif command -v dnf &>/dev/null; then
            write_notice "Installing Node.js via dnf..."
            sudo dnf install -y nodejs npm
        elif command -v pacman &>/dev/null; then
            write_notice "Installing Node.js via pacman..."
            sudo pacman -S --noconfirm nodejs npm
        else
            write_err "No supported package manager found (apt/dnf/pacman/brew)."
            write_notice "Install nvm first: https://github.com/nvm-sh/nvm"
            write_notice "Or install Node.js manually: https://nodejs.org"
            exit 1
        fi
        NODE_VER="$(node --version 2>/dev/null || echo 'unknown')"
        write_ok "Node.js installed: $NODE_VER"
    elif [ "$PLATFORM" = "macos" ]; then
        write_err "Homebrew not found. Install it first: https://brew.sh"
        write_notice "Or install nvm: https://github.com/nvm-sh/nvm"
        exit 1
    fi

    # Reload PATH after installation
    export PATH="$PATH:/usr/local/bin:/usr/bin"
fi

# -- 2.2  uv -------------------------------------------------------------------

write_step "Checking uv (Python package manager)"

if command -v uv &>/dev/null; then
    UV_VER="$(uv --version)"
    write_ok "uv already installed: $UV_VER"
else
    write_notice "uv not found. Installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # uv installs to ~/.local/bin or ~/.cargo/bin -- add to PATH for this session
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    if command -v uv &>/dev/null; then
        UV_VER="$(uv --version)"
        write_ok "uv installed: $UV_VER"
    else
        write_err "uv installed but not found on PATH."
        write_notice "Restart your terminal and re-run setup.sh"
        exit 1
    fi
fi

# -- 2.3  Gemini CLI -----------------------------------------------------------

write_step "Checking Gemini CLI"

GEMINI_INSTALLED=false
if npm list -g @google/gemini-cli 2>/dev/null | grep -q "gemini-cli"; then
    GEMINI_INSTALLED=true
fi
if ! $GEMINI_INSTALLED && command -v gemini &>/dev/null; then
    GEMINI_INSTALLED=true
fi

if $GEMINI_INSTALLED; then
    write_ok "Gemini CLI already installed."
else
    write_notice "Gemini CLI not found. Installing via npm..."
    npm install -g @google/gemini-cli
    write_ok "Gemini CLI installed."
fi

# Resolve gemini CLI path (used for .env and auth later)
GEMINI_CLI_PATH=""
if GEMINI_CLI_PATH="$(command -v gemini 2>/dev/null)"; then
    write_ok "Gemini CLI path: $GEMINI_CLI_PATH"
else
    write_notice "Gemini CLI path not on PATH -- will set to 'gemini' in .env"
    GEMINI_CLI_PATH="gemini"
fi

# =============================================================================
#  PHASE 3: Python Environment
# =============================================================================

write_step "Setting up Python virtual environment"

VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ -f "$VENV_PYTHON" ]; then
    write_ok "Virtual environment already exists: .venv"
else
    write_notice "Creating virtual environment with uv..."
    (cd "$SCRIPT_DIR" && uv venv .venv)
    write_ok "Virtual environment created."
fi

# -- Install Python dependencies -----------------------------------------------

write_step "Installing Python dependencies"

REQUIREMENTS="$SCRIPT_DIR/requirements.txt"
if [ ! -f "$REQUIREMENTS" ]; then
    write_err "requirements.txt not found at: $REQUIREMENTS"
    exit 1
fi

write_notice "Running: uv pip install -r requirements.txt"
(cd "$SCRIPT_DIR" && uv pip install -r requirements.txt --python "$VENV_PYTHON")
write_ok "Dependencies installed."

# =============================================================================
#  PHASE 4: Configuration (.env)
# =============================================================================

write_step "Configuring environment (.env)"

ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

if [ -f "$ENV_FILE" ]; then
    write_ok ".env file already exists."
    write_notice "Skipping configuration prompts (edit .env manually if needed)."
else
    if [ ! -f "$ENV_EXAMPLE" ]; then
        write_err ".env.example template not found!"
        exit 1
    fi

    # Read the template
    ENV_CONTENT="$(cat "$ENV_EXAMPLE")"

    echo ""
    echo -e "    ${BOLD}Please provide the following configuration values.${NC}"
    echo -e "    ${GRAY}(Get a bot token from @BotFather on Telegram)${NC}"
    echo ""

    # -- Prompt: Telegram Bot Token
    BOT_TOKEN="$(ask "Enter your Telegram Bot Token")"
    if [ -n "$BOT_TOKEN" ]; then
        ENV_CONTENT="$(echo "$ENV_CONTENT" | sed "s|TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$BOT_TOKEN|")"
    else
        write_notice "No token entered -- edit .env manually."
    fi

    # -- Prompt: Bot Password
    BOT_PASSWORD="$(ask "Set a Telegram password (or press Enter to skip)")"
    if [ -n "$BOT_PASSWORD" ]; then
        ENV_CONTENT="$(echo "$ENV_CONTENT" | sed "s|BOT_PASSWORD=.*|BOT_PASSWORD=$BOT_PASSWORD|")"
    fi

    # -- Prompt: Workspace Location
    WORKSPACE="$(ask "Workspace directory" "./workdir")"
    ENV_CONTENT="$(echo "$ENV_CONTENT" | sed "s|GEMINI_WORKING_DIR=.*|GEMINI_WORKING_DIR=$WORKSPACE|")"

    # -- Set Gemini CLI path
    ENV_CONTENT="$(echo "$ENV_CONTENT" | sed "s|GEMINI_CLI_PATH=.*|GEMINI_CLI_PATH=$GEMINI_CLI_PATH|")"

    # -- Write .env
    echo "$ENV_CONTENT" > "$ENV_FILE"
    write_ok ".env file created successfully."
fi

# =============================================================================
#  PHASE 5: Service Installation
# =============================================================================

write_step "Background Service Installation"

echo ""
echo -e "    ${BOLD}Install Gelegram as a background service?${NC}"
echo -e "    This makes the bot start automatically and survive crashes."
echo ""

INSTALL_SERVICE="$(ask "Install as service? [Y/n]" "Y")"

if [[ "$INSTALL_SERVICE" =~ ^[Yy]$ ]] || [ "$INSTALL_SERVICE" = "Y" ]; then

    if [ "$PLATFORM" = "linux" ]; then
        # -- systemd user service ------------------------------------------------
        write_notice "Checking systemd user session context..."

        # Self-heal systemd user session variables if missing (common in headless SSH or su)
        if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
            export XDG_RUNTIME_DIR="/run/user/$(id -u)"
            if [ ! -d "$XDG_RUNTIME_DIR" ]; then
                # Fallback if the typical system runtime dir isn't initialized
                export XDG_RUNTIME_DIR="/tmp/user-$(id -u)-runtime"
                mkdir -p "$XDG_RUNTIME_DIR"
                chmod 700 "$XDG_RUNTIME_DIR"
            fi
            write_notice "Self-healed XDG_RUNTIME_DIR to: $XDG_RUNTIME_DIR"
        fi

        # Try to infer local D-Bus address if runtime bus socket exists
        if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "$XDG_RUNTIME_DIR/bus" ]; then
            export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
        fi

        write_notice "Creating systemd user service..."

        SYSTEMD_DIR="$HOME/.config/systemd/user"
        mkdir -p "$SYSTEMD_DIR"

        SERVICE_FILE="$SYSTEMD_DIR/gelegram.service"

        cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Gelegram - Telegram to Gemini CLI Bot
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_PYTHON $SCRIPT_DIR/gateway.py
Restart=always
RestartSec=10
StandardOutput=append:$SCRIPT_DIR/gateway.log
StandardError=append:$SCRIPT_DIR/gateway.log
Environment=HOME=$HOME
Environment=USER=$CURRENT_USER
Environment=XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR

[Install]
WantedBy=default.target
EOF

        write_ok "Service file created: $SERVICE_FILE"

        # Test connection to systemd user manager before running command
        # to avoid crashing under 'set -e' if D-Bus is genuinely inaccessible.
        if systemctl --user daemon-reload >/dev/null 2>&1; then
            systemctl --user enable gelegram.service >/dev/null 2>&1 || true
            systemctl --user start gelegram.service >/dev/null 2>&1 || true

            # Enable lingering so service survives logout
            if command -v loginctl &>/dev/null; then
                loginctl enable-linger "$CURRENT_USER" >/dev/null 2>&1 || true
            fi

            write_ok "Service enabled and started."
            write_notice "Check status with:  systemctl --user status gelegram"
            write_notice "View logs with:     journalctl --user -u gelegram -f"
            write_notice "Stop with:          systemctl --user stop gelegram"
        else
            write_err "Could not connect to systemd user manager."
            write_notice "Please run 'systemctl --user daemon-reload' manually."
            write_notice "Skipping automatic service startup. Moving on to Gemini Auth..."
        fi

    elif [ "$PLATFORM" = "macos" ]; then
        # -- launchd LaunchAgent -------------------------------------------------
        write_notice "Creating launchd LaunchAgent..."

        LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
        mkdir -p "$LAUNCH_AGENTS_DIR"

        PLIST_FILE="$LAUNCH_AGENTS_DIR/com.gelegram.bot.plist"

        cat > "$PLIST_FILE" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.gelegram.bot</string>

    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PYTHON</string>
        <string>$SCRIPT_DIR/gateway.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/gateway.log</string>

    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/gateway.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>$HOME</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF

        write_ok "Plist created: $PLIST_FILE"

        # Load it
        launchctl unload "$PLIST_FILE" 2>/dev/null || true
        launchctl load "$PLIST_FILE"

        write_ok "Service loaded and started."
        write_notice "Check status with:  launchctl list | grep gelegram"
        write_notice "View logs with:     tail -f $SCRIPT_DIR/gateway.log"
        write_notice "Stop with:          launchctl unload $PLIST_FILE"
        write_notice "Remove with:        launchctl unload $PLIST_FILE && rm $PLIST_FILE"
    fi

else
    write_ok "Skipping service installation."
    echo ""
    echo -e "    ${BOLD}To run the bot manually:${NC}"
    echo -e "    ${GRAY}  source .venv/bin/activate${NC}"
    echo -e "    ${GRAY}  python gateway.py    # with watchdog (recommended)${NC}"
    echo -e "    ${GRAY}  python bot.py        # without watchdog${NC}"
    echo ""
    if [ "$PLATFORM" = "linux" ]; then
        write_notice "To install the systemd service later: re-run ./setup.sh"
    elif [ "$PLATFORM" = "macos" ]; then
        write_notice "To install the launchd service later: re-run ./setup.sh"
    fi
fi

# =============================================================================
#  PHASE 6: Gemini Auth (final step)
# =============================================================================

write_step "Gemini CLI Authentication"

echo ""
echo -e "    ${BOLD}Gemini CLI needs Google OAuth to function.${NC}"
echo -e "    A browser window will open -- log in with your Google account."
echo -e "    ${GRAY}Close the gemini session (Ctrl+C) after authentication completes.${NC}"
echo ""

if command -v gemini &>/dev/null; then
    write_notice "Running: gemini auth"
    gemini auth || write_notice "gemini auth exited (this may be normal)"
    write_ok "Gemini authentication step completed."
else
    write_notice "Could not locate gemini CLI to run auth."
    write_notice "After setup, run 'gemini auth' manually to authenticate."
fi

echo ""
echo -e "    ${YELLOW}REMINDER: Make sure you logged in with your Google account!${NC}"
echo -e "    ${YELLOW}If the browser didn't open, run 'gemini auth' manually.${NC}"
echo ""

# =============================================================================
#  SUMMARY
# =============================================================================

echo ""
echo -e "  ${BOLD}========================================================"
echo -e "  ${GREEN}  Gelegram Setup Complete!"
echo -e "  ${BOLD}========================================================${NC}"
echo ""
echo -e "  User       : $CURRENT_USER"
echo -e "  Home       : $USER_HOME"
echo -e "  Project    : $SCRIPT_DIR"
echo -e "  .gemini    : $GEMINI_DIR"
echo ""
echo -e "  ${YELLOW}IMPORTANT REMINDERS:${NC}"
echo -e "  ${YELLOW}  1. If gemini auth didn't complete, run: gemini auth${NC}"
echo -e "  ${YELLOW}  2. Message your bot on Telegram -- it will guide you${NC}"
echo -e "  ${YELLOW}     through identity setup on the first message.${NC}"
echo ""
