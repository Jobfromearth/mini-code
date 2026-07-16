"""MCP 系统:把外部服务器的工具晚绑定进工具池(教学用 mock)。

MCP 被建模成晚绑定工具:先 connect,再把发现的服务器工具以
``mcp__{server}__{tool}`` 的名字并入普通工具池。这里用 mock 服务器演示。

``mcp_clients`` 是原地修改的 dict(connect 时写入),读取安全。
"""

import re


class MCPClient:
    """发现并调用某个 MCP 服务器上的工具(教学用 mock 实现)。"""

    def __init__(self, name: str):
        self.name = name
        self.tools: list[dict] = []
        self._handlers: dict[str, callable] = {}

    def register(self, tool_defs: list[dict],
                 handlers: dict[str, callable]):
        """登记该服务器的工具定义与对应的本地处理函数。"""
        self.tools = tool_defs
        self._handlers = handlers

    def call_tool(self, tool_name: str, args: dict) -> str:
        """调用服务器上的某个工具;未知工具或异常时返回错误字符串。"""
        handler = self._handlers.get(tool_name)
        if not handler:
            return f"MCP error: unknown tool '{tool_name}'"
        try:
            return handler(**args)
        except Exception as e:
            return f"MCP error: {e}"


mcp_clients: dict[str, MCPClient] = {}

_DISALLOWED_CHARS = re.compile(r'[^a-zA-Z0-9_-]')


def normalize_mcp_name(name: str) -> str:
    """把非 [a-zA-Z0-9_-] 的字符替换为下划线,用于安全的工具名前缀。"""
    return _DISALLOWED_CHARS.sub('_', name)


def _mock_server_docs():
    """构造一个 mock 的 'docs' MCP 服务器(只读工具)。"""
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
    """构造一个 mock 的 'deploy' MCP 服务器(含破坏性工具)。"""
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
    """连接一个 mock MCP 服务器并发现其工具;返回结果说明字符串。"""
    if name in mcp_clients:
        return f"MCP server '{name}' already connected"
    factory = MOCK_SERVERS.get(name)
    if not factory:
        available = ", ".join(MOCK_SERVERS.keys())
        return f"Unknown server '{name}'. Available: {available}"
    mcp_client = factory()
    mcp_clients[name] = mcp_client
    tool_names = [t["name"] for t in mcp_client.tools]
    print(f"  \033[31m[mcp] connected: {name} → {tool_names}\033[0m")
    return (f"Connected to MCP server '{name}'. "
            f"Discovered {len(mcp_client.tools)} tools: {', '.join(tool_names)}")
