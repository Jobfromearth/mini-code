"""消息总线与协议状态:teammate 之间的通信基座。

团队通信用追加式 JSONL 邮箱实现,让协议在磁盘上可检视,也让后台 teammate
能发消息。协议状态(shutdown / plan_approval)按 request_id 匹配,防止一条
回复误批到另一条挂起请求。副作用:import 时创建 ``.mailboxes`` 目录。

``BUS``、``active_teammates``、``pending_requests`` 都是共享可变状态,统一通过
``bus.<name>`` 访问。
"""

import json
import random
import time
from dataclasses import dataclass, field

from . import config
from .terminal import terminal_print

config.MAILBOX_DIR.mkdir(exist_ok=True)


class MessageBus:
    """基于每 agent 一个 JSONL 邮箱文件的极简消息总线。"""

    def send(self, from_agent: str, to_agent: str, content: str,
             msg_type: str = "message", metadata: dict = None):
        """把一条消息追加到收件人邮箱(副作用:写磁盘 + 打印)。"""
        msg = {"from": from_agent, "to": to_agent,
               "content": content, "type": msg_type,
               "ts": time.time(), "metadata": metadata or {}}
        inbox = config.MAILBOX_DIR / f"{to_agent}.jsonl"
        with open(inbox, "a") as f:
            f.write(json.dumps(msg) + "\n")
        terminal_print(f"  \033[33m[bus] {from_agent} → {to_agent}: "
                       f"({msg_type}) {content[:50]}\033[0m")

    def read_inbox(self, agent: str) -> list[dict]:
        """读取并清空某 agent 的邮箱,返回消息列表(副作用:删文件)。"""
        inbox = config.MAILBOX_DIR / f"{agent}.jsonl"
        if not inbox.exists():
            return []
        msgs = [json.loads(line) for line in inbox.read_text().splitlines()
                if line.strip()]
        inbox.unlink()
        return msgs


BUS = MessageBus()
active_teammates: dict[str, bool] = {}


@dataclass
class ProtocolState:
    """一条挂起的协议请求(shutdown 或 plan_approval)的状态。"""
    request_id: str
    type: str
    sender: str
    target: str
    status: str
    payload: str
    created_at: float = field(default_factory=time.time)


pending_requests: dict[str, ProtocolState] = {}


def new_request_id() -> str:
    """生成一个随机的协议请求 id。"""
    return f"req_{random.randint(0, 999999):06d}"


def match_response(response_type: str, request_id: str, approve: bool):
    """按 request_id 把一条协议回复匹配到挂起请求并更新其状态。"""
    # 按 request_id 匹配,确保一条回复不会批准到另一条挂起请求;并校验
    # 回复类型与请求类型一致。
    state = pending_requests.get(request_id)
    if not state:
        return
    if state.type == "shutdown" and response_type != "shutdown_response":
        return
    if state.type == "plan_approval" and response_type != "plan_approval_response":
        return
    state.status = "approved" if approve else "rejected"


def consume_lead_inbox(route_protocol=True) -> list[dict]:
    """读取 lead 邮箱;可选地把其中的协议回复路由给 match_response。"""
    msgs = BUS.read_inbox("lead")
    if route_protocol:
        for msg in msgs:
            meta = msg.get("metadata", {})
            req_id = meta.get("request_id", "")
            msg_type = msg.get("type", "")
            if req_id and msg_type.endswith("_response"):
                match_response(msg_type, req_id, meta.get("approve", False))
    return msgs
