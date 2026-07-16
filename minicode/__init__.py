"""minicode — a minimal coding agent.

This package splits what used to be a single monolithic code.py into modules
organized by concern (tasks, worktrees, skills, tools, message bus, protocol,
hooks, context compaction, error recovery, cron, MCP, and the agent main
loop), layered so that dependencies point strictly downward.

Entry point: ``python -m minicode`` (see :mod:`minicode.__main__`).
"""

__version__ = "1.0.0"
