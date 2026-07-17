# minicode

A minimal coding agent: one agent loop that dispatches tools, tracks tasks, and traces its own behavior.

## Language

**Session**:
One running `minicode` process, identified by `tracing.SESSION_ID`. Spans the CLI's entire lifetime and can contain many turns.
_Avoid_: Run, process — when talking about tracing scope specifically, use Session.

**Turn**:
One user prompt through to its `turn_end` trace event. Strictly smaller than a Session — a session contains many turns. This is the unit a single Langfuse trace maps to (see [ADR-0001](docs/adr/0001-self-hosted-langfuse-tracing.md)).
_Avoid_: Request, round — Turn is the canonical term for this unit in the tracing/agent-loop context.
