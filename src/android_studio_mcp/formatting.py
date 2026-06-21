"""Response envelopes and formatting helpers shared by every tool.

All tools return a JSON string. The envelope always contains a ``status`` field
("ok" | "error" | "confirmation_required") and a human-readable ``summary`` so
the output is useful both to the agent (structured) and to a person (summary).
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Optional

from .runner import CommandResult


class ResponseFormat(str, Enum):
    """Output format for tools that can emit human or machine output."""
    MARKDOWN = "markdown"
    JSON = "json"


def _dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def ok(summary: str, **data: Any) -> str:
    payload = {"status": "ok", "summary": summary}
    payload.update(data)
    return _dumps(payload)


def err(message: str, **data: Any) -> str:
    payload = {"status": "error", "summary": message, "error": message}
    payload.update(data)
    return _dumps(payload)


def needs_confirmation(action: str, would_do: dict) -> str:
    return _dumps({
        "status": "confirmation_required",
        "summary": f"Confirmation required for: {action}. Re-call with confirm=true.",
        "action": action,
        "would_do": would_do,
    })


def from_command(
    result: CommandResult,
    success_summary: str,
    failure_summary: Optional[str] = None,
    **extra: Any,
) -> str:
    """Build a standard envelope from a CommandResult."""
    if result.ok:
        return ok(success_summary, command=result.to_dict(), **extra)
    summary = failure_summary or f"Command failed (rc={result.returncode}, timed_out={result.timed_out})."
    return err(summary, command=result.to_dict(), **extra)


def raw(obj: Any) -> str:
    """Serialize an arbitrary object as the tool response."""
    return _dumps(obj)
