"""Message-content predicates: pure functions over dict / SDK-object blocks.

These helpers depend on nothing else in the package and are reused by
subagent, teams, compaction, and loop.
"""


def extract_text(content) -> str:
    """Extract and join all text blocks from assistant content (a block list)."""
    if not isinstance(content, list):
        return str(content)
    return "\n".join(
        getattr(block, "text", "")
        for block in content
        if getattr(block, "type", None) == "text").strip()


def has_tool_use(content) -> bool:
    """Check whether content contains a tool_use block (the loop's continue signal)."""
    # Do not rely on stop_reason alone; the concrete tool_use block is the
    # continuation signal used by the loop.
    return any(getattr(block, "type", None) == "tool_use"
               for block in content)


def block_type(block):
    """Return a block's type, accepting both dicts and SDK objects."""
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


def message_has_tool_use(message: dict) -> bool:
    """Check whether an assistant message contains a tool_use block."""
    if message.get("role") != "assistant":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(block_type(block) == "tool_use" for block in content)


def is_tool_result_message(message: dict) -> bool:
    """Check whether a user message contains a tool_result block."""
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_result"
               for block in content)


def collect_tool_results(messages: list):
    """Collect all tool_result blocks as (message index, block index, block) tuples."""
    found = []
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if msg.get("role") != "user" or not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                found.append((mi, bi, block))
    return found
