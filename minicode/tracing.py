"""Structured JSONL tracing: persistent, machine-readable agent behavior log.

Every notable event (LLM calls with token usage, tool start/end with
duration, permission denials, compactions, cron injections) is appended as
one JSON line to ``config.TRACE_FILE``. Cumulative token totals for the
current session live in ``TOTALS``.

Every event is also dual-written to Langfuse (self-hosted; see
``docs/adr/0001-self-hosted-langfuse-tracing.md``) when ``LANGFUSE_PUBLIC_KEY``/
``LANGFUSE_SECRET_KEY`` are configured. One Langfuse trace maps to one Turn
(``user_prompt`` -> ``turn_end``), not one process-level Session. Langfuse is
strictly additive: any failure to reach it is swallowed, same "never break
the agent loop" guarantee the JSONL writer already has.

Run ``python -m minicode.tracing`` to print a summary of the trace file.
"""

import json
import os
import time
import uuid

from . import config

# One id per process; every record carries it so sessions can be separated.
SESSION_ID = uuid.uuid4().hex[:8]

# Cumulative token usage for this session (mutated by trace_llm_call).
TOTALS = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}

# Langfuse client: lazily constructed once, cached (or cached as None on
# failure/absence so we don't retry every trace() call). _propagate_attributes
# is cached alongside it -- it's a module-level function in the langfuse
# package, not a Langfuse() instance method.
_langfuse_client = None
_langfuse_init_attempted = False
_propagate_attributes = None

# Current open observations, keyed by the Turn/tool-call they belong to.
# Tool calls are sequential within a turn (see loop.py's tool dispatch loop),
# so a single slot for each is enough -- no stack needed.
_current_turn = None
_current_tool_span = None


def clip(value, limit: int = 500) -> str:
    """Stringify a value and truncate it so traces stay small."""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + f"...[+{len(text) - limit}]"


def _get_langfuse_client():
    """Lazily construct the Langfuse client; None if unconfigured or unavailable."""
    global _langfuse_client, _langfuse_init_attempted, _propagate_attributes
    if _langfuse_init_attempted:
        return _langfuse_client
    _langfuse_init_attempted = True
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return None
    try:
        from langfuse import Langfuse, propagate_attributes
        _langfuse_client = Langfuse()
        _propagate_attributes = propagate_attributes
    except Exception:
        _langfuse_client = None  # tracing must never break the agent loop
    return _langfuse_client


def _emit_to_langfuse(event: str, fields: dict):
    """Best-effort mirror of one trace event into Langfuse (never raises)."""
    global _current_turn, _current_tool_span
    client = _get_langfuse_client()
    if client is None:
        return
    try:
        if event == "user_prompt":
            with _propagate_attributes(session_id=SESSION_ID):
                _current_turn = client.start_observation(
                    name="turn", input=fields.get("prompt"))
            return

        if event == "turn_end":
            if _current_turn is not None:
                _current_turn.update(output=fields)
                _current_turn.end()
                _current_turn = None
            return

        if _current_turn is None:
            return  # nothing open to nest this event under; skip quietly

        if event == "tool_start":
            _current_tool_span = _current_turn.start_observation(
                name=fields.get("tool", "tool"), as_type="span",
                input=fields.get("input"))
            return

        if event in ("tool_end", "tool_blocked", "background_start"):
            if _current_tool_span is not None:
                _current_tool_span.update(output=fields)
                _current_tool_span.end()
                _current_tool_span = None
            return

        if event == "llm_call":
            return  # trace_llm_call() already emits this as a generation

        # Point-in-time events: permission_denied, cron_inject, compact,
        # llm_error, model_fallback. as_type="event" auto-ends on creation.
        _current_turn.start_observation(name=event, as_type="event", metadata=fields)
    except Exception:
        pass  # Langfuse must never break the agent loop


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
    _emit_to_langfuse(event, fields)


def _summarize_response_content(content) -> str:
    """Best-effort text summary of a model response, for trace/Langfuse output.

    Falls back to naming the tools called when the model produced no text
    (a pure tool_use turn) so the summary is never just empty.
    """
    if content is None:
        return ""
    from .content import extract_text
    text = extract_text(content)
    if text:
        return text
    if isinstance(content, list):
        names = [name for name in
                 (getattr(b, "name", None) for b in content
                  if getattr(b, "type", None) == "tool_use")
                 if name]
        if names:
            return f"[tool_use: {', '.join(names)}]"
    return ""


def trace_llm_call(response, model: str):
    """Record one model call's usage, stop_reason, and output; update totals."""
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    TOTALS["llm_calls"] += 1
    TOTALS["input_tokens"] += input_tokens
    TOTALS["output_tokens"] += output_tokens
    stop_reason = getattr(response, "stop_reason", None)
    output_text = _summarize_response_content(getattr(response, "content", None))
    trace("llm_call", model=model,
          stop_reason=stop_reason,
          input_tokens=input_tokens, output_tokens=output_tokens,
          total_input=TOTALS["input_tokens"],
          total_output=TOTALS["output_tokens"],
          output_text=clip(output_text))
    _emit_llm_call_to_langfuse(model, input_tokens, output_tokens, stop_reason,
                               output_text)


def _emit_llm_call_to_langfuse(model: str, input_tokens: int, output_tokens: int,
                                stop_reason, output_text: str = ""):
    """Best-effort: record one LLM call as a Langfuse generation (never raises)."""
    if _current_turn is None:
        return
    try:
        generation = _current_turn.start_observation(
            name="llm_call", as_type="generation", model=model,
            usage_details={"input": input_tokens, "output": output_tokens})
        generation.update(output={"stop_reason": stop_reason, "text": output_text})
        generation.end()
    except Exception:
        pass  # Langfuse must never break the agent loop


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
