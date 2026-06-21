"""Prove a real Gradle build of the scaffolded SmokeApp produces a signed APK."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from android_studio_mcp.tools import gradle as G
from android_studio_mcp.tools import apk as K

PROJECT = r"E:\Projekte\_asmcp_smoke\SmokeApp"


async def main() -> int:
    if not Path(PROJECT).exists():
        print(f"Project not found: {PROJECT}. Run smoke_test.py first.")
        return 1
    print(f"Building {PROJECT} (assembleDebug) — first run downloads AGP + deps...")
    res = await G.as_gradle_assemble(G.GradleAssembleInput(project_dir=PROJECT, variant="Debug", timeout=1500))
    obj = json.loads(res)
    print(f"status={obj.get('status')} summary={obj.get('summary')}")
    g = obj.get("gradle", {})
    print(f"build_successful={g.get('build_successful')} duration={g.get('duration')}")
    if g.get("failure_block"):
        print("FAILURE BLOCK:\n", g["failure_block"][:1500])
    if g.get("compiler_errors"):
        print("COMPILER ERRORS:\n", "\n".join(g["compiler_errors"][:20]))
    apks = (obj.get("outputs") or {}).get("apk") or []
    print("APKs:", apks)
    if apks:
        info = json.loads(await K.as_apk_info(K.ApkInput(apk_path=apks[0])))
        print(f"APK package={info.get('package')} versionName={info.get('version_name')} "
              f"minSdk={info.get('min_sdk')} label={info.get('label')}")
        sig = json.loads(await K.as_apk_verify_signature(K.ApkInput(apk_path=apks[0])))
        print(f"signature verified={sig.get('verified')}")
        return 0 if (g.get("build_successful") and info.get("package")) else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
