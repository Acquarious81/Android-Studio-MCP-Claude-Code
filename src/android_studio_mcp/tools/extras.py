"""Extra capability tools: retrace (deobfuscate), screenrecord, bundletool (AAB)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok, needs_confirmation
from ..logging_setup import LOG
from ..runner import run
from ..safety import SafetyError, require_workspace, resolve_existing
from .device import _base, _resolve_single_serial, _require_adb

BUNDLETOOL_VERSION = "1.17.2"
BUNDLETOOL_URL = (
    f"https://github.com/google/bundletool/releases/download/"
    f"{BUNDLETOOL_VERSION}/bundletool-all-{BUNDLETOOL_VERSION}.jar"
)


# ---------------------------------------------------------------------------
# retrace
# ---------------------------------------------------------------------------

class RetraceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    mapping_file: str = Field(..., description="Path to the R8/ProGuard mapping.txt for the build.", min_length=1)
    stacktrace: str = Field(..., description="The obfuscated stack trace text to de-obfuscate.", min_length=1)


@mcp.tool(name="as_retrace", annotations={
    "title": "De-obfuscate a stack trace", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_retrace(params: RetraceInput) -> str:
    """De-obfuscate an R8/ProGuard stack trace using its mapping.txt (retrace).

    Args:
        params (RetraceInput): mapping_file (mapping.txt), stacktrace (text).

    Returns:
        str: JSON with the de-obfuscated trace in 'retraced'.
    """
    if not CONFIG.retrace or not CONFIG.retrace.exists():
        return err(f"retrace not found at {CONFIG.cmdline_bin}\\retrace.bat.")
    try:
        mapping = resolve_existing(params.mapping_file, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    r = await run([str(CONFIG.retrace), str(mapping)], timeout=120, input_text=params.stacktrace)
    if not r.ok and not r.stdout:
        return err("retrace returned no output.", command=r.to_dict())
    return ok("Stack trace de-obfuscated.", retraced=r.stdout.strip(), mapping=str(mapping))


# ---------------------------------------------------------------------------
# screenrecord
# ---------------------------------------------------------------------------

class ScreenrecordInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    out_path: str = Field(..., description="Where to save the .mp4 on this PC (inside a workspace root).", min_length=1)
    seconds: int = Field(default=10, ge=1, le=180, description="Recording length in seconds (max 180).")
    serial: Optional[str] = Field(default=None, description="Device serial. Optional if exactly one device is connected.")


@mcp.tool(name="as_screenrecord", annotations={
    "title": "Record the device screen", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_screenrecord(params: ScreenrecordInput) -> str:
    """Record the device screen to an .mp4 and pull it to this PC.

    Args:
        params (ScreenrecordInput): out_path, seconds, serial.

    Returns:
        str: JSON with the saved local path and size.
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
    remote = "/sdcard/asmcp-screenrecord.mp4"
    rr = await run(_base(serial) + ["shell", "screenrecord", "--time-limit", str(params.seconds), remote],
                   timeout=params.seconds + 20)
    if not rr.ok:
        return err("screenrecord failed (note: not supported on all emulators).", command=rr.to_dict())
    out.parent.mkdir(parents=True, exist_ok=True)
    rp = await run(_base(serial) + ["pull", remote, str(out)], timeout=120)
    await run(_base(serial) + ["shell", "rm", "-f", remote], timeout=20)
    if not out.exists() or out.stat().st_size == 0:
        return err("Recording could not be pulled.", command=rp.to_dict())
    return ok(f"Recorded {params.seconds}s to {out}.", saved_to=str(out), bytes=out.stat().st_size, serial=serial)


# ---------------------------------------------------------------------------
# bundletool (AAB)
# ---------------------------------------------------------------------------

def _bundletool_jar() -> Optional[Path]:
    explicit = os.environ.get("ASMCP_BUNDLETOOL")
    if explicit and Path(explicit).exists():
        return Path(explicit)
    cached = CONFIG.log_dir.parent / f"bundletool-all-{BUNDLETOOL_VERSION}.jar"
    return cached if cached.exists() else None


async def _ensure_bundletool(confirm: bool) -> tuple[Optional[Path], Optional[str]]:
    jar = _bundletool_jar()
    if jar:
        return jar, None
    if not confirm:
        return None, "bundletool not found. Re-call with confirm=true to download it once, or set ASMCP_BUNDLETOOL."
    dest = CONFIG.log_dir.parent / f"bundletool-all-{BUNDLETOOL_VERSION}.jar"
    try:
        LOG.info("downloading bundletool from %s", BUNDLETOOL_URL)
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(BUNDLETOOL_URL)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
    except Exception as exc:
        return None, f"Failed to download bundletool: {exc}"
    return dest, None


class AabBuildInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    aab_path: str = Field(..., description="Path to the .aab to convert.", min_length=1)
    out_apks: str = Field(..., description="Output .apks path on this PC (inside a workspace root).", min_length=1)
    universal: bool = Field(default=True, description="Build a single universal APK set (easier to install on any device).")
    confirm: bool = Field(default=False, description="Required only if bundletool must be downloaded.")


class AabInstallInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    apks_path: str = Field(..., description="Path to a .apks set produced by as_aab_build_apks.", min_length=1)
    serial: Optional[str] = Field(default=None, description="Device serial. Optional if exactly one device is connected.")
    confirm: bool = Field(default=False, description="Required only if bundletool must be downloaded.")


@mcp.tool(name="as_aab_build_apks", annotations={
    "title": "Convert an AAB to an APK set", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_aab_build_apks(params: AabBuildInput) -> str:
    """Convert an Android App Bundle (.aab) into an installable .apks set (bundletool).

    Args:
        params (AabBuildInput): aab_path, out_apks, universal, confirm.

    Returns:
        str: JSON with the produced .apks path.
    """
    if not CONFIG.java_exe:
        return err("Bundled JBR java not found; cannot run bundletool.")
    try:
        aab = resolve_existing(params.aab_path, must_exist=True)
        out = require_workspace(params.out_apks, must_exist=False)
    except SafetyError as exc:
        return err(str(exc))
    jar, msg = await _ensure_bundletool(params.confirm)
    if not jar:
        return needs_confirmation("download bundletool and build the APK set", {"url": BUNDLETOOL_URL}) if "confirm=true" in (msg or "") else err(msg)
    if out.exists():
        out.unlink()
    args = [str(CONFIG.java_exe), "-jar", str(jar), "build-apks",
            f"--bundle={aab}", f"--output={out}"]
    if params.universal:
        args.append("--mode=universal")
    r = await run(args, timeout=300)
    if not out.exists():
        return err("bundletool did not produce an .apks set.", command=r.to_dict())
    return ok(f"Built APK set from {aab.name}.", apks=str(out), command=r.to_dict())


@mcp.tool(name="as_aab_install", annotations={
    "title": "Install an APK set on a device", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_aab_install(params: AabInstallInput) -> str:
    """Install a .apks set onto a connected device (bundletool install-apks).

    Args:
        params (AabInstallInput): apks_path, serial, confirm.

    Returns:
        str: JSON with the install result.
    """
    if not CONFIG.java_exe:
        return err("Bundled JBR java not found; cannot run bundletool.")
    try:
        apks = resolve_existing(params.apks_path, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    jar, msg = await _ensure_bundletool(params.confirm)
    if not jar:
        return needs_confirmation("download bundletool and install the APK set", {"url": BUNDLETOOL_URL}) if "confirm=true" in (msg or "") else err(msg)
    args = [str(CONFIG.java_exe), "-jar", str(jar), "install-apks",
            f"--apks={apks}", f"--device-id={serial}"]
    r = await run(args, timeout=300)
    return (ok if r.ok else err)(
        f"Installed APK set on {serial}." if r.ok else "install-apks returned non-zero.",
        serial=serial, command=r.to_dict())
