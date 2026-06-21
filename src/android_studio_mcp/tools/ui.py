"""Native UI-automation fallback tools (focus, send keys, invoke action, screenshot).

Use these only when there is no CLI/HTTP/MCP path for an action. Every mutating
UI action requires confirm=true and returns a verification screenshot path so
the agent can check the result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok, needs_confirmation
from .. import procutil
from .. import winauto
from ..safety import SafetyError, require_workspace


def _default_shot_path(stem: str) -> Path:
    return CONFIG.log_dir / f"{stem}.png"


class ScreenshotInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    out_path: Optional[str] = Field(default=None, description="Where to save the PNG (workspace root). Defaults to the server log dir.")
    window_only: bool = Field(default=True, description="Capture only the Android Studio window (true) or the whole virtual screen (false).")


class SendKeysInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    keys: str = Field(..., description="A .NET SendKeys sequence, e.g. '^s' (Ctrl+S), '^+a' (Ctrl+Shift+A), '{F6}'. See SendKeys docs.", min_length=1)
    confirm: bool = Field(default=False, description="Must be true; sends real keystrokes to the focused IDE window.")
    screenshot_after: bool = Field(default=True, description="Capture a verification screenshot after sending.")


class InvokeActionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    action: str = Field(..., description="IDE action name as shown in Find Action, e.g. 'Sync Project with Gradle Files', 'Rebuild Project', 'Make Project'.", min_length=1)
    confirm: bool = Field(default=False, description="Must be true; drives the IDE via Find Action (Ctrl+Shift+A).")
    screenshot_after: bool = Field(default=True, description="Capture a verification screenshot after invoking.")


@mcp.tool(name="as_ui_screenshot", annotations={
    "title": "Screenshot the IDE/desktop", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_ui_screenshot(params: ScreenshotInput) -> str:
    """Capture a screenshot of the Android Studio window (or full screen).

    Args:
        params (ScreenshotInput): out_path (optional), window_only (bool).

    Returns:
        str: JSON with 'saved_to' and the captured dimensions.
    """
    if params.out_path:
        try:
            out = require_workspace(params.out_path, must_exist=False)
        except SafetyError as exc:
            return err(str(exc))
    else:
        out = _default_shot_path("studio-screenshot")
    out.parent.mkdir(parents=True, exist_ok=True)
    res = await winauto.run_ps(winauto.screenshot_script(str(out), window_only=params.window_only), timeout=40)
    if res["returncode"] != 0 or not out.exists():
        return err("Screenshot failed.", detail=res)
    return ok(f"Saved screenshot to {out}.", saved_to=str(out), info=res["stdout"], bytes=out.stat().st_size)


@mcp.tool(name="as_ui_focus", annotations={
    "title": "Focus the IDE window", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": True})
async def as_ui_focus() -> str:
    """Bring the Android Studio window to the foreground.

    Returns:
        str: JSON indicating whether a Studio window was found and focused.
    """
    if not await procutil.is_studio_running():
        return err("Android Studio is not running; launch it with as_launch first.")
    res = await winauto.run_ps(winauto.focus_script(), timeout=20)
    focused = "FOCUSED" in res.get("stdout", "")
    return (ok if focused else err)("Focused Android Studio." if focused else "Could not focus a Studio window.", detail=res)


@mcp.tool(name="as_ui_send_keys", annotations={
    "title": "Send keystrokes to the IDE", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": False, "openWorldHint": True})
async def as_ui_send_keys(params: SendKeysInput) -> str:
    """Focus Android Studio and send a raw SendKeys sequence. Requires confirm=true.

    This is a generic, lower-reliability fallback. Prefer the CLI/Gradle/ADB
    tools. After sending, a screenshot is captured for verification.

    Args:
        params (SendKeysInput): keys (SendKeys string), confirm, screenshot_after.

    Returns:
        str: JSON with the send result and a verification screenshot path.
    """
    if not params.confirm:
        return needs_confirmation("send keystrokes to the IDE window", {"keys": params.keys})
    if not await procutil.is_studio_running():
        return err("Android Studio is not running; launch it with as_launch first.")
    res = await winauto.run_ps(winauto.sendkeys_script([params.keys]), timeout=30)
    shot = None
    if params.screenshot_after:
        out = _default_shot_path("after-sendkeys")
        s = await winauto.run_ps(winauto.screenshot_script(str(out), window_only=True), timeout=40)
        shot = str(out) if out.exists() else None
    sent = "SENT" in res.get("stdout", "")
    return (ok if sent else err)("Sent keystrokes." if sent else "SendKeys reported a problem.",
                                 detail=res, screenshot=shot)


@mcp.tool(name="as_ui_invoke_action", annotations={
    "title": "Invoke an IDE action by name", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": False, "openWorldHint": True})
async def as_ui_invoke_action(params: InvokeActionInput) -> str:
    """Invoke any IDE action via Find Action (Ctrl+Shift+A). Requires confirm=true.

    This opens Find Action, types the action name and presses Enter — giving
    access to essentially any IDE command (e.g. 'Sync Project with Gradle Files',
    'Rebuild Project', 'Reformat Code'). It is a best-effort UI path; verify the
    result with the returned screenshot. Prefer dedicated tools where they exist.

    Args:
        params (InvokeActionInput): action (name), confirm, screenshot_after.

    Returns:
        str: JSON with the result and a verification screenshot path.
    """
    if not params.confirm:
        return needs_confirmation("invoke IDE action via Find Action", {"action": params.action})
    if not await procutil.is_studio_running():
        return err("Android Studio is not running; launch it with as_launch first.")
    typed = winauto.escape_sendkeys(params.action)
    # Ctrl+Shift+A (Find Action), then the action text, then Enter.
    sequence = ["^+a", typed, "{ENTER}"]
    res = await winauto.run_ps(winauto.sendkeys_script(sequence), timeout=40)
    shot = None
    if params.screenshot_after:
        out = _default_shot_path("after-action")
        await winauto.run_ps(winauto.screenshot_script(str(out), window_only=True), timeout=40)
        shot = str(out) if out.exists() else None
    sent = "SENT" in res.get("stdout", "")
    return (ok if sent else err)(
        f"Invoked action '{params.action}' via Find Action." if sent else "Action invocation reported a problem.",
        action=params.action, detail=res, screenshot=shot,
        note="Verify via the screenshot; Find Action matching is fuzzy.")
