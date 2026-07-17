#!/usr/bin/env python3
"""Backward-compatibility shim: the real implementation lives in ``minicode/``.

Historically the whole agent lived in a single file named ``code.py``. It has
since been decoupled into a package layered by concern (see ``minicode/``).
This file remains so ``python legacy_entry.py`` still launches the CLI and
``import legacy_entry`` still exposes the commonly used public symbols. It was
renamed from ``code.py`` because that name shadowed the stdlib ``code``
module whenever the repo root was on ``sys.path`` -- anything that did
``import code`` (pytest's own ``--pdb`` support among them) would resolve to
this file instead, cascading into an early, out-of-order import of
``minicode.config`` that crashed with ``KeyError: 'MODEL_ID'`` in any clean
checkout without a local ``.env``.

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
