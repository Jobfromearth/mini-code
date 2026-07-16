"""Error recovery: retries, backoff, 429/529 handling, and model fallback.

``RecoveryState`` carries recovery-related mutable state across one agent
loop run; ``with_retry`` retries rate-limit/overload errors with exponential
backoff plus jitter, switching to the fallback model after consecutive
overloads reach the threshold.
"""

import random
import time

from . import config
from .tracing import trace


class RecoveryState:
    """Recovery state for one agent-loop run (escalation, retry counts, model)."""

    def __init__(self):
        self.has_escalated = False
        self.recovery_count = 0
        self.consecutive_529 = 0
        self.has_attempted_reactive_compact = False
        self.current_model = config.PRIMARY_MODEL


def retry_delay(attempt: int) -> float:
    """Exponential backoff delay (capped at 32s) plus up to 25% jitter."""
    base = min(config.BASE_DELAY_MS * (2 ** attempt), 32000) / 1000
    return base + random.uniform(0, base * 0.25)


def with_retry(fn, state: RecoveryState):
    """Run fn, retrying 429/529 with backoff; fall back to another model if needed."""
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
                    trace("model_fallback", to=config.FALLBACK_MODEL)
                    print(f"  \033[31m[529] switching to {config.FALLBACK_MODEL}\033[0m")
                delay = retry_delay(attempt)
                print(f"  \033[33m[529] retry {attempt + 1}/{config.MAX_RETRIES} "
                      f"after {delay:.1f}s\033[0m")
                time.sleep(delay)
                continue
            raise
    raise RuntimeError(f"Max retries ({config.MAX_RETRIES}) exceeded")


def is_prompt_too_long_error(e: Exception) -> bool:
    """Detect 'prompt too long / context window exceeded' style errors."""
    msg = str(e).lower()
    return (("prompt" in msg and "long" in msg)
            or "context_length_exceeded" in msg
            or "max_context_window" in msg)
