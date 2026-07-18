"""Local, zero-cost stand-in for `sam local invoke` / the Lambda Runtime
Interface Emulator: calls the handler in-process with a synthetic event and
context, exactly as API Gateway / a Function URL would shape them.

No AWS account, Docker, or SAM CLI required -- just the same Python
environment `minicode` already runs in. Use `Dockerfile` instead when you
want closer-to-real fidelity (the actual Lambda base image + RIE) and have
Docker available.

Usage:
    python deploy/lambda/local_invoke.py "What is 7 * 6?"
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

# `lambda` is a reserved word, so this directory can't be dotted through
# (`import deploy.lambda.handler` is a SyntaxError) -- put it on sys.path
# directly and import the module by its bare name instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from handler import lambda_handler  # noqa: E402

FAKE_CONTEXT = SimpleNamespace(
    function_name="minicode-demo",
    memory_limit_in_mb=256,
    aws_request_id="local-0000",
    get_remaining_time_in_millis=lambda: 30000,
)


def main():
    # The handler is BYOK-only (see handler.py) so the public demo can't run
    # up the deployer's usage; for this local harness we reuse the repo's
    # own .env key (already loaded into os.environ by importing `handler`
    # above, which pulls in minicode.config).
    prompt = " ".join(sys.argv[1:]) or "What is 7 * 6? Answer in one short sentence."
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    event = {"body": json.dumps({"prompt": prompt, "api_key": api_key})}
    response = lambda_handler(event, FAKE_CONTEXT)
    print(f"statusCode: {response['statusCode']}")
    print(json.loads(response["body"]).get("reply", response["body"]))


if __name__ == "__main__":
    main()
