"""Live-IDE control: built-in HTTP server status + JetBrains MCP bridge.

These tools only work while Android Studio is running, and the MCP-bridge tools
additionally require the official 'MCP Server' plugin. They degrade gracefully
with actionable messages when those preconditions are not met.
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok
from ..ide_http import about as ide_about
from .. import mcp_bridge
from .. import procutil


class CallMcpToolInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(..., description="Name of the live-IDE MCP tool to invoke (see as_ide_list_mcp_tools).", min_length=1)
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Arguments object for the tool.")
    timeout: int = Field(default=120, ge=10, le=600)


class TimeoutInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    timeout: int = Field(default=60, ge=10, le=600, description="Max seconds to wait for the IDE MCP handshake.")


@mcp.tool(name="as_ide_status", annotations={
    "title": "Live IDE status", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": True})
async def as_ide_status() -> str:
    """Report live-IDE connectivity: process, built-in server, MCP plugin.

    Returns:
        str: JSON with studio_running (bool), builtin_server_up (bool),
        builtin_server_url, about (IDE build info if reachable),
        official_mcp_plugin_installed (bool) and guidance.
    """
    running = await procutil.is_studio_running()
    about = await ide_about()
    plugin = mcp_bridge.plugin_available()
    guidance = []
    if not running:
        guidance.append("Android Studio is not running; start it with as_launch to enable live-IDE tools.")
    elif about is None:
        guidance.append("Studio is running but the built-in server is not answering yet "
                        "(it comes up after a project finishes indexing).")
    if not plugin:
        guidance.append("Official 'MCP Server' plugin not installed; as_ide_list_mcp_tools / "
                        "as_ide_call_mcp_tool are unavailable. Install it from Settings > Plugins.")
    return ok("Live-IDE status.",
              studio_running=running,
              builtin_server_up=about is not None,
              builtin_server_url=CONFIG.builtin_server_url,
              about=about,
              official_mcp_plugin_installed=plugin,
              guidance=guidance)


@mcp.tool(name="as_ide_list_mcp_tools", annotations={
    "title": "List live-IDE MCP tools", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": True})
async def as_ide_list_mcp_tools(params: TimeoutInput) -> str:
    """List the tools exposed by the running IDE's built-in MCP server.

    Bridges to ``studio64.exe stdioMcpServer``. Requires the official MCP Server
    plugin. Returns an actionable message if unavailable.

    Args:
        params (TimeoutInput): timeout.

    Returns:
        str: JSON with available (bool), tools (list) or an error explanation.
    """
    res = await mcp_bridge.list_tools(timeout=params.timeout)
    if res.get("ok"):
        tools = res.get("tools") or []
        names = [t.get("name") for t in tools if isinstance(t, dict)]
        return ok(f"Live IDE exposes {len(names)} MCP tool(s).", tools=tools, tool_names=names)
    return err(res.get("error", "IDE MCP not available."), available=res.get("available"), detail=res)


@mcp.tool(name="as_ide_call_mcp_tool", annotations={
    "title": "Call a live-IDE MCP tool", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_ide_call_mcp_tool(params: CallMcpToolInput) -> str:
    """Invoke a tool on the running IDE's built-in MCP server (live control).

    Use as_ide_list_mcp_tools first to discover available tool names/arguments.
    Requires the official MCP Server plugin and a running IDE.

    Args:
        params (CallMcpToolInput): name, arguments, timeout.

    Returns:
        str: JSON with the tool result, or an error explanation.
    """
    res = await mcp_bridge.call_tool(params.name, params.arguments, timeout=params.timeout)
    if res.get("ok"):
        return ok(f"Called live-IDE tool '{params.name}'.", result=res.get("result"))
    return err(res.get("error", "IDE MCP call failed."), available=res.get("available"), detail=res)
