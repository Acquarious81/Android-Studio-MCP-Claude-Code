"""Functional test for the Ausbaustufe-2 tools against the scaffolded SmokeApp."""

from __future__ import annotations

import asyncio
import json

from android_studio_mcp.tools import watch as W
from android_studio_mcp.tools import run as RUN
from android_studio_mcp.tools import gradle as G

PROJECT = r"E:\Projekte\_asmcp_smoke\SmokeApp"


def show(name, raw):
    o = json.loads(raw)
    print(f"[{o.get('status'):>22}] {name}: {str(o.get('summary'))[:80]}")
    return o


async def main() -> int:
    # process registry empty
    show("as_proc_list", await W.as_proc_list())

    # structured lint on the real project
    print("\n-- as_gradle_lint (structured) --")
    lint = show("as_gradle_lint", await G.as_gradle_lint(G.GradleProjectInput(project_dir=PROJECT, timeout=600)))
    lf = lint.get("lint_findings", {})
    print(f"   lint found={lf.get('found')} total={lf.get('total')} by_severity={lf.get('by_severity')}")

    # composite run: build succeeds, then no-device path is exercised
    print("\n-- as_run_app (build then device step) --")
    r = show("as_run_app", await RUN.as_run_app(RUN.RunAppInput(project_dir=PROJECT, timeout=900)))
    print(f"   build step status={r.get('steps',{}).get('build',{}).get('status')}  apk={bool(r.get('steps',{}).get('apk'))}")

    # logcat watch guard (no device)
    print("\n-- as_logcat_watch_start (guard) --")
    show("as_logcat_watch_start", await W.as_logcat_watch_start(W.WatchStartInput()))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
