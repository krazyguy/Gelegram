"""
=============================================================================
  scheduler.py — Cron Scheduler for Gelegram
=============================================================================
  A lightweight, pure-stdlib scheduled command executor that runs as a
  daemon thread inside gateway.py.

  Features:
    • Reads job definitions from workdir/cron.json
    • Supports interval schedules (every Xm/Xh/Xs), daily (at HH:MM),
      weekly (at HH:MM weekday=day), and one-shot (once YYYY-MM-DD HH:MM)
    • Executes commands as shell subprocesses with configurable timeouts
    • Sends output to Telegram via raw HTTP POST (no python-telegram-bot dep)
    • Hot-reloads cron.json every 60s (watches file mtime)
    • One-shot jobs auto-disable after firing
    • All execution logged to cron.log

  Architecture:
    gateway.py creates a CronScheduler instance, calls start(), and the
    scheduler runs in its own thread. It has zero dependency on bot.py —
    uses urllib to POST directly to api.telegram.org.

  Dependencies: NONE (pure stdlib — threading, subprocess, urllib, json)
=============================================================================
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Logging — separate from gateway.log and bot.log for clean audit trail
# ─────────────────────────────────────────────────────────────────────────────
logger = logging.getLogger("gelegram.scheduler")


# ─────────────────────────────────────────────────────────────────────────────
# Schedule Types
# ─────────────────────────────────────────────────────────────────────────────
WEEKDAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


@dataclass
class ScheduleInfo:
    """Parsed schedule — one of: interval, daily, weekly, or one-shot."""
    kind: str  # "interval" | "daily" | "weekly" | "once"
    interval_seconds: Optional[float] = None   # for "interval"
    time_of_day: Optional[str] = None           # "HH:MM" for daily/weekly/once
    weekday: Optional[int] = None               # 0=Mon..6=Sun for weekly
    once_datetime: Optional[datetime] = None    # exact datetime for one-shot


@dataclass
class CronJob:
    """
    Represents a single scheduled job parsed from cron.json.

    Fields:
      alias          — unique identifier (the JSON key)
      cmd            — shell command to execute
      description    — human-readable label
      schedule_str   — raw schedule string from config
      schedule       — parsed ScheduleInfo
      timeout        — max seconds before subprocess is killed
      notify         — notification mode: always | on_failure | on_output | never
      enabled        — whether the job is active
      chat_id        — Telegram chat ID for notifications (None = use default)
      next_run       — computed next execution time
      last_run       — last execution time (None if never run)
      last_exit_code — exit code from last run (None if never run)
    """
    alias: str
    cmd: str
    description: str = ""
    schedule_str: str = ""
    schedule: Optional[ScheduleInfo] = None
    timeout: int = 30
    notify: str = "always"
    enabled: bool = True
    chat_id: Optional[int] = None
    next_run: Optional[datetime] = None
    last_run: Optional[datetime] = None
    last_exit_code: Optional[int] = None


# ─────────────────────────────────────────────────────────────────────────────
# Schedule Parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_schedule(expr: str) -> ScheduleInfo:
    """
    Parse a human-readable schedule expression into a ScheduleInfo.

    Supported formats:
      every 30s          → interval (30 seconds)
      every 5m           → interval (5 minutes)
      every 2h           → interval (2 hours)
      at 09:00           → daily at 09:00
      at 14:30 weekday=mon  → weekly on Monday at 14:30
      once 2026-05-21 14:00 → one-shot at specific datetime

    Raises ValueError if the expression can't be parsed.
    """
    expr = expr.strip().lower()

    # ── Interval: every Xs / every Xm / every Xh ─────────────────────────
    interval_match = re.match(r"^every\s+(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hr|hours?)$", expr)
    if interval_match:
        value = int(interval_match.group(1))
        unit = interval_match.group(2)[0]  # first char: s, m, or h
        multiplier = {"s": 1, "m": 60, "h": 3600}[unit]
        return ScheduleInfo(kind="interval", interval_seconds=value * multiplier)

    # ── Daily: at HH:MM ──────────────────────────────────────────────────
    daily_match = re.match(r"^at\s+(\d{1,2}):(\d{2})$", expr)
    if daily_match:
        hh, mm = int(daily_match.group(1)), int(daily_match.group(2))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"Invalid time: {hh}:{mm:02d}")
        return ScheduleInfo(kind="daily", time_of_day=f"{hh:02d}:{mm:02d}")

    # ── Weekly: at HH:MM weekday=DAY ─────────────────────────────────────
    weekly_match = re.match(r"^at\s+(\d{1,2}):(\d{2})\s+weekday=(\w+)$", expr)
    if weekly_match:
        hh, mm = int(weekly_match.group(1)), int(weekly_match.group(2))
        day_str = weekly_match.group(3)
        if day_str not in WEEKDAY_MAP:
            raise ValueError(f"Unknown weekday: '{day_str}'. Use: {', '.join(WEEKDAY_MAP.keys())}")
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"Invalid time: {hh}:{mm:02d}")
        return ScheduleInfo(
            kind="weekly",
            time_of_day=f"{hh:02d}:{mm:02d}",
            weekday=WEEKDAY_MAP[day_str],
        )

    # ── One-shot: once YYYY-MM-DD HH:MM ──────────────────────────────────
    once_match = re.match(r"^once\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})$", expr)
    if once_match:
        date_str = once_match.group(1)
        hh, mm = int(once_match.group(2)), int(once_match.group(3))
        try:
            dt = datetime.strptime(f"{date_str} {hh:02d}:{mm:02d}", "%Y-%m-%d %H:%M")
        except ValueError:
            raise ValueError(f"Invalid date/time: {date_str} {hh}:{mm:02d}")
        return ScheduleInfo(kind="once", once_datetime=dt, time_of_day=f"{hh:02d}:{mm:02d}")

    raise ValueError(f"Cannot parse schedule expression: '{expr}'")


def compute_next_run(schedule: ScheduleInfo, after: Optional[datetime] = None) -> Optional[datetime]:
    """
    Compute the next run time for a schedule, relative to `after` (default: now).

    Returns None for one-shot jobs that are in the past (already fired).
    """
    now = after or datetime.now()

    if schedule.kind == "interval":
        # First run: immediately (or `interval` seconds from now if we want a delay)
        # We start with a small offset so jobs don't all fire at t=0
        return now + timedelta(seconds=schedule.interval_seconds)

    elif schedule.kind == "daily":
        hh, mm = map(int, schedule.time_of_day.split(":"))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    elif schedule.kind == "weekly":
        hh, mm = map(int, schedule.time_of_day.split(":"))
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        # Advance to the correct weekday
        days_ahead = schedule.weekday - now.weekday()
        if days_ahead < 0 or (days_ahead == 0 and target <= now):
            days_ahead += 7
        target += timedelta(days=days_ahead)
        return target

    elif schedule.kind == "once":
        if schedule.once_datetime and schedule.once_datetime > now:
            return schedule.once_datetime
        return None  # already in the past

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Telegram Notification (raw HTTP — no python-telegram-bot dependency)
# ─────────────────────────────────────────────────────────────────────────────

def send_telegram_message(chat_id: int, text: str, token: str) -> bool:
    """
    Send a message to a Telegram chat via the Bot API (raw HTTP POST).

    Uses urllib (stdlib) to avoid any dependency on python-telegram-bot or
    requests in the gateway process.

    Returns True on success, False on failure (logged, never raises).
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Truncate to Telegram's 4096-char limit, leaving room for the ellipsis
    if len(text) > 4000:
        text = text[:3997] + "…"

    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return True
            else:
                logger.warning("Telegram API returned status %s", resp.status)
                return False
    except urllib.error.HTTPError as exc:
        logger.error("Telegram API HTTP error: %s %s", exc.code, exc.reason)
        return False
    except Exception as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Job Execution
# ─────────────────────────────────────────────────────────────────────────────

def execute_job(job: CronJob, workdir: str, token: str, default_chat_id: Optional[int]) -> None:
    """
    Execute a single cron job:
      1. Run the command as a shell subprocess
      2. Capture stdout + stderr
      3. Send output to Telegram based on the notify setting
      4. Update job.last_run and job.last_exit_code

    This runs in the scheduler thread — it blocks until the command finishes
    or times out. Since we only have one scheduler thread, jobs run serially.
    For most use cases (quick scripts, health checks) this is fine.
    """
    chat_id = job.chat_id or default_chat_id
    job.last_run = datetime.now()

    logger.info("Executing job '%s': %s (timeout=%ds)", job.alias, job.cmd, job.timeout)

    try:
        result = subprocess.run(
            job.cmd,
            shell=True,
            capture_output=True,
            timeout=job.timeout,
            cwd=workdir,
        )
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        exit_code = result.returncode
        timed_out = False

    except subprocess.TimeoutExpired:
        stdout = ""
        stderr = ""
        exit_code = -1
        timed_out = True
        logger.warning("Job '%s' timed out after %ds", job.alias, job.timeout)

    except Exception as exc:
        stdout = ""
        stderr = str(exc)
        exit_code = -1
        timed_out = False
        logger.error("Job '%s' failed to execute: %s", job.alias, exc)

    job.last_exit_code = exit_code

    # Log the result
    logger.info(
        "Job '%s' finished: exit=%s stdout=%d bytes stderr=%d bytes%s",
        job.alias, exit_code, len(stdout), len(stderr),
        " (TIMEOUT)" if timed_out else "",
    )

    # ── Decide whether to notify ─────────────────────────────────────────
    should_notify = False
    if job.notify == "always":
        should_notify = True
    elif job.notify == "on_failure" and exit_code != 0:
        should_notify = True
    elif job.notify == "on_output" and (stdout or stderr):
        should_notify = True
    # "never" → never notify

    if should_notify and chat_id and token:
        # Build notification message
        parts = []
        if timed_out:
            parts.append(f"⏱️ Cron `{job.alias}` timed out after {job.timeout}s")
        elif exit_code == 0:
            parts.append(f"✅ Cron `{job.alias}` completed")
        else:
            parts.append(f"⚠️ Cron `{job.alias}` exited with code {exit_code}")

        if job.description:
            parts.append(f"_{job.description}_")

        if stdout:
            truncated = stdout[:3000]
            if len(stdout) > 3000:
                truncated += f"\n… ({len(stdout) - 3000} chars truncated)"
            parts.append(f"```\n{truncated}\n```")

        if stderr:
            truncated_err = stderr[:800]
            if len(stderr) > 800:
                truncated_err += f"\n… ({len(stderr) - 800} chars truncated)"
            parts.append(f"⚠️ stderr:\n```\n{truncated_err}\n```")

        if not stdout and not stderr and not timed_out:
            parts.append("_(no output)_")

        message = "\n".join(parts)
        send_telegram_message(chat_id, message, token)

    elif should_notify and not chat_id:
        logger.warning(
            "Job '%s' wants to notify but no chat_id configured. "
            "Set defaults.chat_id in cron.json or add a trusted user.",
            job.alias,
        )


# ─────────────────────────────────────────────────────────────────────────────
# CronScheduler — the main scheduler engine
# ─────────────────────────────────────────────────────────────────────────────

class CronScheduler:
    """
    Manages scheduled jobs defined in cron.json.

    Runs in a dedicated daemon thread. Checks every second if any job is
    due, executes it, and computes the next run time.

    Hot-reload: watches cron.json mtime every 60s and reloads if changed.

    Lifecycle:
      scheduler = CronScheduler(workdir, token)
      scheduler.start()   # spawns the daemon thread
      ...
      scheduler.stop()    # signals the thread to exit, waits for join
    """

    # How often to check for due jobs (seconds)
    _TICK_INTERVAL = 1.0
    # How often to check for cron.json changes (seconds)
    _RELOAD_INTERVAL = 60.0

    def __init__(self, workdir: str, telegram_token: str) -> None:
        self._workdir = workdir
        self._token = telegram_token
        self._cron_json_path = Path(workdir) / "cron.json"
        self._trusted_users_path = Path(workdir).parent / "trusted_users.json"

        self._jobs: dict[str, CronJob] = {}
        self._default_chat_id: Optional[int] = None
        self._default_timeout: int = 30

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_mtime: float = 0.0
        self._last_reload_check: float = 0.0

        # ── State file for tracking last_run across restarts ─────────────
        self._state_file = Path(workdir) / ".cron_state.json"

    # ── Config Loading ───────────────────────────────────────────────────

    def _resolve_default_chat_id(self, config: dict) -> Optional[int]:
        """
        Determine the default chat_id for notifications.

        Priority:
          1. defaults.chat_id in cron.json (explicit override)
          2. First entry in trusted_users.json (auto-detect)
        """
        # Priority 1: explicit in cron.json
        defaults = config.get("defaults", {})
        explicit_id = defaults.get("chat_id")
        if explicit_id is not None:
            return int(explicit_id)

        # Priority 2: auto-detect from trusted_users.json
        # trusted_users.json is in the project root (parent of workdir)
        if self._trusted_users_path.exists():
            try:
                with open(self._trusted_users_path, "r", encoding="utf-8") as f:
                    users = json.load(f)
                if isinstance(users, list) and users:
                    return int(users[0])
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                logger.warning("Could not read trusted_users.json: %s", exc)

        return None

    def _load_state(self) -> dict:
        """Load persisted job state (last_run times) from .cron_state.json."""
        if self._state_file.exists():
            try:
                with open(self._state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_state(self) -> None:
        """Persist current job state so last_run survives gateway restarts."""
        state = {}
        for alias, job in self._jobs.items():
            entry = {}
            if job.last_run:
                entry["last_run"] = job.last_run.isoformat()
            if job.last_exit_code is not None:
                entry["last_exit_code"] = job.last_exit_code
            if entry:
                state[alias] = entry
        try:
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except OSError as exc:
            logger.warning("Could not save cron state: %s", exc)

    def load_jobs(self) -> None:
        """
        (Re)load jobs from cron.json.

        On reload, existing jobs that are still in the config retain their
        last_run and next_run state. New jobs get computed. Removed jobs
        are dropped.
        """
        if not self._cron_json_path.exists():
            logger.info("No cron.json found at %s — no jobs loaded.", self._cron_json_path)
            self._jobs = {}
            return

        try:
            with open(self._cron_json_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load cron.json: %s", exc)
            return  # keep existing jobs on error

        if not isinstance(config.get("jobs"), dict):
            logger.warning("cron.json missing 'jobs' dict — no jobs loaded.")
            self._jobs = {}
            return

        defaults = config.get("defaults", {})
        self._default_timeout = defaults.get("timeout", 30)
        self._default_chat_id = self._resolve_default_chat_id(config)

        # Load persisted state for last_run restoration
        persisted_state = self._load_state()

        new_jobs: dict[str, CronJob] = {}
        jobs_config = config["jobs"]

        for alias, entry in jobs_config.items():
            # Support string shorthand: "alias": "command"
            if isinstance(entry, str):
                entry = {"cmd": entry, "schedule": "every 1h"}

            if not isinstance(entry, dict):
                logger.warning("Job '%s' has invalid config — skipping.", alias)
                continue

            cmd = entry.get("cmd", "")
            schedule_str = entry.get("schedule", "")
            if not cmd or not schedule_str:
                logger.warning("Job '%s' missing 'cmd' or 'schedule' — skipping.", alias)
                continue

            # Parse the schedule expression
            try:
                schedule = parse_schedule(schedule_str)
            except ValueError as exc:
                logger.error("Job '%s' has invalid schedule '%s': %s", alias, schedule_str, exc)
                continue

            # Build the CronJob
            job = CronJob(
                alias=alias,
                cmd=cmd,
                description=entry.get("description", ""),
                schedule_str=schedule_str,
                schedule=schedule,
                timeout=entry.get("timeout", self._default_timeout),
                notify=entry.get("notify", defaults.get("notify", "always")),
                enabled=entry.get("enabled", True),
                chat_id=entry.get("chat_id"),
            )

            # Restore state from previous run if available
            if alias in self._jobs:
                # Existing job — keep runtime state
                old = self._jobs[alias]
                job.last_run = old.last_run
                job.last_exit_code = old.last_exit_code
                # Only recompute next_run if schedule changed
                if old.schedule_str != schedule_str:
                    job.next_run = compute_next_run(schedule)
                else:
                    job.next_run = old.next_run
            elif alias in persisted_state:
                # Restore from persisted state file
                state_entry = persisted_state[alias]
                if "last_run" in state_entry:
                    try:
                        job.last_run = datetime.fromisoformat(state_entry["last_run"])
                    except ValueError:
                        pass
                job.last_exit_code = state_entry.get("last_exit_code")
                job.next_run = compute_next_run(schedule)
            else:
                # New job — compute first run
                job.next_run = compute_next_run(schedule)

            new_jobs[alias] = job

        # Log changes
        added = set(new_jobs) - set(self._jobs)
        removed = set(self._jobs) - set(new_jobs)
        if added:
            logger.info("Cron jobs added: %s", ", ".join(added))
        if removed:
            logger.info("Cron jobs removed: %s", ", ".join(removed))

        self._jobs = new_jobs

        try:
            self._last_mtime = self._cron_json_path.stat().st_mtime
        except OSError:
            pass

        logger.info(
            "Loaded %d cron job(s) (default chat_id=%s, default timeout=%ds)",
            len(self._jobs), self._default_chat_id, self._default_timeout,
        )
        for alias, job in self._jobs.items():
            status = "enabled" if job.enabled else "DISABLED"
            next_str = job.next_run.strftime("%Y-%m-%d %H:%M:%S") if job.next_run else "N/A"
            logger.info("  [%s] %s → '%s' | schedule=%s | next=%s",
                        status, alias, job.cmd[:60], job.schedule_str, next_str)

    def _check_reload(self) -> None:
        """Check if cron.json has been modified and reload if so."""
        now = time.time()
        if now - self._last_reload_check < self._RELOAD_INTERVAL:
            return
        self._last_reload_check = now

        if not self._cron_json_path.exists():
            if self._jobs:
                logger.info("cron.json deleted — clearing all jobs.")
                self._jobs = {}
            return

        try:
            current_mtime = self._cron_json_path.stat().st_mtime
        except OSError:
            return

        if current_mtime != self._last_mtime:
            logger.info("cron.json modified — hot-reloading jobs …")
            self.load_jobs()

    # ── One-shot cleanup ─────────────────────────────────────────────────

    def _disable_oneshot_job(self, alias: str) -> None:
        """
        Mark a one-shot job as disabled in cron.json after it fires.

        Reads, modifies, and writes back the JSON file. This is safe because
        the scheduler is single-threaded — no concurrent writes.
        """
        try:
            with open(self._cron_json_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            if alias in config.get("jobs", {}):
                config["jobs"][alias]["enabled"] = False
                with open(self._cron_json_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                logger.info("One-shot job '%s' auto-disabled in cron.json.", alias)
                # Update mtime so hot-reload doesn't re-trigger from our own write
                self._last_mtime = self._cron_json_path.stat().st_mtime
        except Exception as exc:
            logger.error("Failed to auto-disable one-shot job '%s': %s", alias, exc)

    # ── Main Loop ────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """
        Main scheduler loop — runs in a daemon thread.

        Every tick (1s), checks if any enabled job is due (next_run <= now).
        If so, executes it, computes the next run, and saves state.
        Every 60s, checks if cron.json has been modified and hot-reloads.
        """
        logger.info("Scheduler thread started.")

        while not self._stop_event.is_set():
            try:
                # Check for config changes
                self._check_reload()

                now = datetime.now()

                for alias, job in list(self._jobs.items()):
                    if not job.enabled:
                        continue
                    if job.next_run is None:
                        continue
                    if now < job.next_run:
                        continue

                    # ── Job is due — execute it ──────────────────────────
                    execute_job(job, self._workdir, self._token, self._default_chat_id)

                    # ── Compute next run ─────────────────────────────────
                    if job.schedule and job.schedule.kind == "once":
                        # One-shot: disable after firing
                        job.enabled = False
                        job.next_run = None
                        self._disable_oneshot_job(alias)
                    elif job.schedule and job.schedule.kind == "interval":
                        # Interval: next run = now + interval
                        job.next_run = datetime.now() + timedelta(
                            seconds=job.schedule.interval_seconds
                        )
                    else:
                        # Daily/weekly: compute from current time
                        job.next_run = compute_next_run(job.schedule, after=datetime.now())

                    # Persist state after execution
                    self._save_state()

            except Exception as exc:
                # Never let a bug in one iteration kill the scheduler thread
                logger.exception("Scheduler loop error (will continue): %s", exc)

            # Sleep in small increments so stop_event is checked promptly
            self._stop_event.wait(timeout=self._TICK_INTERVAL)

        logger.info("Scheduler thread stopped.")

    # ── Public API ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Load jobs and start the scheduler daemon thread."""
        self.load_jobs()

        if self._thread is not None and self._thread.is_alive():
            logger.warning("Scheduler already running — skipping start.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="cron-scheduler",
            daemon=True,  # dies with the gateway process
        )
        self._thread.start()
        logger.info("Scheduler started (thread=%s).", self._thread.name)

    def stop(self) -> None:
        """Signal the scheduler thread to stop and wait for it to exit."""
        if self._thread is None or not self._thread.is_alive():
            return

        logger.info("Stopping scheduler …")
        self._stop_event.set()
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            logger.warning("Scheduler thread did not exit within 10s.")
        else:
            logger.info("Scheduler stopped cleanly.")

        # Persist final state
        self._save_state()

    def get_jobs(self) -> dict[str, CronJob]:
        """Return a snapshot of all jobs (for /cron command in bot.py)."""
        return dict(self._jobs)

    def get_default_chat_id(self) -> Optional[int]:
        """Return the resolved default chat_id."""
        return self._default_chat_id
