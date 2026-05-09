"""
=============================================================================
  Telegram → Gemini CLI ACP Bridge Bot
=============================================================================
  Architecture Overview:
  ┌──────────────┐   Telegram API   ┌────────────────┐   JSON-RPC 2.0   ┌─────────────────┐
  │  Telegram    │ ◄──────────────► │   bot.py       │ ◄──────────────► │  gemini --acp   │
  │  User        │   (python-       │   (this file)  │   (stdio pipe)   │  (subprocess)   │
  └──────────────┘    telegram-bot) └────────────────┘                  └─────────────────┘

  Communication with gemini-cli --acp:
  • Transport: subprocess stdin/stdout (newline-delimited JSON)
  • Protocol:  JSON-RPC 2.0
  • Handshake:
      1. Send  → {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
      2. Recv  ← {"jsonrpc":"2.0","id":1,"result":{...}}
      3. Send  → {"jsonrpc":"2.0","id":2,"method":"session/new","params":{}}
      4. Recv  ← {"jsonrpc":"2.0","id":2,"result":{"sessionId":"..."}}
  • Per message:
      5. Send  → {"jsonrpc":"2.0","id":N,"method":"prompt",
                   "params":{"sessionId":"...","prompt":[{"type":"text","text":"..."}]}}
      6. Recv  ← (possibly multiple notification chunks, then a result with id=N)

  Environment variables (from .env):
    TELEGRAM_BOT_TOKEN  - Bot token from @BotFather
    GEMINI_CLI_PATH     - Path to gemini executable (default: "gemini")
    GEMINI_WORKING_DIR  - Working directory for the subprocess (default: cwd)
    ACP_TIMEOUT         - Seconds to wait for a full response (default: 120)
=============================================================================
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─────────────────────────────────────────────────────────────────────────────
# Logging setup – keep it clean and timestamped
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("gelegram")

# Silence overly verbose libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# ─────────────────────────────────────────────────────────────────────────────
# Load environment
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_CLI_PATH: str    = os.getenv("GEMINI_CLI_PATH", "gemini")
GEMINI_WORKING_DIR: str = os.getenv("GEMINI_WORKING_DIR", str(Path.cwd()))
ACP_TIMEOUT: int        = int(os.getenv("ACP_TIMEOUT", "120"))
BOT_PASSWORD: str       = os.getenv("BOT_PASSWORD", "")

# NOTE: Token validation is intentionally deferred to main() so that
# importing this module (e.g. from chat.py) does NOT trigger sys.exit.

# Ensure the working directory exists (create it if missing)
_work_dir = Path(GEMINI_WORKING_DIR)
if not _work_dir.is_absolute():
    _work_dir = Path.cwd() / _work_dir
GEMINI_WORKING_DIR = str(_work_dir)
if not _work_dir.exists():
    logger.warning("GEMINI_WORKING_DIR '%s' does not exist – creating it.", GEMINI_WORKING_DIR)
    _work_dir.mkdir(parents=True, exist_ok=True)

# Scaffold workspace if it's new (creates GEMINI.md, directory structure, starter skills)
# This replaces the old primitive bootstrap that only copied BOOTSTRAP_GEMINI.txt → GEMINI.md.
# Now it creates the full agentic MD system: directories, operational .md files,
# and the memory-agent skill. Identity files (SOUL.md, IDENTITY.md, USER.md, MEMORY.md)
# are NOT created here — the agent creates them interactively during Bootstrap Mode.
from workspace_init import init_workspace
init_workspace(_work_dir)


# ─────────────────────────────────────────────────────────────────────────────
# ACP Client — manages one persistent gemini --acp subprocess
# ─────────────────────────────────────────────────────────────────────────────
class GeminiACPClient:
    """
    Manages the lifecycle of a `gemini --acp` subprocess and provides an
    async interface to send prompts and receive responses via JSON-RPC 2.0.

    The subprocess is started lazily on the first call to `send_prompt()`.
    If the subprocess crashes, it is automatically restarted before the
    next call.

    Thread-safety: All public methods are coroutines and must be awaited
    from the same async event loop.  An asyncio.Lock serialises concurrent
    Telegram messages so that JSON-RPC IDs are never interleaved.
    """

    def __init__(self) -> None:
        self._process:   Optional[asyncio.subprocess.Process] = None
        self._session_id: Optional[str] = None
        self._req_id:    int = 0          # monotonically increasing JSON-RPC id
        self._lock:      asyncio.Lock = asyncio.Lock()
        self._initialized: bool = False
        self._transcript_file: Optional[Path] = None
        self._active_req_id: Optional[int] = None
        self.private_mode: bool = False

    # ── Private helpers ──────────────────────────────────────────────────────

    def _next_id(self) -> int:
        """Return the next unique JSON-RPC request id."""
        self._req_id += 1
        return self._req_id

    async def _send(self, payload: dict) -> None:
        """
        Serialize `payload` as a single newline-terminated JSON line and
        write it to the subprocess stdin.

        ACP uses newline-delimited JSON (NDJSON) over stdio: every message
        is one JSON object followed by exactly one newline character `\n`.
        """
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("ACP process is not running")

        line = json.dumps(payload, separators=(",", ":")) + "\n"
        logger.debug(">> ACP: %s", line.rstrip())
        self._process.stdin.write(line.encode("utf-8"))
        await self._process.stdin.drain()

    async def _recv_message(self, timeout: float = ACP_TIMEOUT) -> dict:
        """
        Read one complete JSON-RPC message from the subprocess stdout.

        Because gemini --acp may emit notification messages (progress
        updates that have no `id`) before the final response, we loop
        until we either see a message that has an `id` field or until
        the timeout is reached.  Notifications are logged but discarded
        here; callers use _recv_response() which handles this properly.
        """
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("ACP process stdout is not available")

        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError("Timed out waiting for ACP response")

            # asyncio.wait_for will cancel the read after `remaining` seconds
            raw_line = await asyncio.wait_for(
                self._process.stdout.readline(), timeout=remaining
            )
            if not raw_line:
                raise EOFError("ACP subprocess closed stdout unexpectedly")

            line_str = raw_line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue  # skip blank lines

            logger.debug("<< ACP: %s", line_str)

            try:
                msg = json.loads(line_str)
            except json.JSONDecodeError as exc:
                logger.warning("ACP sent non-JSON line (ignoring): %s | err: %s", line_str, exc)
                continue

            return msg

    async def _recv_response(self, req_id: int, timeout: float = ACP_TIMEOUT) -> dict:
        """
        Consume ACP messages until we receive the response that matches
        `req_id`.  Notifications (messages without an `id`) are accumulated
        as side-channel data and their text fragments are returned so callers
        can stream them to Telegram if desired.

        Returns the matched JSON-RPC *result* or raises on *error*.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        accumulated_text: list[str] = []

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"Timed out waiting for response to request {req_id}"
                )

            msg = await self._recv_message(timeout=remaining)

            # ── Notification (no id) ──────────────────────────────────────
            if "id" not in msg:
                method = msg.get("method", "")
                params = msg.get("params", {})

                # ── Confirmed gemini-cli streaming format (from live probe) ──
                # method:  "session/update"
                # params:  { "sessionId": "...",
                #             "update": { "sessionUpdate": "agent_message_chunk",
                #                         "content": { "type": "text",
                #                                      "text": "<chunk>" } } }
                if method == "session/update" and isinstance(params, dict):
                    update = params.get("update", {})
                    if update.get("sessionUpdate") == "agent_message_chunk":
                        chunk = update.get("content", {}).get("text", "")
                        if chunk:
                            accumulated_text.append(chunk)
                            logger.debug("ACP chunk: %r", chunk)
                continue

            # ── Server→client REQUEST (has BOTH 'id' AND 'method') ───────────
            # gemini-cli sends tool-confirmation requests to the client as
            # JSON-RPC requests (not responses), identifiable by the presence
            # of a 'method' field.  These MUST be checked BEFORE the id==req_id
            # check below, because the server may reuse an id that collides with
            # our pending prompt request id.
            #
            # Confirmed approval schema (from live logs):
            #   result: { "outcome": { "optionId": "<one of the offered options>" } }
            # Options offered for session/request_permission:
            #   proceed_always  – allow for entire session
            #   proceed_once    – allow just this once
            #   cancel          – reject
            if "method" in msg:
                server_req_id = msg.get("id")
                server_method = msg.get("method", "")
                params        = msg.get("params", {})
                # Pick the most permissive option offered
                options = params.get("options", [])
                option_ids = [o.get("optionId", "") for o in options]
                chosen = next(
                    (o for o in ("proceed_always", "proceed_once") if o in option_ids),
                    option_ids[0] if option_ids else "proceed_always",
                )
                logger.info(
                    "Auto-approving server request: method=%s id=%s option=%s",
                    server_method, server_req_id, chosen,
                )
                # Confirmed schema from live logs:
                # result.outcome.outcome  = "selected" | "cancelled"  (discriminator)
                # result.outcome.optionId = the chosen optionId string
                await self._send({
                    "jsonrpc": "2.0",
                    "id": server_req_id,
                    "result": {
                        "outcome": {
                            "outcome": "selected",   # discriminator value
                            "optionId": chosen,
                        },
                    },
                })
                continue

            # ── Response for our request ──────────────────────────────────────
            # Only pure responses (have 'id', no 'method') reach here.
            if msg.get("id") == req_id:
                if "error" in msg:
                    err = msg["error"]
                    raise RuntimeError(
                        f"ACP error {err.get('code')}: {err.get('message')}"
                    )

                result = msg.get("result", {})
                # Attach any accumulated notification text so callers can use it
                if accumulated_text:
                    result["_notification_text"] = "".join(accumulated_text)
                return result

            # ── Stale response from a previous request ────────────────────────
            logger.debug(
                "Ignoring stale response for id %s (waiting for %s)",
                msg.get("id"), req_id,
            )

    # ── ACP Handshake ────────────────────────────────────────────────────────

    async def _initialize(self) -> None:
        """
        Perform the ACP handshake (2 steps, confirmed by live protocol test):

          1. initialize  – negotiate protocol version & capabilities
                           Response includes authMethods (metadata only, ignore)
          2. session/new – create a session; REQUIRED params:
                             cwd        (str)   working directory
                             mcpServers (list)  MCP servers (can be empty)

        NOTE: gemini-cli does NOT support an 'initialized' notification
        (-32601 Method not found).  Skip it entirely.
        Auth is handled automatically from OS-cached credentials.
        """
        logger.info("Performing ACP handshake …")

        # ── Step 1: initialize ────────────────────────────────────────────
        init_id = self._next_id()
        await self._send({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientInfo": {
                    "name":    "gelegram-telegram-bot",
                    "version": "1.0.0",
                },
                "clientCapabilities": {},
            },
        })
        init_result = await self._recv_response(init_id, timeout=30)
        # authMethods in the response is metadata only — it lists what auth
        # providers are configured on the server.  No auth/select call needed;
        # no 'initialized' notification needed (gemini-cli returns -32601 for it).
        # gemini-cli uses OS-cached credentials (oauth-personal) automatically.
        logger.info(
            "ACP initialized (version=%s, auth=%s)",
            init_result.get("protocolVersion"),
            [m.get("id") for m in init_result.get("authMethods", [])],
        )

        # ── Step 2: create a session ──────────────────────────────────────
        # session/new REQUIRES two params (confirmed from gemini-cli source):
        #   cwd        – working directory string (passed to the agent)
        #   mcpServers – list of MCP servers to connect to (can be empty)
        sess_id = self._next_id()
        await self._send({
            "jsonrpc": "2.0",
            "id": sess_id,
            "method": "session/new",
            "params": {
                "cwd": GEMINI_WORKING_DIR,
                "mcpServers": [],     # no MCP tool servers needed for basic chat
                # Trust the working directory so -y (YOLO) mode is not overridden.
                # Without this gemini-cli prints:
                #   "Approval mode overridden to 'default' because the current
                #    folder is not trusted."
                "trustedFolders": [GEMINI_WORKING_DIR],
            },
        })
        sess_result = await self._recv_response(sess_id, timeout=30)
        self._session_id = sess_result.get("sessionId") or sess_result.get("id")
        logger.info("ACP session created: %s", self._session_id)

        # ── Setup Transcript File ─────────────────────────────────────────
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        transcripts_dir = Path(GEMINI_WORKING_DIR) / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        self._transcript_file = transcripts_dir / f"session_{timestamp}.txt"
        logger.info("Transcript file created: %s", self._transcript_file)

        self._initialized = True

    # ── Process lifecycle ────────────────────────────────────────────────────

    async def _start_process(self) -> None:
        """
        Launch `gemini --acp` as an async subprocess.

        IMPORTANT (Windows 11):
        • asyncio.subprocess requires the ProactorEventLoop on Windows,
          which is the default since Python 3.8+.
        • .cmd files (npm-installed tools on Windows) cannot be exec'd
          directly – they need `cmd /c <file>` to be interpreted by the shell.
        • We capture stderr separately so stray diagnostic text from
          gemini-cli cannot corrupt the JSON-RPC stream on stdout.
        """
        cli = GEMINI_CLI_PATH

        # Windows: .cmd and .bat scripts require the cmd.exe interpreter.
        # asyncio.create_subprocess_exec bypasses the shell, so we must
        # prefix with cmd /c explicitly.
        if sys.platform == "win32" and cli.lower().endswith((".cmd", ".bat")):
            exec_args = ["cmd", "/c", cli, "--acp", "-y"]
        else:
            exec_args = [cli, "--acp", "-y"]
        # -y  →  YOLO mode: auto-approve all tool actions (file edits, shell
        #         commands, etc.) without prompting the user.

        logger.info(
            "Starting ACP subprocess: %s  (cwd=%s)",
            " ".join(exec_args),
            GEMINI_WORKING_DIR,
        )
        self._process = await asyncio.create_subprocess_exec(
            *exec_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,   # capture stderr separately
            cwd=GEMINI_WORKING_DIR,
        )
        logger.info("ACP subprocess started (pid=%s)", self._process.pid)

        # Consume stderr asynchronously so it doesn't fill the OS pipe buffer
        asyncio.create_task(self._drain_stderr(), name="drain-stderr")

    async def _drain_stderr(self) -> None:
        """Read and log gemini-cli stderr at WARNING level so it shows in console."""
        if self._process is None or self._process.stderr is None:
            return
            
        ignore_keywords = [
            "conpty_console_list_agent.js",
            "getConsoleProcessList",
            "Error: AttachConsole failed",
            "at Object.<anonymous>",
            "at Module._compile",
            "at Object..js",
            "at Module.load",
            "at Module._load",
            "at wrapModuleLoad",
            "at Module.executeUserEntryPoint",
            "at node:internal",
            "Node.js v2"
        ]
        
        while True:
            line = await self._process.stderr.readline()
            if not line:
                break
                
            line_str = line.decode("utf-8", errors="replace").rstrip()
            if not line_str:
                continue
                
            if any(kw in line_str for kw in ignore_keywords) or line_str.strip() == "^" or line_str.strip() == "var consoleProcessList = getConsoleProcessList(shellPid);":
                continue
                
            # Log at WARNING so it's always visible — gemini-cli writes useful
            # diagnostics (auth errors, startup failures) to stderr
            logger.warning("[gemini-stderr] %s", line_str)

    def _is_alive(self) -> bool:
        """Return True if the subprocess is running."""
        return self._process is not None and self._process.returncode is None

    async def _ensure_running(self) -> None:
        """
        Guarantee the subprocess is alive and the ACP handshake has been
        completed.  If the process is dead (crashed, never started), it is
        restarted and re-initialised transparently.
        """
        if not self._is_alive():
            logger.info("ACP process is not running – starting …")
            self._initialized = False
            self._session_id  = None
            await self._start_process()
            await self._initialize()
        elif not self._initialized:
            await self._initialize()

    async def stop(self) -> None:
        """Gracefully terminate the gemini-cli subprocess."""
        if self._process is not None and self._is_alive():
            logger.info("Terminating ACP subprocess (pid=%s) …", self._process.pid)
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            logger.info("ACP subprocess terminated.")
        self._process     = None
        self._initialized = False
        self._session_id  = None
        self._active_req_id = None
        self.private_mode = False

    async def cancel_active_request(self) -> bool:
        """Attempt to cancel the currently running request gracefully without killing the process."""
        if not self._is_alive() or self._active_req_id is None:
            return False
        
        logger.info("Sending cancel request for req_id=%s", self._active_req_id)
        # JSON-RPC standard cancellation notification
        await self._send({
            "jsonrpc": "2.0",
            "method": "$/cancelRequest",
            "params": {
                "id": self._active_req_id
            }
        })
        return True

    # ── Public API ───────────────────────────────────────────────────────────

    async def send_prompt(self, text: str, user_name: str = "user", on_timeout_callback=None) -> str:
        """
        Send `text` to gemini-cli and return the full text response.

        This method is serialised by an asyncio.Lock so that concurrent
        Telegram messages do not interleave JSON-RPC ids on the stdio stream.

        Raises:
            RuntimeError   – ACP protocol error returned by gemini-cli
            asyncio.TimeoutError – No response within ACP_TIMEOUT seconds
            Exception      – Subprocess crashed or other unexpected error
        """
        async with self._lock:
            # Restart process if it crashed since the last call
            await self._ensure_running()

            # Log user message to transcript
            if self._transcript_file and not self.private_mode:
                import datetime
                time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(self._transcript_file, "a", encoding="utf-8") as f:
                    f.write(f"{time_str} : {user_name} : {text}\n")

            prompt_id = self._next_id()
            self._active_req_id = prompt_id
            
            await self._send({
                "jsonrpc": "2.0",
                "id": prompt_id,
                "method": "session/prompt",      # confirmed method name in gemini-cli ACP
                "params": {
                    "sessionId": self._session_id,
                    "prompt": [
                        {"type": "text", "text": text}
                    ],
                },
            })

            try:
                result = await self._recv_response(prompt_id, timeout=ACP_TIMEOUT)
            except asyncio.TimeoutError:
                if on_timeout_callback:
                    try:
                        await on_timeout_callback()
                    except Exception as e:
                        logger.error("Error in on_timeout_callback: %s", e)
                # Continue waiting with a generous 30-minute timeout for heavy tasks
                result = await self._recv_response(prompt_id, timeout=1800)
            finally:
                self._active_req_id = None

            # ── Extract plain text from the result payload ────────────────
            response_text = ""
            if "_notification_text" in result and result["_notification_text"].strip():
                response_text = result["_notification_text"].strip()
            elif "text" in result and result["text"]:
                response_text = str(result["text"]).strip()
            elif result.get("candidates", []):
                parts = result["candidates"][0].get("content", {}).get("parts", [])
                text_parts = [p["text"] for p in parts if "text" in p]
                if text_parts:
                    response_text = "".join(text_parts).strip()
            
            if not response_text and "content" in result and result["content"]:
                response_text = str(result["content"]).strip()
            if not response_text and "message" in result and result["message"]:
                response_text = str(result["message"]).strip()
            
            if not response_text:
                logger.warning("Could not extract text from ACP result: %s", result)
                response_text = json.dumps(result, indent=2)

            # Log Gemini response to transcript
            if self._transcript_file and not self.private_mode:
                import datetime
                time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(self._transcript_file, "a", encoding="utf-8") as f:
                    f.write(f"{time_str} : gemini : {response_text}\n")
                    
            return response_text


# ─────────────────────────────────────────────────────────────────────────────
# Telegram bot handlers
# ─────────────────────────────────────────────────────────────────────────────

# Per-chat ACP clients (multi-threading support)
acp_clients: dict[int, GeminiACPClient] = {}
media_groups: dict[str, list[str]] = {}
TRUSTED_USERS_FILE = "trusted_users.json"

try:
    with open(TRUSTED_USERS_FILE, "r") as f:
        trusted_users = set(json.load(f))
except (FileNotFoundError, json.JSONDecodeError):
    trusted_users = set()

def save_trusted_users():
    with open(TRUSTED_USERS_FILE, "w") as f:
        json.dump(list(trusted_users), f)

def get_client(chat_id: int) -> GeminiACPClient:
    if chat_id not in acp_clients:
        acp_clients[chat_id] = GeminiACPClient()
    return acp_clients[chat_id]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command – send a welcome message."""
    await update.message.reply_text(
        "👋 Hello! I'm your Gemini CLI bridge bot.\n\n"
        "Send me any message and I'll relay it to Gemini through the ACP server.\n\n"
        "Commands:\n"
        "  /start    – Show this message\n"
        "  /reset    – Reset the Gemini session\n"
        "  /private  – Toggle private mode (no logging)\n"
        "  /status   – Check the ACP subprocess status"
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reset – tear down and restart the ACP subprocess + session."""
    await update.message.reply_text("🔄 Resetting Gemini session …")
    acp_client = get_client(update.effective_chat.id)
    await acp_client.stop()
    await update.message.reply_text(
        "✅ Session reset. Your next message will start a fresh Gemini session."
    )

async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /kill – attempt to cancel the active request without killing the session."""
    acp_client = get_client(update.effective_chat.id)
    sent = await acp_client.cancel_active_request()
    if sent:
        await update.message.reply_text("🛑 Sent cancellation request to Gemini. It should stop shortly while keeping your context intact.")
    else:
        await update.message.reply_text("⚠️ No active task found to kill.")


async def cmd_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /private – toggle transcript logging for this session."""
    acp_client = get_client(update.effective_chat.id)
    acp_client.private_mode = not acp_client.private_mode
    if acp_client.private_mode:
        await update.message.reply_text("🕵️ Private mode enabled. Messages in this session will not be logged to the transcript. Send /private again to disable.")
    else:
        await update.message.reply_text("📝 Private mode disabled. Messages will now be logged to the transcript.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status – report subprocess health."""
    acp_client = get_client(update.effective_chat.id)
    alive = acp_client._is_alive()
    pid   = acp_client._process.pid if acp_client._process else "N/A"
    sess  = acp_client._session_id or "none"
    status_line = "🟢 Running" if alive else "🔴 Stopped"
    await update.message.reply_text(
        f"ACP subprocess: {status_line}\n"
        f"PID:            {pid}\n"
        f"Session ID:     {sess}\n"
        f"Timeout:        {ACP_TIMEOUT}s"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main handler: forwards every text message to Gemini via ACP and replies
    with the response.

    Flow:
      1. Send a "typing…" action so the user knows we're working.
      2. Forward the message to GeminiACPClient.send_prompt().
      3. Handle errors gracefully and always reply to the user.
    """
    user_text = update.message.text or update.message.caption or ""
    user      = update.effective_user

    # ── Handle Attachments ───────────────────────────────────────────────────
    attachment = update.message.document or update.message.photo or update.message.audio or update.message.video
    if attachment:
        if isinstance(attachment, (list, tuple)):
            attachment = attachment[-1]  # Get highest resolution photo
        
        file_id = attachment.file_id
        file_name = getattr(attachment, "file_name", f"{file_id}.jpg" if update.message.photo else f"{file_id}.file")
        
        # Ensure incoming directory exists
        incoming_dir = Path(GEMINI_WORKING_DIR) / "media" / "incoming"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        file_path = incoming_dir / file_name
        
        # Download file
        file_obj = await context.bot.get_file(file_id)
        await file_obj.download_to_drive(custom_path=file_path)
        
        # Tell Gemini about the file
        file_notice = f"[User attached a file, saved to: {file_path.absolute()}]"
        user_text = f"{user_text}\n{file_notice}".strip()

    # ── Handle Media Groups (Albums) ─────────────────────────────────────────
    media_group_id = update.message.media_group_id
    if media_group_id:
        if media_group_id not in media_groups:
            media_groups[media_group_id] = []
        media_groups[media_group_id].append(user_text)
        
        # Wait slightly to allow all album parts to arrive from Telegram
        await asyncio.sleep(1.5)
        
        # Only the first message processed for this group will actually send it
        if media_group_id in media_groups:
            all_parts = media_groups.pop(media_group_id)
            user_text = "\n\n".join([p for p in all_parts if p]).strip()
        else:
            # We are a subsequent part of the album; our text was already grouped and sent.
            return

    if not user_text:
        return  # Ignore empty messages with no supported attachments

    logger.info(
        "Message from %s (%s): %s",
        user.full_name if user else "unknown",
        user.id if user else "?",
        user_text[:120],  # truncate long messages in logs
    )

    chat_id = update.effective_chat.id
    
    # ── Password Authentication ──────────────────────────────────────────────
    if BOT_PASSWORD and chat_id not in trusted_users:
        if user_text.strip() == BOT_PASSWORD:
            trusted_users.add(chat_id)
            save_trusted_users()
            await update.message.reply_text("✅ Password accepted. You are now a trusted user!")
            return
        else:
            await update.message.reply_text("🔒 Please provide the bot password to use this bot.")
            return

    # Continuous typing indicator task
    async def keep_typing():
        try:
            while True:
                await context.bot.send_chat_action(
                    chat_id=chat_id, action="typing"
                )
                await asyncio.sleep(4)  # Telegram typing status lasts ~5s
        except asyncio.CancelledError:
            pass

    typing_task = asyncio.create_task(keep_typing())

    async def notify_timeout():
        await update.message.reply_text(
            "⏱️ Gemini is taking longer than expected. I will message you as soon as I get a response!"
        )

    try:
        acp_client = get_client(chat_id)
        display_name = user.full_name if user else "user"
        response = await acp_client.send_prompt(
            user_text,
            user_name=display_name,
            on_timeout_callback=notify_timeout
        )

    except asyncio.TimeoutError:
        logger.error("ACP request timed out after maximum wait time (30 mins)")
        response = (
            "⏱️ The request has exceeded the 30-minute maximum execution limit.\n"
            "Please simplify your query and try again."
        )

    except FileNotFoundError:
        logger.error("gemini-cli executable not found at: %s", GEMINI_CLI_PATH)
        response = (
            "❌ Could not find the Gemini CLI executable.\n"
            f"Expected at: `{GEMINI_CLI_PATH}`\n"
            "Check your `.env` file and make sure gemini-cli is installed."
        )

    except RuntimeError as exc:
        logger.error("ACP runtime error: %s", exc)
        response = f"❌ Gemini returned an error:\n`{exc}`"

    except Exception as exc:
        logger.exception("Unexpected error while handling message")
        response = f"❌ An unexpected error occurred: `{exc}`"

    finally:
        typing_task.cancel()

    import re
    import os

    # ── Auto-attach extracted files ──────────────────────────────────────────
    # The prompt instructs Gemini to append "file:<absolute_path>" to its response
    # for any files it wants to attach. We extract these paths and remove them from
    # the message sent to the user.
    file_matches = re.findall(r'file:\s*(.+?)(?:\n|$)', response, flags=re.IGNORECASE)
    
    # Remove the markers from the response text
    clean_response = re.sub(r'file:\s*.+?(?:\n|$)', '', response, flags=re.IGNORECASE).strip()
    if not clean_response:
        clean_response = "📎 File(s) attached."

    # Telegram messages have a 4096-character limit; split if needed
    max_len = 4000
    if len(clean_response) <= max_len:
        await update.message.reply_text(clean_response)
    else:
        # Split at word boundaries where possible
        chunks = [clean_response[i : i + max_len] for i in range(0, len(clean_response), max_len)]
        for i, chunk in enumerate(chunks, 1):
            await update.message.reply_text(
                f"[Part {i}/{len(chunks)}]\n{chunk}"
            )

    # Send the extracted files
    for file_path in set(file_matches):
        file_path = file_path.strip().strip('\'"\\/') # clean up trailing/leading quotes
        if os.path.isfile(file_path):
            try:
                filename = os.path.basename(file_path)
                ext = filename.lower().split('.')[-1]
                if ext in ['jpg', 'jpeg', 'png', 'webp', 'bmp']:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=open(file_path, 'rb'),
                        caption=f"🖼️ {filename}"
                    )
                else:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=open(file_path, 'rb'),
                        caption=f"📎 {filename}"
                    )
            except Exception as e:
                logger.error("Failed to send document %s: %s", file_path, e)
                await update.message.reply_text(f"❌ Failed to attach file: {filename}\n`{e}`")
        else:
            await update.message.reply_text(f"⚠️ Gemini tried to attach a file, but it wasn't found:\n`{file_path}`")
# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Build and run the Telegram bot application."""
    # Guard is here (not module-level) so importing bot.py from chat.py is safe
    if not TELEGRAM_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN is not set. Add it to your .env file.")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("  Gelegram - Telegram -> Gemini CLI ACP Bridge")
    logger.info("=" * 60)
    logger.info("Gemini CLI path : %s", GEMINI_CLI_PATH)
    logger.info("Working dir     : %s", GEMINI_WORKING_DIR)
    logger.info("ACP timeout     : %ss", ACP_TIMEOUT)

    async def post_init(app: Application) -> None:
        await app.bot.set_my_commands([
            BotCommand("start", "Show welcome message"),
            BotCommand("reset", "Reset the Gemini session"),
            BotCommand("private", "Toggle private mode (no logging)"),
            BotCommand("status", "Check ACP subprocess status"),
            BotCommand("kill", "Cancel active request"),
        ])

    # Build the Application (v20+ PTB style)
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )

    # Register handlers
    application.add_handler(CommandHandler("start",  cmd_start))
    application.add_handler(CommandHandler("reset",  cmd_reset))
    application.add_handler(CommandHandler("kill",   cmd_kill))
    application.add_handler(CommandHandler("private", cmd_private))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(
        MessageHandler(~filters.COMMAND, handle_message)
    )

    # Register a post-shutdown cleanup so the ACP subprocess is terminated
    async def shutdown(_app):
        logger.info("Bot shutting down – stopping all ACP subprocesses …")
        for client in acp_clients.values():
            await client.stop()

    application.post_shutdown = shutdown  # type: ignore[assignment]

    logger.info("Bot is polling for updates … (Ctrl+C to stop)")
    # run_polling blocks until the process receives SIGINT / SIGTERM
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,   # ignore messages queued while bot was offline
    )


if __name__ == "__main__":
    main()
