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
    instructions="""
Operate Android Studio and the Android development toolchain.

GENERAL USAGE
- Call as_status to understand the environment before acting.
- Prefer dedicated CLI/Gradle/ADB tools over generic shell commands or
  UI automation when they cover the task.
- Use headless inspection only when Android Studio is not open with
  the same project.
- Follow each tool's inputSchema exactly. Many tools require an outer
  "params" object; parameterless tools do not.
- A validation error such as "params: Missing key" means that the
  "params" property is missing. It does not request a property named "key".
- Do not guess tool names, argument names or calling paths. Use the
  schemas and invocation mechanisms supplied by the client.
- If a required schema is unavailable, report the limitation instead
  of trying arbitrary argument combinations.

EXAMPLE: RECENT PROJECTS
- as_project_list_recent accepts:
  {"params": {"limit": 5}}
- The outer "params" object is required. Its "limit" field is optional,
  defaults to 20, and accepts integers from 1 through 100.

LIVE IDE BRIDGE
- This server's MCP tools/list catalog contains the Maurice tools.
- as_ide_list_mcp_tools lists a separate catalog: the tools exposed by
  the running Android Studio JetBrains MCP server.
- Do not use as_ide_list_mcp_tools to enumerate Maurice's own tools.
- as_ide_call_mcp_tool dispatches a call to a tool from the IDE catalog.
- Discover the IDE tool's inputSchema before invoking it.
- Put the IDE tool name in params.name and its arguments in
  params.arguments.
- Pass projectPath inside params.arguments when the IDE tool accepts
  it and the project path is known.
- Example dispatcher arguments:
  {"params": {
    "name": "get_all_open_file_paths",
    "arguments": {"projectPath": "<absolute project path>"}
  }}
- A successful dispatcher response does not necessarily mean that the
  IDE operation succeeded. Inspect the inner result, including isError.
- An empty successful result is not automatically an error.

SAFETY AND REPORTING
- Respect client permission decisions. Do not try alternate access
  methods after a permission denial.
- Destructive operations require the confirmation specified by the
  tool schema. Preserve backups where supported.
- Tool discovery proves that tools are advertised, not that every
  operation has been tested successfully.
- Distinguish validation errors, transport errors and IDE tool errors.
- MCP resources are not the MCP tool catalog.
""".strip(),
)
