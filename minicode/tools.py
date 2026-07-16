"""基础工具处理器:文件读写、bash、glob、todo,以及通用分发器。

文件类工具通过 ``safe_path`` 被限制在工作区(或 teammate 的 worktree)内;
bash 有意保持强大,改由 permission hook 控制。``CURRENT_TODOS`` 是 todo_write
工具维护的会话级状态 —— 会被重新赋值,外部需通过 ``tools.CURRENT_TODOS`` 访问。
"""

import ast
import json
import subprocess
from pathlib import Path

from . import config

# 当前会话的 todo 列表,由 run_todo_write 重新赋值。
CURRENT_TODOS: list[dict] = []


def safe_path(p: str, cwd: Path = None) -> Path:
    """把相对路径解析到工作区内,越界则抛 ValueError。"""
    # 文件工具留在工作区 / teammate worktree 内;bash 的自由度由 permission
    # hook 而不是这里限制。
    base = cwd or config.WORKDIR
    path = (base / p).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str, cwd: Path = None,
             run_in_background: bool = False) -> str:
    """执行 shell 命令并返回截断后的合并输出(120s 超时)。"""
    # run_in_background 由分发器消费;直接执行时忽略它。
    try:
        r = subprocess.run(command, shell=True, cwd=cwd or config.WORKDIR,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int | None = None,
             offset: int = 0, cwd: Path = None) -> str:
    """读取文件文本,支持 offset/limit 分页;出错返回错误字符串。"""
    try:
        lines = safe_path(path, cwd).read_text().splitlines()
        offset = max(int(offset or 0), 0)
        limit = int(limit) if limit is not None else None
        lines = lines[offset:]
        if limit is not None and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str, cwd: Path = None) -> str:
    """写入文件(必要时创建父目录);返回写入字节数或错误。"""
    try:
        fp = safe_path(path, cwd)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str,
             cwd: Path = None) -> str:
    """把文件中首个 old_text 替换为 new_text;找不到时返回错误。"""
    try:
        fp = safe_path(path, cwd)
        text = fp.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        fp.write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str, cwd: Path = None) -> str:
    """在工作区内按 glob 模式匹配文件,返回换行分隔的路径列表。"""
    import glob as g
    try:
        base = cwd or config.WORKDIR
        results = []
        for match in g.glob(pattern, root_dir=base):
            if (base / match).resolve().is_relative_to(base):
                results.append(match)
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e}"


def call_tool_handler(handler, args: dict, name: str) -> str:
    """用给定参数调用工具处理器;缺失或签名不匹配时返回错误字符串。"""
    if not handler:
        return f"Unknown: {name}"
    try:
        return handler(**(args or {}))
    except TypeError as e:
        return f"Error: {e}"


def _normalize_todos(todos):
    """把 todos 规整为校验过的 list;返回 (todos, None) 或 (None, 错误)。

    接受 list、JSON 数组字符串,或 Python 字面量列表字符串;字符串走
    json/ast.literal_eval 解析,绝不 eval,避免任意代码执行。
    """
    if isinstance(todos, str):
        try:
            todos = json.loads(todos)
        except json.JSONDecodeError:
            try:
                todos = ast.literal_eval(todos)
            except (SyntaxError, ValueError):
                return None, "Error: todos must be a list or JSON array string"
    if not isinstance(todos, list):
        return None, "Error: todos must be a list"
    for i, todo in enumerate(todos):
        if not isinstance(todo, dict):
            return None, f"Error: todos[{i}] must be an object"
        if "content" not in todo or "status" not in todo:
            return None, f"Error: todos[{i}] missing 'content' or 'status'"
        if todo["status"] not in ("pending", "in_progress", "completed"):
            return None, f"Error: todos[{i}] has invalid status '{todo['status']}'"
    return todos, None


def run_todo_write(todos: list) -> str:
    """校验并保存会话 todo 列表(副作用:重设 CURRENT_TODOS)。"""
    global CURRENT_TODOS
    todos, error = _normalize_todos(todos)
    if error:
        return error
    CURRENT_TODOS = todos
    print(f"  \033[33m[todo] updated {len(CURRENT_TODOS)} item(s)\033[0m")
    return f"Updated {len(CURRENT_TODOS)} todos"
