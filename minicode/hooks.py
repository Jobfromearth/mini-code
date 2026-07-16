"""Hook pipeline: permission, logging, large-output, and stop behaviors.

Hooks are intentionally outside tool handlers. The loop can add permission,
logging, and stop behavior without changing each individual tool. Side
effect: registers the default hooks on import.

``HOOKS`` is a dict mutated in place (registration appends), safe to read.
"""

from . import config
from .tools import safe_path
from .tracing import clip, trace, usage_summary

# Event name → list of callbacks.
HOOKS = {"UserPromptSubmit": [], "PreToolUse": [],
         "PostToolUse": [], "Stop": []}


def register_hook(event: str, callback):
    """Append a callback to an event's hook list."""
    HOOKS[event].append(callback)


def trigger_hooks(event: str, *args):
    """Call an event's hooks in order; the first non-None result short-circuits."""
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result
    return None


DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if="]
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]


def permission_hook(block):
    """PreToolUse permission gate: deny-listed commands, path escapes, risky MCP tools.

    Returning an error string blocks execution; returning None allows it.
    May prompt the user via input().
    """
    # The permission layer sees the raw tool_use before dispatch. It can deny,
    # ask the user, or allow execution to continue.
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


def traced_permission_hook(block):
    """permission_hook plus a trace record for every denial (audit trail)."""
    result = permission_hook(block)
    if result is not None:
        trace("permission_denied", tool=block.name, reason=clip(result))
    return result


def log_hook(block):
    """PreToolUse logging hook: print the tool about to run."""
    print(f"\033[90m[HOOK] {block.name}\033[0m")
    return None


def large_output_hook(block, output):
    """PostToolUse hook: warn when a tool produced very large output."""
    if len(str(output)) > 100000:
        print(f"\033[33m[HOOK] large output from {block.name}: "
              f"{len(str(output))} chars\033[0m")
    return None


def user_prompt_hook(query: str):
    """UserPromptSubmit hook: trace the prompt and print the working directory."""
    trace("user_prompt", prompt=clip(query))
    print(f"\033[90m[HOOK] UserPromptSubmit: {config.WORKDIR}\033[0m")
    return None


def stop_hook(messages: list):
    """Stop hook: count tool_results this turn, trace it, print session usage."""
    tool_count = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            tool_count += sum(1 for item in content
                              if isinstance(item, dict)
                              and item.get("type") == "tool_result")
    trace("turn_end", tool_results=tool_count)
    print(f"\033[90m[HOOK] Stop: {tool_count} tool result(s) | "
          f"{usage_summary()}\033[0m")
    return None


register_hook("UserPromptSubmit", user_prompt_hook)
register_hook("PreToolUse", traced_permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", stop_hook)
