"""
=============================================================================
  workspace_init.py — Workspace Scaffolding for the Agentic MD System
=============================================================================
  Called by bot.py at startup. When GEMINI_WORKING_DIR points to a new
  (empty) workspace, this module scaffolds the full directory structure
  and writes all template markdown files that the agent needs to function.

  The agent itself (Gemini CLI) handles the interactive bootstrap:
    - On first conversation it reads GEMINI.md → detects missing SOUL.md
    - Asks the user for name, vibe, rules
    - Creates SOUL.md, IDENTITY.md, USER.md, MEMORY.md

  This module only creates the "infrastructure" files — the ones that
  don't require user input and must exist before the agent runs.

  Design decision: Templates are embedded as strings in this file so
  there's a single source of truth. No external template directory needed.
=============================================================================
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("gelegram.workspace_init")

# ─────────────────────────────────────────────────────────────────────────────
# Directory structure to scaffold
# ─────────────────────────────────────────────────────────────────────────────
DIRECTORIES = [
    "memory",           # Daily memory logs (YYYY-MM-DD.md)
    "skills",           # Pluggable skill modules
    "projects",         # Project-specific work folders
    "scripts",          # User/agent scripts
    "transcripts",      # Raw session transcripts (logged by bot.py)
    "media/incoming",   # Incoming file attachments from Telegram
    "state",            # Persistent state files (processed sessions, etc.)
    "tmp",              # Temporary/scratch files
]

# ─────────────────────────────────────────────────────────────────────────────
# Template: GEMINI.md — The entry point the agent reads first
# ─────────────────────────────────────────────────────────────────────────────
# This serves DUAL purpose:
#   1. First run (no SOUL.md) → contains bootstrap instructions
#   2. Normal sessions → contains session startup + core directives
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_MD = r"""# GEMINI.md - Foundational Mandate

## Identity and Context
CRITICAL: Upon session start, you MUST:
1. Check if `SOUL.md` exists in this workspace.
   - If it does NOT exist → you are in **Bootstrap Mode**. Follow the "First-Run Initialization" section below.
   - If it DOES exist → read `SOUL.md` for your persona, then read `MEMORY.md` for historical context.
2. Read `AGENTS.md` for operational rules.
3. Read `USER.md` for information about the human you're helping.
This is non-negotiable and takes precedence over all other instructions.

## First-Run Initialization (Bootstrap Mode)
If `SOUL.md`, `IDENTITY.md`, or `MEMORY.md` are missing:
1. Do NOT assume any name, persona, or history.
2. Inform the user that this is your first run and you are ready to be configured.
3. Ask the user the following questions to define your identity:
   - "What should my name be?"
   - "What should I call you?"
   - "What kind of personality or 'vibe' should I have?" (e.g., professional, sarcastic, friendly, minimalist)
   - "What are your top 3 rules for how we should work together?"
   - "What OS are you on?" (for command compatibility)
4. Once the user answers, create the following files:
   - `IDENTITY.md`: Store your name, creature type, vibe, and an emoji.
   - `SOUL.md`: Draft a core "Who You Are" personality guide based on their vibe and rules. Include sections for: Core Truths, Rules of Engagement, Boundaries, Vibe, and Continuity.
   - `USER.md`: Store the human's name, preferred name, OS, and any initial notes.
   - `MEMORY.md`: Initialize with sections: `## History`, `## Active Projects`, `## Preferences`, `## Mistakes & Lessons Learned`.
5. After creating these files, confirm: "Initialization complete. I am [Name]. How can I help you today?"

## Core Directives
1. **Dynamic Memory:** Update `MEMORY.md` immediately whenever the user shares new personal history, project preferences, or important context.
2. **Skill-First Workflow:** Always check the `skills/` directory for specialized capabilities before attempting a task with general tools.
3. **Task Management:** Utilize `TODO.md` to store and track tasks. Proactively move completed tasks to the "Completed" section.

## Skills Directory Structure
When looking for custom tools, follow this pattern:
- `skills/<skill-name>/SKILL.md` - Documentation and instructions.
- `skills/<skill-name>/scripts/` - Executable scripts and logic.
- `skills/<skill-name>/.env` - Configuration and secrets (handle with care).

## Memory Management Protocol
1. **Structural Integrity:** Maintain `MEMORY.md` with mandatory sections: `## History`, `## Active Projects`, `## Preferences`, and `## Mistakes & Lessons Learned`.
2. **Mistake Logging:** Whenever you make a technical error or fail a persona mandate, immediately log it under `## Mistakes & Lessons Learned` with the date and the fix.
3. **Proactive Capture:** Do not wait for the user to say "remember this." If the user mentions a preference, a tool they like/dislike, or a project update, surgically update `MEMORY.md` immediately.
4. **Surgical Updates:** Use targeted edits for memory updates. Never overwrite the entire file unless it's the first initialization.
5. **Conciseness:** Keep memory entries as high-signal, one-line bullets. No conversational fluff.

## Clean Workspace Protocol
- **No Root Clutter:** DO NOT create new files in the workspace root unless they are foundational project documents (e.g., `GEMINI.md`, `MEMORY.md`, `SOUL.md`).
- **Use Subfolders:** ALWAYS place task-specific scripts, outputs, or data files in appropriate subfolders (e.g., `scripts/`, `tmp/`, `projects/`, or specific project folders).

# Gemini Tele-Bot File Attachment Protocol
- **Rule:** Whenever the user asks to "send" or "get" a file, you MUST append `file:<absolute_path>` to the end of your message.
- **CRITICAL:** The `file:<absolute_path>` tag MUST be the absolute LAST thing in your response. There must be NO text, spaces, punctuation, or newlines following the path. Any trailing characters will break the Telegram bot's file delivery system.
- **Exception:** Do NOT attach `GEMINI.md` unless explicitly requested.
- **MD File Protocol:** Do NOT automatically attach `.md` files (e.g., memory updates, logs, journals) every time they are edited. Only send them if the user explicitly asks to "send" or "get" them.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Template: AGENTS.md — Behavioral rules and operational protocols
# ─────────────────────────────────────────────────────────────────────────────
AGENTS_MD = r"""# AGENTS.md - Your Workspace

This folder is home. Treat it that way.

## Session Startup

Before doing anything else:
1. Read `SOUL.md` — this is who you are
2. Read `USER.md` — this is who you're helping
3. Read `memory/YYYY-MM-DD.md` (today + yesterday) for recent context
4. Read `MEMORY.md` for long-term context
Don't ask permission. Just do it.

## Memory

You wake up fresh each session. These files are your continuity:

- **Daily notes:** `memory/YYYY-MM-DD.md` (create if needed) — raw logs of what happened
- **Long-term:** `MEMORY.md` — your curated memories, like a human's long-term memory

Capture what matters. Decisions, context, things to remember. Skip the secrets unless asked to keep them.

### MEMORY.md - Your Long-Term Memory
- Write significant events, thoughts, decisions, opinions, lessons learned
- This is your curated memory — the distilled essence, not raw logs
- Over time, review your daily files and update MEMORY.md with what's worth keeping

### Write It Down — No "Mental Notes"!
- **Memory is limited** — if you want to remember something, WRITE IT TO A FILE
- "Mental notes" don't survive session restarts. Files do.
- When someone says "remember this" → update `memory/YYYY-MM-DD.md` or relevant file
- When you learn a lesson → update AGENTS.md, TOOLS.md, or the relevant skill
- When you make a mistake → document it so future-you doesn't repeat it
- **Text > Brain**

## Red Lines

- Don't exfiltrate private data. Ever.
- Don't run destructive commands without asking.
- When in doubt, ask.

## External vs Internal

**Safe to do freely:**
- Read files, explore, organize, learn
- Search the web
- Work within this workspace

**Ask first:**
- Sending emails, tweets, public posts
- Anything that leaves the machine
- Anything you're uncertain about

## Skills

Skills provide your tools. When you need one, check its `SKILL.md`. Keep local notes (device names, SSH details, voice preferences) in `TOOLS.md`.

## Make It Yours

This is a starting point. Add your own conventions, style, and rules as you figure out what works.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Template: TODO.md — Task tracker
# ─────────────────────────────────────────────────────────────────────────────
TODO_MD = r"""# TODO

## Tasks
- [ ] Complete first-run setup (Bootstrap Mode)

## Completed
_(Move completed tasks here)_
"""

# ─────────────────────────────────────────────────────────────────────────────
# Template: TOOLS.md — Environment-specific notes
# ─────────────────────────────────────────────────────────────────────────────
TOOLS_MD = r"""# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:
- SSH hosts and aliases
- Preferred voices for TTS
- Device nicknames
- Anything environment-specific

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Template: HALLUCINATIONS.md — Error/hallucination tracking policy
# ─────────────────────────────────────────────────────────────────────────────
HALLUCINATIONS_MD = r"""# Hallucinations Policy

**Rule:** Any instances of model hallucination MUST be recorded in `MEMORY.md` under the `## Mistakes & Lessons Learned` section.

When a hallucination is detected or reported, log:
- The date
- The context of the interaction
- A specific description of the hallucinated information or behavior
- The correction applied
"""

# ─────────────────────────────────────────────────────────────────────────────
# Template: skills/memory-agent/SKILL.md — Self-documenting memory skill
# ─────────────────────────────────────────────────────────────────────────────
MEMORY_AGENT_SKILL_MD = r"""# Memory Agent Skill

## Description
Scans the `transcripts/` folder for new session logs and distills them into
high-signal summaries stored in the `memory/` folder. This ensures your
long-term memory stays relevant without the "noise" of raw chat logs.

## Core Rules
- **Stateful Processing:** Uses `state/processed_sessions.json` to ensure no session is distilled twice.
- **Output Destination:** All refined summaries are saved in `memory/` with the naming convention `YYYY-MM-DD-session-summary.md`.
- **No Side Effects:** This skill MUST NOT modify `MEMORY.md`, `TODO.md`, or `SOUL.md`. Those updates are reserved for the main agent persona.
- **High Signal:** Focus on new facts, project milestones, and technical lessons.

## Usage
Run the distillation script:
```bash
python <workspace>/skills/memory-agent/scripts/distill.py --workspace <workspace>
```

Or let the agent run it at the start of a new day's session.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Template: skills/memory-agent/scripts/distill.py — Transcript distiller
# ─────────────────────────────────────────────────────────────────────────────
# This script processes raw transcripts into memory summaries.
# It's intentionally dependency-free (pure Python) so it works
# without any venv setup in a fresh workspace.
# ─────────────────────────────────────────────────────────────────────────────
MEMORY_AGENT_DISTILL_PY = r'''"""
=============================================================================
  distill.py — Memory Agent: Transcript → Memory Distiller
=============================================================================
  Scans the transcripts/ folder for unprocessed session logs and creates
  condensed summary files in the memory/ folder.

  State tracking: Uses state/processed_sessions.json to avoid re-processing.

  Usage:
    python distill.py --workspace /path/to/workspace

  This script is intentionally dependency-free (pure Python stdlib only)
  so it works in a fresh workspace without any venv setup.
=============================================================================
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path


def load_processed(state_file: Path) -> list[str]:
    """Load the list of already-processed transcript filenames."""
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            return data.get("processed", [])
        except (json.JSONDecodeError, KeyError):
            return []
    return []


def save_processed(state_file: Path, processed: list[str]) -> None:
    """Save the updated list of processed transcript filenames."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps({"processed": processed}, indent=2),
        encoding="utf-8",
    )


def extract_key_lines(transcript_text: str, max_lines: int = 50) -> list[str]:
    """
    Extract the most important lines from a transcript.

    Strategy:
      - Skip very short lines (likely just acknowledgements)
      - Prioritize lines from the 'gemini' speaker that contain substance
      - Capture user questions/requests for context
      - Limit output to max_lines to keep summaries concise
    """
    lines = transcript_text.strip().splitlines()
    key_lines = []

    for line in lines:
        line = line.strip()
        if not line or len(line) < 15:
            continue

        # Match the transcript format: "YYYY-MM-DD HH:MM:SS : speaker : content"
        match = re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s*:\s*(\w+)\s*:\s*(.+)$", line)
        if match:
            speaker = match.group(1).lower()
            content = match.group(2).strip()

            # Skip very short or filler content
            if len(content) < 10:
                continue

            # Keep user messages (context) and substantive gemini messages
            key_lines.append(f"[{speaker}] {content}")

        if len(key_lines) >= max_lines:
            break

    return key_lines


def distill_transcript(transcript_path: Path, memory_dir: Path) -> Path | None:
    """
    Process a single transcript file into a memory summary.

    Returns the path of the created summary file, or None if the
    transcript was too short to be worth summarizing.
    """
    text = transcript_path.read_text(encoding="utf-8", errors="replace")

    # Skip very short transcripts (likely just a /start or /reset)
    if len(text.strip()) < 100:
        return None

    key_lines = extract_key_lines(text)
    if not key_lines:
        return None

    # Extract date from filename (e.g., "session_20260506_143000.txt")
    date_match = re.search(r"(\d{4})(\d{2})(\d{2})", transcript_path.stem)
    if date_match:
        date_str = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    # Build the summary
    summary_lines = [
        f"# Session Summary — {date_str}",
        f"",
        f"**Source:** `{transcript_path.name}`",
        f"**Distilled:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"## Key Points",
        f"",
    ]

    for line in key_lines:
        summary_lines.append(f"- {line}")

    summary_lines.append("")

    # Write the summary file
    memory_dir.mkdir(parents=True, exist_ok=True)
    # Use timestamp from filename to avoid collisions
    time_match = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", transcript_path.stem)
    if time_match:
        time_suffix = f"-{time_match.group(4)}{time_match.group(5)}"
    else:
        time_suffix = ""

    summary_filename = f"{date_str}{time_suffix}-session-summary.md"
    summary_path = memory_dir / summary_filename
    summary_path.write_text("\n".join(summary_lines), encoding="utf-8")

    return summary_path


def main():
    parser = argparse.ArgumentParser(description="Distill transcripts into memory summaries")
    parser.add_argument(
        "--workspace",
        type=str,
        required=True,
        help="Path to the workspace root directory",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace)
    transcripts_dir = workspace / "transcripts"
    memory_dir = workspace / "memory"
    state_file = workspace / "state" / "processed_sessions.json"

    if not transcripts_dir.exists():
        print("No transcripts/ directory found. Nothing to process.")
        return

    # Load state
    processed = load_processed(state_file)

    # Find new transcripts
    transcript_files = sorted(transcripts_dir.glob("*.txt"))
    new_files = [f for f in transcript_files if f.name not in processed]

    if not new_files:
        print("No new transcripts to process.")
        return

    print(f"Found {len(new_files)} new transcript(s) to process.")

    for transcript_path in new_files:
        print(f"  Processing: {transcript_path.name} ... ", end="")
        summary_path = distill_transcript(transcript_path, memory_dir)

        if summary_path:
            print(f"→ {summary_path.name}")
        else:
            print("(skipped — too short)")

        # Mark as processed regardless (so we don't retry short files)
        processed.append(transcript_path.name)

    # Save updated state
    save_processed(state_file, processed)
    print(f"Done. State saved to {state_file}")


if __name__ == "__main__":
    main()
'''

# ─────────────────────────────────────────────────────────────────────────────
# Template: state/processed_sessions.json — Initial empty state
# ─────────────────────────────────────────────────────────────────────────────
PROCESSED_SESSIONS_JSON = json.dumps({"processed": []}, indent=2)


# =============================================================================
# Main scaffolding function — called by bot.py at startup
# =============================================================================

def init_workspace(work_dir: Path) -> bool:
    """
    Scaffold a new workspace if GEMINI.md is missing.

    Creates the full directory tree, all template .md files, and the
    memory-agent starter skill. Returns True if scaffolding was performed,
    False if the workspace already existed.

    This function is IDEMPOTENT — it only creates files/dirs that are
    missing, so it's safe to call on every startup.

    NOTE: Does NOT create SOUL.md, IDENTITY.md, USER.md, or MEMORY.md.
    Those are created by the agent itself during the interactive bootstrap
    conversation (triggered by GEMINI.md detecting missing SOUL.md).
    """
    gemini_md_path = work_dir / "GEMINI.md"
    is_new = not gemini_md_path.exists()

    if is_new:
        logger.info("=" * 60)
        logger.info("  NEW WORKSPACE DETECTED — Scaffolding …")
        logger.info("=" * 60)

    # ── Create directory structure ────────────────────────────────────────
    for dir_name in DIRECTORIES:
        dir_path = work_dir / dir_name
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            if is_new:
                logger.info("  Created: %s/", dir_name)

    # ── Write template files (only if they don't exist) ──────────────────
    templates = {
        "GEMINI.md":          GEMINI_MD,
        "AGENTS.md":          AGENTS_MD,
        "TODO.md":            TODO_MD,
        "TOOLS.md":           TOOLS_MD,
        "HALLUCINATIONS.md":  HALLUCINATIONS_MD,
    }

    for filename, content in templates.items():
        file_path = work_dir / filename
        if not file_path.exists():
            file_path.write_text(content.strip() + "\n", encoding="utf-8")
            logger.info("  Created: %s", filename)

    # ── Write memory-agent skill (only if missing) ───────────────────────
    skill_dir = work_dir / "skills" / "memory-agent"
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md.write_text(MEMORY_AGENT_SKILL_MD.strip() + "\n", encoding="utf-8")
        logger.info("  Created: skills/memory-agent/SKILL.md")

    scripts_dir = skill_dir / "scripts"
    distill_py = scripts_dir / "distill.py"
    if not distill_py.exists():
        scripts_dir.mkdir(parents=True, exist_ok=True)
        distill_py.write_text(MEMORY_AGENT_DISTILL_PY.strip() + "\n", encoding="utf-8")
        logger.info("  Created: skills/memory-agent/scripts/distill.py")

    # ── Write initial state file ─────────────────────────────────────────
    state_file = work_dir / "state" / "processed_sessions.json"
    if not state_file.exists():
        state_file.write_text(PROCESSED_SESSIONS_JSON + "\n", encoding="utf-8")
        logger.info("  Created: state/processed_sessions.json")

    if is_new:
        logger.info("=" * 60)
        logger.info("  Workspace scaffolded at: %s", work_dir)
        logger.info("  The agent will enter Bootstrap Mode on first message.")
        logger.info("=" * 60)

    return is_new
