"""Reporting, logs, configuration and generic backup/restore tools."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok, needs_confirmation, raw
from ..parsers import parse_adb_devices
from .. import procutil
from ..runner import run
from ..safety import SafetyError, backup_file, require_workspace, resolve_existing


class HealthInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_dir: Optional[str] = Field(default=None, description="Optional project to include in the report.")


class LogTailInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    lines: int = Field(default=200, ge=1, le=5000, description="Number of trailing lines to return.")
    level: Optional[str] = Field(default=None, description="Optional level filter, e.g. 'ERROR' or 'WARN' (substring match).")


class BackupFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path: str = Field(..., description="File to back up (inside a workspace root).", min_length=1)


class RestoreInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    backup_path: str = Field(..., description="Backup file produced earlier (in the server backup dir).", min_length=1)
    restore_to: str = Field(..., description="Destination path to restore to (inside a workspace root).", min_length=1)
    confirm: bool = Field(default=False, description="Must be true; overwrites the destination (a backup of it is made first).")


def _tail(path: Path, lines: int, level: Optional[str]) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.readlines()
    except Exception as exc:
        return f"<could not read {path}: {exc}>"
    if level:
        lv = level.upper()
        data = [l for l in data if lv in l.upper()]
    return "".join(data[-lines:])


@mcp.tool(name="as_report_health", annotations={
    "title": "Environment + project health report", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": True})
async def as_report_health(params: HealthInput) -> str:
    """Produce a comprehensive markdown health report of the dev environment.

    Aggregates installation/runtime status, SDK package count, connected devices,
    configured AVDs and (optionally) a project's metadata into one report.

    Args:
        params (HealthInput): optional project_dir.

    Returns:
        str: JSON with a 'markdown' report plus the underlying structured data.
    """
    running = await procutil.is_studio_running()
    devices, avds, sdk_count = [], [], None
    if CONFIG.adb:
        rd = await run([str(CONFIG.adb), "devices", "-l"], timeout=15)
        devices = parse_adb_devices(rd.stdout)
    if CONFIG.emulator:
        ra = await run([str(CONFIG.emulator), "-list-avds"], timeout=20)
        avds = [l.strip() for l in ra.stdout.splitlines() if l.strip()]
    if CONFIG.sdkmanager and CONFIG.sdkmanager.exists():
        rs = await run([str(CONFIG.sdkmanager), "--list_installed"], timeout=60)
        if rs.ok:
            from ..parsers import parse_sdkmanager_list
            sdk_count = len(parse_sdkmanager_list(rs.stdout)["installed"])

    project_md = ""
    project_data = None
    if params.project_dir:
        from .project import as_project_info, ProjectPathInput
        import json
        try:
            pinfo = await as_project_info(ProjectPathInput(project_dir=params.project_dir))
            project_data = json.loads(pinfo)
            project_md = (
                f"\n## Project: {Path(params.project_dir).name}\n"
                f"- applicationId: {project_data.get('application_id')}\n"
                f"- modules: {', '.join(project_data.get('modules', []) or [])}\n"
                f"- SDK: min={project_data.get('min_sdk')} target={project_data.get('target_sdk')} compile={project_data.get('compile_sdk')}\n"
                f"- wrapper present: {project_data.get('has_wrapper')}\n"
            )
        except Exception as exc:
            project_md = f"\n## Project\n- error reading project: {exc}\n"

    md = f"""# Android Studio MCP — Health Report

## Installation
- Studio installed: {CONFIG.studio_exe.exists()} ({CONFIG.studio_home})
- Studio running: {running}
- SDK: {CONFIG.sdk}
- JBR (Java): {CONFIG.jbr}
- Build-tools: {CONFIG.build_tools_dir}

## Toolchain reachable
- adb: {bool(CONFIG.adb)}  |  emulator: {bool(CONFIG.emulator)}  |  sdkmanager: {bool(CONFIG.sdkmanager)}  |  apkanalyzer: {bool(CONFIG.apkanalyzer)}

## State
- Installed SDK packages: {sdk_count if sdk_count is not None else 'n/a'}
- Connected devices: {len(devices)} ({', '.join(d['serial'] for d in devices) or 'none'})
- Configured AVDs: {len(avds)} ({', '.join(avds) or 'none'})
{project_md}"""

    return ok("Health report generated.", markdown=md, studio_running=running,
              devices=devices, avds=avds, installed_sdk_packages=sdk_count,
              project=project_data)


@mcp.tool(name="as_logs_ide", annotations={
    "title": "Tail Android Studio's idea.log", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False})
async def as_logs_ide(params: LogTailInput) -> str:
    """Return the tail of Android Studio's idea.log (optionally filtered by level).

    Args:
        params (LogTailInput): lines, level (optional 'ERROR'/'WARN').

    Returns:
        str: JSON with 'log' (text) and the source path.
    """
    if not CONFIG.idea_log or not CONFIG.idea_log.exists():
        return err(f"idea.log not found (expected under {CONFIG.system_dir}/log). Is the IDE installed/initialised?")
    text = _tail(CONFIG.idea_log, params.lines, params.level)
    return ok(f"Tail of idea.log ({params.lines} lines).", log=text, source=str(CONFIG.idea_log))


@mcp.tool(name="as_logs_mcp", annotations={
    "title": "Tail this MCP server's log", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False})
async def as_logs_mcp(params: LogTailInput) -> str:
    """Return the tail of this MCP server's own log file.

    Args:
        params (LogTailInput): lines, level (optional).

    Returns:
        str: JSON with 'log' (text) and the source path.
    """
    logf = CONFIG.log_dir / "android-studio-mcp.log"
    if not logf.exists():
        return ok("No server log yet.", log="", source=str(logf))
    text = _tail(logf, params.lines, params.level)
    return ok(f"Tail of server log ({params.lines} lines).", log=text, source=str(logf))


@mcp.tool(name="as_config", annotations={
    "title": "Show resolved configuration", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_config() -> str:
    """Show the fully-resolved configuration (all discovered paths and roots).

    Returns:
        str: JSON with every resolved path, the workspace allow-list, and the
        built-in server URL. Use this to debug discovery/path issues.
    """
    return ok("Resolved configuration.", config=CONFIG.to_dict())


@mcp.tool(name="as_backup_file", annotations={
    "title": "Back up a file", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False})
async def as_backup_file(params: BackupFileInput) -> str:
    """Create a timestamped backup of a single file in the server backup dir.

    Args:
        params (BackupFileInput): path.

    Returns:
        str: JSON with the backup path.
    """
    try:
        p = require_workspace(params.path, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    if not p.is_file():
        return err(f"Not a file: {p}")
    dest = backup_file(p)
    return ok(f"Backed up {p.name}.", backup=dest)


@mcp.tool(name="as_restore_backup", annotations={
    "title": "Restore a backup", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": False, "openWorldHint": False})
async def as_restore_backup(params: RestoreInput) -> str:
    """Restore a previously-created backup file to a destination. Requires confirm=true.

    The current destination (if any) is itself backed up before being overwritten.

    Args:
        params (RestoreInput): backup_path, restore_to, confirm.

    Returns:
        str: JSON with the restore result.
    """
    try:
        src = resolve_existing(params.backup_path, must_exist=True)
        dest = require_workspace(params.restore_to, must_exist=False)
    except SafetyError as exc:
        return err(str(exc))
    # The source must be a server-created backup (prevents copying arbitrary
    # system files into the workspace via this tool).
    try:
        src.resolve().relative_to(CONFIG.backup_dir.resolve())
    except ValueError:
        return err(f"backup_path must be a file under the server backup dir ({CONFIG.backup_dir}).")
    if not params.confirm:
        return needs_confirmation("restore backup (overwrites destination)",
                                  {"backup_path": str(src), "restore_to": str(dest)})
    pre = backup_file(dest) if dest.exists() else None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    except Exception as exc:
        return err(f"Restore failed: {exc}")
    return ok(f"Restored {dest.name}.", restored_to=str(dest), prior_backup=pre)
