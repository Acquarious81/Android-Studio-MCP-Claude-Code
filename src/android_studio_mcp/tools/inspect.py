"""Headless IDE code inspection and reformatting (studio64.exe inspect/format)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok, needs_confirmation
from ..parsers import parse_inspection_results
from .. import procutil
from ..runner import run
from ..safety import SafetyError, backup_file, require_workspace, resolve_existing

_DEFAULT_PROFILE = Path(__file__).resolve().parent.parent / "resources" / "default-inspection-profile.xml"


class InspectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_dir: str = Field(..., description="Project root to inspect (must be a Gradle/IDE project).", min_length=1)
    scope_dir: Optional[str] = Field(default=None, description="Optional sub-directory to limit inspection to (relative or absolute, inside the project).")
    profile: Optional[str] = Field(default=None, description="Path to an inspection profile XML. Defaults to the project's profile or the bundled default.")
    max_problems: int = Field(default=200, ge=1, le=2000, description="Max problems to include in the response (full reports remain on disk).")
    timeout: int = Field(default=900, ge=60, le=3600, description="Max seconds; headless inspection includes indexing and can be slow.")


class FormatInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    target: str = Field(..., description="File or directory to reformat (inside a workspace root).", min_length=1)
    mask: str = Field(default="*.java,*.kt,*.xml", description="Comma-separated file masks to format.")
    recursive: bool = Field(default=True, description="Recurse into subdirectories (-r).")
    confirm: bool = Field(default=False, description="Must be true. Files are reformatted in place (backups are made first).")


@mcp.tool(name="as_inspect_code", annotations={
    "title": "Headless code inspection", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_inspect_code(params: InspectInput) -> str:
    """Run Android Studio's code inspection headlessly and return the problems.

    This launches the IDE's offline inspection engine (the same one behind
    "Analyze > Inspect Code") via studio64.exe inspect, writes XML reports to a
    temp dir and parses them into a structured problem list grouped by severity
    and inspection. It must index the project first, so it can take minutes on a
    large project. Do not run while Android Studio is open with the same project
    (config-dir contention) — close it first or expect a slower run.

    Args:
        params (InspectInput): project_dir, optional scope_dir, optional profile,
            max_problems, timeout.

    Returns:
        str: JSON with total_problems, by_severity, by_inspection, and a
        truncated 'problems' list of {inspection, severity, file, line, description}.
    """
    try:
        project = resolve_existing(params.project_dir, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    if not CONFIG.studio_bat.exists():
        return err(f"studio.bat not found at {CONFIG.studio_bat}.")

    # Choose a profile: explicit > project's Project_Default > bundled default.
    profile = None
    if params.profile:
        try:
            profile = resolve_existing(params.profile, must_exist=True)
        except SafetyError as exc:
            return err(str(exc))
    else:
        proj_profile = project / ".idea" / "inspectionProfiles" / "Project_Default.xml"
        profile = proj_profile if proj_profile.exists() else _DEFAULT_PROFILE
    if not profile.exists():
        return err(f"Inspection profile not found: {profile}")

    # Resolve and constrain the optional scope to inside the project (no traversal).
    scope = None
    if params.scope_dir:
        raw_scope = Path(params.scope_dir)
        scope = ((project / raw_scope) if not raw_scope.is_absolute() else raw_scope).resolve()
        try:
            scope.relative_to(project.resolve())
        except ValueError:
            return err(f"scope_dir must be inside the project ({project}); refused: {scope}")

    out_dir = Path(tempfile.mkdtemp(prefix="asmcp-inspect-"))
    args = [str(CONFIG.studio_bat), "inspect", str(project), str(profile), str(out_dir), "-v2"]
    if scope is not None:
        args += ["-d", str(scope)]

    studio_running = await procutil.is_studio_running()
    r = await run(args, cwd=str(project), timeout=params.timeout)
    parsed = parse_inspection_results(out_dir, params.max_problems)

    if parsed["total_problems"] == 0 and not parsed["result_files"]:
        return err(
            "Inspection produced no result files. Common causes: another Studio "
            "instance is using the same config dir, the project failed to import, "
            "or it timed out. See command output.",
            command=r.to_dict(), studio_was_running=studio_running, report_dir=str(out_dir))
    return ok(
        f"Inspection found {parsed['total_problems']} problem(s).",
        studio_was_running=studio_running, command_ok=r.ok, **parsed)


@mcp.tool(name="as_format_code", annotations={
    "title": "Headless reformat code", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": True, "openWorldHint": False})
async def as_format_code(params: FormatInput) -> str:
    """Reformat files in place using the IDE's formatter (studio64.exe format).

    Backups of every affected file are written to the server backup dir before
    formatting. Requires confirm=true.

    Args:
        params (FormatInput): target, mask, recursive, confirm.

    Returns:
        str: JSON with the formatter output and the list of backups created.
    """
    try:
        target = require_workspace(params.target, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    if not CONFIG.format_bat.exists():
        return err(f"format.bat not found at {CONFIG.format_bat}.")
    if not params.confirm:
        return needs_confirmation("reformat files in place", {"target": str(target), "mask": params.mask})

    # Back up matching files first.
    masks = [m.strip().lstrip("*") for m in params.mask.split(",") if m.strip()]
    backups: List[str] = []
    files: List[Path] = []
    if target.is_file():
        files = [target]
    else:
        for p in (target.rglob("*") if params.recursive else target.glob("*")):
            if p.is_file() and any(p.name.endswith(m) for m in masks):
                files.append(p)
    for f in files[:500]:  # cap backups
        b = backup_file(f)
        if b:
            backups.append(b)

    args = [str(CONFIG.format_bat)]
    if params.recursive:
        args.append("-r")
    args += ["-m", params.mask, str(target)]
    r = await run(args, timeout=300)
    return (ok if r.ok else err)(
        f"Reformatted {len(files)} file(s) under {target.name}." if r.ok else "Formatter returned non-zero.",
        files_considered=len(files), backups=backups[:50], command=r.to_dict())
