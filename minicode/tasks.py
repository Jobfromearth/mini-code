"""任务系统:文件持久化的任务记录 + 依赖图。

任务是极小的持久化记录,后续的 ownership、依赖、worktree、teammate 都建立
在这份文件后端状态之上。副作用:import 时创建 ``.tasks`` 目录。
"""

import json
import random
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from . import config

config.TASKS_DIR.mkdir(exist_ok=True)


@dataclass
class Task:
    """一条任务记录。``blockedBy`` 列出必须先完成的前置任务 id。"""
    id: str
    subject: str
    description: str
    status: str
    owner: str | None
    blockedBy: list[str]
    worktree: str | None = None


def _task_path(task_id: str) -> Path:
    """返回某个任务在磁盘上的 JSON 路径。"""
    return config.TASKS_DIR / f"{task_id}.json"


def create_task(subject: str, description: str = "",
                blockedBy: list[str] | None = None) -> Task:
    """创建并落盘一条 pending 任务,返回该 Task。"""
    task = Task(
        id=f"task_{int(time.time())}_{random.randint(0, 9999):04d}",
        subject=subject, description=description,
        status="pending", owner=None,
        blockedBy=blockedBy or [],
    )
    save_task(task)
    return task


def save_task(task: Task):
    """把任务写入其 JSON 文件(副作用:写磁盘)。"""
    _task_path(task.id).write_text(json.dumps(asdict(task), indent=2))


def load_task(task_id: str) -> Task:
    """从磁盘读取任务;不存在时抛 FileNotFoundError。"""
    return Task(**json.loads(_task_path(task_id).read_text()))


def list_tasks() -> list[Task]:
    """按 id 排序返回全部任务。"""
    return [Task(**json.loads(p.read_text()))
            for p in sorted(config.TASKS_DIR.glob("task_*.json"))]


def get_task_json(task_id: str) -> str:
    """返回某任务的格式化 JSON 字符串。"""
    return json.dumps(asdict(load_task(task_id)), indent=2)


def can_start(task_id: str) -> bool:
    """判断任务是否可开始:所有前置任务都必须存在且已 completed。"""
    task = load_task(task_id)
    for dep_id in task.blockedBy:
        if not _task_path(dep_id).exists():
            return False
        if load_task(dep_id).status != "completed":
            return False
    return True


def claim_task(task_id: str, owner: str = "agent") -> str:
    """认领一条 pending 任务并置为 in_progress;返回结果说明字符串。"""
    task = load_task(task_id)
    if task.status != "pending":
        return f"Task {task_id} is {task.status}, cannot claim"
    if task.owner:
        return f"Task {task_id} already owned by {task.owner}"
    if not can_start(task_id):
        deps = [d for d in task.blockedBy
                if _task_path(d).exists() and load_task(d).status != "completed"]
        missing = [d for d in task.blockedBy if not _task_path(d).exists()]
        parts = []
        if deps: parts.append(f"blocked by: {deps}")
        if missing: parts.append(f"missing deps: {missing}")
        return "Cannot start — " + ", ".join(parts)
    task.owner = owner
    task.status = "in_progress"
    save_task(task)
    print(f"  \033[36m[claim] {task.subject} → in_progress\033[0m")
    return f"Claimed {task.id} ({task.subject})"


def complete_task(task_id: str) -> str:
    """把 in_progress 任务标记为 completed,并报告因此被解锁的任务。"""
    task = load_task(task_id)
    if task.status != "in_progress":
        return f"Task {task_id} is {task.status}, cannot complete"
    task.status = "completed"
    save_task(task)
    unblocked = [t.subject for t in list_tasks()
                 if t.status == "pending" and t.blockedBy and can_start(t.id)]
    print(f"  \033[32m[complete] {task.subject} ✓\033[0m")
    msg = f"Completed {task.id} ({task.subject})"
    if unblocked:
        msg += f"\nUnblocked: {', '.join(unblocked)}"
    return msg
