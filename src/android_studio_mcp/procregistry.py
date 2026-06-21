"""In-memory registry for long-running child processes (logcat watch, etc.).

The MCP tools are otherwise stateless; this gives a few tools the ability to
start a background process (e.g. streaming `adb logcat` to a file), poll its
output incrementally, and stop it later — without blocking a tool call.

Processes write their output to a file under the user data dir so output
survives across polls and the server only holds a handle + offset.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import CONFIG
from .logging_setup import LOG
from .runner import CREATE_NO_WINDOW, CREATE_NEW_PROCESS_GROUP, build_env

_LOCK = threading.Lock()
_REGISTRY: dict[str, "ManagedProc"] = {}
_COUNTER = 0


def _watch_dir() -> Path:
    d = CONFIG.log_dir / "watch"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class ManagedProc:
    id: str
    kind: str
    args: list[str]
    out_path: Path
    popen: subprocess.Popen
    file_handle: object = field(default=None, repr=False)
    label: Optional[str] = None
    serial: Optional[str] = None

    def is_running(self) -> bool:
        return self.popen.poll() is None

    def info(self) -> dict:
        size = self.out_path.stat().st_size if self.out_path.exists() else 0
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "serial": self.serial,
            "running": self.is_running(),
            "pid": self.popen.pid,
            "returncode": self.popen.poll(),
            "output_file": str(self.out_path),
            "output_bytes": size,
            "command": " ".join(self.args),
        }


def start(kind: str, args: list[str], label: Optional[str] = None,
          serial: Optional[str] = None, env_extra: Optional[dict] = None) -> ManagedProc:
    """Spawn a background process writing stdout/stderr to a file; register it."""
    global _COUNTER
    with _LOCK:
        _COUNTER += 1
        pid_id = f"{kind}-{_COUNTER}"
    out_path = _watch_dir() / f"{pid_id}.log"
    fh = open(out_path, "w", encoding="utf-8", errors="replace")
    popen = subprocess.Popen(
        [str(a) for a in args], stdout=fh, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, env=build_env(env_extra),
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
    )
    mp = ManagedProc(id=pid_id, kind=kind, args=[str(a) for a in args],
                     out_path=out_path, popen=popen, file_handle=fh,
                     label=label, serial=serial)
    with _LOCK:
        _REGISTRY[pid_id] = mp
    LOG.info("procregistry start %s pid=%s: %s", pid_id, popen.pid, " ".join(mp.args))
    return mp


def get(proc_id: str) -> Optional[ManagedProc]:
    with _LOCK:
        return _REGISTRY.get(proc_id)


def read(proc_id: str, since_offset: int = 0, max_bytes: int = 200_000) -> dict:
    """Read output from a managed process starting at a byte offset."""
    mp = get(proc_id)
    if not mp:
        return {"error": f"unknown process id: {proc_id}"}
    if not mp.out_path.exists():
        return {"id": proc_id, "running": mp.is_running(), "offset": 0, "text": ""}
    size = mp.out_path.stat().st_size
    start_at = max(0, min(since_offset, size))
    with open(mp.out_path, "r", encoding="utf-8", errors="replace") as fh:
        fh.seek(start_at)
        text = fh.read(max_bytes)
    new_offset = start_at + len(text.encode("utf-8", "replace"))
    return {
        "id": proc_id, "running": mp.is_running(), "kind": mp.kind,
        "offset": new_offset, "total_bytes": size, "truncated": (size - start_at) > max_bytes,
        "text": text,
    }


def stop(proc_id: str) -> dict:
    mp = get(proc_id)
    if not mp:
        return {"error": f"unknown process id: {proc_id}"}
    try:
        if mp.is_running():
            mp.popen.terminate()
            try:
                mp.popen.wait(timeout=8)
            except Exception:
                mp.popen.kill()
    except Exception as exc:
        LOG.warning("procregistry stop %s error: %s", proc_id, exc)
    finally:
        try:
            if mp.file_handle:
                mp.file_handle.close()
        except Exception:
            pass
    return mp.info()


def list_all() -> list[dict]:
    with _LOCK:
        procs = list(_REGISTRY.values())
    return [mp.info() for mp in procs]


def stop_all() -> int:
    with _LOCK:
        ids = list(_REGISTRY.keys())
    for pid_id in ids:
        stop(pid_id)
    return len(ids)
