r"""
=============================================================================
  chat.py  –  CMD chat interface for the Gemini CLI ACP bridge
=============================================================================
  A standalone test harness that talks directly to gemini --acp without
  needing a Telegram bot.  Useful for verifying the ACP connection works
  before wiring up the full bot.

  Usage:
      .venv\Scripts\python.exe chat.py

  Commands (type in the prompt):
      /quit   or  /exit  – exit the chat
      /reset             – kill & restart the ACP subprocess + session
      /status            – show subprocess PID and session ID
      /help              – show this command list

  Environment (loaded from .env, same as bot.py):
      GEMINI_CLI_PATH    – path to gemini executable  (default: "gemini")
      GEMINI_WORKING_DIR – working dir for subprocess (default: cwd)
      ACP_TIMEOUT        – seconds before timeout     (default: 120)
=============================================================================
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# ── Import the shared ACP client from bot.py ─────────────────────────────────
# We reuse GeminiACPClient directly so this script tests the *exact* same code
# path that the Telegram bot uses.  Any fix here works in the bot too.
try:
    from bot import GeminiACPClient, GEMINI_CLI_PATH, GEMINI_WORKING_DIR, ACP_TIMEOUT
except ImportError as exc:
    print(f"[ERROR] Could not import from bot.py: {exc}")
    print("Make sure chat.py is in the same folder as bot.py.")
    sys.exit(1)

load_dotenv()

# ── Logging: only show WARNING+ in the console so the chat stays clean ────────
# Debug-level ACP traffic is still written to bot.log (inherited from bot.py)
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    level=logging.WARNING,
)
# Redirect root logger file handler so debug messages still go to bot.log
file_handler = logging.FileHandler("bot.log", encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
)
logging.getLogger().addHandler(file_handler)
logging.getLogger("bot").setLevel(logging.DEBUG)      # capture ACP traffic in log


# ─────────────────────────────────────────────────────────────────────────────
# Pretty-print helpers
# ─────────────────────────────────────────────────────────────────────────────

CYAN    = "\033[96m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
DIM     = "\033[2m"
BOLD    = "\033[1m"
RESET   = "\033[0m"

# Windows cmd/PowerShell: enable ANSI colour codes
if sys.platform == "win32":
    os.system("")   # activates VT100 processing in conhost


def banner() -> None:
    print(f"""
{BOLD}{CYAN}╔══════════════════════════════════════════════════════╗
║   Gelegram – ACP Bridge  ·  CMD Test Mode            ║
╚══════════════════════════════════════════════════════╝{RESET}
{DIM}  gemini cli : {GEMINI_CLI_PATH}
  working dir : {GEMINI_WORKING_DIR}
  timeout     : {ACP_TIMEOUT}s
  (type /help for commands){RESET}
""")


def print_user(text: str) -> None:
    print(f"\n{BOLD}{GREEN}You ›{RESET} {text}")


def print_gemini(text: str) -> None:
    """Print Gemini's response with a separator and word-wrap at 80 chars."""
    print(f"\n{BOLD}{CYAN}Gemini ›{RESET}")
    # Simple word-wrap: split on newlines already present in the response
    for line in text.splitlines():
        # Indent continuation lines for readability
        print(f"  {line}")
    print()


def print_info(text: str) -> None:
    print(f"{YELLOW}  ℹ  {text}{RESET}")


def print_error(text: str) -> None:
    print(f"{RED}  ✗  {text}{RESET}")


def print_success(text: str) -> None:
    print(f"{GREEN}  ✓  {text}{RESET}")


def show_help() -> None:
    print(f"""
{BOLD}Commands:{RESET}
  {CYAN}/help{RESET}    Show this message
  {CYAN}/status{RESET}  Show ACP subprocess PID and session ID
  {CYAN}/reset{RESET}   Kill and restart the Gemini session
  {CYAN}/quit{RESET}    Exit the chat  (also: /exit or Ctrl+C)

{DIM}Anything else is sent directly to Gemini.{RESET}
""")


# ─────────────────────────────────────────────────────────────────────────────
# Main async REPL
# ─────────────────────────────────────────────────────────────────────────────

async def repl() -> None:
    """
    Read-Eval-Print Loop that connects to gemini --acp via GeminiACPClient.

    asyncio.get_event_loop().run_in_executor() is used for the blocking
    input() call so the event loop stays free to handle async I/O while
    waiting for the user to type.
    """
    client = GeminiACPClient()
    loop   = asyncio.get_event_loop()

    banner()
    print_info("Type a message and press Enter.  First message will start the ACP subprocess.")

    try:
        while True:
            # ── Non-blocking input ─────────────────────────────────────────
            # run_in_executor runs the blocking input() in a thread pool so
            # the event loop is not blocked while the user is typing.
            try:
                user_input: str = await loop.run_in_executor(
                    None, lambda: input(f"{DIM}> {RESET}")
                )
            except EOFError:
                # Piped input ended (e.g. echo "hello" | python chat.py)
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # ── Built-in commands ──────────────────────────────────────────
            lower = user_input.lower()

            if lower in ("/quit", "/exit"):
                print_info("Goodbye!")
                break

            if lower == "/help":
                show_help()
                continue

            if lower == "/status":
                alive = client._is_alive()
                pid   = client._process.pid if client._process else "N/A"
                sess  = client._session_id  or "none"
                print_info(
                    f"Subprocess: {'🟢 running' if alive else '🔴 stopped'} "
                    f"| PID: {pid} | Session: {sess}"
                )
                continue

            if lower == "/reset":
                print_info("Resetting Gemini session …")
                await client.stop()
                print_success("Session reset.  Next message starts a fresh session.")
                continue

            # ── Forward to Gemini ACP ──────────────────────────────────────
            print_user(user_input)
            print_info("Waiting for Gemini …")

            try:
                response = await client.send_prompt(user_input)
                print_gemini(response)

            except asyncio.TimeoutError:
                print_error(
                    f"Gemini took longer than {ACP_TIMEOUT}s to respond.\n"
                    "  Try /reset if it stays stuck."
                )

            except FileNotFoundError:
                print_error(
                    f"Gemini CLI not found at: '{GEMINI_CLI_PATH}'\n"
                    "  Check GEMINI_CLI_PATH in your .env file."
                )

            except RuntimeError as exc:
                print_error(f"ACP error: {exc}")

            except Exception as exc:
                print_error(f"Unexpected error: {exc}")
                logging.exception("Unexpected error in REPL")

    except KeyboardInterrupt:
        print(f"\n{DIM}  (Interrupted){RESET}")

    finally:
        print_info("Shutting down ACP subprocess …")
        await client.stop()
        print_success("Done. Bye!")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # On Windows, asyncio defaults to ProactorEventLoop (required for subprocesses).
    # No extra configuration needed since Python 3.8+.
    asyncio.run(repl())
