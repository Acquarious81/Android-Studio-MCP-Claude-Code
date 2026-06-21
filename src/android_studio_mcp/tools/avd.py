"""Android Virtual Device tools (avdmanager + emulator)."""

from __future__ import annotations

import asyncio
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok, needs_confirmation
from ..parsers import parse_adb_devices, parse_avdmanager_list
from ..runner import run, spawn_detached


def _require_emulator() -> Optional[str]:
    if not CONFIG.emulator or not CONFIG.emulator.exists():
        return f"emulator not found. Expected at {CONFIG.sdk}\\emulator\\emulator.exe."
    return None


class AvdListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    detailed: bool = Field(default=True, description="If true, also run 'avdmanager list avd' for device/target details.")


class AvdCreateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(..., description="AVD name (no spaces), e.g. 'Pixel_7_API_34'.", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    system_image: str = Field(..., description="System image package, e.g. 'system-images;android-34;google_apis;x86_64'. Must already be installed (use as_sdk_install).", min_length=1)
    device: str = Field(default="pixel_7", description="Hardware profile id, e.g. 'pixel_7', 'pixel_6', 'Nexus 6'.")
    confirm: bool = Field(default=False, description="Must be true to create the AVD.")


class AvdDeleteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(..., description="Name of the AVD to delete.", min_length=1)
    confirm: bool = Field(default=False, description="Must be true. Deletes the AVD and its data permanently.")


class AvdStartInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(..., description="AVD name to boot.", min_length=1)
    cold_boot: bool = Field(default=False, description="If true, do a cold boot (-no-snapshot-load).")
    wipe_data: bool = Field(default=False, description="If true, factory reset the AVD on boot (-wipe-data).")
    headless: bool = Field(default=False, description="If true, run without a window (-no-window).")
    wait_boot: bool = Field(default=False, description="If true, wait until the device reports boot completed (up to ~120s).")


class AvdStopInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    serial: Optional[str] = Field(default=None, description="Emulator serial to stop, e.g. 'emulator-5554'. If omitted, stops the first running emulator.")


@mcp.tool(name="as_avd_list", annotations={
    "title": "List AVDs", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_avd_list(params: AvdListInput) -> str:
    """List configured Android Virtual Devices.

    Args:
        params (AvdListInput): detailed (bool).

    Returns:
        str: JSON with 'names' (list) and, if detailed, 'avds' (list of
        {name, device, target, path, based_on}).
    """
    guard = _require_emulator()
    if guard:
        return err(guard)
    r = await run([str(CONFIG.emulator), "-list-avds"], timeout=30)
    names = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
    detail = []
    if params.detailed and CONFIG.avdmanager and CONFIG.avdmanager.exists():
        rd = await run([str(CONFIG.avdmanager), "list", "avd"], timeout=40)
        detail = parse_avdmanager_list(rd.stdout)
    return ok(f"{len(names)} AVD(s) configured.", names=names, avds=detail)


@mcp.tool(name="as_avd_create", annotations={
    "title": "Create an AVD", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False})
async def as_avd_create(params: AvdCreateInput) -> str:
    """Create a new Android Virtual Device from an installed system image.

    The system image must already be installed (use as_sdk_install with e.g.
    'system-images;android-34;google_apis;x86_64').

    Args:
        params (AvdCreateInput): name, system_image, device, confirm.

    Returns:
        str: JSON with the create command output, or confirmation_required.
    """
    if not CONFIG.avdmanager or not CONFIG.avdmanager.exists():
        return err(f"avdmanager not found at {CONFIG.sdk}\\cmdline-tools\\latest\\bin\\avdmanager.bat.")
    if not params.confirm:
        return needs_confirmation("create AVD", {"name": params.name, "system_image": params.system_image, "device": params.device})
    args = [str(CONFIG.avdmanager), "create", "avd", "-n", params.name,
            "-k", params.system_image, "-d", params.device, "--force"]
    r = await run(args, timeout=180, input_text="no\n")  # decline custom hardware profile prompt
    return (ok if r.ok else err)(
        f"Created AVD '{params.name}'." if r.ok else "AVD creation returned non-zero (is the system image installed?).",
        command=r.to_dict())


@mcp.tool(name="as_avd_delete", annotations={
    "title": "Delete an AVD", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": True, "openWorldHint": False})
async def as_avd_delete(params: AvdDeleteInput) -> str:
    """Delete an Android Virtual Device permanently. Requires confirm=true.

    Args:
        params (AvdDeleteInput): name, confirm.

    Returns:
        str: JSON with the delete command output, or confirmation_required.
    """
    if not CONFIG.avdmanager or not CONFIG.avdmanager.exists():
        return err("avdmanager not found.")
    if not params.confirm:
        return needs_confirmation("delete AVD", {"name": params.name})
    r = await run([str(CONFIG.avdmanager), "delete", "avd", "-n", params.name], timeout=60)
    return (ok if r.ok else err)(
        f"Deleted AVD '{params.name}'." if r.ok else "Delete returned non-zero.", command=r.to_dict())


async def _wait_for_boot(timeout_s: int = 120) -> Optional[str]:
    """Poll adb until an emulator reports sys.boot_completed=1. Returns serial."""
    if not CONFIG.adb:
        return None
    waited = 0
    while waited < timeout_s:
        rd = await run([str(CONFIG.adb), "devices"], timeout=15)
        devs = [d for d in parse_adb_devices(rd.stdout) if d["is_emulator"] and d["state"] == "device"]
        for d in devs:
            rb = await run([str(CONFIG.adb), "-s", d["serial"], "shell", "getprop", "sys.boot_completed"], timeout=15)
            if rb.stdout.strip() == "1":
                return d["serial"]
        await asyncio.sleep(3)
        waited += 3
    return None


@mcp.tool(name="as_avd_start", annotations={
    "title": "Start an emulator", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False})
async def as_avd_start(params: AvdStartInput) -> str:
    """Boot an AVD in the emulator (detached) and optionally wait for boot.

    Args:
        params (AvdStartInput): name, cold_boot, wipe_data, headless, wait_boot.

    Returns:
        str: JSON with the emulator pid and (if wait_boot) the booted serial.
    """
    guard = _require_emulator()
    if guard:
        return err(guard)
    args = [str(CONFIG.emulator), "-avd", params.name]
    if params.cold_boot:
        args.append("-no-snapshot-load")
    if params.wipe_data:
        args.append("-wipe-data")
    if params.headless:
        args.append("-no-window")
    pid = spawn_detached(args)
    result = {"pid": pid, "command": " ".join(args)}
    if params.wait_boot:
        serial = await _wait_for_boot(120)
        result["booted_serial"] = serial
        result["boot_completed"] = serial is not None
        msg = f"Started AVD '{params.name}'." + (f" Booted as {serial}." if serial else " Boot not confirmed within 120s.")
        return (ok if serial else err)(msg, **result)
    return ok(f"Started AVD '{params.name}' (pid={pid}). Boot takes ~20-60s.", **result)


@mcp.tool(name="as_avd_stop", annotations={
    "title": "Stop an emulator", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": True, "openWorldHint": False})
async def as_avd_stop(params: AvdStopInput) -> str:
    """Stop a running emulator (adb emu kill).

    Args:
        params (AvdStopInput): optional serial (else the first running emulator).

    Returns:
        str: JSON with the command output.
    """
    if not CONFIG.adb:
        return err("adb not found.")
    serial = params.serial
    if not serial:
        rd = await run([str(CONFIG.adb), "devices"], timeout=15)
        emus = [d for d in parse_adb_devices(rd.stdout) if d["is_emulator"]]
        if not emus:
            return ok("No running emulator found.", running=False)
        serial = emus[0]["serial"]
    r = await run([str(CONFIG.adb), "-s", serial, "emu", "kill"], timeout=30)
    return (ok if r.ok else err)(f"Stopped emulator {serial}." if r.ok else "Stop returned non-zero.", serial=serial, command=r.to_dict())
