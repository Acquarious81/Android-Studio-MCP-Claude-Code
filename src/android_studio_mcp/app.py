"""Shared FastMCP application instance.

Tool modules import `mcp` from here and register their tools via decorators.
Keeping the instance in its own module avoids circular imports between
`server` (which imports every tool module) and the tool modules themselves.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

# Server name follows the {service}_mcp convention.
mcp = FastMCP(
    "android_studio_mcp",
    instructions=(
        "Operate Android Studio and the full Android toolchain. Prefer the "
        "robust CLI tools (Gradle, ADB, emulator, sdkmanager, apkanalyzer, "
        "headless inspection) over UI automation. Destructive actions require "
        "confirm=true and create backups first. Call as_status to understand "
        "the environment before acting."
    ),
)
