"""错误恢复:重试、退避、429/529 处理与模型回退。

``RecoveryState`` 跨一轮 agent 循环携带恢复相关的可变状态;``with_retry`` 用
指数退避 + 抖动重试限流/过载错误,连续过载达到阈值时切换到回退模型。
"""

import random
import time

from . import config


class RecoveryState:
    """单轮 agent 循环内的恢复状态(升配、重试计数、当前模型等)。"""

    def __init__(self):
        self.has_escalated = False
        self.recovery_count = 0
        self.consecutive_529 = 0
        self.has_attempted_reactive_compact = False
        self.current_model = config.PRIMARY_MODEL


def retry_delay(attempt: int) -> float:
    """指数退避延迟(上限 32s)加上最多 25% 抖动。"""
    base = min(config.BASE_DELAY_MS * (2 ** attempt), 32000) / 1000
    return base + random.uniform(0, base * 0.25)


def with_retry(fn, state: RecoveryState):
    """执行 fn,对 429/529 退避重试;连续过载达阈值则切回退模型。"""
    for attempt in range(config.MAX_RETRIES):
        try:
            result = fn()
            state.consecutive_529 = 0
            return result
        except Exception as e:
            name = type(e).__name__.lower()
            msg = str(e).lower()
            if "ratelimit" in name or "429" in msg:
                delay = retry_delay(attempt)
                print(f"  \033[33m[429] retry {attempt + 1}/{config.MAX_RETRIES} "
                      f"after {delay:.1f}s\033[0m")
                time.sleep(delay)
                continue
            if "overloaded" in name or "529" in msg or "overloaded" in msg:
                state.consecutive_529 += 1
                if state.consecutive_529 >= config.MAX_CONSECUTIVE_529 and config.FALLBACK_MODEL:
                    state.current_model = config.FALLBACK_MODEL
                    state.consecutive_529 = 0
                    print(f"  \033[31m[529] switching to {config.FALLBACK_MODEL}\033[0m")
                delay = retry_delay(attempt)
                print(f"  \033[33m[529] retry {attempt + 1}/{config.MAX_RETRIES} "
                      f"after {delay:.1f}s\033[0m")
                time.sleep(delay)
                continue
            raise
    raise RuntimeError(f"Max retries ({config.MAX_RETRIES}) exceeded")


def is_prompt_too_long_error(e: Exception) -> bool:
    """判断异常是否为“提示词过长 / 超出上下文窗口”类错误。"""
    msg = str(e).lower()
    return (("prompt" in msg and "long" in msg)
            or "context_length_exceeded" in msg
            or "max_context_window" in msg)
