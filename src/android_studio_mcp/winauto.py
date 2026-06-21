"""Native Windows UI automation helpers (PowerShell-backed).

Used as the last-resort fallback for actions that have no CLI/HTTP path. These
helpers drive the Android Studio window via Win32 (focus) and SendKeys, and can
capture a screenshot for verification. They are deliberately self-contained
(no extra Python deps and no reliance on the interactive computer-use grant).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .runner import CREATE_NO_WINDOW, build_env
from .logging_setup import LOG

_SENDKEYS_SPECIAL = set("+^%~(){}[]")


def escape_sendkeys(text: str) -> str:
    """Escape characters that are special to .NET SendKeys."""
    return "".join("{" + c + "}" if c in _SENDKEYS_SPECIAL else c for c in text)


def _run_ps(script: str, timeout: float = 60.0) -> dict:
    fd, tmp_path = tempfile.mkstemp(prefix="asmcp-ui-", suffix=".ps1")
    os.close(fd)  # close the OS handle; we write via Path below
    tmp = Path(tmp_path)
    try:
        tmp.write_text(script, encoding="utf-8")
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(tmp)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=build_env(), creationflags=CREATE_NO_WINDOW,
        )
        return {"returncode": proc.returncode, "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip()}
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "PowerShell UI script timed out"}
    except Exception as exc:
        return {"returncode": -2, "stdout": "", "stderr": str(exc)}
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


async def run_ps(script: str, timeout: float = 60.0) -> dict:
    return await asyncio.to_thread(_run_ps, script, timeout)


# ---------------------------------------------------------------------------
# Script builders
# ---------------------------------------------------------------------------

_FOCUS_SNIPPET = r"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool IsIconic(IntPtr h);
}
"@
$p = Get-Process studio64 -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { Write-Output "NO_WINDOW"; exit 3 }
$h = $p.MainWindowHandle
if ([W]::IsIconic($h)) { [W]::ShowWindow($h, 9) | Out-Null }  # SW_RESTORE
[W]::ShowWindow($h, 5) | Out-Null  # SW_SHOW
[W]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 400
Write-Output "FOCUSED $h"
"""


def focus_script() -> str:
    return _FOCUS_SNIPPET


def sendkeys_script(sequence_lines: list[str], focus: bool = True) -> str:
    """Build a script that focuses Studio and sends one or more SendKeys steps.

    Each item in sequence_lines is a tuple-like 'keys|sleep_ms' string, but to
    keep things simple we accept already-encoded SendKeys strings and insert a
    short delay between them.
    """
    parts = ["Add-Type -AssemblyName System.Windows.Forms"]
    if focus:
        parts.append(_FOCUS_SNIPPET)
    for keys in sequence_lines:
        safe = keys.replace("'", "''")
        parts.append(f"[System.Windows.Forms.SendKeys]::SendWait('{safe}')")
        parts.append("Start-Sleep -Milliseconds 600")
    parts.append("Write-Output 'SENT'")
    return "\n".join(parts)


def screenshot_script(out_path: str, window_only: bool = False) -> str:
    out = out_path.replace("'", "''")
    if window_only:
        return r"""
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public struct RECT { public int Left, Top, Right, Bottom; }
public class WS {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
}
"@
$p = Get-Process studio64 -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { Write-Output 'NO_WINDOW'; exit 3 }
$r = New-Object RECT
[WS]::GetWindowRect($p.MainWindowHandle, [ref]$r) | Out-Null
$w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
$bmp.Save('""" + out + r"""', [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output ('SAVED ' + $w + 'x' + $h)
"""
    return r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
$bmp.Save('""" + out + r"""', [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output ('SAVED ' + $b.Width + 'x' + $b.Height)
"""
