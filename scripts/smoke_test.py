"""End-to-end smoke test for android_studio_mcp.

Calls the real tool functions (read-only first, then a scaffold round-trip) and
prints a concise PASS/FAIL line for each. Run with:

    uv run python scripts/smoke_test.py            # fast checks + scaffold
    uv run python scripts/smoke_test.py --gradle   # also run a real Gradle build
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from android_studio_mcp.config import CONFIG
from android_studio_mcp.tools import lifecycle as L
from android_studio_mcp.tools import device as D
from android_studio_mcp.tools import avd as A
from android_studio_mcp.tools import sdk as S
from android_studio_mcp.tools import project as P
from android_studio_mcp.tools import apk as K
from android_studio_mcp.tools import gradle as G
from android_studio_mcp.tools import ide as I
from android_studio_mcp.tools import reports as R

PASS, FAIL = 0, 0


def check(name: str, raw: str, want_status: str = "ok", must_contain: list[str] | None = None) -> dict:
    global PASS, FAIL
    try:
        obj = json.loads(raw)
    except Exception as exc:
        print(f"[FAIL] {name}: not JSON ({exc})")
        FAIL += 1
        return {}
    ok_status = obj.get("status") == want_status
    contains_ok = all((k in raw) for k in (must_contain or []))
    if ok_status and contains_ok:
        print(f"[PASS] {name}: {obj.get('summary','')[:90]}")
        PASS += 1
    else:
        print(f"[FAIL] {name}: status={obj.get('status')} summary={obj.get('summary','')[:120]}")
        FAIL += 1
    return obj


async def main() -> int:
    do_gradle = "--gradle" in sys.argv
    print("=" * 70)
    print("android_studio_mcp smoke test")
    print("=" * 70)

    # --- read-only environment checks -------------------------------------
    check("as_config", await R.as_config(), must_contain=["config"])
    check("as_status", await L.as_status(L.StatusInput(deep=True)),
          must_contain=["studio_installed", "paths"])
    check("as_is_running", await L.as_is_running(), must_contain=["running"])
    check("as_adb_devices", await D.as_adb_devices(), must_contain=["devices"])
    check("as_avd_list", await A.as_avd_list(A.AvdListInput(detailed=True)), must_contain=["names"])
    check("as_sdk_list", await S.as_sdk_list(S.ListInput(installed_only=True)), must_contain=["installed"])
    check("as_ide_status", await I.as_ide_status(), must_contain=["studio_running"])
    check("as_project_list_recent", await P.as_project_list_recent(P.RecentInput(limit=10)), must_contain=["recent"])
    check("as_logs_ide", await R.as_logs_ide(R.LogTailInput(lines=20)),
          want_status="ok" if (CONFIG.idea_log and CONFIG.idea_log.exists()) else "error")

    # --- confirmation gating works ----------------------------------------
    check("as_close (no confirm -> gated)",
          await L.as_close(L.CloseInput(confirm=False)), want_status="confirmation_required")
    check("as_adb_uninstall (no confirm -> gated)",
          await D.as_adb_uninstall(D.PackageInput(package="com.does.not.exist", confirm=False)),
          want_status="confirmation_required")

    # --- path allow-list rejects outside paths ----------------------------
    check("as_project_create (outside workspace -> error)",
          await P.as_project_create(P.CreateProjectInput(
              parent_dir="C:/Windows", name="X", application_id="com.x.y", confirm=True)),
          want_status="error")

    # --- scaffold round-trip ----------------------------------------------
    import shutil
    work = Path(r"E:\Projekte\_asmcp_smoke")
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    check("as_project_create (gated without confirm)",
          await P.as_project_create(P.CreateProjectInput(
              parent_dir=str(work), name="SmokeApp", application_id="com.example.smoke", confirm=False)),
          want_status="confirmation_required")

    created = check("as_project_create (views)",
                    await P.as_project_create(P.CreateProjectInput(
                        parent_dir=str(work), name="SmokeApp",
                        application_id="com.example.smoke", template="views", confirm=True)),
                    must_contain=["files_written"])
    project_dir = created.get("project_dir")
    if project_dir:
        check("as_project_validate", await P.as_project_validate(P.ProjectPathInput(project_dir=project_dir)),
              must_contain=["checks"])
        check("as_project_info", await P.as_project_info(P.ProjectPathInput(project_dir=project_dir)),
              must_contain=["application_id"])
        check("as_project_backup", await P.as_project_backup(P.ProjectPathInput(project_dir=project_dir)),
              must_contain=["backup"])

        if do_gradle:
            print("\n--- running real Gradle build (may download AGP + deps) ---")
            res = await G.as_gradle_assemble(G.GradleAssembleInput(
                project_dir=project_dir, variant="Debug", timeout=1200))
            obj = check("as_gradle_assemble", res, must_contain=["gradle"])
            apks = (obj.get("outputs") or {}).get("apk") or []
            if apks:
                check("as_apk_info (built apk)", await K.as_apk_info(K.ApkInput(apk_path=apks[0])),
                      must_contain=["package"])
                check("as_apk_verify_signature", await K.as_apk_verify_signature(K.ApkInput(apk_path=apks[0])),
                      must_contain=["verified"])

    print("=" * 70)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
