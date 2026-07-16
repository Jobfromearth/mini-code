"""Cron 调度器:定时把 prompt 注入 agent 循环。

Cron 任务与对话历史分开存储。任务触发时变成一个 scheduled prompt,被回注到
同一个 agent 循环。副作用:import 时加载持久化任务并启动后台调度线程。

``scheduled_jobs`` / ``cron_queue`` / ``_last_fired`` 由调度线程与主线程共享,
统一用 ``cron_lock`` 保护。
"""

import json
import random
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime

from . import config


@dataclass
class CronJob:
    """一条定时任务:5 段 cron 表达式 + 触发时注入的 prompt。"""
    id: str
    cron: str
    prompt: str
    recurring: bool
    durable: bool


scheduled_jobs: dict[str, CronJob] = {}
cron_queue: list[CronJob] = []
cron_lock = threading.Lock()
_last_fired: dict[str, str] = {}


def _cron_field_matches(field: str, value: int) -> bool:
    """判断单个 cron 字段(*, */n, a-b, 列表, 具体值)是否匹配 value。"""
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return step > 0 and value % step == 0
    if "," in field:
        return any(_cron_field_matches(part.strip(), value)
                   for part in field.split(","))
    if "-" in field:
        lo, hi = field.split("-", 1)
        return int(lo) <= value <= int(hi)
    return value == int(field)


def cron_matches(cron_expr: str, dt: datetime) -> bool:
    """判断某个 5 段 cron 表达式是否匹配给定时间(含 dom/dow 或语义)。"""
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    dow_val = (dt.weekday() + 1) % 7
    m = _cron_field_matches(minute, dt.minute)
    h = _cron_field_matches(hour, dt.hour)
    dom_ok = _cron_field_matches(dom, dt.day)
    month_ok = _cron_field_matches(month, dt.month)
    dow_ok = _cron_field_matches(dow, dow_val)
    if not (m and h and month_ok):
        return False
    if dom == "*" and dow == "*":
        return True
    if dom == "*":
        return dow_ok
    if dow == "*":
        return dom_ok
    return dom_ok or dow_ok


def _validate_cron_field(field: str, lo: int, hi: int) -> str | None:
    """校验单个 cron 字段的语法与边界;合法返回 None。"""
    if field == "*":
        return None
    if field.startswith("*/"):
        step = field[2:]
        if not step.isdigit() or int(step) <= 0:
            return f"Invalid step: {field}"
        return None
    if "," in field:
        for part in field.split(","):
            err = _validate_cron_field(part.strip(), lo, hi)
            if err:
                return err
        return None
    if "-" in field:
        left, right = field.split("-", 1)
        if not left.isdigit() or not right.isdigit():
            return f"Invalid range: {field}"
        a, b = int(left), int(right)
        if a < lo or a > hi or b < lo or b > hi:
            return f"Range {field} out of bounds [{lo}-{hi}]"
        if a > b:
            return f"Range start > end: {field}"
        return None
    if not field.isdigit():
        return f"Invalid field: {field}"
    value = int(field)
    if value < lo or value > hi:
        return f"Value {value} out of bounds [{lo}-{hi}]"
    return None


def validate_cron(cron_expr: str) -> str | None:
    """校验整条 5 段 cron 表达式;合法返回 None,否则返回错误信息。"""
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return f"Expected 5 fields, got {len(fields)}"
    bounds = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    names = ["minute", "hour", "day-of-month", "month", "day-of-week"]
    for field, (lo, hi), name in zip(fields, bounds, names):
        err = _validate_cron_field(field, lo, hi)
        if err:
            return f"{name}: {err}"
    return None


def save_durable_jobs():
    """把标记为 durable 的任务持久化到磁盘(副作用:写文件)。"""
    durable = [asdict(job) for job in scheduled_jobs.values() if job.durable]
    config.DURABLE_PATH.write_text(json.dumps(durable, indent=2))


def load_durable_jobs():
    """从磁盘加载持久化任务(跳过 cron 非法者)。"""
    if not config.DURABLE_PATH.exists():
        return
    try:
        for item in json.loads(config.DURABLE_PATH.read_text()):
            job = CronJob(**item)
            if not validate_cron(job.cron):
                scheduled_jobs[job.id] = job
    except Exception:
        pass


def schedule_job(cron: str, prompt: str,
                 recurring: bool = True, durable: bool = True) -> "CronJob | str":
    """注册一条 cron 任务;cron 非法时返回错误字符串,否则返回 CronJob。"""
    err = validate_cron(cron)
    if err:
        return err
    job = CronJob(
        id=f"cron_{random.randint(0, 999999):06d}",
        cron=cron, prompt=prompt,
        recurring=recurring, durable=durable)
    with cron_lock:
        scheduled_jobs[job.id] = job
    if durable:
        save_durable_jobs()
    return job


def cancel_job(job_id: str) -> str:
    """取消一条 cron 任务;返回结果说明字符串。"""
    with cron_lock:
        job = scheduled_jobs.pop(job_id, None)
    if not job:
        return f"Job {job_id} not found"
    if job.durable:
        save_durable_jobs()
    return f"Cancelled {job_id}"


def cron_scheduler_loop():
    """后台线程:每秒检查一次,把到点的任务放入 cron_queue。"""
    while True:
        time.sleep(1)
        now = datetime.now()
        marker = now.strftime("%Y-%m-%d %H:%M")
        with cron_lock:
            for job in list(scheduled_jobs.values()):
                try:
                    if cron_matches(job.cron, now) and _last_fired.get(job.id) != marker:
                        cron_queue.append(job)
                        _last_fired[job.id] = marker
                        if not job.recurring:
                            scheduled_jobs.pop(job.id, None)
                            if job.durable:
                                save_durable_jobs()
                except Exception as e:
                    print(f"  \033[31m[cron error] {job.id}: {e}\033[0m")


def consume_cron_queue() -> list[CronJob]:
    """取出并清空 cron_queue,返回已触发的任务列表。"""
    with cron_lock:
        fired = list(cron_queue)
        cron_queue.clear()
    return fired


def run_schedule_cron(cron: str, prompt: str,
                      recurring: bool = True, durable: bool = True) -> str:
    """schedule_cron 工具包装:注册任务并返回人类可读结果。"""
    result = schedule_job(cron, prompt, recurring, durable)
    if isinstance(result, str):
        return f"Error: {result}"
    return f"Scheduled {result.id}: '{cron}' -> {prompt}"


def run_list_crons() -> str:
    """list_crons 工具包装:列出所有已注册的 cron 任务。"""
    with cron_lock:
        jobs = list(scheduled_jobs.values())
    if not jobs:
        return "No cron jobs."
    return "\n".join(
        f"  {job.id}: '{job.cron}' -> {job.prompt[:40]} "
        f"[{'recurring' if job.recurring else 'one-shot'}, "
        f"{'durable' if job.durable else 'session'}]"
        for job in jobs)


def run_cancel_cron(job_id: str) -> str:
    """cancel_cron 工具包装。"""
    return cancel_job(job_id)


load_durable_jobs()
threading.Thread(target=cron_scheduler_loop, daemon=True).start()
