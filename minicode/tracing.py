"""Structured JSONL tracing: persistent, machine-readable agent behavior log.

Every notable event (LLM calls with token usage, tool start/end with
duration, permission denials, compactions, cron injections) is appended as
one JSON line to ``config.TRACE_FILE``. Cumulative token totals for the
current session live in ``TOTALS``.

Run ``python -m minicode.tracing`` to print a summary of the trace file.
"""

import json
import time
import uuid

from . import config

# One id per process; every record carries it so sessions can be separated.
SESSION_ID = uuid.uuid4().hex[:8]

# Cumulative token usage for this session (mutated by trace_llm_call).
TOTALS = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}


def clip(value, limit: int = 500) -> str:
    """Stringify a value and truncate it so traces stay small."""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + f"...[+{len(text) - limit}]"


def trace(event: str, **fields):
    """Append one event record to the JSONL trace file (never raises)."""
    record = {"ts": round(time.time(), 3), "session": SESSION_ID,
              "event": event, **fields}
    try:
        config.TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with config.TRACE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass  # tracing must never break the agent loop


def trace_llm_call(response, model: str):
    """Record one model call's usage and stop_reason; update session totals."""
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    TOTALS["llm_calls"] += 1
    TOTALS["input_tokens"] += input_tokens
    TOTALS["output_tokens"] += output_tokens
    trace("llm_call", model=model,
          stop_reason=getattr(response, "stop_reason", None),
          input_tokens=input_tokens, output_tokens=output_tokens,
          total_input=TOTALS["input_tokens"],
          total_output=TOTALS["output_tokens"])


def usage_summary() -> str:
    """One-line cumulative usage string for end-of-turn printing."""
    return (f"{TOTALS['llm_calls']} LLM call(s), "
            f"{TOTALS['input_tokens']} in / {TOTALS['output_tokens']} out tokens")


def summarize_trace_file() -> str:
    """Aggregate the trace file: per-session tokens and per-tool call stats."""
    if not config.TRACE_FILE.exists():
        return f"No trace file at {config.TRACE_FILE}"
    sessions = {}   # session -> {"llm_calls", "input_tokens", "output_tokens"}
    tools = {}      # tool -> {"calls", "blocked", "total_ms"}
    for line in config.TRACE_FILE.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = rec.get("event")
        if event == "llm_call":
            s = sessions.setdefault(rec.get("session"),
                                    {"llm_calls": 0, "input_tokens": 0,
                                     "output_tokens": 0})
            s["llm_calls"] += 1
            s["input_tokens"] += rec.get("input_tokens", 0)
            s["output_tokens"] += rec.get("output_tokens", 0)
        elif event in ("tool_end", "tool_blocked"):
            t = tools.setdefault(rec.get("tool"),
                                 {"calls": 0, "blocked": 0, "total_ms": 0.0})
            if event == "tool_blocked":
                t["blocked"] += 1
            else:
                t["calls"] += 1
                t["total_ms"] += rec.get("duration_ms", 0)
    lines = [f"Trace: {config.TRACE_FILE}", "", "Sessions:"]
    for sid, s in sessions.items():
        lines.append(f"  {sid}: {s['llm_calls']} call(s), "
                     f"{s['input_tokens']} in / {s['output_tokens']} out tokens")
    lines.append("")
    lines.append("Tools:")
    for name, t in sorted(tools.items(), key=lambda kv: -kv[1]["calls"]):
        avg = t["total_ms"] / t["calls"] if t["calls"] else 0
        lines.append(f"  {name}: {t['calls']} call(s), avg {avg:.0f}ms"
                     + (f", {t['blocked']} blocked" if t["blocked"] else ""))
    return "\n".join(lines)


if __name__ == "__main__":
    print(summarize_trace_file())
