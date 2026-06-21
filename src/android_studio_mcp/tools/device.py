"""Device control via ADB: install, logcat, screenshot, input, app control, files."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok, needs_confirmation
from ..parsers import parse_adb_devices
from ..runner import run
from ..safety import SafetyError, require_workspace, resolve_existing

# Shell first-words considered read-only / safe to run without confirmation.
_SAFE_SHELL = {
    "getprop", "dumpsys", "ls", "cat", "df", "ps", "top", "wm", "uptime",
    "date", "whoami", "id", "service", "settings", "input", "screencap",
    "pm", "cmd", "am", "logcat", "free", "cat/proc", "getenforce",
}
# Substrings that force confirmation regardless of the first word. Includes
# destructive commands AND shell control operators (chaining / redirection /
# substitution) that could smuggle a second command past the allow-list.
_DANGEROUS_MARKERS = [
    "rm ", "reboot", "wipe", "format", " uninstall", " clear ",
    "dd ", "mkfs", "fastboot", "su -", "su ", "mount ", ">/", " > /",
    ";", "|", "&", "$(", "`", ">", "<", "\n", "\r",  # shell operators
]


def _require_adb() -> Optional[str]:
    if not CONFIG.adb or not CONFIG.adb.exists():
        return f"adb not found. Expected at {CONFIG.sdk}\\platform-tools\\adb.exe."
    return None


def _base(serial: Optional[str]) -> List[str]:
    base = [str(CONFIG.adb)]
    if serial:
        base += ["-s", serial]
    return base


async def _resolve_single_serial(serial: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """If serial is given, use it. Else require exactly one device. Returns (serial, error)."""
    if serial:
        return serial, None
    rd = await run([str(CONFIG.adb), "devices", "-l"], timeout=15)
    devs = [d for d in parse_adb_devices(rd.stdout) if d["state"] == "device"]
    if not devs:
        return None, "No connected device/emulator in 'device' state. Start one with as_avd_start or connect a phone."
    if len(devs) > 1:
        serials = ", ".join(d["serial"] for d in devs)
        return None, f"Multiple devices connected ({serials}); pass 'serial' to choose one."
    return devs[0]["serial"], None


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class SerialInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    serial: Optional[str] = Field(default=None, description="Target device serial (e.g. 'emulator-5554'). Optional if exactly one device is connected.")


class InstallInput(SerialInput):
    apk_path: str = Field(..., description="Path to the .apk to install.", min_length=1)
    reinstall: bool = Field(default=True, description="Keep app data and reinstall (-r).")
    allow_downgrade: bool = Field(default=False, description="Allow version downgrade (-d).")
    grant_permissions: bool = Field(default=False, description="Grant all runtime permissions (-g).")


class PackageInput(SerialInput):
    package: str = Field(..., description="Application id, e.g. 'com.example.app'.", min_length=1)
    confirm: bool = Field(default=False, description="Required for destructive operations (uninstall).")


class LogcatInput(SerialInput):
    lines: int = Field(default=200, description="Max recent lines to return.", ge=1, le=5000)
    filter_spec: Optional[str] = Field(default=None, description="Optional logcat filter, e.g. 'ActivityManager:I *:S' or a tag.")
    clear_first: bool = Field(default=False, description="Clear the log buffer before dumping (returns only new logs after clearing).")
    grep: Optional[str] = Field(default=None, description="Only return lines containing this substring (case-insensitive).")


class ShellInput(SerialInput):
    command: str = Field(..., description="Shell command to run on the device, e.g. 'getprop ro.build.version.sdk'.", min_length=1)
    confirm: bool = Field(default=False, description="Required if the command is not recognised as read-only/safe.")


class ScreenshotInput(SerialInput):
    out_path: str = Field(..., description="Where to save the PNG on this PC (inside an allowed workspace root).", min_length=1)


class InputEventInput(SerialInput):
    kind: str = Field(..., description="One of: 'text', 'keyevent', 'tap', 'swipe'.", pattern=r"^(text|keyevent|tap|swipe)$")
    value: str = Field(..., description="For text: the text. For keyevent: keycode/name (e.g. 'KEYCODE_HOME' or '3'). For tap: 'x y'. For swipe: 'x1 y1 x2 y2 [ms]'.", min_length=1)


class StartAppInput(SerialInput):
    component: Optional[str] = Field(default=None, description="Explicit component 'pkg/.Activity'. If omitted, launches the package's default launcher activity.")
    package: Optional[str] = Field(default=None, description="Application id to launch via monkey if component is unknown.")


class FileTransferInput(SerialInput):
    local: str = Field(..., description="Local path (inside an allowed workspace root).", min_length=1)
    remote: str = Field(..., description="Remote device path, e.g. '/sdcard/Download/file.txt'.", min_length=1)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(name="as_adb_devices", annotations={
    "title": "List connected devices", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": True})
async def as_adb_devices() -> str:
    """List connected devices and emulators (adb devices -l).

    Returns:
        str: JSON with 'devices': list of {serial, state, model, device,
        product, transport_id, is_emulator} and 'count'.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    r = await run([str(CONFIG.adb), "devices", "-l"], timeout=15)
    devs = parse_adb_devices(r.stdout)
    return ok(f"{len(devs)} device(s) connected.", devices=devs, count=len(devs))


@mcp.tool(name="as_adb_install", annotations={
    "title": "Install an APK", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_adb_install(params: InstallInput) -> str:
    """Install an APK onto a device/emulator.

    Args:
        params (InstallInput): apk_path, serial, reinstall, allow_downgrade, grant_permissions.

    Returns:
        str: JSON with the install result.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    try:
        apk = resolve_existing(params.apk_path, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    if apk.suffix.lower() != ".apk":
        return err(f"Not an .apk file: {apk}")
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    flags = ["install"]
    if params.reinstall:
        flags.append("-r")
    if params.allow_downgrade:
        flags.append("-d")
    if params.grant_permissions:
        flags.append("-g")
    flags.append(str(apk))
    r = await run(_base(serial) + flags, timeout=300)
    success = r.ok and "Success" in (r.stdout + r.stderr)
    return (ok if success else err)(
        f"Installed {apk.name} on {serial}." if success else "Install did not report Success.",
        serial=serial, command=r.to_dict())


@mcp.tool(name="as_adb_uninstall", annotations={
    "title": "Uninstall an app", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": True, "openWorldHint": True})
async def as_adb_uninstall(params: PackageInput) -> str:
    """Uninstall an application (removes app + data). Requires confirm=true.

    Args:
        params (PackageInput): package, serial, confirm.

    Returns:
        str: JSON with the uninstall result, or confirmation_required.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    if not params.confirm:
        return needs_confirmation("uninstall app (removes data)", {"package": params.package})
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    r = await run(_base(serial) + ["uninstall", params.package], timeout=120)
    success = r.ok and "Success" in (r.stdout + r.stderr)
    return (ok if success else err)(
        f"Uninstalled {params.package}." if success else "Uninstall did not report Success.",
        serial=serial, command=r.to_dict())


@mcp.tool(name="as_adb_logcat", annotations={
    "title": "Read device logs (logcat)", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_adb_logcat(params: LogcatInput) -> str:
    """Dump a snapshot of the most recent device logs (non-streaming).

    Args:
        params (LogcatInput): serial, lines, filter_spec, clear_first, grep.

    Returns:
        str: JSON with 'log' (the captured text) and 'line_count'.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    if params.clear_first:
        await run(_base(serial) + ["logcat", "-c"], timeout=20)
    args = _base(serial) + ["logcat", "-d", "-v", "time", "-t", str(params.lines)]
    if params.filter_spec:
        try:
            args += shlex.split(params.filter_spec)
        except ValueError:
            args += params.filter_spec.split()
    r = await run(args, timeout=60)
    text = r.stdout or ""
    if params.grep:
        g = params.grep.lower()
        text = "\n".join(l for l in text.splitlines() if g in l.lower())
    return ok(f"Captured {len(text.splitlines())} log line(s) from {serial}.",
              serial=serial, log=text, line_count=len(text.splitlines()))


@mcp.tool(name="as_adb_logcat_clear", annotations={
    "title": "Clear logcat buffer", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": True, "openWorldHint": True})
async def as_adb_logcat_clear(params: SerialInput) -> str:
    """Clear the device log buffer (adb logcat -c).

    Args:
        params (SerialInput): serial.

    Returns:
        str: JSON with the command result.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    r = await run(_base(serial) + ["logcat", "-c"], timeout=20)
    return (ok if r.ok else err)("Cleared logcat buffer." if r.ok else "Clear returned non-zero.", serial=serial)


@mcp.tool(name="as_adb_shell", annotations={
    "title": "Run a device shell command", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": False, "openWorldHint": True})
async def as_adb_shell(params: ShellInput) -> str:
    """Run a shell command on the device. Read-only commands run freely; anything
    potentially destructive requires confirm=true.

    Recognised safe first-words include getprop, dumpsys, pm list, settings get,
    ls, cat, df, ps, am, input, wm, screencap. Commands containing markers like
    'rm ', 'reboot', 'uninstall', 'clear', 'wipe' always require confirm=true.

    Args:
        params (ShellInput): command, serial, confirm.

    Returns:
        str: JSON with stdout/stderr, or confirmation_required.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    cmd = params.command.strip()
    try:
        tokens = shlex.split(cmd)
    except ValueError as exc:
        return err(f"Could not parse shell command (unbalanced quotes?): {exc}")
    if not tokens:
        return err("Empty shell command.")
    first = tokens[0]
    low = " " + cmd.lower() + " "
    dangerous = any(m in low for m in _DANGEROUS_MARKERS)
    safe = (first in _SAFE_SHELL) and not dangerous
    if not safe and not params.confirm:
        return needs_confirmation(
            "run a non-read-only device shell command",
            {"command": cmd, "reason": "contains a shell operator or destructive marker" if dangerous else "first word not recognised as read-only"},
        )
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    r = await run(_base(serial) + ["shell"] + tokens, timeout=120)
    return (ok if r.ok else err)(
        f"shell: {cmd}" if r.ok else f"shell command returned rc={r.returncode}",
        serial=serial, command=r.to_dict())


@mcp.tool(name="as_adb_screenshot", annotations={
    "title": "Capture a device screenshot", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_adb_screenshot(params: ScreenshotInput) -> str:
    """Capture the device screen to a PNG on this PC (adb exec-out screencap).

    Args:
        params (ScreenshotInput): out_path (inside a workspace root), serial.

    Returns:
        str: JSON with 'saved_to' and the file size.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    try:
        out = require_workspace(params.out_path, must_exist=False)
    except SafetyError as exc:
        return err(str(exc))
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    out.parent.mkdir(parents=True, exist_ok=True)
    # exec-out streams raw PNG bytes; capture to file in a thread.
    import asyncio
    import subprocess
    from ..runner import build_env, CREATE_NO_WINDOW

    def _capture() -> int:
        with open(out, "wb") as fh:
            proc = subprocess.run(_base(serial) + ["exec-out", "screencap", "-p"],
                                  stdout=fh, stderr=subprocess.PIPE, env=build_env(),
                                  timeout=60, creationflags=CREATE_NO_WINDOW)
            return proc.returncode
    rc = await asyncio.to_thread(_capture)
    if rc != 0 or not out.exists() or out.stat().st_size == 0:
        return err("Screenshot failed or produced an empty file.", serial=serial, returncode=rc)
    return ok(f"Saved device screenshot to {out}.", saved_to=str(out), bytes=out.stat().st_size, serial=serial)


@mcp.tool(name="as_adb_input", annotations={
    "title": "Send input to device", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_adb_input(params: InputEventInput) -> str:
    """Send a tap/swipe/text/keyevent to the device (UI automation of the app).

    Args:
        params (InputEventInput): kind ('text'|'keyevent'|'tap'|'swipe'), value, serial.

    Returns:
        str: JSON with the command result.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    sub = {"text": ["text", params.value], "keyevent": ["keyevent", params.value],
           "tap": ["tap"] + params.value.split(), "swipe": ["swipe"] + params.value.split()}[params.kind]
    r = await run(_base(serial) + ["shell", "input"] + sub, timeout=30)
    return (ok if r.ok else err)(f"input {params.kind} sent." if r.ok else "input returned non-zero.", serial=serial, command=r.to_dict())


@mcp.tool(name="as_adb_start_app", annotations={
    "title": "Launch an app", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_adb_start_app(params: StartAppInput) -> str:
    """Launch an app by component or by package (via monkey launcher intent).

    Args:
        params (StartAppInput): component ('pkg/.Activity') or package, serial.

    Returns:
        str: JSON with the launch result.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    if not params.component and not params.package:
        return err("Provide either 'component' (pkg/.Activity) or 'package'.")
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    if params.component:
        args = _base(serial) + ["shell", "am", "start", "-n", params.component]
    else:
        args = _base(serial) + ["shell", "monkey", "-p", params.package, "-c",
                                "android.intent.category.LAUNCHER", "1"]
    r = await run(args, timeout=40)
    return (ok if r.ok else err)("Launched app." if r.ok else "Launch returned non-zero.", serial=serial, command=r.to_dict())


@mcp.tool(name="as_adb_stop_app", annotations={
    "title": "Force-stop an app", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": True})
async def as_adb_stop_app(params: PackageInput) -> str:
    """Force-stop an app (am force-stop). Does not delete data.

    Args:
        params (PackageInput): package, serial (confirm not required).

    Returns:
        str: JSON with the command result.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    r = await run(_base(serial) + ["shell", "am", "force-stop", params.package], timeout=30)
    return (ok if r.ok else err)(f"Force-stopped {params.package}." if r.ok else "force-stop returned non-zero.", serial=serial, command=r.to_dict())


@mcp.tool(name="as_adb_pull", annotations={
    "title": "Pull a file from device", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_adb_pull(params: FileTransferInput) -> str:
    """Copy a file from the device to this PC (adb pull).

    Args:
        params (FileTransferInput): remote (device path), local (workspace path), serial.

    Returns:
        str: JSON with the result and saved local path.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    try:
        local = require_workspace(params.local, must_exist=False)
    except SafetyError as exc:
        return err(str(exc))
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    local.parent.mkdir(parents=True, exist_ok=True)
    r = await run(_base(serial) + ["pull", params.remote, str(local)], timeout=120)
    return (ok if r.ok and local.exists() else err)(
        f"Pulled to {local}." if local.exists() else "Pull failed.", saved_to=str(local), command=r.to_dict())


@mcp.tool(name="as_adb_push", annotations={
    "title": "Push a file to device", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_adb_push(params: FileTransferInput) -> str:
    """Copy a file from this PC to the device (adb push).

    Args:
        params (FileTransferInput): local (workspace path), remote (device path), serial.

    Returns:
        str: JSON with the result.
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    try:
        local = require_workspace(params.local, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    r = await run(_base(serial) + ["push", str(local), params.remote], timeout=120)
    return (ok if r.ok else err)(f"Pushed to {params.remote}." if r.ok else "Push returned non-zero.", command=r.to_dict())
