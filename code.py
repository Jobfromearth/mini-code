#!/usr/bin/env python3
"""向后兼容薄壳:真实实现已拆分到 ``minicode/`` 包。

历史上整个 agent 都在这个单文件里。现在它被解耦成一个按关注点分层的包
(见 ``minicode/``)。保留本文件是为了让老的 ``python code.py`` 仍能启动 CLI,
并让 ``import code`` 仍能拿到常用的公开符号。

新代码请直接用 ``python -m minicode`` 与 ``from minicode import ...``。
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
