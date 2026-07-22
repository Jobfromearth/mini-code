# Eval harness

Measures the minicode agent two ways at once:

1. **Does it work?** — resolve rate on self-contained coding tasks.
2. **What does each subsystem buy you?** — the same tasks re-run with one
   subsystem ablated (memory / multi-agent / skills), so you can attribute the
   difference in resolve rate and token cost to that subsystem.

This is the difference between a resume that says *"built a multi-agent layer"*
and one that says *"multi-agent raised resolve rate from X% to Y% at Z% more
tokens on N tasks"* — the second is defensible in an interview because you have
the table.

## How it works

`config.WORKDIR` is bound to the process cwd at import, so every task runs in
its **own child process** with cwd set to a throwaway copy of the task
workspace. That keeps each run's on-disk state (memory, traces, worktrees)
isolated, and makes `tracing.TOTALS` at process exit equal to exactly that
task's token cost — no extra instrumentation.

- `harness.py` (parent) — runs the task x condition matrix, applies a per-run
  timeout, verifies the result, writes `results/<timestamp>.json`, prints a
  Markdown table.
- `runner.py` (child) — runs the agent on one task, writes token/latency
  metrics. It does **not** decide pass/fail.
- Verification is parent-side: the task's `verify.py` runs against the workspace
  after the agent finishes (or is killed on timeout), so even a runaway run is
  still scored. `verify.py` lives outside `workspace/`, so the agent never sees
  the cases it's graded on.

## Conditions

| Condition | Env flag set | Turns off |
|---|---|---|
| `full` | (none) | nothing — every subsystem on |
| `no-memory` | `MINICODE_ABLATE_MEMORY=1` | memory injected into the system prompt |
| `no-multiagent` | `MINICODE_ABLATE_MULTIAGENT=1` | `task` subagent + teammate protocol tools |
| `no-skills` | `MINICODE_ABLATE_SKILLS=1` | skills catalog + `load_skill` |

The flags are read once at import (`minicode/config.py`) and gate exactly one
place each: memory in `loop.update_context`, the tools in
`registry.assemble_tool_pool`, the catalog in `skills.assemble_system_prompt`.

## Run it

Needs a working `.env` (real `MODEL_ID` + API key) — the harness calls the model.
Start small; every run costs tokens.

```bash
python -m evals.harness --list                       # what tasks exist
python -m evals.harness --tasks fix-palindrome        # one task, all conditions
python -m evals.harness --conditions full no-skills   # all tasks, two conditions
python -m evals.harness                               # full matrix
```

## Adding a task

```
evals/tasks/<id>/
  task.json          {"id","prompt","max_rounds","timeout_seconds"}
  workspace/         seed files copied into the agent's cwd (the agent edits these)
  verify.py          run with cwd=workspace; exit 0 == solved. Keep it OUT of workspace/.
```

## Roadmap

- **Memory-reuse eval.** On independent tasks the `no-memory` column won't move,
  because there's nothing to remember yet. To measure cross-session reuse, run a
  *sequence* of related tasks sharing one memory store
  (`MINICODE_MEMORY_DIR=/some/dir`) and compare full vs `no-memory` on the later
  tasks. The plumbing (env override, ablation flag) is already here; the task
  sequences are the next thing to add.
- **SWE-bench adapter.** These hand-written tasks prove the loop end to end.
  Real signal comes from SWE-bench Verified / Lite: add an adapter that
  materializes each instance's repo at the base commit into `workspace/` and
  uses the instance's `FAIL_TO_PASS` tests as `verify.py`. The task interface
  (workspace + prompt + verifier) already fits; report a fixed subset size and
  say it's a subset.
