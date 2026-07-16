"""Runtime configuration: .env loading, API client, model choice, paths, constants.

This is the bottom layer (L0) of the dependency graph: every other module may
import it, and it imports nothing from the package. Side effects: on import it
runs ``load_dotenv`` once and instantiates the shared Anthropic ``client``
used by every module that calls the model.
"""

import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

# Load .env at import time. When a custom base_url is used, drop a possibly
# conflicting auth token.
load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# The working directory is fixed at import time (= process cwd); all on-disk
# state is relative to it.
WORKDIR = Path.cwd()

# Shared model client and model ids.
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]
PRIMARY_MODEL = MODEL
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_ID")

# ── Directory / file paths (definitions only; each owning module mkdirs on import) ──
SKILLS_DIR = WORKDIR / "skills"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"
TASKS_DIR = WORKDIR / ".tasks"
WORKTREES_DIR = WORKDIR / ".worktrees"
MAILBOX_DIR = WORKDIR / ".mailboxes"
DURABLE_PATH = WORKDIR / ".scheduled_tasks.json"
MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

# ── Tuning constants ──
DEFAULT_MAX_TOKENS = 8000
ESCALATED_MAX_TOKENS = 16000
MAX_RETRIES = 3
MAX_CONSECUTIVE_529 = 2
MAX_RECOVERY_RETRIES = 2
BASE_DELAY_MS = 500
CONTEXT_LIMIT = 50000
KEEP_RECENT_TOOL_RESULTS = 3
PERSIST_THRESHOLD = 30000
CONTINUATION_PROMPT = "Continue from the previous response. Do not repeat completed work."

# CLI prompt string.
PROMPT = "\033[36mminicode >> \033[0m"
