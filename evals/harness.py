"""Eval harness (parent): run the task x condition matrix and report.

For each task and each ablation condition it:
  1. copies the task's ``workspace/`` into a fresh temp dir,
  2. runs the agent there in a child process (evals.runner) with the
     condition's ablation flags set and a wall-clock timeout,
  3. reads the child's token/latency metrics,
  4. runs the task's ``verify.py`` against the workspace (exit 0 == solved),
  5. records one result row.

Results are written to ``evals/results/<timestamp>.json`` and summarized as a
Markdown table grouped by condition. That table is the artifact you quote on a
resume — resolve rate and mean token cost, with and without each subsystem.

Usage:
  python -m evals.harness                       # all tasks, all conditions
  python -m evals.harness --tasks fix-palindrome
  python -m evals.harness --conditions full no-memory
  python -m evals.harness --list                # list tasks and exit
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = Path(__file__).resolve().parent / "tasks"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# condition name -> the ablation env flags that define it. "full" = every
# subsystem on; each other condition turns exactly one off.
CONDITIONS = {
    "full": {},
    "no-memory": {"MINICODE_ABLATE_MEMORY": "1"},
    "no-multiagent": {"MINICODE_ABLATE_MULTIAGENT": "1"},
    "no-skills": {"MINICODE_ABLATE_SKILLS": "1"},
}

DEFAULT_TIMEOUT = 300  # seconds; per (task, condition) run
VERIFY_TIMEOUT = 60


def load_tasks(names: list[str] | None = None) -> list[dict]:
    """Load task definitions from ``tasks/<id>/task.json`` (optionally filtered)."""
    tasks = []
    for task_dir in sorted(TASKS_DIR.iterdir()) if TASKS_DIR.exists() else []:
        manifest = task_dir / "task.json"
        if not manifest.exists():
            continue
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        meta["_dir"] = task_dir
        if names is None or meta["id"] in names:
            tasks.append(meta)
    return tasks


def _child_env(base: dict, condition_flags: dict, task: dict,
               metrics_file: Path) -> dict:
    """Build the child process environment for one run."""
    import os
    env = dict(os.environ if base is None else base)
    env.update(condition_flags)
    env["MINICODE_EVAL_PROMPT"] = task["prompt"]
    env["MINICODE_EVAL_MAX_ROUNDS"] = str(task.get("max_rounds", 30))
    env["MINICODE_EVAL_METRICS_FILE"] = str(metrics_file)
    # cwd is the task workspace; keep the package importable regardless.
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) + (";" + existing if existing else "")
    return env


def run_one(task: dict, condition: str) -> dict:
    """Run a single (task, condition) and return a result row."""
    import os
    flags = CONDITIONS[condition]
    timeout = task.get("timeout_seconds", DEFAULT_TIMEOUT)
    workspace_src = task["_dir"] / "workspace"
    verify_script = task["_dir"] / "verify.py"

    tmp = Path(tempfile.mkdtemp(prefix=f"eval-{task['id']}-"))
    workspace = tmp / "workspace"
    shutil.copytree(workspace_src, workspace)
    metrics_file = tmp / "metrics.json"

    row = {"task": task["id"], "condition": condition, "solved": False,
           "llm_calls": None, "input_tokens": None, "output_tokens": None,
           "wall_seconds": None, "timed_out": False, "error": None}

    env = _child_env(os.environ, flags, task, metrics_file)
    try:
        subprocess.run([sys.executable, "-m", "evals.runner"],
                       cwd=workspace, env=env, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        row["timed_out"] = True

    if metrics_file.exists():
        m = json.loads(metrics_file.read_text(encoding="utf-8"))
        row.update({k: m.get(k) for k in
                    ("llm_calls", "input_tokens", "output_tokens",
                     "wall_seconds", "error")})

    # Verify against the workspace even if the agent process was killed.
    if verify_script.exists():
        try:
            proc = subprocess.run([sys.executable, str(verify_script)],
                                  cwd=workspace, timeout=VERIFY_TIMEOUT,
                                  capture_output=True, text=True)
            row["solved"] = proc.returncode == 0
        except subprocess.TimeoutExpired:
            row["solved"] = False

    shutil.rmtree(tmp, ignore_errors=True)
    return row


def render_report(results: list[dict]) -> str:
    """Aggregate result rows into a Markdown table grouped by condition."""
    conditions = [c for c in CONDITIONS if any(r["condition"] == c for r in results)]
    lines = [
        "| Condition | Solved | Resolve rate | Mean input tok | Mean output tok | Mean wall (s) |",
        "|---|---|---|---|---|---|",
    ]
    for cond in conditions:
        rows = [r for r in results if r["condition"] == cond]
        n = len(rows)
        solved = sum(1 for r in rows if r["solved"])
        rate = f"{solved / n * 100:.0f}%" if n else "-"

        def mean(key):
            vals = [r[key] for r in rows if r[key] is not None]
            return f"{sum(vals) / len(vals):.0f}" if vals else "-"

        lines.append(
            f"| {cond} | {solved}/{n} | {rate} | "
            f"{mean('input_tokens')} | {mean('output_tokens')} | {mean('wall_seconds')} |")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="minicode eval harness")
    parser.add_argument("--tasks", nargs="*", help="task ids (default: all)")
    parser.add_argument("--conditions", nargs="*", choices=list(CONDITIONS),
                        help="conditions to run (default: all)")
    parser.add_argument("--list", action="store_true", help="list tasks and exit")
    args = parser.parse_args(argv)

    tasks = load_tasks(args.tasks)
    if args.list:
        for t in tasks:
            print(f"{t['id']}: {t['prompt'][:80]}")
        return 0
    if not tasks:
        print("No tasks found under evals/tasks/.", file=sys.stderr)
        return 1

    conditions = args.conditions or list(CONDITIONS)
    results = []
    for task in tasks:
        for cond in conditions:
            print(f"[run] {task['id']} / {cond} ...", flush=True)
            row = run_one(task, cond)
            status = "solved" if row["solved"] else (
                "timeout" if row["timed_out"] else "failed")
            print(f"      -> {status}  "
                  f"({row['input_tokens']}in/{row['output_tokens']}out tok, "
                  f"{row['wall_seconds']}s)", flush=True)
            results.append(row)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    report = render_report(results)
    print("\n" + report + "\n")
    print(f"Raw results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
