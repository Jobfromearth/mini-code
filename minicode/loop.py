"""Agent main loop: the context-budget pipeline plus turn-by-turn model calls.

One cycle: inject fired cron / background notifications, run the context
budget pipeline, call the model, execute tool_use blocks, append
tool_results, repeat — until the model stops requesting tools.

``rounds_since_todo`` is reassigned (managed via ``global``); external readers
should use ``loop.rounds_since_todo``. ``agent_lock`` serializes the main loop
against the cron autorun thread.
"""

import threading

from . import bus
from . import config
from . import mcp
from .background import (collect_background_results, should_run_background,
                        start_background_task)
from .compaction import (compact_history, estimate_size, micro_compact,
                        reactive_compact, snip_compact, tool_result_budget)
from .content import block_type, has_tool_use
from .hooks import trigger_hooks
from .recovery import RecoveryState, is_prompt_too_long_error, with_retry
from .registry import assemble_tool_pool
from .cron import consume_cron_queue
from .skills import assemble_system_prompt
from .terminal import terminal_print
from .tools import call_tool_handler


def update_context(context: dict, messages: list) -> dict:
    """Refresh context from disk memory and runtime state (memory, MCP, teammates)."""
    memories = ""
    if config.MEMORY_INDEX.exists():
        memories = config.MEMORY_INDEX.read_text()[:2000]
    return {
        "memories": memories,
        "connected_mcp": list(mcp.mcp_clients.keys()),
        "active_teammates": list(bus.active_teammates.keys()),
    }


rounds_since_todo = 0
agent_lock = threading.Lock()


def prepare_context(messages: list) -> list:
    """Run every LLM turn through the same context budget pipeline (mutates messages)."""
    messages[:] = tool_result_budget(messages)
    messages[:] = snip_compact(messages)
    messages[:] = micro_compact(messages)
    if estimate_size(messages) > config.CONTEXT_LIMIT:
        messages[:] = compact_history(messages)
    return messages


def build_user_content(results: list[dict]) -> list[dict]:
    """Return tool_results plus completed background notifications as user content."""
    # Tool results and completed background notifications are both returned to
    # the model as user-side content, matching the tool_result feedback loop.
    content = list(results)
    for note in collect_background_results():
        content.append({"type": "text", "text": note})
    return content


def inject_background_notifications(messages: list):
    """Append a user message with notifications for any finished background tasks."""
    notes = collect_background_results()
    if notes:
        messages.append({"role": "user", "content": [
            {"type": "text", "text": note} for note in notes]})


def call_llm(messages: list, context: dict, tools: list,
             state: RecoveryState, max_tokens: int):
    """Assemble the system prompt and call the model with retry."""
    system = assemble_system_prompt(context)
    return with_retry(
        lambda: config.client.messages.create(
            model=state.current_model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens),
        state)


def agent_loop(messages: list, context: dict):
    """Drive one full agent turn until the model stops requesting tools (mutates messages)."""
    global rounds_since_todo
    tools, handlers = assemble_tool_pool()
    state = RecoveryState()
    max_tokens = config.DEFAULT_MAX_TOKENS

    while True:
        # One cycle: inject scheduled/background work, prepare context, call
        # the model, execute tool_use blocks, append tool_results, repeat.
        fired = consume_cron_queue()
        for job in fired:
            messages.append({"role": "user",
                             "content": f"[Scheduled] {job.prompt}"})
            print(f"  \033[35m[cron inject] {job.prompt[:60]}\033[0m")

        inject_background_notifications(messages)

        if rounds_since_todo >= 3:
            messages.append({"role": "user",
                             "content": "<reminder>Update your todos.</reminder>"})
            rounds_since_todo = 0

        prepare_context(messages)
        context = update_context(context, messages)
        tools, handlers = assemble_tool_pool()

        try:
            response = call_llm(messages, context, tools, state, max_tokens)
        except Exception as e:
            if is_prompt_too_long_error(e) and not state.has_attempted_reactive_compact:
                messages[:] = reactive_compact(messages)
                state.has_attempted_reactive_compact = True
                continue
            messages.append({"role": "assistant", "content": [
                {"type": "text", "text": f"[Error] {type(e).__name__}: {e}"}]})
            return

        if response.stop_reason == "max_tokens":
            if not state.has_escalated:
                max_tokens = config.ESCALATED_MAX_TOKENS
                state.has_escalated = True
                print(f"  \033[33m[max_tokens] retry with {max_tokens}\033[0m")
                continue
            messages.append({"role": "assistant", "content": response.content})
            if state.recovery_count < config.MAX_RECOVERY_RETRIES:
                messages.append({"role": "user", "content": config.CONTINUATION_PROMPT})
                state.recovery_count += 1
                continue
            return

        max_tokens = config.DEFAULT_MAX_TOKENS
        state.has_escalated = False
        messages.append({"role": "assistant", "content": response.content})
        if not has_tool_use(response.content):
            trigger_hooks("Stop", messages)
            return

        results = []
        compacted_now = False
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"\033[36m> {block.name}\033[0m")

            if block.name == "compact":
                messages[:] = compact_history(messages)
                messages.append({"role": "user",
                                 "content": "[Compacted. Continue with summarized context.]"})
                compacted_now = True
                break

            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(blocked)})
                continue

            if should_run_background(block.name, block.input):
                bg_id = start_background_task(block, handlers)
                output = (f"[Background task {bg_id} started] "
                          "Result will arrive as a task_notification.")
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": output})
                continue

            handler = handlers.get(block.name)
            output = call_tool_handler(handler, block.input, block.name)
            trigger_hooks("PostToolUse", block, output)
            print(str(output)[:300])

            if block.name == "todo_write":
                rounds_since_todo = 0
            else:
                rounds_since_todo += 1

            results.append({"type": "tool_result",
                            "tool_use_id": block.id, "content": output})

        if compacted_now:
            continue

        messages.append({"role": "user", "content": build_user_content(results)})


def print_turn_assistants(messages: list, turn_start: int):
    """Print all assistant text blocks produced this turn (from turn_start on)."""
    for msg in messages[turn_start:]:
        if msg.get("role") != "assistant":
            continue
        for block in msg.get("content", []):
            if block_type(block) == "text":
                terminal_print(block["text"] if isinstance(block, dict) else block.text)


def cron_autorun_loop(history: list, context: dict):
    """Autorun thread: when cron jobs fire, run an agent turn under the lock and print."""
    import time
    while True:
        time.sleep(1)
        fired = consume_cron_queue()
        if not fired:
            continue
        with agent_lock:
            turn_start = len(history)
            for job in fired:
                history.append({"role": "user",
                                "content": f"[Scheduled] {job.prompt}"})
                terminal_print(
                    f"  \033[35m[cron auto] {job.prompt[:60]}\033[0m")
            agent_loop(history, context)
            context.update(update_context(context, history))
            print_turn_assistants(history, turn_start)
