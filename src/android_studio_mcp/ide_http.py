"""Client for the IntelliJ/Android Studio built-in web server (port 63342).

Only usable while the IDE is running. The built-in server exposes a small REST
surface; the most useful endpoints are ``/api/about`` (liveness + build) and
``/api/file`` (open a file at a line in the running editor). The first call to
a mutating endpoint may trigger a one-time "accept connection" prompt inside the
IDE; this is by design and cannot be bypassed programmatically.
"""

from __future__ import annotations

from typing import Optional

import httpx

from .config import CONFIG
from .logging_setup import LOG


async def _get(path: str, params: Optional[dict] = None, timeout: float = 5.0) -> tuple[int, str]:
    url = f"{CONFIG.builtin_server_url}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, params=params)
        return resp.status_code, resp.text


async def about() -> Optional[dict]:
    """Return the IDE 'about' payload, or None if the server is not reachable."""
    try:
        code, text = await _get("/api/about", timeout=4.0)
        if code == 200:
            try:
                import json
                return json.loads(text)
            except Exception:
                return {"raw": text[:500]}
    except Exception as exc:
        LOG.info("built-in server not reachable: %s", exc)
    return None


async def is_up() -> bool:
    return (await about()) is not None


async def open_file(file_path: str, line: Optional[int] = None,
                    column: Optional[int] = None) -> dict:
    """Ask the running IDE to open *file_path* at an optional line/column."""
    params: dict = {"file": file_path}
    if line is not None:
        params["line"] = line
    if column is not None:
        params["column"] = column
    try:
        code, text = await _get("/api/file", params=params, timeout=6.0)
        return {"http_status": code, "response": text[:500],
                "opened": code in (200, 204)}
    except Exception as exc:
        return {"http_status": None, "error": str(exc), "opened": False}
