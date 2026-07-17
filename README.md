<div align="center">

# 🤖 minicode

**A Minimal coding agent, decoupled into a cleanly layered Python package.**

Tasks · Worktrees · Skills · Teams · Hooks · Compaction · Cron · MCP — one agent loop, one concern per module.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-1.0.0-green.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-27%20passed-brightgreen.svg)](tests/)

**English** | [中文](README-zh.md)

</div>

---

## ✨ What is this?

`minicode` is a minimal but complete coding agent built on the Anthropic API. 

It demonstrates, in readable Python, the core mechanisms behind real coding agents:

| Capability | Module | What it is |
|---|---|---|
| 🔧 Tool dispatch | `tools.py` `registry.py` | Schema/handler split; the model sees schemas, Python executes handlers |
| 🛡️ Permission hooks | `hooks.py` | Deny-lists, destructive-command confirmation, path escapes |
| 📝 Todo tracking | `tools.py` | Session todos with strict validation (no `eval`) |
| 🤏 Subagents | `subagent.py` | Focused child agents that return only a final summary |
| 📚 Skills | `skills.py` | Frontmatter-based skill catalog injected into the system prompt |
| 🗜️ Context compaction | `compaction.py` | Layered budget: persist → snip → micro-compact → summarize |
| 🔁 Error recovery | `recovery.py` | Backoff with jitter, 429/529 handling, model fallback |
| 🗂️ Task graph | `tasks.py` | File-backed tasks with `blockedBy` dependencies |
| ⏳ Background tasks | `background.py` | Slow tools return placeholders; results arrive as notifications |
| ⏰ Cron scheduler | `cron.py` | Durable scheduled prompts injected back into the loop |
| 👥 Teammates | `teams.py` `bus.py` | Autonomous threads, JSONL mailboxes, plan-approval protocol |
| 🌲 Git worktrees | `worktrees.py` | Isolated work directories bound to tasks |
| 🔌 MCP | `mcp.py` | Late-bound external tools merged into the tool pool |
| 📊 Tracing | `tracing.py` | JSONL event log, dual-written to a self-hosted [Langfuse](https://langfuse.com) UI: token usage, tool durations, permission denials |

## 🚀 Quick start

```bash
# 1. Install (editable)
pip install -e .

# 2. Configure — copy the example and fill in your key
cp .env.example .env
#    Required: ANTHROPIC_API_KEY, MODEL_ID

# 3. Run
minicode            # console script
python -m minicode  # or as a module
python code.py      # legacy entry point (back-compat shim)
```

Type a question at the `minicode >>` prompt; type `q` to quit.

## 🏗️ Architecture

Dependencies point strictly downward — a module may only import from layers below it:

```
┌────────────────────────────────────────────────┐
│  __main__          CLI entry                   │
├────────────────────────────────────────────────┤
│  loop              agent main loop             │
├────────────────────────────────────────────────┤
│  registry          tool schemas + handlers     │
├────────────────────────────────────────────────┤
│  teams             autonomous teammates        │
│                    (own mini-loop, not loop.py)│
├────────────────────────────────────────────────┤
│  subagent  background                          │
├────────────────────────────────────────────────┤
│  tools  hooks                                  │
├────────────────────────────────────────────────┤
│  tasks  worktrees  skills  bus  mcp            │
│  recovery  compaction  cron                    │
├────────────────────────────────────────────────┤
│  terminal  content                             │
├────────────────────────────────────────────────┤
│  config            env, client, constants      │
└────────────────────────────────────────────────┘
```

## 📊 Observability

Every event (LLM calls, tool start/end, permission denials, compactions, cron injections) is appended to a local JSONL trace file, and dual-written to a self-hosted [Langfuse](https://langfuse.com) instance when configured — one Langfuse trace per conversational **Turn**, with tool calls and LLM generations nested underneath, showing the model's actual output rather than just bookkeeping counts.

```bash
# Spin up self-hosted Langfuse (Postgres + ClickHouse + Redis + MinIO)
cd deploy/langfuse
cp .env.example .env
docker compose up -d          # after ~2-3 min: http://localhost:3000

# Point minicode at it (repo root .env)
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000
```

Langfuse is entirely optional and additive: unset or unreachable, tracing silently falls back to JSONL-only — see [ADR-0001](docs/adr/0001-self-hosted-langfuse-tracing.md) for the full design rationale (why self-hosted, why per-turn granularity, why dual-write).

## 📂 Project layout

```
minicode/            the package (see table above)
code.py              back-compat shim: `python code.py` still works
skills/              skill catalog (SKILL.md with YAML frontmatter)
deploy/langfuse/     self-hosted Langfuse docker-compose stack
docs/adr/            architecture decision records
docs/agents/         issue-tracker / triage-label conventions for engineering skills
CONTEXT.md           domain glossary (Session vs. Turn, etc.)
tests/               pytest suite
pyproject.toml       packaging + `minicode` console script
```

Runtime state lives in dot-directories created on demand: `.tasks/`, `.worktrees/`, `.mailboxes/`, `.transcripts/`, `.memory/`, `.scheduled_tasks.json`, `.traces/`.

## 🧪 Testing

```bash
python -m pytest tests/ -v
```

The suite covers import integrity (no circular imports), todo validation and injection safety, compaction keeping `tool_use`/`tool_result` pairs intact, background-task detection, cron validation, and Langfuse dual-write behavior — including that tracing never raises even when Langfuse is unreachable or misconfigured.

## 📄 License

[MIT](LICENSE)
