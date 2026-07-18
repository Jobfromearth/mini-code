"""Minimal stdlib HTTP server for the shareable VPS demo -- no Flask, no
Docker, no API Gateway. Serves `demo.html` at `/` and proxies POST /invoke to
`handler.lambda_handler`, translating between a normal HTTP request/response
and the Lambda event/response shape.

Deliberately single-threaded: `handler.py` swaps a process-global Anthropic
client in and out per request (BYOK -- see its docstring), which is only
safe with one request in flight at a time.

Usage:
    python deploy/lambda/server.py [port]   # default 8080

Put this behind nginx/Caddy for TLS and a real domain; it has no auth or
rate limiting of its own beyond requiring each caller's own API key.
"""

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

# `lambda` is a reserved word, so this directory can't be dotted through
# (`import deploy.lambda.handler` is a SyntaxError) -- put it on sys.path
# directly and import the module by its bare name instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from handler import CORS_HEADERS, lambda_handler  # noqa: E402

DEMO_HTML = (Path(__file__).resolve().parent / "demo.html").read_bytes()

FAKE_CONTEXT = SimpleNamespace(
    function_name="minicode-demo",
    memory_limit_in_mb=256,
    aws_request_id="server-0000",
    get_remaining_time_in_millis=lambda: 30000,
)


class DemoRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(DEMO_HTML)))
        self.end_headers()
        self.wfile.write(DEMO_HTML)

    def do_OPTIONS(self):
        self.send_response(204)
        for key, value in CORS_HEADERS.items():
            self.send_header(key, value)
        self.end_headers()

    def do_POST(self):
        if self.path != "/invoke":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        result = lambda_handler({"body": body}, FAKE_CONTEXT)
        payload = result["body"].encode("utf-8")
        self.send_response(result["statusCode"])
        for key, value in result.get("headers", {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print(f"Serving demo.html and /invoke on http://0.0.0.0:{port}")
    HTTPServer(("0.0.0.0", port), DemoRequestHandler).serve_forever()


if __name__ == "__main__":
    main()
