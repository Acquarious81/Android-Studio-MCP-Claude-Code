"""APK / AAB analysis tools (apkanalyzer, aapt2, apksigner)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok
from ..parsers import parse_aapt_badging
from ..runner import run
from ..safety import SafetyError, resolve_existing


class ApkInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    apk_path: str = Field(..., description="Path to an .apk file.", min_length=1)


class ApkCompareInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    apk_a: str = Field(..., description="First .apk (baseline).", min_length=1)
    apk_b: str = Field(..., description="Second .apk (to compare).", min_length=1)


def _resolve_apk(path: str) -> tuple[Optional[str], Optional[str]]:
    try:
        p = resolve_existing(path, must_exist=True)
    except SafetyError as exc:
        return None, str(exc)
    if p.suffix.lower() not in (".apk", ".aab"):
        return None, f"Not an .apk/.aab file: {p}"
    return str(p), None


@mcp.tool(name="as_apk_info", annotations={
    "title": "APK summary & manifest", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_apk_info(params: ApkInput) -> str:
    """Summarise an APK: package, version, SDK levels, permissions, label.

    Uses aapt2 badging (most reliable) and falls back to apkanalyzer.

    Args:
        params (ApkInput): apk_path.

    Returns:
        str: JSON with package, version_code/name, min_sdk, target_sdk, label,
        launchable_activity and permissions.
    """
    apk, e = _resolve_apk(params.apk_path)
    if e:
        return err(e)
    aapt2 = CONFIG.build_tool("aapt2.exe")
    if aapt2:
        r = await run([str(aapt2), "dump", "badging", apk], timeout=60)
        if r.ok and r.stdout:
            info = parse_aapt_badging(r.stdout)
            return ok(f"APK {info.get('package')} v{info.get('version_name')}.", **info, source="aapt2")
    # Fallback to apkanalyzer summary
    if CONFIG.apkanalyzer and CONFIG.apkanalyzer.exists():
        r = await run([str(CONFIG.apkanalyzer), "apk", "summary", apk], timeout=60)
        if r.ok:
            return ok("APK summary (apkanalyzer).", summary=r.stdout.strip(), source="apkanalyzer")
    return err("Neither aapt2 nor apkanalyzer produced output. Check build-tools install.")


@mcp.tool(name="as_apk_methods", annotations={
    "title": "APK method/dex counts", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_apk_methods(params: ApkInput) -> str:
    """Report dex method counts and the largest packages (apkanalyzer dex).

    Args:
        params (ApkInput): apk_path.

    Returns:
        str: JSON with the reference method count and per-package breakdown text.
    """
    apk, e = _resolve_apk(params.apk_path)
    if e:
        return err(e)
    if not CONFIG.apkanalyzer or not CONFIG.apkanalyzer.exists():
        return err("apkanalyzer not found in cmdline-tools.")
    refs = await run([str(CONFIG.apkanalyzer), "dex", "references", apk], timeout=60)
    pkgs = await run([str(CONFIG.apkanalyzer), "dex", "packages", apk], timeout=90)
    return ok("Dex analysis complete.",
              references=refs.stdout.strip(),
              packages=(pkgs.stdout[:8000] if pkgs.ok else pkgs.stderr.strip()))


@mcp.tool(name="as_apk_filesize", annotations={
    "title": "APK file/download size", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_apk_filesize(params: ApkInput) -> str:
    """Report the on-disk and estimated download size of an APK.

    Args:
        params (ApkInput): apk_path.

    Returns:
        str: JSON with file_size and download_size (bytes).
    """
    apk, e = _resolve_apk(params.apk_path)
    if e:
        return err(e)
    if not CONFIG.apkanalyzer or not CONFIG.apkanalyzer.exists():
        return err("apkanalyzer not found in cmdline-tools.")
    fs = await run([str(CONFIG.apkanalyzer), "apk", "file-size", apk], timeout=40)
    ds = await run([str(CONFIG.apkanalyzer), "apk", "download-size", apk], timeout=40)
    return ok("APK size analysis.",
              file_size=fs.stdout.strip() if fs.ok else None,
              download_size=ds.stdout.strip() if ds.ok else None)


@mcp.tool(name="as_apk_verify_signature", annotations={
    "title": "Verify APK signature", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_apk_verify_signature(params: ApkInput) -> str:
    """Verify an APK's signature and print the signing certificates (apksigner).

    Args:
        params (ApkInput): apk_path.

    Returns:
        str: JSON with verified (bool) and the certificate details.
    """
    apk, e = _resolve_apk(params.apk_path)
    if e:
        return err(e)
    apksigner = CONFIG.build_tool("apksigner.bat")
    if not apksigner:
        return err("apksigner not found in build-tools.")
    r = await run([str(apksigner), "verify", "--print-certs", "-v", apk], timeout=60)
    verified = r.ok and "Verified" in (r.stdout + r.stderr)
    return ok("Signature verification done.", verified=verified, output=r.stdout.strip() or r.stderr.strip())


@mcp.tool(name="as_apk_compare", annotations={
    "title": "Compare two APKs", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_apk_compare(params: ApkCompareInput) -> str:
    """Compare the file sizes of two APKs (apkanalyzer apk compare).

    Args:
        params (ApkCompareInput): apk_a (baseline), apk_b.

    Returns:
        str: JSON with the comparison table.
    """
    a, ea = _resolve_apk(params.apk_a)
    b, eb = _resolve_apk(params.apk_b)
    if ea or eb:
        return err(ea or eb)
    if not CONFIG.apkanalyzer or not CONFIG.apkanalyzer.exists():
        return err("apkanalyzer not found in cmdline-tools.")
    r = await run([str(CONFIG.apkanalyzer), "apk", "compare", a, b], timeout=90)
    return (ok if r.ok else err)("APK comparison complete." if r.ok else "compare returned non-zero.",
                                 comparison=r.stdout.strip() or r.stderr.strip())
