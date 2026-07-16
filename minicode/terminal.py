"""Terminal output helpers.

Keeps prints from background threads from clobbering the line the user is
typing. ``CLI_ACTIVE`` is a module-level flag reassigned by ``__main__`` —
other modules must access it as ``terminal.CLI_ACTIVE`` (never
``from terminal import CLI_ACTIVE``, which would capture a stale binding).
"""

import threading

from . import config

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    READLINE_AVAILABLE = True
except ImportError:
    READLINE_AVAILABLE = False

# Whether an interactive CLI session is running; set to True by __main__.
CLI_ACTIVE = False


def terminal_print(text: str):
    """Print a line in a thread-safe way.

    The main thread (or non-CLI contexts) prints directly; background threads
    in a live CLI first erase and redraw the user's current input line so
    output and input don't interleave.
    """
    if threading.current_thread() is threading.main_thread() or not CLI_ACTIVE:
        print(text)
        return
    line = ""
    if READLINE_AVAILABLE:
        try:
            line = readline.get_line_buffer()
        except Exception:
            line = ""
    print(f"\r\033[K{text}")
    print(config.PROMPT + line, end="", flush=True)
