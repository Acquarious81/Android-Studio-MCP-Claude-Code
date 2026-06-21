"""Entry point for the android_studio_mcp server.

Usage:
    python -m android_studio_mcp              # run the stdio MCP server
    python -m android_studio_mcp --list-tools # print registered tool names (stderr) and exit
    python -m android_studio_mcp --selfcheck  # print resolved config (stderr) and exit
"""

from __future__ import annotations

import atexit
import json
import sys

from .server import mcp
from .config import CONFIG
from .logging_setup import LOG
from . import procregistry

# Make sure any background watches are terminated when the server exits.
atexit.register(procregistry.stop_all)


def _list_tools() -> list[str]:
    try:
        tools = mcp._tool_manager.list_tools()  # type: ignore[attr-defined]
        return sorted(t.name for t in tools)
    except Exception as exc:  # pragma: no cover
        LOG.warning("could not list tools: %s", exc)
        return []


def main() -> None:
    args = set(sys.argv[1:])
    if "--list-tools" in args:
        names = _list_tools()
        print(f"{len(names)} tools registered:", file=sys.stderr)
        for n in names:
            print(f"  - {n}", file=sys.stderr)
        return
    if "--selfcheck" in args:
        print(json.dumps(CONFIG.to_dict(), indent=2, default=str), file=sys.stderr)
        print(f"\n{len(_list_tools())} tools registered.", file=sys.stderr)
        return
    LOG.info("Starting android_studio_mcp (stdio). Studio=%s SDK=%s",
             CONFIG.studio_exe.exists(), CONFIG.sdk)
    mcp.run()


if __name__ == "__main__":
    main()
