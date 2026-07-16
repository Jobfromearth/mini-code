"""终端输出工具。

负责在有后台线程并发写终端时,让打印不会破坏用户正在输入的那一行。
``CLI_ACTIVE`` 是一个会被 ``__main__`` 重新赋值的模块级标志 —— 其它模块必须
通过 ``terminal.CLI_ACTIVE`` 访问它(不要 ``from terminal import CLI_ACTIVE``,
否则拿到的是旧绑定)。
"""

import threading

from . import config

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    READLINE_AVAILABLE = True
except ImportError:
    READLINE_AVAILABLE = False

# 是否处于交互式 CLI 会话;由 __main__ 在启动时置为 True。
CLI_ACTIVE = False


def terminal_print(text: str):
    """线程安全地打印一行文本。

    主线程(或非 CLI 场景)直接 print;后台线程在 CLI 运行时会先擦除并
    重绘用户当前输入行,避免输出与输入交错。
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
