"""Android SDK manager tools (sdkmanager)."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok, needs_confirmation
from ..parsers import parse_sdkmanager_list
from ..runner import run


def _require_sdkmanager() -> str | None:
    if not CONFIG.sdkmanager or not CONFIG.sdkmanager.exists():
        return (f"sdkmanager not found. Expected at "
                f"{CONFIG.sdk}\\cmdline-tools\\latest\\bin\\sdkmanager.bat. "
                f"Install 'Android SDK Command-line Tools (latest)' via the SDK Manager.")
    return None


class ListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    installed_only: bool = Field(default=True, description="If true, only list installed packages (fast). If false, also fetch the available catalog (network).")
    timeout: int = Field(default=120, ge=10, le=600)


class InstallInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    packages: List[str] = Field(..., description="SDK package paths, e.g. ['platforms;android-35', 'build-tools;35.0.0', 'system-images;android-34;google_apis;x86_64'].", min_length=1, max_length=20)
    confirm: bool = Field(default=False, description="Must be true. Installs download data and accept licenses.")
    timeout: int = Field(default=1800, ge=30, le=3600)


class LicensesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    confirm: bool = Field(default=False, description="Must be true to accept all pending SDK licenses.")
    timeout: int = Field(default=180, ge=10, le=600)


@mcp.tool(name="as_sdk_list", annotations={
    "title": "List SDK packages", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": True})
async def as_sdk_list(params: ListInput) -> str:
    """List installed (and optionally available) Android SDK packages.

    Args:
        params (ListInput): installed_only (bool), timeout.

    Returns:
        str: JSON with 'installed' and 'available' lists of
        {path, version, description}, plus the raw output.
    """
    guard = _require_sdkmanager()
    if guard:
        return err(guard)
    args = [str(CONFIG.sdkmanager), "--list_installed"] if params.installed_only else [str(CONFIG.sdkmanager), "--list"]
    r = await run(args, timeout=params.timeout)
    if not r.ok and not r.stdout:
        return err("sdkmanager --list failed.", command=r.to_dict())
    parsed = parse_sdkmanager_list(r.stdout)
    return ok(f"{len(parsed['installed'])} installed, {len(parsed['available'])} available packages.",
              installed=parsed["installed"], available=parsed["available"], command=r.to_dict())


@mcp.tool(name="as_sdk_install", annotations={
    "title": "Install SDK packages", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": True})
async def as_sdk_install(params: InstallInput) -> str:
    """Install one or more Android SDK packages (downloads + accepts licenses).

    Args:
        params (InstallInput): packages (list of sdkmanager paths), confirm, timeout.

    Returns:
        str: JSON with the install command output, or confirmation_required.
    """
    guard = _require_sdkmanager()
    if guard:
        return err(guard)
    if not params.confirm:
        return needs_confirmation("install SDK packages", {"packages": params.packages})
    args = [str(CONFIG.sdkmanager)] + params.packages
    # Auto-accept license prompts for the packages being installed.
    r = await run(args, timeout=params.timeout, input_text="y\n" * 100)
    return (ok if r.ok else err)(
        f"Installed {len(params.packages)} package(s)." if r.ok else "Install command returned non-zero.",
        packages=params.packages, command=r.to_dict())


@mcp.tool(name="as_sdk_accept_licenses", annotations={
    "title": "Accept SDK licenses", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": True})
async def as_sdk_accept_licenses(params: LicensesInput) -> str:
    """Accept all pending Android SDK licenses (sdkmanager --licenses).

    Args:
        params (LicensesInput): confirm, timeout.

    Returns:
        str: JSON with the command output, or confirmation_required.
    """
    guard = _require_sdkmanager()
    if guard:
        return err(guard)
    if not params.confirm:
        return needs_confirmation("accept all pending SDK licenses", {})
    r = await run([str(CONFIG.sdkmanager), "--licenses"], timeout=params.timeout, input_text="y\n" * 100)
    return (ok if r.ok else err)("Accepted SDK licenses." if r.ok else "License command returned non-zero.", command=r.to_dict())
