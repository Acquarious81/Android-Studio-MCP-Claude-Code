"""Long-running process tools: streaming logcat watch + generic process control.

Backed by procregistry: a background process writes output to a file and the
agent polls it incrementally with a byte offset (no blocking, no streaming MCP).
"""

from __future__ import annotations

import shlex
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok
from .. import procregistry
from ..runner import run
from .device import _base, _resolve_single_serial, _require_adb


class WatchStartInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    serial: Optional[str] = Field(default=None, description="Device serial. Optional if exactly one device is connected.")
    filter_spec: Optional[str] = Field(default=None, description="Optional logcat filter, e.g. 'MyTag:D *:S'.")
    clear_first: bool = Field(default=True, description="Clear the log buffer before starting the watch.")


class WatchReadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    watch_id: str = Field(..., description="Watch id from as_logcat_watch_start (e.g. 'logcat-1').", min_length=1)
    since_offset: int = Field(default=0, description="Byte offset to read from (use the 'offset' returned by the previous read to get only new lines).", ge=0)
    grep: Optional[str] = Field(default=None, description="Only return lines containing this substring (case-insensitive).")


class ProcIdInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    proc_id: str = Field(..., description="Process id from as_proc_list / a watch id.", min_length=1)


@mcp.tool(name="as_logcat_watch_start", annotations={
    "title": "Start a streaming logcat watch", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_logcat_watch_start(params: WatchStartInput) -> str:
    """Start a background logcat capture you can poll incrementally.

    Returns a watch_id; read new lines with as_logcat_watch_read (pass the
    returned offset back each time) and stop it with as_logcat_watch_stop.

    Args:
        params (WatchStartInput): serial, filter_spec, clear_first.

    Returns:
        str: JSON with watch_id and the initial offset (0).
    """
    guard = _require_adb()
    if guard:
        return err(guard)
    serial, e = await _resolve_single_serial(params.serial)
    if e:
        return err(e)
    if params.clear_first:
        await run(_base(serial) + ["logcat", "-c"], timeout=20)
    args = _base(serial) + ["logcat", "-v", "time"]
    if params.filter_spec:
        try:
            args += shlex.split(params.filter_spec)
        except ValueError:
            args += params.filter_spec.split()
    mp = procregistry.start("logcat", args, serial=serial,
                            label=f"logcat {params.filter_spec or 'all'}")
    return ok(f"Started logcat watch {mp.id} on {serial}.", watch_id=mp.id, offset=0,
              note="Poll with as_logcat_watch_read(watch_id, since_offset); stop with as_logcat_watch_stop.")


@mcp.tool(name="as_logcat_watch_read", annotations={
    "title": "Read new logcat-watch output", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": True})
async def as_logcat_watch_read(params: WatchReadInput) -> str:
    """Read new output from a running logcat watch since a byte offset.

    Args:
        params (WatchReadInput): watch_id, since_offset, grep.

    Returns:
        str: JSON with 'text' (new lines), 'offset' (pass to the next read),
        'running' and 'total_bytes'.
    """
    res = procregistry.read(params.watch_id, since_offset=params.since_offset)
    if "error" in res:
        return err(res["error"])
    if params.grep and res.get("text"):
        g = params.grep.lower()
        res["text"] = "\n".join(l for l in res["text"].splitlines() if g in l.lower())
    return ok(f"Read {len(res.get('text','').splitlines())} new line(s) from {params.watch_id}.", **res)


@mcp.tool(name="as_logcat_watch_stop", annotations={
    "title": "Stop a logcat watch", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": True})
async def as_logcat_watch_stop(params: ProcIdInput) -> str:
    """Stop a running logcat watch (or any managed process) by id.

    Args:
        params (ProcIdInput): proc_id (the watch_id).

    Returns:
        str: JSON with the final process info.
    """
    res = procregistry.stop(params.proc_id)
    if "error" in res:
        return err(res["error"])
    return ok(f"Stopped {params.proc_id}.", **res)


@mcp.tool(name="as_proc_list", annotations={
    "title": "List managed background processes", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_proc_list() -> str:
    """List all background processes started by this server (watches etc.).

    Returns:
        str: JSON with 'processes': list of {id, kind, running, pid, output_file, ...}.
    """
    procs = procregistry.list_all()
    return ok(f"{len(procs)} managed process(es).", processes=procs)


@mcp.tool(name="as_proc_stop", annotations={
    "title": "Stop a managed process", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": True, "openWorldHint": False})
async def as_proc_stop(params: ProcIdInput) -> str:
    """Stop a managed background process by id.

    Args:
        params (ProcIdInput): proc_id.

    Returns:
        str: JSON with the final process info.
    """
    res = procregistry.stop(params.proc_id)
    if "error" in res:
        return err(res["error"])
    return ok(f"Stopped {params.proc_id}.", **res)
