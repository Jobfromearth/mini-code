"""MCP system: late-bound external tools merged into the tool pool.

MCP is modeled as late-bound tools: connect first, then discovered server
tools are merged into the normal tool pool with ``mcp__{server}__{tool}``
names. Two client kinds share one interface: ``StdioMCPClient`` speaks real
JSON-RPC 2.0 over stdio to an external server process (the actual MCP wire
protocol), while the mock servers keep the demo runnable offline.

``mcp_clients`` is a dict mutated in place (written on connect), safe to read.
"""

import json
import queue
import re
import shutil
import subprocess
import threading

from . import config


class MCPClient:
    """Discovers and calls tools on an MCP server (mock implementation)."""

    def __init__(self, name: str):
        self.name = name
        self.tools: list[dict] = []
        self._handlers: dict[str, callable] = {}

    def register(self, tool_defs: list[dict],
                 handlers: dict[str, callable]):
        """Register the server's tool definitions and their local handlers."""
        self.tools = tool_defs
        self._handlers = handlers

    def call_tool(self, tool_name: str, args: dict) -> str:
        """Call a tool on the server; return an error string on unknown tool/failure."""
        handler = self._handlers.get(tool_name)
        if not handler:
            return f"MCP error: unknown tool '{tool_name}'"
        try:
            return handler(**args)
        except Exception as e:
            return f"MCP error: {e}"


class StdioMCPClient(MCPClient):
    """A real MCP client: JSON-RPC 2.0 over stdio to an external server process.

    Implements the minimal MCP handshake — ``initialize``,
    ``notifications/initialized``, ``tools/list`` — and routes ``call_tool``
    through ``tools/call``. Messages are newline-delimited JSON (the MCP stdio
    transport). A reader thread feeds a queue so requests can time out instead
    of blocking forever on a hung server.
    """

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, name: str, command: list[str], timeout: float = 60.0):
        super().__init__(name)
        self._id = 0
        self._timeout = timeout
        exe = shutil.which(command[0]) or command[0]
        self._proc = subprocess.Popen(
            [exe] + command[1:],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=config.WORKDIR, text=True, encoding="utf-8")
        self._lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

        self._rpc("initialize", {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "minicode", "version": "1.0.0"},
        })
        self._notify("notifications/initialized")
        self.tools = self._rpc("tools/list", {}).get("tools", [])

    def _reader(self):
        """Reader thread: push every stdout line into the queue until EOF."""
        for line in self._proc.stdout:
            self._lines.put(line)
        self._lines.put(None)  # EOF marker

    def _send(self, msg: dict):
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str, params: dict = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self._send(msg)

    def _rpc(self, method: str, params: dict) -> dict:
        """Send a request and wait for its response, skipping unrelated messages."""
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id,
                    "method": method, "params": params})
        while True:
            line = self._lines.get(timeout=self._timeout)
            if line is None:
                raise RuntimeError(f"MCP server '{self.name}' closed the pipe")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Ignore server notifications and requests; match our response by id.
            if msg.get("id") != self._id or "method" in msg:
                continue
            if "error" in msg:
                raise RuntimeError(msg["error"].get("message", str(msg["error"])))
            return msg.get("result", {})

    def call_tool(self, tool_name: str, args: dict) -> str:
        """Call a server tool via tools/call; flatten text content into a string."""
        try:
            result = self._rpc("tools/call",
                               {"name": tool_name, "arguments": args or {}})
        except Exception as e:
            return f"MCP error: {e}"
        parts = [c.get("text", "") for c in result.get("content", [])
                 if isinstance(c, dict) and c.get("type") == "text"]
        text = "\n".join(p for p in parts if p) or json.dumps(result)
        return f"MCP error: {text}" if result.get("isError") else text

    def close(self):
        """Terminate the server process."""
        try:
            self._proc.terminate()
        except Exception:
            pass


# Real servers: name → command line that starts an MCP server on stdio.
# These use the official reference servers via npx (requires Node.js).
REAL_SERVERS: dict[str, list[str]] = {
    "filesystem": ["npx", "-y", "@modelcontextprotocol/server-filesystem",
                   str(config.WORKDIR)],
    "everything": ["npx", "-y", "@modelcontextprotocol/server-everything"],
}


mcp_clients: dict[str, MCPClient] = {}

_DISALLOWED_CHARS = re.compile(r'[^a-zA-Z0-9_-]')


def normalize_mcp_name(name: str) -> str:
    """Replace non [a-zA-Z0-9_-] characters with underscores for safe tool prefixes."""
    return _DISALLOWED_CHARS.sub('_', name)


def _mock_server_docs():
    """Build a mock 'docs' MCP server (read-only tools)."""
    mcp_client = MCPClient("docs")
    mcp_client.register(
        tool_defs=[
            {"name": "search", "description": "Search documentation. (readOnly)",
             "inputSchema": {"type": "object",
                             "properties": {"query": {"type": "string"}},
                             "required": ["query"]}},
            {"name": "get_version", "description": "Get API version. (readOnly)",
             "inputSchema": {"type": "object", "properties": {},
                             "required": []}},
        ],
        handlers={
            "search": lambda query: f"[docs] Found 3 results for '{query}'",
            "get_version": lambda: "[docs] API v2.1.0",
        })
    return mcp_client


def _mock_server_deploy():
    """Build a mock 'deploy' MCP server (includes a destructive tool)."""
    mcp_client = MCPClient("deploy")
    mcp_client.register(
        tool_defs=[
            {"name": "trigger",
             "description": "Trigger a deployment. (destructive — requires approval in real CC)",
             "inputSchema": {"type": "object",
                             "properties": {"service": {"type": "string"}},
                             "required": ["service"]}},
            {"name": "status", "description": "Check deployment status. (readOnly)",
             "inputSchema": {"type": "object",
                             "properties": {"service": {"type": "string"}},
                             "required": ["service"]}},
        ],
        handlers={
            "trigger": lambda service: f"[deploy] Triggered: {service}",
            "status": lambda service: f"[deploy] {service}: running (v1.4.2)",
        })
    return mcp_client


MOCK_SERVERS = {
    "docs": _mock_server_docs,
    "deploy": _mock_server_deploy,
}


def connect_mcp(name: str) -> str:
    """Connect an MCP server (real stdio server first, mock as fallback).

    Real servers spawn an external process and do the full MCP handshake;
    mocks stay available so the demo works without Node.js.
    """
    if name in mcp_clients:
        return f"MCP server '{name}' already connected"
    if name in REAL_SERVERS:
        try:
            mcp_client = StdioMCPClient(name, REAL_SERVERS[name])
        except Exception as e:
            return f"Failed to start MCP server '{name}': {e}"
    else:
        factory = MOCK_SERVERS.get(name)
        if not factory:
            available = ", ".join(list(REAL_SERVERS) + list(MOCK_SERVERS))
            return f"Unknown server '{name}'. Available: {available}"
        mcp_client = factory()
    mcp_clients[name] = mcp_client
    tool_names = [t["name"] for t in mcp_client.tools]
    print(f"  \033[31m[mcp] connected: {name} → {tool_names}\033[0m")
    return (f"Connected to MCP server '{name}'. "
            f"Discovered {len(mcp_client.tools)} tools: {', '.join(tool_names)}")
