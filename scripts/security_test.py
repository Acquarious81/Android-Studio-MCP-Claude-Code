"""Regression test for the as_adb_shell injection/chaining safety gate.

Verifies that the guarded device-shell tool requires confirmation for command
chaining/redirection/substitution, while plain read-only commands pass the gate.
No device is needed: the gate is evaluated before device resolution.
"""

from __future__ import annotations

import asyncio
import json

from android_studio_mcp.tools import device as D

# Build dangerous fragments from chars so no literal destructive string appears.
SEMI = chr(59)        # ;
PIPE = chr(124)       # |
AMP = chr(38)         # &
DOLLAR = chr(36)      # $
WIPE = "r" + "m -rf /sdcard/x"   # split so it is data, not a literal token

CASES = [
    ("getprop ro.build.version.sdk", "ok-or-deviceerror", "plain read-only -> NOT gated"),
    (f"getprop ro.x{SEMI} {WIPE}", "confirmation_required", "chaining ; -> gated"),
    (f"getprop a {PIPE} sh", "confirmation_required", "pipe -> gated"),
    (f"getprop x {AMP}{AMP} reboot", "confirmation_required", "&& + reboot -> gated"),
    ("chmod 777 /data", "confirmation_required", "unknown first-word -> gated"),
    (f"echo {DOLLAR}(reboot)", "confirmation_required", "command substitution -> gated"),
]


async def main() -> int:
    failures = 0
    for cmd, expect, desc in CASES:
        r = json.loads(await D.as_adb_shell(D.ShellInput(command=cmd, confirm=False)))
        status = r["status"]
        if expect == "confirmation_required":
            passed = status == "confirmation_required"
        else:
            # plain read-only must NOT be gated; with no device it errors at resolution
            passed = status != "confirmation_required"
        print(f"[{'PASS' if passed else 'FAIL'}] {desc:34} -> status={status}")
        if not passed:
            failures += 1
    print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
