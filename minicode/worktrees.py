"""Worktree 系统:隔离的 git worktree 管理 + git 命令封装 + 事件日志。

Worktree 名会变成文件系统路径,所以校验规则保持严格,并在 create/remove/keep
之间复用同一套校验。副作用:import 时创建 ``.worktrees`` 目录。
"""

import json
import re
import subprocess
import time
from pathlib import Path

from . import config
from .tasks import load_task, save_task

config.WORKTREES_DIR.mkdir(exist_ok=True)

VALID_WT_NAME = re.compile(r'^[A-Za-z0-9._-]{1,64}$')


def validate_worktree_name(name: str) -> str | None:
    """校验 worktree 名;合法返回 None,否则返回错误信息。"""
    if not name:
        return "Worktree name cannot be empty"
    if name in (".", ".."):
        return f"'{name}' is not a valid worktree name"
    if not VALID_WT_NAME.match(name):
        return (f"Invalid worktree name '{name}': "
                "only letters, digits, dots, underscores, dashes (1-64 chars)")
    return None


def run_git(args: list[str]) -> tuple[bool, str]:
    """在 WORKDIR 下运行一条 git 命令,返回 (是否成功, 截断后的输出)。"""
    try:
        r = subprocess.run(["git"] + args, cwd=config.WORKDIR,
                           capture_output=True, text=True, timeout=30)
        out = (r.stdout + r.stderr).strip()
        return r.returncode == 0, out[:5000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return False, "Error: git timeout"


def log_event(event_type: str, worktree_name: str, task_id: str = ""):
    """把一条 worktree 事件追加到 events.jsonl(副作用:写磁盘)。"""
    event = {"type": event_type, "worktree": worktree_name,
             "task_id": task_id, "ts": time.time()}
    events_file = config.WORKTREES_DIR / "events.jsonl"
    with open(events_file, "a") as f:
        f.write(json.dumps(event) + "\n")


def create_worktree(name: str, task_id: str = "") -> str:
    """新建一个 git worktree(可选绑定任务);返回结果说明字符串。"""
    # 工具层校验是安全边界的一部分:要在 git 看到名字之前做,而不是只依赖
    # git 恰好拒绝了某些输入。
    err = validate_worktree_name(name)
    if err:
        return f"Error: {err}"
    if task_id:
        try:
            load_task(task_id)
        except FileNotFoundError:
            return f"Error: task {task_id} not found"
    path = config.WORKTREES_DIR / name
    if path.exists():
        return f"Worktree '{name}' already exists at {path}"
    ok, result = run_git(["worktree", "add", str(path), "-b", f"wt/{name}", "HEAD"])
    if not ok:
        return f"Git error: {result}"
    if task_id:
        bind_task_to_worktree(task_id, name)
    log_event("create", name, task_id)
    print(f"  \033[33m[worktree] created: {name} at {path}\033[0m")
    return f"Worktree '{name}' created at {path}"


def bind_task_to_worktree(task_id: str, worktree_name: str):
    """把某任务的 worktree 字段指向给定 worktree(副作用:写任务文件)。"""
    task = load_task(task_id)
    task.worktree = worktree_name
    save_task(task)


def _count_worktree_changes(path: Path) -> tuple[int, int]:
    """返回 (未提交文件数, 未推送提交数);无法确定时返回 (-1, -1)。"""
    try:
        r1 = subprocess.run(["git", "status", "--porcelain"],
                            cwd=path, capture_output=True, text=True, timeout=10)
        files = len([l for l in r1.stdout.strip().splitlines() if l.strip()])
        r2 = subprocess.run(["git", "log", "@{push}..HEAD", "--oneline"],
                            cwd=path, capture_output=True, text=True, timeout=10)
        commits = len([l for l in r2.stdout.strip().splitlines() if l.strip()])
        return files, commits
    except Exception:
        return -1, -1


def remove_worktree(name: str, discard_changes: bool = False) -> str:
    """删除一个 worktree;若存在未保存改动且未强制则拒绝。"""
    err = validate_worktree_name(name)
    if err:
        return err
    path = config.WORKTREES_DIR / name
    if not path.exists():
        return f"Worktree '{name}' not found"
    if not discard_changes:
        files, commits = _count_worktree_changes(path)
        if files < 0:
            return "Cannot verify status. Use discard_changes=true to force."
        if files > 0 or commits > 0:
            return (f"Worktree '{name}' has {files} file(s), {commits} commit(s). "
                    "Use discard_changes=true or keep_worktree.")
    ok1, _ = run_git(["worktree", "remove", str(path), "--force"])
    if not ok1:
        return f"Failed to remove worktree '{name}'"
    run_git(["branch", "-D", f"wt/{name}"])
    log_event("remove", name)
    print(f"  \033[33m[worktree] removed: {name}\033[0m")
    return f"Worktree '{name}' removed"


def keep_worktree(name: str) -> str:
    """保留 worktree 供人工审查(只记事件,不删除)。"""
    err = validate_worktree_name(name)
    if err:
        return err
    log_event("keep", name)
    return f"Worktree '{name}' kept for review (branch: wt/{name})"
