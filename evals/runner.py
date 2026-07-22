"""Eval child process: run the agent on ONE task in the current working dir.

The parent (harness.py) launches this with ``cwd`` set to a fresh copy of the
task workspace and the task details passed through the environment. Because
``config.WORKDIR`` is bound to the process cwd at import time, one task per
process is what keeps every task's on-disk state (memory, traces, worktrees)
isolated — and makes ``tracing.TOTALS`` at exit equal to exactly this task's
token cost.

This process only runs the agent and writes token/latency metrics. Whether the
task was actually solved is decided by the parent running the task's verifier
against the same workspace — so a timed-out, killed child is still scored.

Env in:  MINICODE_EVAL_PROMPT, MINICODE_EVAL_MAX_ROUNDS, MINICODE_EVAL_METRICS_FILE
         (+ any MINICODE_ABLATE_* flags the parent set for this condition)
File out: MINICODE_EVAL_METRICS_FILE  <- JSON {llm_calls, tokens, wall, error}
"""

import json
import os
import time


def main():
    # Imported here (not at module top) so the ablation env flags the parent set
    # are already in place when config reads them at import time.
    from minicode import tracing
    from minicode.loop import agent_loop, update_context

    prompt = os.environ["MINICODE_EVAL_PROMPT"]
    max_rounds = int(os.environ.get("MINICODE_EVAL_MAX_ROUNDS", "30"))
    metrics_file = os.environ["MINICODE_EVAL_METRICS_FILE"]

    messages = [{"role": "user", "content": prompt}]
    context = update_context({}, [])

    start = time.perf_counter()
    error = None
    try:
        agent_loop(messages, context, max_rounds=max_rounds)
    except Exception as e:  # a crash is a datapoint, not a harness failure
        error = f"{type(e).__name__}: {e}"
    wall = time.perf_counter() - start

    metrics = {
        "llm_calls": tracing.TOTALS["llm_calls"],
        "input_tokens": tracing.TOTALS["input_tokens"],
        "output_tokens": tracing.TOTALS["output_tokens"],
        "wall_seconds": round(wall, 2),
        "error": error,
    }
    with open(metrics_file, "w", encoding="utf-8") as f:
        json.dump(metrics, f)


if __name__ == "__main__":
    main()
