# Gelegram 🤖

> **Your personal AI agent — accessible from Telegram, powered by Gemini. It knows who you are, remembers what matters, learns new skills, and keeps running 24/7 as a Windows service.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://python.org)
[![Gemini CLI](https://img.shields.io/badge/Gemini_CLI-required-4285F4?logo=google)](https://github.com/google-gemini/gemini-cli)
[![Windows](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows)](https://microsoft.com/windows)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Gelegram is a **lightweight, self-hosted AI agent** built on the Gemini CLI. Think of it as your own pocket **OpenClaw** — an agent with a **persistent identity**, **long and short-term memory**, and a **pluggable skills system** — all accessible from your phone via Telegram.

You define who the agent is on first run. It remembers your preferences, your projects, your history. You can extend it with custom skills, and it can read/write files, run scripts, and search the web. Throw files at it, ask it complex tasks, and it'll send results straight back to your chat.

---

## 🚀 Quick Start

> **Prerequisites:** [Node.js 18+](https://nodejs.org) · [Python 3.11+](https://python.org) · [uv](https://github.com/astral-sh/uv) · A Telegram bot token from [@BotFather](https://t.me/BotFather)

```powershell
# 1. Install Gemini CLI and authenticate (one-time)
npm install -g @google/gemini-cli
gemini   # completes Google OAuth — close it after login

# 2. Clone and set up the project
git clone https://github.com/krazyguy/Gelegram.git
cd Gelegram
uv venv .venv
.venv\Scripts\activate
uv pip install -r requirements.txt

# 3. Configure
Copy-Item .env.example .env
# Edit .env — paste your TELEGRAM_BOT_TOKEN at minimum

# 4. Run
python bot.py
```

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
| 🖥️ **Windows service** | Run 24/7 via NSSM — auto-starts on boot, survives crashes |
| 🔄 **Watchdog gateway** | Exponential backoff restart if the agent process crashes |

---

## Table of Contents

- [How It Works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Bot](#running-the-bot)
- [Bot Commands](#bot-commands)
- [Agentic Workspace System](#agentic-workspace-system)
- [Windows Service (Production)](#windows-service-production)
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

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| **Python** | 3.11+ | Tested on 3.12 / 3.13 |
| **uv** | latest | Recommended package manager |
| **Node.js** | 18+ | Required to run gemini-cli |
| **Gemini CLI** | latest | `npm install -g @google/gemini-cli` |
| **Telegram Bot Token** | — | Create one via [@BotFather](https://t.me/BotFather) |
| **Google Account** | — | Used by gemini-cli for authentication (OAuth) |

> **Gemini CLI is the core AI engine.** It must be installed, authenticated, and accessible from your PATH before running Gelegram.

### Install Gemini CLI

```powershell
npm install -g @google/gemini-cli

# Verify installation
gemini --version
```

**Authenticate once** by running Gemini interactively — this caches OAuth credentials that Gelegram will reuse:

```powershell
gemini
```

> **Important (Windows):** After installing via npm, the binary is usually at `%APPDATA%\npm\gemini.cmd`. If `gemini` is not on your PATH, find its full path with `where gemini` in PowerShell and use it in `.env`.

---

## Installation

### 1. Clone the Repository

```powershell
git clone https://github.com/krazyguy/gelegram.git
cd gelegram
```

### 2. Create a Virtual Environment

```powershell
# Using uv (recommended)
uv venv .venv
.venv\Scripts\activate

# Or using standard venv
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install Dependencies

```powershell
uv pip install -r requirements.txt

# Or with pip
pip install -r requirements.txt
```

---

## Configuration

### 1. Create Your `.env` File

```powershell
Copy-Item .env.example .env
```

### 2. Edit `.env`

```ini
# ── Required ──────────────────────────────────────────────────────────
# Your Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxyz

# ── Optional ──────────────────────────────────────────────────────────

# Full path to the gemini executable (default: "gemini" — uses PATH)
# Windows: usually at C:\Users\<you>\AppData\Roaming\npm\gemini.cmd
# Run: where gemini   to find the path
GEMINI_CLI_PATH=gemini

# Working directory for the gemini-cli subprocess.
# Gemini will read/write files here. The agentic workspace is scaffolded here.
# Use an absolute path, e.g. D:\my-agent-workspace
GEMINI_WORKING_DIR=.\workdir

# Maximum seconds to wait for a Gemini response (default: 120)
# Increase for complex/long-running tasks. After ACP_TIMEOUT, the user is
# notified and the bot waits up to 30 minutes before giving up.
ACP_TIMEOUT=120

# Optional: restrict the bot to users who know this password.
# Leave empty (or omit) to allow all users.
BOT_PASSWORD=
```

---

## Running the Bot

### Development (foreground)

```powershell
# Activate the virtual environment
.venv\Scripts\activate

# Start the bot
python bot.py
```

Expected output:
```
2026-05-10 12:00:00 | INFO     | gelegram | ============================================================
2026-05-10 12:00:00 | INFO     | gelegram |   Gelegram - Telegram -> Gemini CLI ACP Bridge
2026-05-10 12:00:00 | INFO     | gelegram | ============================================================
2026-05-10 12:00:00 | INFO     | gelegram | Bot is polling for updates … (Ctrl+C to stop)
```

On the **first message**, the bot will:
1. Start the `gemini --acp` subprocess.
2. Perform the JSON-RPC handshake (`initialize` → `session/new`).
3. Forward your message and return the streaming response.

### With the Gateway Watchdog (recommended for development)

```powershell
python gateway.py
```

The gateway automatically restarts `bot.py` if it crashes, with exponential backoff.

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and usage instructions |
| `/reset` | Terminate and restart the Gemini session (fresh context) |
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
├── GEMINI.md              ← Agent entry point & identity bootstrap instructions
├── AGENTS.md              ← Behavioral rules and operational protocols
├── TODO.md                ← Task tracker
├── TOOLS.md               ← Environment-specific notes (SSH, devices, etc.)
├── HALLUCINATIONS.md      ← Hallucination tracking policy
├── memory/                ← Daily memory logs (YYYY-MM-DD.md)
├── skills/
│   └── memory-agent/
│       ├── SKILL.md       ← Memory distillation skill documentation
│       └── scripts/
│           └── distill.py ← Transcript → memory summary tool
├── projects/              ← Project-specific work folders
├── scripts/               ← User/agent scripts
├── transcripts/           ← Raw session transcripts (auto-logged)
├── media/incoming/        ← Files received from Telegram
├── state/                 ← Persistent state (processed sessions, etc.)
└── tmp/                   ← Scratch files
```

### Bootstrap Mode (First Conversation)

When `SOUL.md` is missing, `GEMINI.md` triggers **Bootstrap Mode**:

1. Gemini detects no identity files exist.
2. It asks the user to define the agent's name, personality vibe, and rules.
3. It creates `SOUL.md`, `IDENTITY.md`, `USER.md`, and `MEMORY.md` interactively.
4. Subsequent sessions load these files automatically for persistent context.

### Memory Distillation

The included `skills/memory-agent/scripts/distill.py` can be run to convert raw session transcripts into concise memory summaries stored in `memory/`:

```powershell
python <GEMINI_WORKING_DIR>/skills/memory-agent/scripts/distill.py --workspace <GEMINI_WORKING_DIR>
```

---

## Windows Service (Production)

Gelegram can run as a persistent Windows background service using **NSSM** (Non-Sucking Service Manager) with a two-layer resilience architecture:

```
Windows Service (NSSM)
    └── gateway.py  (watchdog with exponential backoff)
            └── bot.py  (the actual Telegram bot)
```

### Install as a Service

```powershell
# Run as Administrator
.\install_service.ps1
```

The script:
1. Downloads NSSM automatically (if not present).
2. Creates a Windows service pointing to `gateway.py`.
3. Configures automatic startup on boot.
4. Sets up log rotation to `logs/`.

### Uninstall Service

```powershell
# Run as Administrator
.\uninstall_service.ps1
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
| **Separate logging** | All lifecycle events go to `gateway.log` |

---

## Architecture Notes

### ACP Protocol (JSON-RPC 2.0 over stdio)

The ACP server communicates via **newline-delimited JSON** on `stdin`/`stdout`:

```json
// 1. Initialize
→ {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientInfo":{"name":"gelegram","version":"1.0.0"},"clientCapabilities":{}}}
← {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1,"authMethods":[...]}}

// 2. Create session
→ {"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"...","mcpServers":[],"trustedFolders":["..."]}}
← {"jsonrpc":"2.0","id":2,"result":{"sessionId":"sess-abc123"}}

// 3. Send prompt (streaming)
→ {"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"sessionId":"sess-abc123","prompt":[{"type":"text","text":"Hello!"}]}}
← {"jsonrpc":"2.0","method":"session/update","params":{"update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"Hello"}}}}
← {"jsonrpc":"2.0","method":"session/update","params":{"update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" there!"}}}}
← {"jsonrpc":"2.0","id":3,"result":{}}
```

### Concurrency

- An `asyncio.Lock` serialises all ACP calls **per chat**, preventing JSON-RPC ID collisions.
- `concurrent_updates=True` allows multiple chats to be handled simultaneously.
- Each chat ID gets its own `GeminiACPClient` instance with an isolated session.

### Tool Auto-Approval

`gemini --acp -y` runs in YOLO mode. When Gemini emits a `session/request_permission` server-request for tool actions, the bot automatically responds with `proceed_always`. This allows Gemini to edit files, run shell commands, and use web search without manual confirmation.

### Message Size Handling

Telegram has a 4096-character message limit. Long responses are automatically split into numbered parts (`[Part 1/N]`, `[Part 2/N]`, …).

---

## Project Structure

```
gelegram/
├── bot.py               ← Main bot: Telegram polling + GeminiACPClient
├── gateway.py           ← Watchdog: auto-restarts bot.py with backoff
├── workspace_init.py    ← Scaffolds agentic workspace on first run
├── chat.py              ← (utility module)
├── install_service.ps1  ← NSSM Windows service installer
├── uninstall_service.ps1← NSSM Windows service uninstaller
├── run_gateway.bat      ← Simple bat launcher for the gateway
├── requirements.txt     ← Python dependencies
├── .env.example         ← Configuration template (copy to .env)
├── .env                 ← Your secrets ⚠️ never commit this!
├── trusted_users.json   ← Authenticated chat IDs (auto-managed)
├── bot.log              ← Bot runtime log (created on first run)
├── gateway.log          ← Gateway lifecycle log
├── bot.pid              ← Current bot.py PID (auto-managed)
├── workdir/             ← Default agentic workspace (see GEMINI_WORKING_DIR)
└── logs/                ← Service stdout/stderr logs (created by NSSM)
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `TELEGRAM_BOT_TOKEN is not set` | Create `.env` and add your token from @BotFather |
| `Could not find Gemini CLI executable` | Set `GEMINI_CLI_PATH` to the full path in `.env` |
| Bot hangs on first message | Run `gemini` interactively to complete Google OAuth |
| `Conflict: terminated by other getUpdates` | Wait 10s and try again; the gateway handles this automatically on restart |
| `ACP error` in logs | Check `bot.log` for the raw JSON-RPC error message |
| Timeout after `ACP_TIMEOUT` seconds | The user is notified automatically; bot waits up to 30 min. Increase `ACP_TIMEOUT` for complex tasks |
| Bot stops responding | Send `/reset` to restart the Gemini session |
| Session not persisting after restart | This is expected — send any message and a fresh session starts automatically |
| `EOFError: ACP subprocess closed stdout` | Gemini CLI crashed; the bot auto-restarts it on the next message |
| Windows service not starting | Run `install_service.ps1` as Administrator; check `logs/` for NSSM output |

### Enable Debug Logging

To see all raw JSON-RPC messages exchanged with gemini-cli, edit `bot.py`:

```python
logging.basicConfig(..., level=logging.DEBUG, ...)
```

### Check Service Status (PowerShell)

```powershell
# Check if the service is running
Get-Service -Name "Gelegram"

# View recent service logs
Get-Content .\logs\gelegram_stdout.log -Tail 50
```

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

*Built with [python-telegram-bot](https://python-telegram-bot.org/) and [Gemini CLI](https://github.com/google-gemini/gemini-cli).*
