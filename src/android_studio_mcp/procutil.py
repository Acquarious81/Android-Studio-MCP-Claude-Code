"""Windows process helpers (detection + graceful/forced termination)."""

from __future__ import annotations

import asyncio
import subprocess
from typing import Optional

from .runner import CREATE_NO_WINDOW
from .logging_setup import LOG

STUDIO_IMAGE = "studio64.exe"
EMULATOR_IMAGE = "qemu-system-x86_64.exe"  # emulator backend process


def _tasklist_sync(image: str) -> list[dict]:
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:  # pragma: no cover
        LOG.warning("tasklist failed: %s", exc)
        return []
    rows = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or not line.startswith('"'):
            continue
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) >= 2 and cols[0].lower() == image.lower():
            rows.append({"image": cols[0], "pid": cols[1],
                         "session": cols[2] if len(cols) > 2 else None,
                         "mem": cols[-1] if len(cols) > 4 else None})
    return rows


async def list_processes(image: str) -> list[dict]:
    return await asyncio.to_thread(_tasklist_sync, image)


async def is_studio_running() -> bool:
    return len(await list_processes(STUDIO_IMAGE)) > 0


def _taskkill_sync(image: Optional[str], pid: Optional[str], force: bool) -> dict:
    args = ["taskkill"]
    if force:
        args.append("/F")
    if pid:
        args += ["/PID", str(pid)]
    elif image:
        args += ["/IM", image]
    args.append("/T")  # also terminate child processes
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30, creationflags=CREATE_NO_WINDOW)
        return {"returncode": proc.returncode, "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip(), "command": " ".join(args)}
    except Exception as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc), "command": " ".join(args)}


async def kill(image: Optional[str] = None, pid: Optional[str] = None, force: bool = False) -> dict:
    return await asyncio.to_thread(_taskkill_sync, image, pid, force)
