"""Safe subprocess execution for external tools (Gradle, ADB, sdkmanager, ...).

Responsibilities:
    * Inject a correct environment (JAVA_HOME -> bundled JBR, ANDROID_HOME ->
      SDK, prepend tool dirs to PATH).
    * Run ``.bat``/``.cmd`` scripts correctly on Windows (via ``cmd /c``).
    * Enforce timeouts, capture stdout/stderr, never pop up console windows.
    * Redact secrets (keystore passwords etc.) from captured output and logs.
    * Provide a detached-spawn helper for long-lived processes (emulator,
      the IDE itself, a running app) that must not block the tool call.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .config import CONFIG
from .logging_setup import LOG

# Windows process-creation flags
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

# Nominal post-exit cleanup budget (seconds), shared by the post-kill wait
# and every thread join. Each wait/join gets only the remaining time on a
# monotonic clock; this is a nominal budget, not a real-time guarantee.
_CLEANUP_BUDGET = 5.0

MAX_CAPTURE_CHARS = 24_000  # cap returned stdout/stderr to keep context small

_SECRET_PATTERNS = [
    re.compile(r"(?i)(store?pass(word)?\s*[=:]\s*)(\S+)"),
    re.compile(r"(?i)(key ?pass(word)?\s*[=:]\s*)(\S+)"),
    re.compile(r"(?i)(-P[\w.]*[Pp]ass(word)?=)(\S+)"),
    re.compile(r"(?i)(password\s*[=:]\s*)(\S+)"),
    re.compile(r"(?i)(token\s*[=:]\s*)(\S+)"),
]


def redact(text: Optional[str]) -> str:
    """Mask obvious secrets in tool output before it leaves the process."""
    if not text:
        return ""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(lambda m: m.group(1) + "***REDACTED***", out)
    return out


def _to_text(x) -> str:
    """Normalise stdout/stderr (which may be str, bytes or None) to text."""
    if x is None:
        return ""
    if isinstance(x, bytes):
        return x.decode("utf-8", "replace")
    return x


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_CAPTURE_CHARS:
        return text, False
    head = text[: MAX_CAPTURE_CHARS // 2]
    tail = text[-MAX_CAPTURE_CHARS // 2:]
    return head + "\n...[truncated]...\n" + tail, True


def build_env(extra: Optional[dict] = None) -> dict:
    """Return an environment dict with the Android toolchain wired up."""
    env = os.environ.copy()
    if CONFIG.jbr:
        env["JAVA_HOME"] = str(CONFIG.jbr)
        env["STUDIO_JDK"] = str(CONFIG.jbr)
    if CONFIG.sdk:
        env["ANDROID_HOME"] = str(CONFIG.sdk)
        env["ANDROID_SDK_ROOT"] = str(CONFIG.sdk)
    path_parts = []
    for p in (
        CONFIG.platform_tools,
        CONFIG.cmdline_bin,
        CONFIG.emulator_dir,
        (CONFIG.jbr / "bin") if CONFIG.jbr else None,
        CONFIG.build_tools_dir,
    ):
        if p and Path(p).exists():
            path_parts.append(str(p))
    if path_parts:
        env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH", "")])
    # Gradle is happier with an explicit non-interactive, console-plain setup.
    env.setdefault("GRADLE_OPTS", "-Dorg.gradle.daemon=true")
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def _normalize_args(args: Sequence[str]) -> list[str]:
    """Wrap .bat/.cmd scripts with ``cmd /c`` so CreateProcess can run them."""
    args = [str(a) for a in args]
    if not args:
        return args
    first = args[0].lower()
    if first.endswith(".bat") or first.endswith(".cmd"):
        return ["cmd", "/c"] + args
    return args


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    cwd: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def to_dict(self) -> dict:
        return {
            "command": " ".join(self.args),
            "returncode": self.returncode,
            "ok": self.ok,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "cwd": self.cwd,
        }


def _run_sync(
    args: Sequence[str],
    cwd: Optional[str],
    env: dict,
    timeout: float,
    input_text: Optional[str],
) -> CommandResult:
    """Run args to completion (blocking) with bounded waits.

    stdin: when ``input_text`` is not None (including "") the text is piped
    to the child by a dedicated writer thread and stdin is closed when the
    write completes - the same effect as piping text into a console. If
    the child closes stdin early, the write failure is swallowed: delivery
    of the remaining input is NOT guaranteed (only what the OS already
    buffered may be consumed) and no hang or unhandled exception
    propagates. With ``input_text=None`` (and only then) stdin is
    /dev/null, so a child reading stdin just sees EOF.

    Readers: stdout and stderr are drained concurrently, partial output
    survives; redaction and truncation are unchanged.

    Timeout: only the DIRECT child process is killed. Descendants are NOT
    terminated and may keep the inherited pipe handles open. A single
    monotonic cleanup budget (_CLEANUP_BUDGET) is armed only AFTER the
    exit is observed (normal exit) or right after the kill (timeout
    branch): every cleanup wait/join gets only the remaining time, so the
    cleanup cost is never mixed with the command execution time and this
    call returns at most about timeout + _CLEANUP_BUDGET later. Threads
    that never see EOF (a descendant holding a pipe open) may still be
    alive when this function returns: they are daemon threads that exit by
    themselves once the pipe is finally closed; incomplete collection is
    flagged via ``truncated`` and logged, which is a residual resource -
    NOT a "complete cleanup". If the post-kill wait also expires, reaping
    of the child is NOT guaranteed.
    """
    norm = _normalize_args(args)
    stdin_mode = subprocess.PIPE if input_text is not None else subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            norm,
            cwd=cwd,
            env=env,
            stdin=stdin_mode,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            args=list(norm), returncode=-2, stdout="",
            stderr=f"Executable not found: {exc}", timed_out=False,
            truncated=False, cwd=cwd,
        )

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def _pump(stream, sink):
        try:
            for line in iter(stream.readline, ""):
                sink.append(line)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _feed_stdin(stream, text):
        try:
            if text:
                stream.write(text)
                stream.flush()
        except (OSError, ValueError):
            # Child closed stdin early or the pipe is broken: whatever the
            # OS buffered may have been consumed, the rest is not sent.
            # Never propagate to the caller.
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    out_thread = threading.Thread(
        target=_pump, name="runner-stdout-pump", args=(proc.stdout, stdout_parts))
    err_thread = threading.Thread(
        target=_pump, name="runner-stderr-pump", args=(proc.stderr, stderr_parts))
    out_thread.daemon = err_thread.daemon = True
    out_thread.start()
    err_thread.start()

    stdin_thread: Optional[threading.Thread] = None
    if input_text is not None:
        stdin_thread = threading.Thread(
            target=_feed_stdin, name="runner-stdin-writer",
            args=(proc.stdin, input_text))
        stdin_thread.daemon = True
        stdin_thread.start()

    # Cleanup deadline: armed ONLY after the exit is observed (branches
    # below) or right after the kill, so the shared _CLEANUP_BUDGET covers
    # the cleanup steps and never the command execution time. Held in a
    # one-element list because it is assigned from both branches.
    _deadline: list[float] = []

    def _left() -> float:
        if not _deadline:
            return 0.0
        return max(0.0, _deadline[0] - time.monotonic())

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()  # direct child only; descendants are NOT killed
        except Exception:
            pass
        _deadline[:] = [time.monotonic() + _CLEANUP_BUDGET]
        reaped = True
        try:
            proc.wait(timeout=_left())
        except subprocess.TimeoutExpired:
            reaped = False
            LOG.warning("run: child not confirmed reaped after kill deadline")
        out_thread.join(timeout=_left())
        err_thread.join(timeout=_left())
        if stdin_thread is not None:
            stdin_thread.join(timeout=_left())
        alive = [t.name for t in (out_thread, err_thread, stdin_thread)
                 if t is not None and t.is_alive()]
        if alive:
            LOG.warning(
                "run: timeout - threads still blocked on inherited pipe"
                " handles after kill: %s (partial output returned)", alive)
        out, t1 = _truncate(redact("".join(stdout_parts)))
        err, t2 = _truncate(redact("".join(stderr_parts)))
        return CommandResult(
            args=list(norm), returncode=-1, stdout=out, stderr=err,
            timed_out=True, truncated=t1 or t2 or bool(alive), cwd=cwd,
        )

    # Normal path: the cleanup budget starts NOW, after the exit was
    # observed - not during the command execution.
    _deadline[:] = [time.monotonic() + _CLEANUP_BUDGET]
    out_thread.join(timeout=_left())
    err_thread.join(timeout=_left())
    if stdin_thread is not None:
        stdin_thread.join(timeout=_left())
    alive = [t.name for t in (out_thread, err_thread, stdin_thread)
             if t is not None and t.is_alive()]
    if alive:
        LOG.warning(
            "run: child exited but threads still blocked on inherited pipe"
            " handles: %s (partial output returned)", alive)
    out, t1 = _truncate(redact("".join(stdout_parts)))
    err, t2 = _truncate(redact("".join(stderr_parts)))
    return CommandResult(
        args=list(norm), returncode=proc.returncode, stdout=out, stderr=err,
        timed_out=False, truncated=t1 or t2 or bool(alive), cwd=cwd,
    )


async def run(
    args: Sequence[str],
    cwd: Optional[str] = None,
    timeout: float = 120.0,
    input_text: Optional[str] = None,
    env_extra: Optional[dict] = None,
) -> CommandResult:
    """Run a command to completion off the event loop and return its result."""
    env = build_env(env_extra)
    LOG.info("run: %s (cwd=%s, timeout=%ss)", " ".join(str(a) for a in args), cwd, timeout)
    result = await asyncio.to_thread(_run_sync, args, cwd, env, timeout, input_text)
    if not result.ok:
        LOG.warning("command non-zero/timeout (rc=%s, timed_out=%s)", result.returncode, result.timed_out)
    return result


def spawn_detached(
    args: Sequence[str],
    cwd: Optional[str] = None,
    env_extra: Optional[dict] = None,
) -> int:
    """Start a long-lived process detached from this server; return its PID."""
    env = build_env(env_extra)
    norm = _normalize_args(args)
    LOG.info("spawn_detached: %s (cwd=%s)", " ".join(norm), cwd)
    proc = subprocess.Popen(
        norm,
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
    )
    return proc.pid
