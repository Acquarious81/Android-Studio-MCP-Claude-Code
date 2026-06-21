"""Composite 'Run on device' tool: build -> install -> launch -> logs -> screenshot.

This mirrors Android Studio's Run button as a single MCP call, returning a
per-step report so the agent can see exactly where it succeeded or failed.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok
from ..parsers import parse_aapt_badging
from ..runner import run
from ..safety import SafetyError, require_workspace
from .device import _base, _resolve_single_serial
from .gradle import GradleAssembleInput, as_gradle_assemble


class RunAppInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_dir: str = Field(..., description="Android project root containing gradlew.bat.", min_length=1)
    variant: str = Field(default="Debug", description="Build variant to assemble/run, e.g. 'Debug'.")
    module: Optional[str] = Field(default=None, description="Optional module path, e.g. ':app'.")
    serial: Optional[str] = Field(default=None, description="Target device serial. Optional if exactly one device is connected.")
    install: bool = Field(default=True, description="Install the built APK before launching.")
    launch: bool = Field(default=True, description="Launch the app's launcher activity after install.")
    capture_logcat_lines: int = Field(default=120, ge=0, le=2000, description="How many recent app log lines to capture after launch (0 = skip).")
    screenshot: bool = Field(default=False, description="Capture a device screenshot after launch.")
    timeout: int = Field(default=900, ge=30, le=3600, description="Max seconds for the build phase.")


async def _apk_meta(apk: str) -> dict:
    aapt2 = CONFIG.build_tool("aapt2.exe")
    if not aapt2:
        return {}
    r = await run([str(aapt2), "dump", "badging", apk], timeout=60)
    return parse_aapt_badging(r.stdout) if r.ok else {}


def _pick_apk(apks: list[str], variant: str) -> Optional[str]:
    if not apks:
        return None
    v = variant.lower()
    # prefer the variant, avoid unsigned/unaligned intermediates
    preferred = [a for a in apks if v in a.lower() and "unsigned" not in a.lower()]
    return (preferred or apks)[0]


@mcp.tool(name="as_run_app", annotations={
    "title": "Build, install & run app on device", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
async def as_run_app(params: RunAppInput) -> str:
    """Build, install and launch an app on a device/emulator, then capture logs.

    One-shot equivalent of Android Studio's Run button:
      1. assemble<Variant>  2. install the APK  3. launch the launcher activity
      4. capture the app's recent logcat (and detect crashes)  5. optional screenshot.

    Args:
        params (RunAppInput): project_dir, variant, module, serial, install,
            launch, capture_logcat_lines, screenshot, timeout.

    Returns:
        str: JSON with a 'steps' object (build/install/launch/logcat/screenshot),
        the apk path, package, and 'crash_detected' (bool).
    """
    steps: dict = {}

    # 1. Build
    build_raw = await as_gradle_assemble(GradleAssembleInput(
        project_dir=params.project_dir, variant=params.variant,
        module=params.module, timeout=params.timeout))
    build = json.loads(build_raw)
    steps["build"] = {"status": build.get("status"), "summary": build.get("summary"),
                      "remediation": build.get("remediation")}
    if build.get("status") != "ok":
        return err("Build failed; aborting run.", steps=steps, build=build)

    apk = _pick_apk((build.get("outputs") or {}).get("apk") or [], params.variant)
    if not apk:
        return err("Build succeeded but no APK was found.", steps=steps)
    steps["apk"] = apk

    meta = await _apk_meta(apk)
    package = meta.get("package")
    activity = meta.get("launchable_activity")
    steps["package"] = package

    # 2. Resolve device
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e, steps=steps, hint="Start an emulator with as_avd_start(wait_boot=true).")
    steps["serial"] = serial

    # 3. Install
    if params.install:
        ri = await run(_base(serial) + ["install", "-r", "-t", apk], timeout=300)
        success = ri.ok and "Success" in (ri.stdout + ri.stderr)
        steps["install"] = {"ok": success, "output": (ri.stdout or ri.stderr)[:600]}
        if not success:
            return err("Install failed.", steps=steps)

    # 4. Launch
    if params.launch and package:
        if activity:
            comp = activity if "/" in activity else f"{package}/{activity}"
            rl = await run(_base(serial) + ["shell", "am", "start", "-n", comp], timeout=40)
        else:
            rl = await run(_base(serial) + ["shell", "monkey", "-p", package, "-c",
                                            "android.intent.category.LAUNCHER", "1"], timeout=40)
        steps["launch"] = {"ok": rl.ok, "output": (rl.stdout or rl.stderr)[:400]}
        await asyncio.sleep(2.5)  # let the app start before sampling logs

    # 5. Logcat (scoped to the app pid when possible) + crash detection
    crash_detected = False
    if params.capture_logcat_lines and package:
        pid = None
        rp = await run(_base(serial) + ["shell", "pidof", package], timeout=15)
        if rp.ok and rp.stdout.strip():
            pid = rp.stdout.strip().split()[0]
        args = _base(serial) + ["logcat", "-d", "-v", "time", "-t", str(params.capture_logcat_lines)]
        if pid:
            args += [f"--pid={pid}"]
        rlog = await run(args, timeout=40)
        log_text = rlog.stdout or ""
        crash_detected = bool(re.search(r"FATAL EXCEPTION|ANR in|signal \d+ \(SIG", log_text))
        steps["logcat"] = {"pid": pid, "lines": len(log_text.splitlines()),
                           "crash_detected": crash_detected, "log": log_text}

    # 6. Screenshot
    if params.screenshot:
        out = CONFIG.log_dir / "run-app-screenshot.png"
        import subprocess
        from ..runner import build_env, CREATE_NO_WINDOW

        def _cap() -> int:
            with open(out, "wb") as fh:
                return subprocess.run(_base(serial) + ["exec-out", "screencap", "-p"],
                                      stdout=fh, stderr=subprocess.DEVNULL, env=build_env(),
                                      timeout=60, creationflags=CREATE_NO_WINDOW).returncode
        rc = await asyncio.to_thread(_cap)
        steps["screenshot"] = str(out) if (rc == 0 and out.exists() and out.stat().st_size) else None

    summary = (f"Ran {package or Path(apk).name} on {serial}: build ok, "
               f"{'installed, ' if params.install else ''}{'launched' if params.launch else 'not launched'}"
               + (" — CRASH detected" if crash_detected else ""))
    return (err if crash_detected else ok)(summary, steps=steps, apk=apk,
                                            package=package, crash_detected=crash_detected)
