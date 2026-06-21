"""MCP resources and prompts for android_studio_mcp.

Resources expose semi-static data (config, IDE log, recent projects) under
asmcp:// URIs. Prompts provide ready-made workflows the client can surface.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..app import mcp
from ..config import CONFIG


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource("asmcp://config")
def resource_config() -> str:
    """Resolved server configuration (all discovered tool paths and roots)."""
    return json.dumps(CONFIG.to_dict(), indent=2, default=str)


@mcp.resource("asmcp://idea-log")
def resource_idea_log() -> str:
    """The last 200 lines of Android Studio's idea.log."""
    if not CONFIG.idea_log or not CONFIG.idea_log.exists():
        return "idea.log not found."
    try:
        lines = CONFIG.idea_log.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return f"could not read idea.log: {exc}"
    return "\n".join(lines[-200:])


@mcp.resource("asmcp://projects/recent")
def resource_recent_projects() -> str:
    """Recently opened Android Studio projects (from recentProjects.xml)."""
    import xml.etree.ElementTree as ET
    opts = CONFIG.options_dir
    rp = (opts / "recentProjects.xml") if opts else None
    recents = []
    if rp and rp.exists():
        try:
            for entry in ET.parse(rp).getroot().iter("entry"):
                key = entry.get("key")
                if key:
                    recents.append(key.replace("$USER_HOME$", str(Path.home())))
        except Exception:
            pass
    return json.dumps({"recent": recents}, indent=2)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@mcp.prompt(name="run_app", description="Build, install and run an Android project on a device/emulator.")
def prompt_run_app(project_dir: str, variant: str = "Debug") -> str:
    return (
        f"Build and run the Android project at '{project_dir}' (variant {variant}).\n"
        "1. Call as_status to confirm the environment.\n"
        "2. If no device is connected (as_adb_devices), start one with as_avd_start(wait_boot=true).\n"
        f"3. Call as_run_app(project_dir='{project_dir}', variant='{variant}', screenshot=true).\n"
        "4. If crash_detected is true, inspect the returned logcat and use as_retrace if the trace is obfuscated.\n"
        "Report the APK path, whether it launched, and any crash."
    )


@mcp.prompt(name="diagnose_build", description="Diagnose and fix a failing Gradle build.")
def prompt_diagnose_build(project_dir: str) -> str:
    return (
        f"A Gradle build for '{project_dir}' is failing. Diagnose and fix it.\n"
        "1. Run as_gradle_assemble (or as_gradle_run_task) and read the 'remediation' list in the result.\n"
        "2. Apply the suggested tool for each remediation item (e.g. as_sdk_accept_licenses, as_sdk_install).\n"
        "3. Re-run the build and confirm BUILD SUCCESSFUL.\n"
        "Summarise the root cause and the fix you applied."
    )
