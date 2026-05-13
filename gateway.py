"""
=============================================================================
  Gelegram Gateway – Windows Service Watchdog
=============================================================================
  Purpose:
    This script wraps bot.py with automatic restart capabilities.
    It is designed to be run as a Windows service (via NSSM) or standalone.

  Behavior:
    • Launches bot.py as a subprocess using the project's venv Python
    • Monitors the process health continuously
    • Auto-restarts on crash with exponential backoff (5s → 10s → 20s → max 120s)
    • Resets backoff timer after 60s of stable running (not a flapping crash loop)
    • Logs all lifecycle events to gateway.log
    • Handles graceful shutdown on SIGINT/SIGTERM/Ctrl+C (Windows CTRL_BREAK)
    • Captures and forwards bot.py stdout/stderr to the gateway log

  Usage:
    Standalone:   python gateway.py
    As service:   Registered via install_service.ps1 (uses NSSM)

  Configuration:
    BOT_SCRIPT       – Relative path to the bot entry point (default: bot.py)
    RESTART_DELAY    – Initial delay in seconds before restarting (default: 5)
    MAX_RESTART_DELAY – Maximum backoff delay in seconds (default: 120)
    STABLE_THRESHOLD – Seconds of uptime before backoff resets (default: 60)
=============================================================================
"""

import subprocess
import sys
import os
import time
import signal
import logging
from pathlib import Path
from datetime import datetime

# psutil is used for orphan-process cleanup (pip install psutil)
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Resolve absolute paths so the script works from any CWD (important for services)
SCRIPT_DIR = Path(__file__).resolve().parent
BOT_SCRIPT = SCRIPT_DIR / "bot.py"

# Cross-platform venv Python path:
#   Windows : .venv\Scripts\python.exe
#   Linux / macOS : .venv/bin/python
if sys.platform == "win32":
    VENV_PYTHON = SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"
else:
    VENV_PYTHON = SCRIPT_DIR / ".venv" / "bin" / "python"

BOT_PID_FILE = SCRIPT_DIR / "bot.pid"   # tracks the current bot.py PID across restarts

# Restart backoff settings
RESTART_DELAY = 5          # Initial delay before restart (seconds)
MAX_RESTART_DELAY = 120    # Maximum backoff delay (seconds)
STABLE_THRESHOLD = 60      # Seconds of stable uptime before resetting backoff
# Extra seconds to wait after bot exits before launching a new one.
# Gives Telegram time to close the previous getUpdates long-poll session,
# preventing "Conflict: terminated by other getUpdates request" errors.
TELEGRAM_COOLDOWN = 5

# ─────────────────────────────────────────────────────────────────────────────
# Logging – separate from bot.log to keep gateway lifecycle events distinct
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | gateway | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(SCRIPT_DIR / "gateway.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("gateway")


# ─────────────────────────────────────────────────────────────────────────────
# Globals for signal handling
# ─────────────────────────────────────────────────────────────────────────────
_shutdown_requested = False
_current_process: subprocess.Popen = None


def _signal_handler(signum, frame):
    """
    Handle shutdown signals (SIGINT, SIGTERM, CTRL_BREAK on Windows).
    Sets the shutdown flag so the main loop exits cleanly, and terminates
    the child bot.py process if it's running.
    """
    global _shutdown_requested
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
    logger.info("Received signal %s – initiating graceful shutdown …", sig_name)
    _shutdown_requested = True

    # Terminate the child process immediately so we don't wait for the restart loop
    if _current_process and _current_process.poll() is None:
        logger.info("Terminating bot.py subprocess (pid=%s) …", _current_process.pid)
        _current_process.terminate()


def _kill_orphan_bots() -> None:
    """
    Kill any leftover bot.py processes from a previous gateway run.

    Two strategies are used in order:
      1. PID file – if bot.pid exists from a prior run, kill that specific PID.
      2. psutil scan – if psutil is available, walk all python processes and
         kill any whose cmdline includes bot.py that aren't the current process.

    This prevents the Telegram "Conflict: terminated by other getUpdates request"
    error that occurs when a stale bot.py is still holding a getUpdates session
    while the new one starts up.
    """
    own_pid = os.getpid()

    # ── Strategy 1: PID file left by previous gateway run ────────────────────
    if BOT_PID_FILE.exists():
        try:
            old_pid = int(BOT_PID_FILE.read_text().strip())
            if old_pid and old_pid != own_pid:
                if _PSUTIL_AVAILABLE:
                    try:
                        proc = psutil.Process(old_pid)
                        logger.info("Killing orphan bot.py from PID file (pid=%d) …", old_pid)
                        proc.kill()
                        proc.wait(timeout=5)
                        logger.info("Orphan pid=%d killed.", old_pid)
                    except psutil.NoSuchProcess:
                        pass  # already gone
                    except Exception as exc:
                        logger.warning("Could not kill orphan pid=%d: %s", old_pid, exc)
                else:
                    # Fallback: taskkill (Windows)
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(old_pid)],
                            capture_output=True, timeout=5,
                        )
                        logger.info("Orphan pid=%d killed via taskkill.", old_pid)
                    except Exception as exc:
                        logger.warning("taskkill for pid=%d failed: %s", old_pid, exc)
        except ValueError:
            pass  # corrupt pid file
        finally:
            BOT_PID_FILE.unlink(missing_ok=True)

    # ── Strategy 2: psutil process scan ──────────────────────────────────────
    if not _PSUTIL_AVAILABLE:
        return

    bot_script_str = str(BOT_SCRIPT).lower()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.pid == own_pid:
                continue
            cmdline = proc.info.get("cmdline") or []
            # Match python processes running bot.py
            if any("python" in c.lower() for c in cmdline) and \
               any(bot_script_str in c.lower() for c in cmdline):
                logger.info(
                    "Killing orphan bot.py process (pid=%d, cmd=%s) …",
                    proc.pid, " ".join(cmdline[:3]),
                )
                proc.kill()
                proc.wait(timeout=5)
                logger.info("Orphan pid=%d killed.", proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception as exc:
            logger.warning("Error checking process pid=%s: %s", proc.pid, exc)


def _validate_environment():
    """
    Pre-flight checks: ensure the venv Python, bot.py, and Gemini CLI exist.
    Exits with a clear error message if venv or bot.py are missing.
    Warns (but does not exit) if the Gemini CLI path cannot be verified —
    bot.py will surface a clearer error when it actually tries to spawn the
    subprocess.
    """
    if not VENV_PYTHON.exists():
        logger.critical(
            "Virtual environment Python not found at: %s\n"
            "  → Create it with: uv venv .venv\n"
            "  → Then install deps: uv pip install -r requirements.txt",
            VENV_PYTHON,
        )
        sys.exit(1)

    if not BOT_SCRIPT.exists():
        logger.critical("Bot script not found at: %s", BOT_SCRIPT)
        sys.exit(1)

    # ── Gemini CLI path validation ────────────────────────────────────────────
    # Read GEMINI_CLI_PATH from .env (same as bot.py does) and verify it exists.
    # This catches misconfigurations early with a clear warning instead of a
    # cryptic ACP JSON-RPC error later when bot.py tries to spawn the subprocess.
    from dotenv import load_dotenv
    load_dotenv(SCRIPT_DIR / ".env")
    gemini_cli_path = os.environ.get("GEMINI_CLI_PATH", "gemini")

    gemini_found = False

    # Strategy 1: Check if the configured path is an existing file
    if Path(gemini_cli_path).exists():
        gemini_found = True
        logger.info("Gemini CLI verified: %s", gemini_cli_path)

    # Strategy 2: Try where.exe (works if gemini is on PATH)
    if not gemini_found:
        try:
            result = subprocess.run(
                ["where.exe", gemini_cli_path],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                resolved = result.stdout.strip().splitlines()[0]
                gemini_found = True
                logger.info("Gemini CLI found via where: %s", resolved)
        except Exception:
            pass

    # Strategy 3: Fallback to standard npm global install path on Windows
    if not gemini_found and sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            default_path = Path(appdata) / "npm" / "gemini.cmd"
            if default_path.exists():
                gemini_found = True
                logger.info("Gemini CLI found at default npm path: %s", default_path)
                logger.info(
                    "Consider setting GEMINI_CLI_PATH=%s in .env for reliability.",
                    default_path,
                )

    if not gemini_found:
        logger.warning(
            "Gemini CLI could not be found (GEMINI_CLI_PATH=%s). "
            "The bot will fail to start ACP sessions until this is resolved.\n"
            "  → Install: npm install -g @google/gemini-cli\n"
            "  → Or set GEMINI_CLI_PATH in .env to the full path of gemini.cmd",
            gemini_cli_path,
        )


def _run_bot() -> int:
    """
    Launch bot.py as a subprocess and wait for it to exit.
    Returns the process exit code.

    stdout/stderr are inherited so they flow to the gateway's own streams
    (which are captured by NSSM when running as a service, or visible in
    the console when running standalone).
    """
    global _current_process

    logger.info("Starting bot.py (python=%s) …", VENV_PYTHON)
    logger.info("  Working directory: %s", SCRIPT_DIR)

    _current_process = subprocess.Popen(
        [str(VENV_PYTHON), str(BOT_SCRIPT)],
        cwd=str(SCRIPT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # Use CREATE_NEW_PROCESS_GROUP on Windows so the child can be
        # terminated independently without killing the gateway itself.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )

    logger.info("bot.py started (pid=%s)", _current_process.pid)

    # Write PID to file so the next gateway instance can kill this process
    # if it somehow survives a crash/restart cycle.
    try:
        BOT_PID_FILE.write_text(str(_current_process.pid))
    except Exception as exc:
        logger.warning("Could not write bot.pid: %s", exc)

    # Stream bot output to gateway log in real-time
    # This ensures all bot logs are captured even when running as a service
    try:
        for line in iter(_current_process.stdout.readline, b""):
            decoded = line.decode("utf-8", errors="replace").rstrip()
            if decoded:
                logger.info("[bot] %s", decoded)
    except Exception as e:
        logger.warning("Error reading bot stdout: %s", e)

    # Wait for process to fully exit and get return code
    exit_code = _current_process.wait()
    _current_process = None

    # Clean up PID file after normal exit
    BOT_PID_FILE.unlink(missing_ok=True)

    return exit_code


def main():
    """
    Main gateway loop with exponential backoff restart logic.

    Restart behavior:
      • On crash: wait RESTART_DELAY seconds, then restart
      • Each consecutive crash doubles the delay (capped at MAX_RESTART_DELAY)
      • If the bot runs for > STABLE_THRESHOLD seconds, the backoff resets
      • On graceful shutdown (signal), exit without restarting
    """
    global _shutdown_requested

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    # Windows-specific: CTRL_BREAK_EVENT can be sent to the process
    if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _signal_handler)

    _validate_environment()

    logger.info("=" * 60)
    logger.info("  Gelegram Gateway – Watchdog Service")
    logger.info("=" * 60)
    logger.info("  Bot script : %s", BOT_SCRIPT)
    logger.info("  Python     : %s", VENV_PYTHON)
    logger.info("  PID        : %s", os.getpid())
    logger.info("=" * 60)

    current_delay = RESTART_DELAY
    restart_count = 0

    # Kill any orphan bot.py processes left by a previous gateway run
    # before entering the restart loop.
    _kill_orphan_bots()

    while not _shutdown_requested:
        start_time = time.time()

        exit_code = _run_bot()
        uptime = time.time() - start_time

        if _shutdown_requested:
            logger.info("Shutdown requested – not restarting.")
            break

        restart_count += 1
        uptime_str = _format_duration(uptime)

        logger.warning(
            "bot.py exited with code %s after %s (restart #%d)",
            exit_code, uptime_str, restart_count,
        )

        # ── Telegram cool-down ────────────────────────────────────────────────
        # Wait a few seconds after the bot exits to allow Telegram's servers
        # to drop the previous getUpdates long-poll connection.  Without this
        # pause the new bot.py can start polling before Telegram has fully
        # closed the old session, producing:
        #   "Conflict: terminated by other getUpdates request"
        logger.info(
            "Waiting %ds for Telegram to close previous getUpdates session …",
            TELEGRAM_COOLDOWN,
        )
        for _ in range(TELEGRAM_COOLDOWN):
            if _shutdown_requested:
                break
            time.sleep(1)

        if _shutdown_requested:
            logger.info("Shutdown requested during Telegram cool-down – exiting.")
            break

        # Kill any orphan processes that may have been left behind
        _kill_orphan_bots()

        # Reset backoff if the bot was stable for long enough
        # This prevents a single crash after hours of uptime from
        # accumulating backoff as if it were a crash loop.
        if uptime > STABLE_THRESHOLD:
            current_delay = RESTART_DELAY
            logger.info("Bot ran stably for %s – backoff reset to %ds.", uptime_str, current_delay)
        else:
            logger.warning(
                "Bot crashed quickly (<%ds) – possible crash loop. Backing off %ds before restart.",
                STABLE_THRESHOLD, current_delay,
            )

        # Wait before restarting (interruptible by shutdown signal)
        logger.info("Waiting %ds before restart …", current_delay)
        for _ in range(int(current_delay)):
            if _shutdown_requested:
                logger.info("Shutdown requested during restart delay – exiting.")
                break
            time.sleep(1)

        if _shutdown_requested:
            break

        # Exponential backoff for crash loops (only increases if unstable)
        if uptime <= STABLE_THRESHOLD:
            current_delay = min(current_delay * 2, MAX_RESTART_DELAY)

    logger.info("Gateway shutdown complete. Total restarts: %d", restart_count)


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string like '2h 15m 30s'."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h {m}m {s}s"


if __name__ == "__main__":
    main()
