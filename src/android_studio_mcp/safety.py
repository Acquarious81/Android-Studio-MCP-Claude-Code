"""Safety primitives: path allow-listing, confirmation gating, backups.

Design rules enforced here (see README "Safety model"):
    * Write/delete operations must target a path inside an allowed workspace
      root (prevents accidental writes to system locations).
    * Destructive operations require an explicit ``confirm=True`` from the
      caller; otherwise a structured "confirmation required" response is
      returned describing exactly what would happen.
    * Before mutating a file or directory, a timestamped backup is created.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import CONFIG
from .logging_setup import LOG


class SafetyError(Exception):
    """Raised when an operation violates a safety rule."""


def _now_stamp() -> str:
    # Date.now-free deterministic-ish stamp using process clock.
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


def resolve_existing(path: str, must_exist: bool = True) -> Path:
    """Resolve a user-supplied path, optionally requiring existence."""
    if not path or not str(path).strip():
        raise SafetyError("Path is empty.")
    p = Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
    if must_exist and not p.exists():
        raise SafetyError(f"Path does not exist: {p}")
    return p


def is_within_workspace(path: Path) -> bool:
    """True if *path* is inside one of the configured workspace roots."""
    try:
        rp = path.resolve()
    except Exception:
        return False
    for root in CONFIG.workspace_roots:
        try:
            rp.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


def require_workspace(path: str, must_exist: bool = True) -> Path:
    """Resolve *path* and ensure it is inside an allowed workspace root."""
    p = resolve_existing(path, must_exist=must_exist)
    if not is_within_workspace(p):
        roots = ", ".join(str(r) for r in CONFIG.workspace_roots)
        raise SafetyError(
            f"Refusing to operate on '{p}': outside allowed workspace roots ({roots}). "
            f"Set ASMCP_WORKSPACE_ROOTS to extend the allow-list."
        )
    return p


def confirmation_required(action: str, details: dict) -> dict:
    """Standard payload returned when a destructive call lacks confirm=True."""
    return {
        "status": "confirmation_required",
        "action": action,
        "message": (
            f"This is a potentially destructive action ({action}). "
            f"Re-call with confirm=true to proceed."
        ),
        "would_do": details,
    }


def backup_file(path: Path) -> Optional[str]:
    """Copy a single file into the backup dir; return the backup path."""
    if not path.is_file():
        return None
    dest = CONFIG.backup_dir / f"{path.name}.{_now_stamp()}.bak"
    shutil.copy2(path, dest)
    LOG.info("backup_file: %s -> %s", path, dest)
    return str(dest)


# Directories that never belong in a project backup.
_BACKUP_EXCLUDES = {".gradle", "build", ".idea", ".git", "__pycache__", ".kotlin", "captures"}


def backup_dir_zip(path: Path, label: str = "project") -> str:
    """Zip a directory (excluding build artefacts) into the backup dir."""
    if not path.is_dir():
        raise SafetyError(f"Not a directory: {path}")
    dest = CONFIG.backup_dir / f"{label}-{path.name}-{_now_stamp()}.zip"
    count = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(path):
            # prune excluded directories in-place
            dirs[:] = [d for d in dirs if d not in _BACKUP_EXCLUDES]
            for f in files:
                full = Path(root) / f
                try:
                    arc = full.relative_to(path)
                    zf.write(full, arc)
                    count += 1
                except Exception:
                    continue
    LOG.info("backup_dir_zip: %s -> %s (%d files)", path, dest, count)
    return str(dest)
