"""运行时配置:加载 .env、创建 API client、模型选择、路径与常量。

这是依赖图最底层(L0)的模块,任何其它模块都可以 import 它,而它自己
不 import 包内任何模块。副作用:import 时执行一次 ``load_dotenv`` 并实例化
共享的 Anthropic ``client``,后者被所有需要调用模型的模块共用。
"""

import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

# import 时加载 .env。使用自定义 base_url 时清掉可能冲突的 auth token。
load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# 工作目录在 import 时确定(= 进程当前目录),所有磁盘状态都相对于它。
WORKDIR = Path.cwd()

# 全局共享的模型客户端与模型 id。
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]
PRIMARY_MODEL = MODEL
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_ID")

# ── 目录 / 文件路径(仅定义;创建 mkdir 由各自的属主模块在 import 时完成)──
SKILLS_DIR = WORKDIR / "skills"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"
TASKS_DIR = WORKDIR / ".tasks"
WORKTREES_DIR = WORKDIR / ".worktrees"
MAILBOX_DIR = WORKDIR / ".mailboxes"
DURABLE_PATH = WORKDIR / ".scheduled_tasks.json"
MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

# ── 调优常量 ──
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

# CLI 提示符。
PROMPT = "\033[36mminicode >> \033[0m"
