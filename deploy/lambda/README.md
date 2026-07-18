# Lambda demo

Wraps one minicode agent turn in an [AWS Lambda handler](handler.py):
POST a `prompt` and your own Anthropic `api_key`, get back the model's
reply. Nothing here touches a real AWS account.

Each invocation is a fresh, stateless single-turn conversation (no history
across calls), matching how Lambda actually invokes a function.

The handler is **bring-your-own-key (BYOK)**: every caller supplies their
own Anthropic API key rather than spending the deployer's, built into a
request-scoped client and never logged or persisted (see the docstring in
`handler.py`). This is what makes Option C safe to hand out as a public
link -- nobody can run up your Anthropic usage by hitting it.

## Option A -- pure Python, no Docker (fastest way to verify the code works)

```bash
python deploy/lambda/local_invoke.py "What is 7 * 6?"
```

Calls `handler.lambda_handler` in-process with a synthetic API-Gateway-shaped
event and a fake Lambda context object, passing through the repo root
`.env`'s `ANTHROPIC_API_KEY` as the BYOK key (developer convenience only --
still a real Anthropic API call, a separate cost from AWS).

## Option B -- real Lambda base image + Runtime Interface Emulator

Higher-fidelity (actual Lambda Python runtime, real cold start), still fully
local -- `public.ecr.aws/lambda/python` is a public image pull and the RIE
only binds to localhost. No AWS account or credentials required. Needs
Docker.

```bash
cd <repo root>
docker build -f deploy/lambda/Dockerfile -t minicode-lambda .
docker run --rm -p 9000:8080 minicode-lambda   # no --env-file needed: BYOK

# in another terminal
curl -s "http://localhost:9000/2015-03-31/functions/function/invocations" \
  -d '{"body": "{\"prompt\": \"What is 7 * 6?\", \"api_key\": \"sk-ant-...\"}"}'
```

## Option C -- shareable public demo on your VPS

A tiny stdlib-only HTTP server (`server.py`, no Flask, no Docker) serves a
one-page UI (`demo.html`: prompt box, API-key box, send button) at `/` and
proxies `POST /invoke` to the same `handler.lambda_handler` used above,
translating between a normal HTTP request/response and the Lambda
event/response shape.

```bash
# on the VPS
git clone <this repo> && cd myMinicode
python -m venv .venv && . .venv/bin/activate
pip install -e .
python deploy/lambda/server.py 8080
```

Deliberately single-threaded (`http.server.HTTPServer`, not
`ThreadingHTTPServer`): the BYOK client swap in `handler.py` mutates a
process-global for the duration of one request, which is only safe with one
request in flight at a time. Fine for a low-traffic demo link, not for
production concurrency.

It has no auth or rate limiting beyond "every caller must supply their own
key" -- reasonable for a portfolio link, not for anything sensitive. Put it
behind nginx or Caddy for TLS and a domain, e.g.:

```nginx
server {
    listen 443 ssl;
    server_name demo.example.com;
    location / { proxy_pass http://127.0.0.1:8080; }
}
```

Run `server.py` under `systemd` or `tmux`/`screen` so it survives your SSH
session ending -- it's a plain foreground process otherwise.

## Why the handler chdirs on import

`minicode.config` derives its working directories (`.tasks/`, `.worktrees/`,
`.mailboxes/`, `.transcripts/`, `.traces/`) from `Path.cwd()` at import time,
and several modules `mkdir` those paths as an import side effect. Real
Lambda's filesystem is read-only outside `/tmp`, so `handler.py` chdirs into
a scratch directory under the platform temp dir *before* importing
`minicode` -- the same fix applies whether you're on Lambda or testing
locally. One consequence: file/shell tools the agent invokes during the demo
operate on that scratch directory, not your actual repo checkout.

## Going from here to a real deployment

Not covered by either option above -- both are local-only by design. If you
later want an actual deployment: package this directory with the Lambda
Python runtime (`zip` + `aws lambda create-function`, or `sam deploy`), and
note that Lambda's free tier (1M requests + 400,000 GB-seconds/month) is
perpetual, not a 12-month trial, so a low-traffic demo can plausibly stay
free -- but that depends on your account and usage, so verify current pricing
before relying on it.
