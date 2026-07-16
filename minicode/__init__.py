"""minicode —— 教学用的迷你版编码 Agent。

这个包把原本挤在单个 code.py 里的十几个子系统(任务、worktree、skills、
工具、消息总线、协议、hooks、上下文压缩、错误恢复、cron、MCP、agent 主循环)
按关注点拆成独立模块,并按依赖方向分层组织,便于阅读、测试与复用。

入口:``python -m minicode``(见 :mod:`minicode.__main__`)。
"""

__version__ = "1.0.0"
