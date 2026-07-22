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
# MEMORY_DIR is env-overridable so an eval harness can point a whole task
# sequence at one shared memory store (to measure cross-session reuse).
MEMORY_DIR = Path(os.getenv("MINICODE_MEMORY_DIR", WORKDIR / ".memory"))
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
TRACE_FILE = WORKDIR / ".traces" / "trace.jsonl"

# ── Ablation flags (for the eval harness; default = every feature ON) ──
# Each flag turns off one of the harness subsystems so its contribution can be
# measured in isolation. Read once at import: the eval harness sets them in the
# child process's environment before importing the package. See evals/README.md.
ABLATE_MEMORY = os.getenv("MINICODE_ABLATE_MEMORY") == "1"
ABLATE_MULTIAGENT = os.getenv("MINICODE_ABLATE_MULTIAGENT") == "1"
ABLATE_SKILLS = os.getenv("MINICODE_ABLATE_SKILLS") == "1"

# The tool names that make up the multi-agent layer (subagent + teammate
# protocol). Dropped from the tool pool when ABLATE_MULTIAGENT is set.
MULTIAGENT_TOOLS = frozenset({
    "task", "spawn_teammate", "send_message", "check_inbox",
    "request_shutdown", "request_plan", "review_plan",
})

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
