"""Agent 主循环:上下文预算管线 + 一轮轮的模型调用与工具执行。

每轮流程:注入到点的 cron / 后台通知 → 走上下文预算管线 → 调模型 →
执行 tool_use 块 → 追加 tool_result → 重复,直到模型不再请求工具。

``rounds_since_todo`` 会被重新赋值,内部通过 ``global`` 维护;外部若需读取
应使用 ``loop.rounds_since_todo``。``agent_lock`` 序列化主循环与 cron 自动运行。
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
    """从磁盘 memory 和运行时状态刷新 context(memory、MCP、teammate)。"""
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
    """让每轮 LLM 调用都经过同一条上下文预算管线(就地修改 messages)。"""
    messages[:] = tool_result_budget(messages)
    messages[:] = snip_compact(messages)
    messages[:] = micro_compact(messages)
    if estimate_size(messages) > config.CONTEXT_LIMIT:
        messages[:] = compact_history(messages)
    return messages


def build_user_content(results: list[dict]) -> list[dict]:
    """把 tool_result 与已完成的后台通知一起作为 user 侧内容返回。"""
    content = list(results)
    for note in collect_background_results():
        content.append({"type": "text", "text": note})
    return content


def inject_background_notifications(messages: list):
    """若有已完成的后台任务,追加一条包含其通知的 user 消息。"""
    notes = collect_background_results()
    if notes:
        messages.append({"role": "user", "content": [
            {"type": "text", "text": note} for note in notes]})


def call_llm(messages: list, context: dict, tools: list,
             state: RecoveryState, max_tokens: int):
    """组装系统提示词并带重试地调用模型。"""
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
    """驱动一整轮 agent 对话直到模型停止请求工具(就地修改 messages)。"""
    global rounds_since_todo
    tools, handlers = assemble_tool_pool()
    state = RecoveryState()
    max_tokens = config.DEFAULT_MAX_TOKENS

    while True:
        # 一个循环周期:注入定时/后台工作、准备上下文、调模型、执行 tool_use
        # 块、追加 tool_result、再循环。
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
    """打印本轮(从 turn_start 起)所有 assistant 文本块。"""
    for msg in messages[turn_start:]:
        if msg.get("role") != "assistant":
            continue
        for block in msg.get("content", []):
            if block_type(block) == "text":
                terminal_print(block["text"] if isinstance(block, dict) else block.text)


def cron_autorun_loop(history: list, context: dict):
    """后台线程:cron 任务到点时,在锁内自动跑一轮 agent 并打印结果。"""
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
