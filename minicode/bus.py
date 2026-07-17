"""Message bus and protocol state: the communication substrate for teammates.

Team communication is append-only JSONL mailboxes. This keeps the protocol
inspectable on disk and lets background teammates send messages. Protocol
state (shutdown / plan_approval) is matched by request_id so one reply cannot
approve a different pending request. Side effect: creates the ``.mailboxes``
directory on import.

``BUS``, ``active_teammates``, and ``pending_requests`` are shared mutable
state; access them through the module (``bus.<name>``).
"""

import json
import random
import time
from dataclasses import dataclass, field

from . import config
from .terminal import terminal_print

config.MAILBOX_DIR.mkdir(exist_ok=True)


class MessageBus:
    """A minimal message bus backed by one JSONL mailbox file per agent."""

    def send(self, from_agent: str, to_agent: str, content: str,
             msg_type: str = "message", metadata: dict = None):
        """Append a message to the recipient's mailbox (writes disk, prints)."""
        msg = {"from": from_agent, "to": to_agent,
               "content": content, "type": msg_type,
               "ts": time.time(), "metadata": metadata or {}}
        inbox = config.MAILBOX_DIR / f"{to_agent}.jsonl"
        with open(inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg) + "\n")
        terminal_print(f"  \033[33m[bus] {from_agent} → {to_agent}: "
                       f"({msg_type}) {content[:50]}\033[0m")

    def read_inbox(self, agent: str) -> list[dict]:
        """Read and drain an agent's mailbox; returns the messages (deletes file)."""
        inbox = config.MAILBOX_DIR / f"{agent}.jsonl"
        if not inbox.exists():
            return []
        msgs = [json.loads(line) for line in
                inbox.read_text(encoding="utf-8").splitlines() if line.strip()]
        inbox.unlink()
        return msgs


BUS = MessageBus()
active_teammates: dict[str, bool] = {}


@dataclass
class ProtocolState:
    """State of one pending protocol request (shutdown or plan_approval)."""
    request_id: str
    type: str
    sender: str
    target: str
    status: str
    payload: str
    created_at: float = field(default_factory=time.time)


pending_requests: dict[str, ProtocolState] = {}


def new_request_id() -> str:
    """Generate a random protocol request id."""
    return f"req_{random.randint(0, 999999):06d}"


def match_response(response_type: str, request_id: str, approve: bool):
    """Match a protocol reply to its pending request by id and update its status."""
    # Responses are matched by request_id so one protocol reply cannot approve
    # a different pending request; the reply type must also match the request.
    state = pending_requests.get(request_id)
    if not state:
        return
    if state.type == "shutdown" and response_type != "shutdown_response":
        return
    if state.type == "plan_approval" and response_type != "plan_approval_response":
        return
    state.status = "approved" if approve else "rejected"


def consume_lead_inbox(route_protocol=True) -> list[dict]:
    """Drain the lead mailbox; optionally route protocol replies to match_response."""
    msgs = BUS.read_inbox("lead")
    if route_protocol:
        for msg in msgs:
            meta = msg.get("metadata", {})
            req_id = meta.get("request_id", "")
            msg_type = msg.get("type", "")
            if req_id and msg_type.endswith("_response"):
                match_response(msg_type, req_id, meta.get("approve", False))
    return msgs
