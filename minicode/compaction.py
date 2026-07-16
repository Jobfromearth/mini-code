"""上下文压缩:分层缩减对话规模。

分层策略:先压缩超大的 tool_result,再裁剪旧的消息区间,只有当上下文仍然
过大或模型显式请求 compact 时,才调用模型做摘要。压缩时始终保持
tool_use / tool_result 成对,避免产生孤立的 tool_result。
"""

import json
import time
from pathlib import Path

from . import config
from .content import (collect_tool_results, extract_text,
                      is_tool_result_message, message_has_tool_use)


def estimate_size(messages: list) -> int:
    """用 JSON 序列化长度粗略估计消息列表的字节规模。"""
    return len(json.dumps(messages, default=str))


def persist_large_output(tool_use_id: str, output: str) -> str:
    """超阈值的工具输出落盘,返回带预览的占位文本(副作用:写磁盘)。"""
    if len(output) <= config.PERSIST_THRESHOLD:
        return output
    config.TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.TOOL_RESULTS_DIR / f"{tool_use_id}.txt"
    if not path.exists():
        path.write_text(output)
    return (f"<persisted-output>\nFull output: {path}\n"
            f"Preview:\n{output[:2000]}\n</persisted-output>")


def tool_result_budget(messages: list, max_bytes: int = 200_000) -> list:
    """把最后一条 user 消息里超预算的 tool_result 逐个落盘缩减。"""
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    if last.get("role") != "user" or not isinstance(content, list):
        return messages
    blocks = [(i, b) for i, b in enumerate(content)
              if isinstance(b, dict) and b.get("type") == "tool_result"]
    total = sum(len(str(b.get("content", ""))) for _, b in blocks)
    if total <= max_bytes:
        return messages
    for _, block in sorted(blocks,
                           key=lambda pair: len(str(pair[1].get("content", ""))),
                           reverse=True):
        if total <= max_bytes:
            break
        text = str(block.get("content", ""))
        block["content"] = persist_large_output(
            block.get("tool_use_id", "unknown"), text)
        total = sum(len(str(b.get("content", ""))) for _, b in blocks)
    return messages


def snip_compact(messages: list, max_messages: int = 50) -> list:
    """超过 max_messages 时裁掉中段,保留头尾并维持 tool 对完整。"""
    if len(messages) <= max_messages:
        return messages
    head_end, tail_start = 3, len(messages) - (max_messages - 3)
    if head_end > 0 and message_has_tool_use(messages[head_end - 1]):
        while head_end < len(messages) and is_tool_result_message(messages[head_end]):
            head_end += 1
    if (tail_start > 0 and tail_start < len(messages)
            and is_tool_result_message(messages[tail_start])
            and message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    if head_end >= tail_start:
        return messages
    snipped = tail_start - head_end
    return (messages[:head_end]
            + [{"role": "user", "content": f"[snipped {snipped} messages]"}]
            + messages[tail_start:])


def micro_compact(messages: list) -> list:
    """把较旧的大 tool_result 就地替换为占位提示,保留最近若干条。"""
    tool_results = collect_tool_results(messages)
    if len(tool_results) <= config.KEEP_RECENT_TOOL_RESULTS:
        return messages
    for _, _, block in tool_results[:-config.KEEP_RECENT_TOOL_RESULTS]:
        if len(str(block.get("content", ""))) > 120:
            block["content"] = "[Earlier tool result compacted. Re-run if needed.]"
    return messages


def write_transcript(messages: list) -> Path:
    """把完整消息列表写成一个 JSONL transcript,返回其路径。"""
    config.TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with path.open("w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")
    return path


def summarize_history(messages: list) -> str:
    """调用模型把对话摘要成可继续工作的简报。"""
    conversation = json.dumps(messages, default=str)[:80000]
    prompt = ("Summarize this coding-agent conversation so work can continue. "
              "Preserve current goal, key findings, changed files, remaining work, "
              "and user constraints.\n\n" + conversation)
    response = config.client.messages.create(
        model=config.MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000)
    return extract_text(response.content) or "(empty summary)"


def compact_history(messages: list) -> list:
    """存档 transcript 后,用一条摘要消息替换整段历史。"""
    transcript = write_transcript(messages)
    print(f"  \033[36m[compact] transcript saved: {transcript}\033[0m")
    summary = summarize_history(messages)
    return [{"role": "user", "content": f"[Compacted]\n\n{summary}"}]


def reactive_compact(messages: list) -> list:
    """prompt-too-long 后的应急压缩:摘要旧历史,原样保留最近 tail。"""
    transcript = write_transcript(messages)
    print(f"  \033[31m[reactive compact] transcript saved: {transcript}\033[0m")
    tail_start = max(0, len(messages) - 5)
    if (tail_start > 0 and tail_start < len(messages)
            and is_tool_result_message(messages[tail_start])
            and message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    try:
        summary = summarize_history(messages[:tail_start])
    except Exception:
        summary = "Earlier conversation was trimmed after a prompt-too-long error."
    return [{"role": "user", "content": f"[Reactive compact]\n\n{summary}"},
            *messages[tail_start:]]
