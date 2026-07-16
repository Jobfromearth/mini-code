"""消息内容判定小工具:跨 dict / SDK 对象两种表示形式的纯函数。

这些 helper 不依赖包内其它模块,被 subagent、teams、compaction、loop 复用。
"""


def extract_text(content) -> str:
    """从 assistant content(block 列表)中提取并拼接所有 text 块。"""
    if not isinstance(content, list):
        return str(content)
    return "\n".join(
        getattr(block, "text", "")
        for block in content
        if getattr(block, "type", None) == "text").strip()


def has_tool_use(content) -> bool:
    """判断一段 content 是否包含 tool_use 块(循环的续跑信号)。"""
    # 不要只依赖 stop_reason;具体的 tool_use 块才是主循环判断是否继续的依据。
    return any(getattr(block, "type", None) == "tool_use"
               for block in content)


def block_type(block):
    """取出 block 的 type,兼容 dict 与 SDK 对象两种形态。"""
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


def message_has_tool_use(message: dict) -> bool:
    """判断一条 assistant 消息是否含 tool_use 块。"""
    if message.get("role") != "assistant":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(block_type(block) == "tool_use" for block in content)


def is_tool_result_message(message: dict) -> bool:
    """判断一条 user 消息是否含 tool_result 块。"""
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_result"
               for block in content)


def collect_tool_results(messages: list):
    """收集全部 tool_result 块,返回 (消息下标, 块下标, 块) 三元组列表。"""
    found = []
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if msg.get("role") != "user" or not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                found.append((mi, bi, block))
    return found
