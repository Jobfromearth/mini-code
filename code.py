#!/usr/bin/env python3
"""Backward-compatibility shim: the real implementation lives in ``minicode/``.

Historically the whole agent lived in this single file. It has been decoupled
into a package layered by concern (see ``minicode/``). This file remains so
the old ``python code.py`` still launches the CLI and ``import code`` still
exposes the commonly used public symbols.

For new code, use ``python -m minicode`` and ``from minicode import ...``.
"""

from minicode.__main__ import main
from minicode.loop import agent_loop, update_context
from minicode.compaction import (compact_history, reactive_compact,
                                  snip_compact, summarize_history,
                                  write_transcript)
from minicode.content import extract_text, has_tool_use
from minicode.tools import CURRENT_TODOS, run_todo_write

if __name__ == "__main__":
    main()
