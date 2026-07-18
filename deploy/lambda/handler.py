"""AWS Lambda handler: runs one minicode agent turn per invocation.

Lambda's filesystem is read-only outside ``/tmp``, but ``minicode.config``
derives its working directories (tasks, worktrees, mailboxes, transcripts,
...) from ``Path.cwd()`` at import time, and several modules ``mkdir`` those
paths as a side effect of import. We chdir into a scratch directory under the
platform temp dir *before* importing minicode, so those mkdirs land
somewhere writable both on real Lambda and when testing locally (see
``local_invoke.py``).

Each invocation starts a fresh single-turn conversation -- there is no
cross-invocation history, matching Lambda's stateless execution model.

Bring-your-own-key: this handler is meant to sit behind a public demo link
(see ``demo.html`` / ``server.py``), so every caller supplies their own
Anthropic API key rather than spending the deployer's. The key is used to
build a request-scoped client, swapped into ``config.client`` only for the
duration of that invocation, and never logged or written to disk. This
assumes one invocation runs at a time per process -- true for a single
Lambda execution environment, and for ``server.py``'s single-threaded
``HTTPServer`` -- so the swap can't race with another request's client.
"""

import json
import os
import tempfile
from pathlib import Path

_SCRATCH = Path(tempfile.gettempdir()) / "minicode-lambda"
_SCRATCH.mkdir(parents=True, exist_ok=True)
os.chdir(_SCRATCH)

from anthropic import Anthropic  # noqa: E402

from minicode import config  # noqa: E402
from minicode.content import extract_text  # noqa: E402
from minicode.loop import agent_loop, update_context  # noqa: E402

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
}


def _response(status: int, payload: dict) -> dict:
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json", **CORS_HEADERS},
            "body": json.dumps(payload)}


def _parse_body(event: dict) -> dict:
    body = event.get("body")
    payload = json.loads(body) if isinstance(body, str) else (body or event)
    return payload or {}


def lambda_handler(event, context):
    """Entry point registered as the Lambda function handler."""
    try:
        payload = _parse_body(event)
    except json.JSONDecodeError:
        return _response(400, {"error": "body must be JSON"})

    prompt = (payload.get("prompt") or "").strip()
    api_key = (payload.get("api_key") or "").strip()
    if not prompt or not api_key:
        return _response(400, {"error": "'prompt' and 'api_key' are both required"})

    original_client = config.client
    config.client = Anthropic(api_key=api_key, base_url=os.getenv("ANTHROPIC_BASE_URL"))
    try:
        history = [{"role": "user", "content": prompt}]
        agent_loop(history, update_context({}, []))
    except Exception as e:
        return _response(502, {"error": f"{type(e).__name__}: {e}"})
    finally:
        config.client = original_client

    reply = "\n".join(
        extract_text(msg["content"]) for msg in history
        if msg.get("role") == "assistant").strip()
    return _response(200, {"reply": reply})
