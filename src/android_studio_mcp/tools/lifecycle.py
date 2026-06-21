"""Application lifecycle & navigation tools.

Covers: status, launch, close, restart, open project, open file, diff.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import ResponseFormat, err, ok, needs_confirmation
from ..ide_http import about as ide_about
from ..logging_setup import LOG
from .. import procutil
from ..runner import run, spawn_detached
from ..safety import SafetyError, resolve_existing

# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class StatusInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="'json' (structured) or 'markdown' (human-readable summary).",
    )
    deep: bool = Field(
        default=True,
        description="If true, also query adb devices and the built-in IDE server "
                    "(slightly slower). If false, only fast local checks run.",
    )


class LaunchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_dir: Optional[str] = Field(
        default=None,
        description="Optional project directory to open on launch (e.g. 'E:/Projekte/MyApp').",
    )


class CloseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    force: bool = Field(default=False, description="Force-kill (/F) instead of a graceful close. Loses unsaved work.")
    confirm: bool = Field(default=False, description="Must be true to actually close Android Studio.")


class OpenProjectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_dir: str = Field(..., description="Project directory to open (must exist).", min_length=1)


class OpenFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    file: str = Field(..., description="File path to open in the IDE.", min_length=1)
    line: Optional[int] = Field(default=None, description="1-based line to navigate to.", ge=1)
    column: Optional[int] = Field(default=None, description="1-based column to navigate to.", ge=1)


class DiffInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    left: str = Field(..., description="Left file/dir for the diff.", min_length=1)
    right: str = Field(..., description="Right file/dir for the diff.", min_length=1)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _read_build_version() -> Optional[str]:
    bt = CONFIG.studio_home / "build.txt"
    try:
        if bt.is_file():
            return bt.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


def _installed_ide_plugins() -> dict:
    """Read which (third-party/bundled) plugins the IDE has, incl. any MCP plugin."""
    out = {"third_party": [], "ide_mcp_plugin": None, "official_mcp_available": False}
    if not CONFIG.config_dir:
        return out
    try:
        bp = CONFIG.config_dir / "bundled_plugins.txt"
        if bp.is_file():
            text = bp.read_text(encoding="utf-8", errors="replace")
            out["official_mcp_available"] = "mcpserver" in text.lower() or "mcpServer" in text
    except Exception:
        pass
    # The mcpserver plugin folder being present is the real signal.
    out["official_mcp_available"] = (CONFIG.studio_home / "plugins" / "mcpserver").exists()
    # Detect a third-party IDE MCP plugin from the installed-plugins config dir.
    try:
        plugins_dir = CONFIG.config_dir / "plugins"
        if plugins_dir.is_dir():
            for p in plugins_dir.iterdir():
                name = p.name.lower()
                out["third_party"].append(p.name)
                if "mcp" in name:
                    out["ide_mcp_plugin"] = p.name
    except Exception:
        pass
    return out


@mcp.tool(
    name="as_status",
    annotations={"title": "Android Studio environment status", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def as_status(params: StatusInput) -> str:
    """Report the full Android Studio / toolchain environment status.

    Use this FIRST to understand what is available before acting. It reports
    whether Android Studio is installed and running, the resolved paths for the
    SDK / JBR / adb / emulator / sdkmanager, the IDE build number, whether the
    built-in IDE server is reachable, connected devices and a count of AVDs.

    Args:
        params (StatusInput): response_format ('json'|'markdown') and deep (bool).

    Returns:
        str: JSON (or markdown) with keys:
            studio_installed (bool), studio_running (bool), studio_build (str),
            paths (dict of resolved tool paths), adb_version (str|None),
            devices (list), avds (list[str]), java_version (str|None),
            builtin_server_up (bool), ide_plugins (dict).
    """
    studio_running = await procutil.is_studio_running()
    data = {
        "studio_installed": CONFIG.studio_exe.exists(),
        "studio_running": studio_running,
        "studio_build": _read_build_version(),
        "paths": CONFIG.to_dict(),
        "ide_plugins": _installed_ide_plugins(),
        "adb_version": None,
        "devices": [],
        "avds": [],
        "java_version": None,
        "builtin_server_up": False,
    }

    if params.deep:
        if CONFIG.adb:
            r = await run([str(CONFIG.adb), "version"], timeout=15)
            if r.ok:
                data["adb_version"] = r.stdout.splitlines()[0].strip() if r.stdout else None
            from ..parsers import parse_adb_devices
            rd = await run([str(CONFIG.adb), "devices", "-l"], timeout=15)
            if rd.ok:
                data["devices"] = parse_adb_devices(rd.stdout)
        if CONFIG.emulator:
            re_ = await run([str(CONFIG.emulator), "-list-avds"], timeout=20)
            if re_.ok:
                data["avds"] = [l.strip() for l in re_.stdout.splitlines() if l.strip()]
        if CONFIG.java_exe:
            rj = await run([str(CONFIG.java_exe), "-version"], timeout=15)
            # java -version prints to stderr
            ver = (rj.stderr or rj.stdout or "").splitlines()
            data["java_version"] = ver[0].strip() if ver else None
        data["builtin_server_up"] = (await ide_about()) is not None

    if params.response_format == ResponseFormat.MARKDOWN:
        p = data["paths"]
        lines = [
            "# Android Studio MCP — Status", "",
            f"- **Installed**: {data['studio_installed']}  |  **Running**: {data['studio_running']}",
            f"- **Build**: {data['studio_build']}",
            f"- **SDK**: {p.get('sdk')}",
            f"- **adb**: {data['adb_version']}",
            f"- **Java (JBR)**: {data['java_version']}",
            f"- **Built-in server up**: {data['builtin_server_up']}",
            f"- **Devices**: {len(data['devices'])}  |  **AVDs**: {len(data['avds'])} ({', '.join(data['avds']) or 'none'})",
        ]
        return ok("Android Studio status (markdown).", markdown="\n".join(lines), **data)
    return ok("Android Studio environment status.", **data)


@mcp.tool(
    name="as_is_running",
    annotations={"title": "Is Android Studio running", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def as_is_running() -> str:
    """Quick check whether an Android Studio process (studio64.exe) is running.

    Returns:
        str: JSON with running (bool) and processes (list of {image,pid,...}).
    """
    procs = await procutil.list_processes(procutil.STUDIO_IMAGE)
    return ok(f"Android Studio running: {bool(procs)}", running=bool(procs), processes=procs)


@mcp.tool(
    name="as_launch",
    annotations={"title": "Launch Android Studio", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def as_launch(params: LaunchInput) -> str:
    """Launch Android Studio (optionally opening a project), detached.

    The IDE is started as an independent process so the call returns immediately
    with its PID. If a project_dir is given it is passed to studio64.exe to open
    that project. The IDE's built-in server takes a few seconds to come up.

    Args:
        params (LaunchInput): optional project_dir to open.

    Returns:
        str: JSON with pid, already_running (bool) and the launched command.
    """
    if not CONFIG.studio_exe.exists():
        return err(f"Android Studio not found at {CONFIG.studio_exe}. Set ASMCP_STUDIO_HOME.")
    already = await procutil.is_studio_running()
    args = [str(CONFIG.studio_exe)]
    if params.project_dir:
        try:
            proj = resolve_existing(params.project_dir, must_exist=True)
        except SafetyError as exc:
            return err(str(exc))
        args.append(str(proj))
    try:
        pid = spawn_detached(args)
    except Exception as exc:
        return err(f"Failed to launch Android Studio: {exc}")
    return ok(
        f"Launched Android Studio (pid={pid}).",
        pid=pid, already_running=already, command=" ".join(args),
        note="The built-in IDE server (port %d) becomes available a few seconds after a project finishes indexing." % CONFIG.builtin_server_port,
    )


@mcp.tool(
    name="as_close",
    annotations={"title": "Close Android Studio", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
)
async def as_close(params: CloseInput) -> str:
    """Close Android Studio. Graceful by default; force-kill only on request.

    A graceful close (default) sends a normal terminate signal so the IDE can
    save state. force=true uses taskkill /F which can lose unsaved work, so it
    additionally requires confirm=true.

    Args:
        params (CloseInput): force (bool), confirm (bool).

    Returns:
        str: JSON with the taskkill result, or a confirmation_required payload.
    """
    if not params.confirm:
        return needs_confirmation(
            "close Android Studio" + (" (FORCE)" if params.force else ""),
            {"force": params.force, "image": procutil.STUDIO_IMAGE},
        )
    if not await procutil.is_studio_running():
        return ok("Android Studio is not running; nothing to close.", running=False)
    result = await procutil.kill(image=procutil.STUDIO_IMAGE, force=params.force)
    success = result["returncode"] == 0
    return (ok if success else err)(
        "Closed Android Studio." if success else "Close command returned non-zero.",
        result=result,
    )


@mcp.tool(
    name="as_restart",
    annotations={"title": "Restart Android Studio", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
)
async def as_restart(params: CloseInput) -> str:
    """Restart Android Studio (close then relaunch). Requires confirm=true.

    Args:
        params (CloseInput): force (bool) for the close phase, confirm (bool).

    Returns:
        str: JSON describing the close result and the relaunch pid.
    """
    if not params.confirm:
        return needs_confirmation("restart Android Studio", {"force": params.force})
    close_result = None
    if await procutil.is_studio_running():
        close_result = await procutil.kill(image=procutil.STUDIO_IMAGE, force=params.force)
        # brief settle handled by the OS; we immediately relaunch
    if not CONFIG.studio_exe.exists():
        return err(f"Android Studio not found at {CONFIG.studio_exe}.")
    try:
        pid = spawn_detached([str(CONFIG.studio_exe)])
    except Exception as exc:
        return err(f"Closed but failed to relaunch: {exc}", close_result=close_result)
    return ok("Restarted Android Studio.", close_result=close_result, new_pid=pid)


@mcp.tool(
    name="as_open_project",
    annotations={"title": "Open project in Android Studio", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def as_open_project(params: OpenProjectInput) -> str:
    """Open an existing project directory in Android Studio.

    Launches (or reuses) the IDE with the given project. This does not modify
    project files.

    Args:
        params (OpenProjectInput): project_dir (must exist).

    Returns:
        str: JSON with the launched command and pid.
    """
    try:
        proj = resolve_existing(params.project_dir, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    if not proj.is_dir():
        return err(f"Not a directory: {proj}")
    if not CONFIG.studio_exe.exists():
        return err(f"Android Studio not found at {CONFIG.studio_exe}.")
    pid = spawn_detached([str(CONFIG.studio_exe), str(proj)])
    return ok(f"Opening project {proj} in Android Studio.", project_dir=str(proj), pid=pid)


@mcp.tool(
    name="as_open_file",
    annotations={"title": "Open file at line in IDE", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def as_open_file(params: OpenFileInput) -> str:
    """Open a file (optionally at a line/column) in the running Android Studio.

    Prefers the built-in IDE server (instant, reuses the running window). Falls
    back to launching studio64.exe with --line if the server is not reachable.

    Args:
        params (OpenFileInput): file (path), line (optional), column (optional).

    Returns:
        str: JSON describing which method was used and whether it succeeded.
    """
    try:
        f = resolve_existing(params.file, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))

    # Try the built-in server first.
    from ..ide_http import is_up, open_file as http_open
    if await is_up():
        res = await http_open(str(f), params.line, params.column)
        if res.get("opened"):
            return ok(f"Opened {f} via built-in IDE server.", method="builtin_server", result=res)
        LOG.info("built-in open_file did not confirm; falling back to CLI")

    if not CONFIG.studio_exe.exists():
        return err(f"Android Studio not found at {CONFIG.studio_exe}.")
    args = [str(CONFIG.studio_exe)]
    if params.line:
        args += ["--line", str(params.line)]
    if params.column:
        args += ["--column", str(params.column)]
    args.append(str(f))
    pid = spawn_detached(args)
    return ok(f"Opening {f} via studio64.exe.", method="cli", pid=pid, command=" ".join(args))


@mcp.tool(
    name="as_diff",
    annotations={"title": "Open a diff in the IDE", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def as_diff(params: DiffInput) -> str:
    """Open the IDE's visual diff viewer for two files or directories.

    Uses ``studio64.exe diff <left> <right>``.

    Args:
        params (DiffInput): left and right paths.

    Returns:
        str: JSON with the launched command and pid.
    """
    try:
        left = resolve_existing(params.left, must_exist=True)
        right = resolve_existing(params.right, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    if not CONFIG.studio_exe.exists():
        return err(f"Android Studio not found at {CONFIG.studio_exe}.")
    cmd = [str(CONFIG.studio_exe), "diff", str(left), str(right)]
    pid = spawn_detached(cmd)
    return ok(f"Opening diff of {left.name} vs {right.name}.", pid=pid, command=" ".join(cmd))
