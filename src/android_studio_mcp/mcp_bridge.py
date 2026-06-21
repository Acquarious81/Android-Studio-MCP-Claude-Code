"""Bridge to Android Studio's built-in JetBrains MCP server (`stdioMcpServer`).

The IDE declares a custom launcher command ``studio64.exe stdioMcpServer`` whose
main class is ``com.intellij.mcpserver.stdio.McpStdioRunnerKt``. When the MCP
Server plugin is installed and an IDE instance is running, this speaks MCP over
stdio and proxies tool calls to the live IDE.

This module performs a bounded JSON-RPC handshake (initialize -> tools/list or
tools/call) and returns the result. It degrades gracefully: if the plugin is not
installed or the bridge does not respond, it returns a structured explanation
instead of hanging.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any, Optional

from .config import CONFIG
from .logging_setup import LOG
from .runner import CREATE_NO_WINDOW, build_env


def plugin_available() -> bool:
    """Heuristic: the official MCP Server plugin ships a 'mcpserver' plugin dir."""
    return (CONFIG.studio_home / "plugins" / "mcpserver").exists()


def _handshake_sync(payloads: list[dict], timeout: float) -> dict:
    """Spawn the stdio MCP, send the given JSON-RPC payloads, collect responses."""
    if not CONFIG.studio_exe.exists():
        return {"ok": False, "error": f"studio64.exe not found at {CONFIG.studio_exe}"}
    cmd = [str(CONFIG.studio_exe), "stdioMcpServer"]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=build_env(),
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        return {"ok": False, "error": f"failed to spawn stdio MCP: {exc}"}

    stdin_data = "".join(json.dumps(p) + "\n" for p in payloads)
    try:
        out, errtext = proc.communicate(input=stdin_data, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        # Reap the killed process without a second communicate() (which can hang
        # on Windows after kill). wait() releases the handle; terminate() is a
        # last-resort if it is somehow still alive.
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        return {"ok": False, "error": "stdio MCP did not respond within the timeout"}

    responses = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            responses.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"ok": True, "responses": responses, "stderr": (errtext or "")[:2000]}


def _init_payloads(extra: Optional[list[dict]] = None) -> list[dict]:
    payloads = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "android-studio-mcp-bridge", "version": "1.0.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    if extra:
        payloads.extend(extra)
    return payloads


async def list_tools(timeout: float = 60.0) -> dict:
    """Return the tools exposed by the live IDE's MCP server (best-effort)."""
    if not plugin_available():
        return {"ok": False, "available": False,
                "error": "The official 'MCP Server' plugin (com.intellij.mcpServer) is not "
                         "installed in this Android Studio, so stdioMcpServer cannot serve "
                         "live-IDE tools. Install it from Settings > Plugins (Marketplace), "
                         "or rely on the CLI/Gradle/ADB tools which do not need it."}
    payloads = _init_payloads([{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}])
    res = await asyncio.to_thread(_handshake_sync, payloads, timeout)
    if not res.get("ok"):
        return {"ok": False, "available": True, **res}
    tools = None
    for r in res["responses"]:
        if r.get("id") == 2 and "result" in r:
            tools = r["result"].get("tools")
    return {"ok": tools is not None, "available": True, "tools": tools, "raw": res["responses"]}


async def call_tool(name: str, arguments: dict, timeout: float = 120.0) -> dict:
    """Invoke a named tool on the live IDE's MCP server (best-effort)."""
    if not plugin_available():
        return {"ok": False, "available": False,
                "error": "The official 'MCP Server' plugin is not installed; cannot call "
                         "live-IDE MCP tools."}
    payloads = _init_payloads([
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}}])
    res = await asyncio.to_thread(_handshake_sync, payloads, timeout)
    if not res.get("ok"):
        return {"ok": False, "available": True, **res}
    result = None
    for r in res["responses"]:
        if r.get("id") == 2:
            result = r.get("result") or {"error": r.get("error")}
    return {"ok": result is not None, "available": True, "result": result}
