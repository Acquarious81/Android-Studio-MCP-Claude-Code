"""Bridge to the live Android Studio MCP server using the MCP Python SDK."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import CONFIG
from .runner import build_env


PLUGIN_JARS = (
    "mcpserver.jar",
    "io.modelcontextprotocol.kotlin.sdk.jar",
    "io.github.oshai.kotlin.logging.jvm.jar",
)

IDE_JARS = (
    "util-8.jar",
    "intellij.libraries.kotlinx.coroutines.core.jar",
    "intellij.libraries.ktor.client.cio.jar",
    "intellij.libraries.ktor.client.jar",
    "intellij.libraries.ktor.network.tls.jar",
    "intellij.libraries.ktor.io.jar",
    "intellij.libraries.ktor.utils.jar",
    "intellij.libraries.kotlinx.io.jar",
    "intellij.libraries.kotlinx.serialization.core.jar",
    "intellij.libraries.kotlinx.serialization.json.jar",
)


def _plugin_candidates() -> tuple[Path, ...]:
    return (
        CONFIG.config_dir / "plugins" / "mcpserver",
        CONFIG.studio_home / "plugins" / "mcpserver",
    )


def _plugin_dir() -> Path | None:
    for directory in _plugin_candidates():
        if (directory / "lib" / "mcpserver.jar").is_file():
            return directory
    return None


def plugin_available() -> bool:
    """Check user-installed and bundled locations for the MCP plugin."""
    return _plugin_dir() is not None


def _server_parameters() -> StdioServerParameters:
    plugin = _plugin_dir()
    if plugin is None:
        searched = ", ".join(str(path) for path in _plugin_candidates())
        raise FileNotFoundError(
            f"MCP Server plugin not found. Searched: {searched}"
        )

    java = CONFIG.java_exe
    if not java.is_file():
        raise FileNotFoundError(f"Java not found at {java}")

    jars = [
        *(plugin / "lib" / name for name in PLUGIN_JARS),
        *(CONFIG.studio_home / "lib" / name for name in IDE_JARS),
    ]

    missing = [str(path) for path in jars if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing MCP classpath files:\n" + "\n".join(missing)
        )

    env = dict(build_env())
    env.setdefault("IJ_MCP_SERVER_PORT", "64342")

    return StdioServerParameters(
        command=str(java),
        args=[
            "-classpath",
            os.pathsep.join(str(path) for path in jars),
            "com.intellij.mcpserver.stdio.McpStdioRunnerKt",
        ],
        env=env,
    )


def _error_text(exc: BaseException) -> str:
    children = getattr(exc, "exceptions", None)
    if children:
        return "; ".join(_error_text(child) for child in children)

    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


async def list_tools(timeout: float = 60.0) -> dict:
    """List tools from the live IDE through an initialized MCP session."""
    available = plugin_available()

    try:
        server = _server_parameters()

        async with asyncio.timeout(timeout):
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    tools = []
                    cursor = None

                    while True:
                        page = await session.list_tools(cursor=cursor)
                        tools.extend(
                            tool.model_dump(mode="json", by_alias=True)
                            for tool in page.tools
                        )
                        cursor = page.nextCursor
                        if not cursor:
                            break

        return {
            "ok": True,
            "available": True,
            "tools": tools,
        }

    except Exception as exc:
        return {
            "ok": False,
            "available": available,
            "error": _error_text(exc),
        }


async def call_tool(
    name: str,
    arguments: dict,
    timeout: float = 120.0,
) -> dict:
    """Invoke an IDE tool through an initialized MCP session."""
    available = plugin_available()

    try:
        server = _server_parameters()

        async with asyncio.timeout(timeout):
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        name,
                        arguments=arguments,
                    )

        return {
            "ok": True,
            "available": True,
            "result": result.model_dump(mode="json", by_alias=True),
        }

    except Exception as exc:
        return {
            "ok": False,
            "available": available,
            "error": _error_text(exc),
        }