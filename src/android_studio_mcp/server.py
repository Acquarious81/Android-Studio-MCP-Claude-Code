"""Server assembly: import every tool module so its @mcp.tool functions register.

Importing this module has the side effect of registering all tools on the shared
FastMCP instance (android_studio_mcp.app.mcp).
"""

from __future__ import annotations

from .app import mcp
from .logging_setup import LOG

# Importing the tool packages registers their tools via decorators.
from .tools import (  # noqa: F401  (imported for side effects)
    lifecycle,
    gradle,
    sdk,
    avd,
    device,
    project,
    apk,
    inspect,
    ide,
    ui,
    reports,
    run,
    watch,
    extras,
    resources,
)

LOG.info("android_studio_mcp tools registered.")

__all__ = ["mcp"]
