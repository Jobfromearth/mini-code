"""自主 teammate:后台线程里运行的独立 mini agent 循环 + 协议工具。

teammate 醒来时先处理收件箱消息(协议优先),再找无主任务认领。它自带一个
受限的工具子集与自己的 while 循环,**不调用主 ``agent_loop``** —— 这正是
teams 与 loop 之间没有循环依赖的原因,也是整个包能干净分层的前提。

plan 审批是真正的门闩:submit_plan 之后,teammate 停止一切模型/工具步骤,
直到 lead 发来 plan_approval_response。
"""

import json
import re
import threading
import time
from pathlib import Path

from . import bus
from . import config
from .content import has_tool_use
from .tasks import can_start, claim_task, complete_task, list_tasks, load_task
from .tools import call_tool_handler, run_bash, run_read, run_write

IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60


def scan_unclaimed_tasks() -> list[dict]:
    """返回当前可认领(pending、无主、依赖已满足)的任务原始 dict 列表。"""
    unclaimed = []
    for f in sorted(config.TASKS_DIR.glob("task_*.json")):
        task = json.loads(f.read_text())
        if (task.get("status") == "pending"
                and not task.get("owner")
                and can_start(task["id"])):
            unclaimed.append(task)
    return unclaimed


def idle_poll(agent_name: str, messages: list,
              name: str, role: str,
              worktree_context: dict | None = None) -> str:
    """teammate 空闲轮询:优先响应收件箱,其次认领无主任务。

    返回 'shutdown' / 'work' / 'timeout' 之一。副作用:可能给 messages 追加内容、
    认领任务、发送 shutdown 回复。
    """
    for _ in range(IDLE_TIMEOUT // IDLE_POLL_INTERVAL):
        time.sleep(IDLE_POLL_INTERVAL)
        inbox = bus.BUS.read_inbox(agent_name)
        if inbox:
            for msg in inbox:
                if msg.get("type") == "shutdown_request":
                    req_id = msg.get("metadata", {}).get("request_id", "")
                    bus.BUS.send(name, "lead", "Shutting down.",
                                 "shutdown_response",
                                 {"request_id": req_id, "approve": True})
                    return "shutdown"
            messages.append({"role": "user",
                "content": "<inbox>" + json.dumps(inbox) + "</inbox>"})
            return "work"
        unclaimed = scan_unclaimed_tasks()
        if unclaimed:
            task_data = unclaimed[0]
            result = claim_task(task_data["id"], agent_name)
            if "Claimed" in result:
                wt_info = ""
                if task_data.get("worktree"):
                    wt_path = config.WORKTREES_DIR / task_data["worktree"]
                    wt_info = f"\nWork directory: {wt_path}"
                    if worktree_context is not None:
                        worktree_context["path"] = str(wt_path)
                messages.append({"role": "user",
                    "content": f"<auto-claimed>Task {task_data['id']}: "
                               f"{task_data['subject']}{wt_info}</auto-claimed>"})
                return "work"
    return "timeout"


def spawn_teammate_thread(name: str, role: str, prompt: str) -> str:
    """在后台线程启动一个自主 teammate;返回是否成功的说明字符串。

    副作用:登记 ``bus.active_teammates``、起 daemon 线程。teammate 在其中运行
    自带的 mini agent 循环,通过 BUS 与 lead 协作。
    """
    if name in bus.active_teammates:
        return f"Teammate '{name}' already exists"

    # plan 审批是真门闩:submit_plan 之后,teammate 停止模型/工具步骤,直到
    # lead 发来 plan_approval_response。
    protocol_ctx = {"waiting_plan": None}
    system = (f"You are '{name}', a {role}. "
              f"Use tools to complete tasks. "
              f"If a task has a worktree, work in that directory.")

    def handle_inbox_message(name: str, msg: dict, messages: list):
        msg_type = msg.get("type", "message")
        meta = msg.get("metadata", {})
        req_id = meta.get("request_id", "")
        if msg_type == "shutdown_request":
            bus.BUS.send(name, "lead", "Shutting down.",
                         "shutdown_response",
                         {"request_id": req_id, "approve": True})
            return True
        if msg_type == "plan_approval_response":
            approve = meta.get("approve", False)
            if req_id == protocol_ctx["waiting_plan"]:
                protocol_ctx["waiting_plan"] = None
            messages.append({"role": "user",
                "content": "[Plan approved]" if approve
                           else f"[Plan rejected] {msg['content']}"})
        return False

    def run():
        wt_ctx = {"path": None}

        def _wt_cwd():
            # 一旦认领了带 worktree 的任务,teammate 的文件工具都透明地在那个
            # 隔离目录里执行。
            p = wt_ctx["path"]
            return Path(p) if p else None

        def _run_bash(command: str) -> str:
            return run_bash(command, cwd=_wt_cwd())

        def _run_read(path: str) -> str:
            return run_read(path, cwd=_wt_cwd())

        def _run_write(path: str, content: str) -> str:
            return run_write(path, content, cwd=_wt_cwd())

        def _run_list_tasks():
            tasks = list_tasks()
            if not tasks:
                return "No tasks."
            return "\n".join(
                f"  {t.id}: {t.subject} [{t.status}]"
                + (f" (wt:{t.worktree})" if t.worktree else "")
                for t in tasks)

        def _run_claim_task(task_id: str):
            result = claim_task(task_id, owner=name)
            if "Claimed" in result:
                task = load_task(task_id)
                wt_ctx["path"] = (str(config.WORKTREES_DIR / task.worktree)
                                  if task.worktree else None)
            return result

        def _run_complete_task(task_id: str):
            result = complete_task(task_id)
            wt_ctx["path"] = None
            return result

        messages = [{"role": "user", "content": prompt}]
        sub_tools = [
            {"name": "bash", "description": "Run a shell command.",
             "input_schema": {"type": "object",
                              "properties": {"command": {"type": "string"}},
                              "required": ["command"]}},
            {"name": "read_file", "description": "Read file.",
             "input_schema": {"type": "object",
                              "properties": {"path": {"type": "string"},
                                             "limit": {"type": "integer"},
                                             "offset": {"type": "integer"}},
                              "required": ["path"]}},
            {"name": "write_file", "description": "Write file.",
             "input_schema": {"type": "object",
                              "properties": {"path": {"type": "string"},
                                             "content": {"type": "string"}},
                              "required": ["path", "content"]}},
            {"name": "send_message",
             "description": "Send message to another agent.",
             "input_schema": {"type": "object",
                              "properties": {"to": {"type": "string"},
                                             "content": {"type": "string"}},
                              "required": ["to", "content"]}},
            {"name": "submit_plan",
             "description": "Submit a plan for Lead approval.",
             "input_schema": {"type": "object",
                              "properties": {"plan": {"type": "string"}},
                              "required": ["plan"]}},
            {"name": "list_tasks",
             "description": "List all tasks.",
             "input_schema": {"type": "object", "properties": {},
                              "required": []}},
            {"name": "claim_task",
             "description": "Claim a pending task.",
             "input_schema": {"type": "object",
                              "properties": {"task_id": {"type": "string"}},
                              "required": ["task_id"]}},
            {"name": "complete_task",
             "description": "Mark an in-progress task as completed.",
             "input_schema": {"type": "object",
                              "properties": {"task_id": {"type": "string"}},
                              "required": ["task_id"]}},
        ]

        sub_handlers = {
            "bash": _run_bash, "read_file": _run_read,
            "write_file": _run_write,
            "send_message": lambda to, content: (bus.BUS.send(name, to, content),
                                                  "Sent")[1],
            "list_tasks": _run_list_tasks,
            "claim_task": _run_claim_task,
            "complete_task": _run_complete_task,
        }

        while True:
            if len(messages) <= 3:
                messages.insert(0, {"role": "user",
                    "content": f"<identity>You are '{name}', role: {role}. "
                               f"Continue your work.</identity>"})
            should_shutdown = False
            for _ in range(10):
                inbox = bus.BUS.read_inbox(name)
                for msg in inbox:
                    stopped = handle_inbox_message(name, msg, messages)
                    if stopped:
                        should_shutdown = True
                        break
                if should_shutdown:
                    break
                if protocol_ctx["waiting_plan"]:
                    # 审批门闩关闭期间只轮询协议回复,不让模型继续任务。
                    time.sleep(IDLE_POLL_INTERVAL)
                    continue
                if inbox and not should_shutdown:
                    non_protocol = [m for m in inbox
                                    if m.get("type") == "message"]
                    if non_protocol:
                        messages.append({"role": "user",
                            "content": "<inbox>" + json.dumps(non_protocol) + "</inbox>"})
                try:
                    response = config.client.messages.create(
                        model=config.MODEL, system=system, messages=messages[-20:],
                        tools=sub_tools, max_tokens=8000)
                except Exception:
                    break
                messages.append({"role": "assistant", "content": response.content})
                if not has_tool_use(response.content):
                    break
                results = []
                for block in response.content:
                    if block.type == "tool_use":
                        if block.name == "submit_plan":
                            output = _teammate_submit_plan(
                                name, block.input.get("plan", ""))
                            match = re.search(r"\((req_\d+)\)", output)
                            protocol_ctx["waiting_plan"] = (
                                match.group(1) if match else output)
                        else:
                            handler = sub_handlers.get(block.name)
                            output = call_tool_handler(handler, block.input,
                                                       block.name)
                        results.append({"type": "tool_result",
                                        "tool_use_id": block.id,
                                        "content": str(output)})
                        if protocol_ctx["waiting_plan"]:
                            # 忽略同一条模型响应里后续的 tool_use 块;它们属于
                            # 审批之后,而不是之前。
                            break
                messages.append({"role": "user", "content": results})
                if protocol_ctx["waiting_plan"]:
                    break
            if should_shutdown:
                break
            if protocol_ctx["waiting_plan"]:
                continue
            idle_result = idle_poll(name, messages, name, role, wt_ctx)
            if idle_result in ("shutdown", "timeout"):
                break

        summary = "Done."
        for msg in reversed(messages):
            if msg["role"] == "assistant" and isinstance(msg["content"], list):
                for b in msg["content"]:
                    if getattr(b, "type", None) == "text":
                        summary = b.text
                        break
                else:
                    continue
                break
        bus.BUS.send(name, "lead", summary, "result")
        bus.active_teammates.pop(name, None)

    bus.active_teammates[name] = True
    threading.Thread(target=run, daemon=True).start()
    return f"Teammate '{name}' spawned as {role}"


def _teammate_submit_plan(from_name: str, plan: str) -> str:
    """teammate 提交 plan:登记 pending 请求并向 lead 发审批请求。"""
    req_id = bus.new_request_id()
    bus.pending_requests[req_id] = bus.ProtocolState(
        request_id=req_id, type="plan_approval",
        sender=from_name, target="lead",
        status="pending", payload=plan)
    bus.BUS.send(from_name, "lead", plan,
                 "plan_approval_request",
                 {"request_id": req_id})
    return f"Plan submitted ({req_id})"


def run_request_shutdown(teammate: str) -> str:
    """lead 工具:向某 teammate 发起 shutdown 请求。"""
    req_id = bus.new_request_id()
    bus.pending_requests[req_id] = bus.ProtocolState(
        request_id=req_id, type="shutdown",
        sender="lead", target=teammate,
        status="pending", payload="")
    bus.BUS.send("lead", teammate, "Shut down.", "shutdown_request",
                 {"request_id": req_id})
    return f"Shutdown request sent to {teammate}"


def run_request_plan(teammate: str, task: str) -> str:
    """lead 工具:要求某 teammate 就某任务提交 plan。"""
    bus.BUS.send("lead", teammate, f"Submit plan for: {task}", "message")
    return f"Asked {teammate} to submit a plan"


def run_review_plan(request_id: str, approve: bool,
                    feedback: str = "") -> str:
    """lead 工具:批准或拒绝一条已提交的 plan,并回发响应。"""
    state = bus.pending_requests.get(request_id)
    if not state:
        return f"Request {request_id} not found"
    state.status = "approved" if approve else "rejected"
    bus.BUS.send("lead", state.sender,
                 feedback or ("Approved" if approve else "Rejected"),
                 "plan_approval_response",
                 {"request_id": request_id, "approve": approve})
    return f"Plan {'approved' if approve else 'rejected'}"
