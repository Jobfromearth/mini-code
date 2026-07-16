"""Hook 管线:权限、日志、大输出、停止等横切行为。

Hook 有意放在工具处理器之外,这样主循环可以在不改动每个工具的前提下加上
权限、日志、停止行为。副作用:import 时注册默认 hook。

``HOOKS`` 是原地修改的 dict(注册即 append),读取安全。
"""

from . import config
from .tools import safe_path

# 事件名 → 回调列表。
HOOKS = {"UserPromptSubmit": [], "PreToolUse": [],
         "PostToolUse": [], "Stop": []}


def register_hook(event: str, callback):
    """把回调追加到某事件的 hook 列表。"""
    HOOKS[event].append(callback)


def trigger_hooks(event: str, *args):
    """依次调用某事件的 hook;任一返回非 None 即短路返回该值。"""
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result
    return None


DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if="]
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]


def permission_hook(block):
    """PreToolUse 权限门:拦截 deny 命令、越界写入、危险 MCP 工具。

    返回错误字符串表示拒绝执行;返回 None 表示放行。可能通过 input() 询问用户。
    """
    if block.name == "bash":
        command = block.input.get("command", "")
        for pattern in DENY_LIST:
            if pattern in command:
                return f"Permission denied: '{pattern}' is on the deny list"
        if any(token in command for token in DESTRUCTIVE):
            print(f"\n\033[33m[permission] destructive command\033[0m")
            print(f"  {command}")
            choice = input("  Allow? [y/N] ").strip().lower()
            if choice not in ("y", "yes"):
                return "Permission denied by user"
    if block.name in ("write_file", "edit_file"):
        path = block.input.get("path", "")
        try:
            safe_path(path)
        except Exception:
            return f"Permission denied: path escapes workspace: {path}"
    if block.name.startswith("mcp__") and "deploy" in block.name:
        print(f"\n\033[33m[permission] MCP destructive-looking tool: {block.name}\033[0m")
        choice = input("  Allow? [y/N] ").strip().lower()
        if choice not in ("y", "yes"):
            return "Permission denied by user"
    return None


def log_hook(block):
    """PreToolUse 日志 hook:打印将要执行的工具名。"""
    print(f"\033[90m[HOOK] {block.name}\033[0m")
    return None


def large_output_hook(block, output):
    """PostToolUse hook:输出过大时打印告警。"""
    if len(str(output)) > 100000:
        print(f"\033[33m[HOOK] large output from {block.name}: "
              f"{len(str(output))} chars\033[0m")
    return None


def user_prompt_hook(query: str):
    """UserPromptSubmit hook:打印当前工作目录。"""
    print(f"\033[90m[HOOK] UserPromptSubmit: {config.WORKDIR}\033[0m")
    return None


def stop_hook(messages: list):
    """Stop hook:统计并打印本轮产生的 tool_result 数量。"""
    tool_count = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            tool_count += sum(1 for item in content
                              if isinstance(item, dict)
                              and item.get("type") == "tool_result")
    print(f"\033[90m[HOOK] Stop: {tool_count} tool result(s)\033[0m")
    return None


register_hook("UserPromptSubmit", user_prompt_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", stop_hook)
