# Self-hosted Langfuse v3 for LLM tracing, dual-written alongside JSONL

`tracing.py` currently writes every LLM/tool/agent event to a local JSONL file — reliable and dependency-free, but not queryable or browsable. We're adding Langfuse (self-hosted v3: Postgres + ClickHouse + Redis + MinIO, via `deploy/langfuse/docker-compose.yml`) as a queryable UI over the same events, using its SDK's explicit client calls (`start_span`/`start_generation`/`.end()`, not the `@observe` decorator) to match the existing event-driven call sites in `loop.py`, `hooks.py`, and `recovery.py`. The JSONL writer stays as-is and keeps its "tracing must never break the agent loop" guarantee — Langfuse is a network call that can fail in ways a local file write can't, so it's additive, not a replacement. Each Langfuse trace maps to one **Turn** (`user_prompt` → `turn_end`), not one process-level **Session**, so a session's activity shows up as a sequence of linked traces (sharing a `session_id` field) rather than one trace with hundreds of nested observations. Server credentials are provisioned via `LANGFUSE_INIT_*` env vars so the whole stack is reproducible with `docker compose up` alone, without a manual UI signup step.

## Considered Options

- **Langfuse Cloud / LangSmith** — rejected; self-hosting the storage stack (Postgres/ClickHouse/Redis/MinIO) was itself part of the goal, and keeps trace data local.
- **Langfuse v2 self-host** (Postgres only) — rejected; deprecated architecture, would mean a second migration later.
- **Replacing JSONL outright** — rejected; loses the no-break guarantee if Langfuse is down or misconfigured.
- **One Langfuse trace per Session** — rejected; would nest an entire CLI session's observations under one trace and erase the UI's main benefit (seeing one interaction at a time).

## Consequences

- `tracing.py` gains a second write path (JSONL + Langfuse client calls at the same call sites).
- Langfuse being down degrades observability only, never agent functionality.
- `deploy/langfuse/docker-compose.yml` is the first infra-as-code in this repo; future deployment config (minicode's own Dockerfile, Kubernetes manifests) should follow the same `deploy/<component>/` layout.
