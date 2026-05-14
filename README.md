# Gelegram 🤖

> **Your personal AI agent — accessible from Telegram, powered by Gemini. It knows who you are, remembers what matters, learns new skills, and keeps running 24/7.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://python.org)
[![Gemini CLI](https://img.shields.io/badge/Gemini_CLI-required-4285F4?logo=google)](https://github.com/google-gemini/gemini-cli)
[![Windows](https://img.shields.io/badge/Windows-0078D6?logo=windows)](https://microsoft.com/windows)
[![Linux](https://img.shields.io/badge/Linux-FCC624?logo=linux&logoColor=black)](https://kernel.org)
[![macOS](https://img.shields.io/badge/macOS-000000?logo=apple)](https://apple.com/macos)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Gelegram is a **lightweight, self-hosted AI agent** built on the Gemini CLI. Think of it as your own pocket assistant — an agent with a **persistent identity**, **long and short-term memory**, and a **pluggable skills system** — all accessible from your phone via Telegram.

You define who the agent is on first run. It remembers your preferences, your projects, your history. You can extend it with custom skills, and it can read/write files, run scripts, and search the web. Throw files at it, ask it complex tasks, and it'll send results straight back to your chat.

---

## 🚀 Quick Start

> **Only prerequisite:** [Python 3.11+](https://python.org) and a Telegram bot token from [@BotFather](https://t.me/BotFather). The setup script installs everything else automatically.

### Windows

```powershell
git clone https://github.com/krazyguy/Gelegram.git
cd Gelegram
powershell -ExecutionPolicy Bypass -File setup.ps1
```

### Linux / macOS

```bash
git clone https://github.com/krazyguy/Gelegram.git
cd Gelegram
chmod +x setup.sh && ./setup.sh
```

The setup script will:
1. ✅ Install **Node.js** (winget / brew / apt / nvm as appropriate)
2. ✅ Install **uv** (Python package manager)
3. ✅ Install **Gemini CLI** via npm
4. ✅ Create Python **virtual environment** and install dependencies
5. ✅ Generate your **`.env`** file (prompts for bot token, password, workspace)
6. ✅ Optionally install as a **background service** (NSSM on Windows, systemd on Linux, launchd on macOS)
7. ✅ Run **`gemini auth`** for Google OAuth

**That's it.** Message your bot on Telegram — on the **first message**, the agent will introduce itself and walk you through setting up its identity (name, personality, rules). After that it's yours. 🎉

---

## ✨ Features

| Feature | Details |
|---------|---------|
| 🧠 **Persistent identity** | Agent has a name, personality, and memory that survive restarts |
| 💾 **Long-term memory** | Curated `MEMORY.md` — facts, preferences, lessons, project history |
| 📅 **Short-term memory** | Daily session logs distilled into `memory/YYYY-MM-DD.md` summaries |
| 🔧 **Pluggable skills** | Drop a `SKILL.md` + scripts into `skills/` and the agent gains new abilities |
| 🗣️ **Gemini CLI ACP bridge** | JSON-RPC 2.0 over stdio — real-time streaming responses |
| 👥 **Multi-chat isolation** | Each Telegram chat gets its own independent agent session |
| 📎 **File attachments** | Send photos, docs, audio or video — Gemini can send files back |
| 🖼️ **Media album support** | Multiple photos in one album are delivered to the agent together |
| 🔒 **Password authentication** | Restrict access with an optional bot password |
| 🕵️ **Private mode** | Toggle transcript logging per session with `/private` |
| ⚡ **Auto-tool approval** | File edits, shell commands, web search — all auto-approved |
| 🛑 **Task cancellation** | `/kill` stops a long-running task without resetting the session |
| 🖥️ **Cross-platform service** | Windows (NSSM), Linux (systemd), macOS (launchd) — all via one setup script |
| 🔄 **Watchdog gateway** | Exponential backoff restart if the agent process crashes |
| 🔍 **Startup CLI validation** | Gateway checks Gemini CLI path on start, warns early if misconfigured |
| 📊 **Tool call audit log** | Every tool Gemini invokes is logged to `tools.log` with full params |
| ⚡ **Live activity panel** | A single Telegram message updates in real-time showing the current tool being used |
| 🔥 **Session warm-up** | `/memory` (or auto on `/start`/`/reset`) pre-loads identity & memory so first replies are instant |

---

## Table of Contents

- [How It Works](#how-it-works)
- [Platform Support](#platform-support)
- [Manual Installation](#manual-installation)
- [Configuration](#configuration)
- [Running the Bot](#running-the-bot)
- [Bot Commands](#bot-commands)
- [Agentic Workspace System](#agentic-workspace-system)
- [Background Service (Production)](#background-service-production)
- [Architecture Notes](#architecture-notes)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)

---

## How It Works

```
Telegram User  ──────────────────────►  bot.py  ────────────────►  gemini --acp
               (python-telegram-bot)            (JSON-RPC 2.0)     (subprocess)
               ◄──────────────────────         ◄────────────────
```

1. **User sends a message** (text, photo, document, audio, or video) via Telegram.
2. **`bot.py`** receives it via the Telegram API (async long-polling).
3. **`GeminiACPClient`** forwards the prompt to a persistent `gemini --acp` subprocess using JSON-RPC 2.0 over `stdio`.
4. Streaming response chunks are accumulated and returned to the Telegram user.
5. If Gemini appends a `file:<path>` tag to its response, the bot automatically attaches and sends that file.

The ACP subprocess is **started once on the first message** and kept alive for subsequent messages, maintaining full context across the conversation. If it crashes, it is transparently restarted.

---

## Platform Support

Gelegram runs on **Windows, Linux, and macOS**. The core bot (`bot.py` + `gateway.py`) is pure Python and works identically across all platforms. The only platform-specific component is the background service mechanism.

| Platform | Setup Script | Service Manager | Node.js Install |
|----------|-------------|-----------------|-----------------|
| **Windows** | `setup.ps1` | NSSM (auto-downloaded) | winget → MSI fallback |
| **Linux** | `setup.sh` | systemd user service | nvm → apt/dnf/pacman |
| **macOS** | `setup.sh` | launchd LaunchAgent | nvm → Homebrew |

> **Note (Windows):** After installing via npm, the Gemini CLI binary is at `%APPDATA%\npm\gemini.cmd`. The setup script detects this automatically and writes it to `.env`.

> **Note (Linux/macOS):** The systemd service uses `loginctl enable-linger` so the bot keeps running after you log out of SSH.

---

## Manual Installation

If you prefer to set up manually instead of using the setup script:

### 1. Install Prerequisites

```bash
# Install Node.js 18+ from https://nodejs.org
# Install uv from https://github.com/astral-sh/uv

# Install Gemini CLI
npm install -g @google/gemini-cli

# Authenticate (opens browser for Google OAuth — do this once)
gemini auth
```

### 2. Clone and Set Up

```bash
git clone https://github.com/krazyguy/gelegram.git
cd gelegram

# Create virtual environment
uv venv .venv

# Install dependencies
uv pip install -r requirements.txt
```

### 3. Configure

```bash
# Windows
Copy-Item .env.example .env

# Linux / macOS
cp .env.example .env

# Edit .env and add your TELEGRAM_BOT_TOKEN at minimum
```

---

## Configuration

### `.env` Reference

```ini
# -- Required ------------------------------------------------------------------
# Your Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxyz

# -- Optional ------------------------------------------------------------------

# Full path to the gemini executable (default: "gemini" -- uses PATH)
# Windows: usually at C:\Users\<you>\AppData\Roaming\npm\gemini.cmd
# Run: where gemini  (Windows) or  which gemini  (Linux/macOS)
GEMINI_CLI_PATH=gemini

# Working directory for the gemini-cli subprocess.
# Gemini will read/write files here. The agentic workspace is scaffolded here.
# Use an absolute path for reliability.
GEMINI_WORKING_DIR=./workdir

# Maximum seconds to wait for a Gemini response (default: 120)
# After ACP_TIMEOUT, the user is notified and the bot waits up to 30 minutes.
ACP_TIMEOUT=120

# Optional bot password. Users must send this before the bot accepts messages.
# Leave empty to allow all users.
BOT_PASSWORD=
```

---

## Running the Bot

### Development (foreground)

```bash
# Windows
.venv\Scripts\activate
python bot.py

# Linux / macOS
source .venv/bin/activate
python bot.py
```

### With Gateway Watchdog (recommended)

```bash
python gateway.py
```

The gateway automatically restarts `bot.py` if it crashes, with exponential backoff. It also validates the Gemini CLI path on startup and warns early if it's misconfigured.

Expected output:
```
2026-05-13 12:00:00 | INFO     | gateway | ============================================================
2026-05-13 12:00:00 | INFO     | gateway |   Gelegram Gateway - Watchdog Service
2026-05-13 12:00:00 | INFO     | gateway | ============================================================
2026-05-13 12:00:00 | INFO     | gateway | bot.py started (pid=12345)
```

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message, then auto-loads memory so you can chat immediately |
| `/reset` | Restart the Gemini session, then auto-loads memory |
| `/new` | Alias for `/reset` |
| `/memory` | Pre-warm session — loads identity & memory files silently (shows live tool panel) |
| `/kill` | Cancel the active long-running request without resetting the session |
| `/private` | Toggle private mode — disables transcript logging for this session |
| `/status` | Show ACP subprocess status (PID, session ID, timeout) |
| *(any text)* | Forwards the message to Gemini and replies with the response |

### File Handling

- **Send a file** to Gelegram and it is saved to `<GEMINI_WORKING_DIR>/media/incoming/` and the path is shared with Gemini.
- **Gemini sends a file back** by appending `file:<absolute_path>` to its response. The bot intercepts this tag and delivers the file as a Telegram attachment (photo or document).
- **Media albums** (multiple photos sent together) are collected and delivered to Gemini as a single grouped prompt.

### Password Authentication

If `BOT_PASSWORD` is set in `.env`, users must send the correct password before the bot will accept any messages. Once authenticated, the chat ID is saved to `trusted_users.json` so users don't need to re-authenticate after a restart.

---

## Agentic Workspace System

Gelegram includes a full **agentic markdown memory system** that scaffolds itself automatically when a new `GEMINI_WORKING_DIR` is detected.

### Auto-Scaffolded Workspace

On first run with a new working directory, `workspace_init.py` creates:

```
<GEMINI_WORKING_DIR>/
├── GEMINI.md              <- Agent entry point & identity bootstrap instructions
├── AGENTS.md              <- Behavioral rules and operational protocols
├── TODO.md                <- Task tracker
├── TOOLS.md               <- Environment-specific notes (SSH, devices, etc.)
├── HALLUCINATIONS.md      <- Hallucination tracking policy
├── memory/                <- Daily memory logs (YYYY-MM-DD.md)
├── skills/
│   └── memory-agent/
│       ├── SKILL.md       <- Memory distillation skill documentation
│       └── scripts/
│           └── distill.py <- Transcript -> memory summary tool
├── projects/              <- Project-specific work folders
├── scripts/               <- User/agent scripts
├── transcripts/           <- Raw session transcripts (auto-logged)
├── media/incoming/        <- Files received from Telegram
├── state/                 <- Persistent state (processed sessions, etc.)
└── tmp/                   <- Scratch files
```

### Bootstrap Mode (First Conversation)

When `SOUL.md` is missing, `GEMINI.md` triggers **Bootstrap Mode**:

1. Gemini detects no identity files exist.
2. It asks the user to define the agent's name, personality vibe, and rules.
3. It creates `SOUL.md`, `IDENTITY.md`, `USER.md`, and `MEMORY.md` interactively.
4. Subsequent sessions load these files automatically for persistent context.

### Memory Distillation

The included `skills/memory-agent/scripts/distill.py` converts raw session transcripts into concise memory summaries stored in `memory/`:

```bash
python <GEMINI_WORKING_DIR>/skills/memory-agent/scripts/distill.py --workspace <GEMINI_WORKING_DIR>
```

---

## Background Service (Production)

The recommended production setup uses a two-layer resilience architecture:

```
OS Service Manager  (NSSM / systemd / launchd)
    └── gateway.py  (watchdog with exponential backoff)
            └── bot.py  (the actual Telegram bot)
```

The setup script handles service installation automatically. For manual control:

### Windows (NSSM)

```powershell
# Install (requires Administrator)
powershell -ExecutionPolicy Bypass -File install_service.ps1

# Uninstall
powershell -ExecutionPolicy Bypass -File uninstall_service.ps1

# Status / control
Get-Service Gelegram
Start-Service Gelegram
Stop-Service Gelegram
Restart-Service Gelegram

# Live log tail
Get-Content .\logs\gelegram_stdout.log -Tail 50 -Wait
```

### Linux (systemd)

```bash
# Status
systemctl --user status gelegram

# Control
systemctl --user start gelegram
systemctl --user stop gelegram
systemctl --user restart gelegram

# Live logs
journalctl --user -u gelegram -f

# Remove service
systemctl --user disable --now gelegram
rm ~/.config/systemd/user/gelegram.service
systemctl --user daemon-reload
```

### macOS (launchd)

```bash
PLIST="$HOME/Library/LaunchAgents/com.gelegram.bot.plist"

# Status
launchctl list | grep gelegram

# Stop / Start
launchctl unload "$PLIST"
launchctl load "$PLIST"

# Live logs
tail -f gateway.log

# Remove service
launchctl unload "$PLIST" && rm "$PLIST"
```

### Gateway Watchdog Behavior

`gateway.py` wraps `bot.py` with:

| Feature | Details |
|---------|---------|
| **Exponential backoff** | 5s → 10s → 20s → … → max 120s between restarts |
| **Stability reset** | Backoff resets after 60s of stable uptime |
| **Telegram cool-down** | 5s delay after bot exit before restart (prevents getUpdates conflicts) |
| **Orphan cleanup** | Kills leftover `bot.py` processes from previous runs (via `bot.pid` + psutil) |
| **Graceful shutdown** | Handles SIGINT / SIGTERM / CTRL+BREAK cleanly |
| **CLI path validation** | Checks Gemini CLI exists on startup; warns with install hint if missing |
| **Separate logging** | All lifecycle events go to `gateway.log` |

---

## Architecture Notes

### ACP Protocol (JSON-RPC 2.0 over stdio)

The ACP server communicates via **newline-delimited JSON** on `stdin`/`stdout`:

```json
// 1. Initialize
-> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientInfo":{"name":"gelegram","version":"1.0.0"},"clientCapabilities":{}}}
<- {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1,"authMethods":[...]}}

// 2. Create session
-> {"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"...","mcpServers":[],"trustedFolders":["..."]}}
<- {"jsonrpc":"2.0","id":2,"result":{"sessionId":"sess-abc123"}}

// 3. Send prompt (streaming)
-> {"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":"sess-abc123","prompt":[{"type":"text","text":"Hello!"}]}}
<- {"jsonrpc":"2.0","method":"session/update","params":{"update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"Hello"}}}}
<- {"jsonrpc":"2.0","id":3,"result":{}}
```

### Concurrency

- An `asyncio.Lock` serialises all ACP calls **per chat**, preventing JSON-RPC ID collisions.
- `concurrent_updates=True` allows multiple chats to be handled simultaneously.
- Each chat ID gets its own `GeminiACPClient` instance with an isolated session.

### Live Observability

Gelegram provides real-time visibility into what the agent is doing while it processes your message:

**Live activity panel** — a single Telegram message is sent immediately when you submit a request. It acts as a live status bar:

```
⚙️ Working …
📖 MEMORY.md          ← updates to the current tool in real-time
```

When the agent starts streaming its reply, the panel transitions to show a text preview. When the final response is sent, the panel is silently deleted — leaving only the clean reply.

**Tool call audit log** — every tool invocation (file reads, shell commands, web searches) is written to `tools.log` with a timestamp and full parameters:

```
2026-05-14 22:01:34 | sessionUpdate=tool_call   update={"kind":"read","title":"SOUL.md",...}
2026-05-14 22:01:35 | sessionUpdate=tool_call   update={"kind":"read","title":"MEMORY.md",...}
```

**Session warm-up** — `/memory` (automatically triggered by `/start`, `/reset`, and `/new`) sends a silent primer to Gemini asking it to read all startup files. The user sees the live tool panel while this runs, then `✅ Memory loaded!` — and the next real message gets an instant reply.

### Tool Auto-Approval

`gemini --acp -y` runs in YOLO mode. When Gemini emits a `session/request_permission` server-request for tool actions, the bot automatically responds with `proceed_always`. This allows Gemini to edit files, run shell commands, and use web search without manual confirmation.

### Message Size Handling

Telegram has a 4096-character message limit. Long responses are automatically split into numbered parts (`[Part 1/N]`, `[Part 2/N]`, …).

---

## Project Structure

```
gelegram/
├── bot.py                <- Main bot: Telegram polling + GeminiACPClient
├── gateway.py            <- Watchdog: auto-restarts bot.py with backoff (cross-platform)
├── workspace_init.py     <- Scaffolds agentic workspace on first run
├── chat.py               <- (utility module)
├── setup.ps1             <- One-command onboarding script (Windows)
├── setup.sh              <- One-command onboarding script (Linux / macOS)
├── install_service.ps1   <- NSSM Windows service installer
├── uninstall_service.ps1 <- NSSM Windows service uninstaller
├── run_gateway.bat       <- Batch launcher for the Windows service
├── requirements.txt      <- Python dependencies
├── .env.example          <- Configuration template (copy to .env)
├── .env                  <- Your secrets ⚠️ never commit this!
├── trusted_users.json    <- Authenticated chat IDs (auto-managed)
├── bot.log               <- Bot runtime log (created on first run)
├── gateway.log           <- Gateway lifecycle log
├── tools.log             <- Tool call audit log (every Gemini tool invocation)
├── bot.pid               <- Current bot.py PID (auto-managed)
├── workdir/              <- Default agentic workspace (see GEMINI_WORKING_DIR)
└── logs/                 <- Service stdout/stderr logs (created by service managers)
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `TELEGRAM_BOT_TOKEN is not set` | Create `.env` and add your token from @BotFather |
| `Could not find Gemini CLI executable` | Set `GEMINI_CLI_PATH` in `.env` to the full path; on Windows try `%APPDATA%\npm\gemini.cmd` |
| Bot hangs on first message | Run `gemini auth` to complete Google OAuth |
| `Conflict: terminated by other getUpdates` | Wait 10s and try again; the gateway handles this automatically on restart |
| `ACP error` in logs | Check `bot.log` for the raw JSON-RPC error message |
| Timeout after `ACP_TIMEOUT` seconds | The user is notified automatically; bot waits up to 30 min. Increase `ACP_TIMEOUT` for complex tasks |
| Bot stops responding | Send `/reset` to restart and auto-reload memory |
| Session not persisting after restart | This is expected — send any message or `/memory` and a fresh session starts automatically |
| `EOFError: ACP subprocess closed stdout` | Gemini CLI crashed; the bot auto-restarts it on the next message |
| Windows service not starting | Run `install_service.ps1` as Administrator; check `logs/` for NSSM output |
| Linux service not starting | Run `journalctl --user -u gelegram -n 50` to see startup errors |
| macOS service not starting | Run `tail -50 gateway.log`; check plist with `launchctl list \| grep gelegram` |
| Gateway warns "Gemini CLI could not be found" | Set `GEMINI_CLI_PATH` in `.env` to the absolute path of `gemini` or `gemini.cmd` |
| `tools.log` is empty | The bot hasn't processed a message yet — tool calls only appear after Gemini uses a tool |

### Enable Debug Logging

To see all raw JSON-RPC messages exchanged with gemini-cli, edit `bot.py`:

```python
logging.basicConfig(..., level=logging.DEBUG, ...)
```

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

*Built with [python-telegram-bot](https://python-telegram-bot.org/) and [Gemini CLI](https://github.com/google-gemini/gemini-cli).*
