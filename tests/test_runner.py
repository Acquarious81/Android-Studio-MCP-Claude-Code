"""Unit tests for the runner's stdin routing and bounded cleanup.

Uses only local ``sys.executable`` child processes (no Android tooling, no
network, no new dependencies). Every test that spawns a possibly-escaping
process registers its PIDs on known tmp_path files (child.pid,
descendant.pid) and, in a finally block, terminates each of them and
verifies the termination separately by PID - not via the runner's
captured stdout and not via generic tasklist scanning.

Thread identity is tracked by OBJECT INSTANCE: the test fixture replaces
threading.Thread with a recording subclass, so the exact Thread objects
created by a specific _run_sync call can be awaited and verified. Names
are kept by the runner for log readability only; they are not used as
identifiers in assertions.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from android_studio_mcp.runner import (
    _CLEANUP_BUDGET as RUNNER_CLEANUP_BUDGET,
    _run_sync, build_env,
)

CREATE_NO_WINDOW = 0x08000000

RUNNER_THREAD_PREFIX = "runner-"

DEADLINE_BUDGET = RUNNER_CLEANUP_BUDGET


# --- helpers ----------------------------------------------------------------

@pytest.fixture
def thread_recorder(monkeypatch):
    """Replace threading.Thread with a subclass recording every instance.

    Returns a function collecting ONLY the objects the runner created
    (name prefix "runner-"), so tests assert identity of the instances of
    the specific call, not name-colliding sets.
    """
    created: list[threading.Thread] = []

    class RecordedThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(threading, "Thread", RecordedThread)

    def runner_threads() -> list[threading.Thread]:
        return [t for t in created if t.name.startswith(RUNNER_THREAD_PREFIX)]

    return runner_threads


def _run(args, *, timeout, input_text=None):
    return _run_sync(args, None, build_env(), timeout, input_text)


def _alive(pid: int) -> bool:
    # Explicit timeout; a tasklist ERROR must never be read as "absent".
    cp = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True, text=True,
        creationflags=CREATE_NO_WINDOW, timeout=10,
    )
    if cp.returncode != 0:
        raise RuntimeError(
            f"tasklist failed rc={cp.returncode}: "
            f"{(cp.stderr or cp.stdout or '').strip()}"
        )
    return str(pid) in (cp.stdout or "")


def _terminate_verified(pid, *, timeout=8.0):
    """Terminate one test process by PID and verify the termination.

    taskkill gets an explicit timeout too; the final assertion IS the
    verification. Raises on failure so callers can COLLECT errors per
    process and report them once, after every attempt.
    """
    if pid is None:
        return
    deadline = time.monotonic() + timeout
    if _alive(pid):
        cp = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                            capture_output=True, text=True,
                            creationflags=CREATE_NO_WINDOW, timeout=10)
        if cp.returncode != 0 and _alive(pid):
            # non-rc0 is tolerated only until the deadline re-check below
            pass
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.1)
    if _alive(pid):  # second chance, direct /F without /T
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, text=True,
                       creationflags=CREATE_NO_WINDOW, timeout=10)
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.1)
    assert not _alive(pid), f"test process {pid} still alive after cleanup"


def _read_pid(path: Path, *, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text.isdigit():
                return int(text)
        time.sleep(0.05)
    pytest.fail(f"PID file not found or unreadable in time: {path}")


def _assert_threads_dead(threads: list[threading.Thread], *, grace=20.0):
    """Verify the termination of the SPECIFIC tracked Thread instances."""
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not any(t.is_alive() for t in threads):
            return
        time.sleep(0.2)
    alive_names = [t.name for t in threads if t.is_alive()]
    pytest.fail(f"tracked threads still alive after cleanup: {alive_names}")


def _test_pids_cleanup(tmp_path: Path, known: dict, failures: list):
    """Cleanup of BOTH test processes with collected errors.

    An error in the parent cleanup never prevents the descendant attempt:
    every process gets its own try/except; reports happen only after all
    attempts, by the caller.
    """
    for fname, key in (("child.pid", "cpid"), ("descendant.pid", "gpid")):
        try:
            pid = known.get(key)
            if pid is None:
                path = tmp_path / fname
                if path.exists():
                    pid = _read_pid(path, timeout=0.2)
            _terminate_verified(pid)
        except Exception as exc:  # noqa: BLE001 - collected, not raised here
            failures.append(f"{key} cleanup: {exc!r}")


# --- stdin routing ----------------------------------------------------------

def test_stdin_multiline_reaches_eof():
    # Exact content: the full echoed payload (including the empty line and
    # trailing newlines), not merely the presence of each line.
    payload = "lineA\nlineB\n\nlineC\n"
    r = _run([sys.executable, "-c",
              "import sys; d = sys.stdin.read();"
              " sys.stdout.write('ECHO<%s>END' % d)"],
             timeout=10, input_text=payload)
    assert r.ok and not r.timed_out and not r.truncated
    assert r.stdout == f"ECHO<{payload}>END"
    assert "lineA" in r.stdout and "lineB" in r.stdout and "lineC" in r.stdout


def test_stdin_empty():
    r = _run([sys.executable, "-c",
              "import sys; d = sys.stdin.read();"
              " print('EMPTY%d' % len(d), flush=True)"],
             timeout=10, input_text="")
    assert r.ok and not r.timed_out and not r.truncated
    assert "EMPTY0" in r.stdout


def test_no_input_text_none():
    # input_text=None -> /dev/null: a child reading stdin sees EOF at once.
    r = _run([sys.executable, "-c",
              "import sys; d = sys.stdin.read();"
              " print('NINPUT%d' % len(d), flush=True)"],
             timeout=10, input_text=None)
    assert r.ok and not r.timed_out and not r.truncated
    assert "NINPUT0" in r.stdout


def test_stdout_and_stderr_together():
    r = _run([sys.executable, "-c",
              "import sys; print('OUTLINE', flush=True);"
              " print('ERRLINE', file=sys.stderr, flush=True)"],
             timeout=10)
    assert r.ok and not r.truncated
    assert "OUTLINE" in r.stdout
    assert "ERRLINE" in r.stderr


def test_child_closes_stdin_early():
    # The child consumes a tiny prefix and exits while the writer thread is
    # still pushing a payload larger than the pipe buffer. The write may
    # fail with a broken pipe: it must be swallowed (no hang, no propagated
    # exception) and the child's output must be returned with its real
    # exit code. Full delivery of the buffered input is NOT guaranteed by
    # design (documented runner limitation).
    start = time.monotonic()
    r = _run([sys.executable, "-c",
              "import sys; d = sys.stdin.read(8); print('GOT:' + d, flush=True)"],
             timeout=15, input_text="x" * 200_000)
    elapsed = time.monotonic() - start
    assert r.ok and not r.timed_out
    assert "GOT:xxxxxxxx" in r.stdout
    assert elapsed < 15.0


def test_timeout_recovers_partial_output():
    child = "print('PARTIAL', flush=True); import time; time.sleep(20)"
    start = time.monotonic()
    r = _run([sys.executable, "-c", child], timeout=1.0)
    elapsed = time.monotonic() - start
    assert r.timed_out and r.returncode == -1
    assert "PARTIAL" in r.stdout
    assert elapsed < 1.0 + DEADLINE_BUDGET + 4.0  # bounded, no hang


# --- budget accounting (execution vs cleanup) -------------------------------

def test_cleanup_budget_starts_after_normal_exit(monkeypatch):
    """Semantic check, no long waits and no fragile time assertions.

    The runner joins its threads with the REMAINING cleanup time. If the
    budget had been armed before `proc.wait` (the old defect), the first
    join would receive roughly _CLEANUP_BUDGET minus the child's runtime
    (1.0 s here). After the fix it must receive ~_CLEANUP_BUDGET.
    """
    recorded: list[tuple] = []
    original_join = threading.Thread.join

    def join(self, timeout=None):
        recorded.append((self.name, timeout))
        return original_join(self, timeout=timeout)

    monkeypatch.setattr(threading.Thread, "join", join)

    child = "import time; time.sleep(1.0); print('SLEPT', flush=True)"
    r = _run([sys.executable, "-c", child], timeout=10)
    assert r.ok and "SLEPT" in r.stdout

    joins_for_runner = [t for (name, t) in recorded
                        if name is None or name.startswith(RUNNER_THREAD_PREFIX)]
    assert len(joins_for_runner) >= 2, (
        f"runner threads must be joined explicitly, got {recorded}")
    first_timeout = joins_for_runner[0]
    assert first_timeout is not None, "joins must be bounded in the runner"
    assert first_timeout > RUNNER_CLEANUP_BUDGET - 0.75, (
        f"cleanup budget started too early: first join timeout {first_timeout:.3f}")


def test_timeout_branch_shares_one_deadline(monkeypatch):
    """In the timeout branch, one deadline covers wait and every join:
    the recorded join timeouts must be bounded by the budget and strictly
    consume the same shared clock (non-increasing, small deltas)."""
    recorded: list[tuple] = []
    original_join = threading.Thread.join

    def join(self, timeout=None):
        recorded.append((self.name, timeout))
        return original_join(self, timeout=timeout)

    monkeypatch.setattr(threading.Thread, "join", join)

    child = "print('PARTIAL', flush=True); import time; time.sleep(20)"
    r = _run([sys.executable, "-c", child], timeout=0.8)
    assert r.timed_out and "PARTIAL" in r.stdout

    timeouts = [t for (name, t) in recorded
                if name is not None and name.startswith(RUNNER_THREAD_PREFIX)]
    assert timeouts, "runner threads must be joined after the kill"
    assert all(t is not None and t <= RUNNER_CLEANUP_BUDGET for t in timeouts), (
        f"shared deadline exceeded: {timeouts}")
    # Every join consumes the same clock: values may decrease only a bit
    # (thread exits are quick after the kill), never grow.
    for prev, curr in zip(timeouts, timeouts[1:]):
        assert curr <= prev + 0.05, f"deadline not shared: {timeouts}"


# --- thread identity --------------------------------------------------------

@pytest.mark.parametrize("with_input,expected_count", [
    pytest.param(True, 3, id="stdin-writer-included"),   # out+err+stdin
    pytest.param(False, 2, id="only-readers"),           # out+err
])
def test_thread_identity_is_instance_based(thread_recorder, with_input,
                                           expected_count):
    # The recorder fixture replaces threading.Thread, so these are the
    # very instances created by THIS call, not name-based guesses.
    payload = "hi\n" if with_input else None
    r = _run([sys.executable, "-c", "print('PING', flush=True)"],
             timeout=10, input_text=payload)
    assert r.ok and "PING" in r.stdout

    threads = thread_recorder()
    assert len(threads) == expected_count, (
        f"expected {expected_count} runner threads, got {threads}")
    # Distinct instances (identity, not name), and all terminated now that
    # the child exited and pipes closed.
    assert len({id(t) for t in threads}) == len(threads)
    assert all(not t.is_alive() for t in threads)


# --- descendant scenarios ---------------------------------------------------

GRANDCHILD_CODE = (
    "import os, pathlib, sys, time\n"
    "pathlib.Path(sys.argv[1], 'grandchild.pid')"
    ".write_text(str(os.getpid()), encoding='utf-8')\n"
    "time.sleep(60)\n"
)

TAIL = {
    "timeout": "time.sleep(30)\n",   # child outlives the runner timeout
    "normal": "sys.exit(0)\n",       # child exits by itself, grandchild lives
}


def _write_descendant_fixtures(tmp_path: Path, tail: str):
    """Write gc.py and child.py; return the runner argv for the child."""
    (tmp_path / "gc.py").write_text(GRANDCHILD_CODE, encoding="utf-8")
    (tmp_path / "child.py").write_text(
        "import os, pathlib, subprocess, sys, time\n"
        "base = pathlib.Path(sys.argv[1])\n"
        "(base / 'child.pid').write_text(str(os.getpid()), encoding='utf-8')\n"
        "g = subprocess.Popen([sys.executable, str(base / 'gc.py'), str(base)],"
        " stdout=sys.stdout, stderr=sys.stderr)\n"
        "(base / 'descendant.pid').write_text(str(g.pid), encoding='utf-8')\n"
        "print('CHILD_READY', flush=True)\n" + TAIL[tail],
        encoding="utf-8",
    )
    return [sys.executable, str(tmp_path / "child.py"), str(tmp_path)]


@pytest.mark.parametrize("tail,expected_rc", [
    pytest.param("timeout", None, id="child-killed-at-timeout"),
    pytest.param("normal", 0, id="child-exits-normal"),
])
def test_descendant_keeps_pipes_open(tmp_path: Path, tail, expected_rc,
                                     thread_recorder):
    # The grandchild inherits the child's stdout/stderr handles: after the
    # child is killed (or exits by itself), the runner's reader threads
    # stay blocked on the inherited handles. The call must return within
    # timeout + shared budget and flag the incomplete collection.
    args = _write_descendant_fixtures(tmp_path, tail)
    known: dict = {"cpid": None, "gpid": None}
    failures: list[str] = []
    observed_error = None
    try:
        start = time.monotonic()
        r = _run(args, timeout=1.2)
        elapsed = time.monotonic() - start
        assert "CHILD_READY" in r.stdout
        # PIDs come from the independent files written by the test child
        # process itself; the runner's stdout is only a content check.
        known["cpid"] = _read_pid(tmp_path / "child.pid")
        known["gpid"] = _read_pid(tmp_path / "descendant.pid")
        assert known["gpid"] != known["cpid"]
        if expected_rc is None:
            assert r.timed_out and r.returncode == -1
        else:
            assert not r.timed_out and r.returncode == expected_rc
        assert r.truncated, (
            "grandchild held the pipes: incomplete collection must be flagged")
        assert elapsed < 1.2 + DEADLINE_BUDGET + 4.0  # bounded, no hang
    except BaseException as exc:  # keep going: cleanup must not be skipped
        observed_error = exc if isinstance(exc, Exception) else None
        if observed_error is None:
            raise  # KeyboardInterrupt/SystemExit propagate immediately
    finally:
        # 1) terminate BOTH processes with per-process error collection
        _test_pids_cleanup(tmp_path, known, failures)
        # 2) verify the tracked THREAD instances of this call terminate
        #    (even if an earlier assert already failed), then 3) report
        #    everything once, after all attempts.
        threads = thread_recorder()
        try:
            _assert_threads_dead(threads)
        except Exception as exc:
            failures.append(f"thread termination: {exc}")
        if failures:
            pytest.fail(" | ".join(failures))
    if observed_error is not None:
        raise observed_error
