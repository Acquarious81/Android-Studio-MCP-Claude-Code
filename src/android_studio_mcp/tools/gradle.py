"""Gradle tools — the robust core for building/testing/linting Android projects.

Every tool uses the project's own ``gradlew.bat`` (Gradle wrapper) with the
bundled JBR as JAVA_HOME, so builds match what Android Studio would produce.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok
from ..parsers import find_build_outputs, parse_gradle_result
from ..runner import CommandResult, run
from ..safety import SafetyError, require_workspace

# Common flags: plain console for parseable output.
_BASE_FLAGS = ["--console=plain"]


def _resolve_gradle(project: Path) -> Optional[List[str]]:
    """Return the base command (gradlew.bat or fallback launcher) for a project."""
    wrapper = project / "gradlew.bat"
    if wrapper.exists():
        return [str(wrapper)]
    if CONFIG.gradle_launcher and Path(CONFIG.gradle_launcher).exists():
        return [str(CONFIG.gradle_launcher), "-p", str(project)]
    return None


async def _run_gradle(
    project_dir: str, tasks: List[str], extra_args: Optional[List[str]] = None,
    timeout: float = 600.0, collect_outputs: bool = False,
) -> str:
    try:
        project = require_workspace(project_dir, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    if not project.is_dir():
        return err(f"Not a directory: {project}")
    base = _resolve_gradle(project)
    if base is None:
        return err(
            f"No Gradle wrapper found in {project} (gradlew.bat missing) and no "
            f"fallback Gradle launcher configured. Open the project in Android "
            f"Studio once to generate the wrapper, or run 'gradle wrapper'."
        )
    cmd = base + _BASE_FLAGS + list(tasks) + (extra_args or [])
    result: CommandResult = await run(cmd, cwd=str(project), timeout=timeout)
    parsed = parse_gradle_result(result.stdout, result.stderr)
    extra = {"gradle": parsed}
    if collect_outputs:
        extra["outputs"] = find_build_outputs(project)
    summary = (
        f"Gradle {' '.join(tasks)} -> "
        + ("BUILD SUCCESSFUL" if parsed["build_successful"]
           else ("BUILD FAILED" if parsed["build_failed"] else f"rc={result.returncode}"))
    )
    if result.ok and parsed["build_successful"]:
        return ok(summary, command=result.to_dict(), **extra)
    from ..diagnostics import diagnose
    remediation = diagnose((result.stdout or "") + "\n" + (result.stderr or ""))
    return err(summary, command=result.to_dict(), remediation=remediation, **extra)


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class GradleProjectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_dir: str = Field(..., description="Android project root containing gradlew.bat.", min_length=1)
    timeout: int = Field(default=600, description="Max seconds to wait.", ge=10, le=3600)


class GradleAssembleInput(GradleProjectInput):
    variant: str = Field(default="Debug", description="Build variant, e.g. 'Debug' or 'Release'.")
    module: Optional[str] = Field(default=None, description="Optional Gradle module path, e.g. ':app'. If set, runs ':app:assembleDebug'.")


class GradleTestInput(GradleProjectInput):
    instrumented: bool = Field(default=False, description="If true, run connectedAndroidTest (needs a device/emulator) instead of JVM unit tests.")
    module: Optional[str] = Field(default=None, description="Optional module path, e.g. ':app'.")


class GradleTasksInput(GradleProjectInput):
    all_tasks: bool = Field(default=False, description="Include tasks from all task groups (--all).")


class GradleRunTaskInput(GradleProjectInput):
    tasks: List[str] = Field(..., description="Gradle tasks to run, e.g. ['clean', ':app:assembleDebug'].", min_length=1, max_length=20)
    args: Optional[List[str]] = Field(default_factory=list, description="Extra Gradle args, e.g. ['--stacktrace', '-PsomeProp=1'].", max_length=30)


class GradleDependenciesInput(GradleProjectInput):
    module: str = Field(default=":app", description="Module to inspect, e.g. ':app'.")
    configuration: Optional[str] = Field(default=None, description="Optional configuration filter, e.g. 'releaseRuntimeClasspath'.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(name="as_gradle_tasks", annotations={
    "title": "List Gradle tasks", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_gradle_tasks(params: GradleTasksInput) -> str:
    """List the Gradle tasks available in a project.

    Args:
        params (GradleTasksInput): project_dir, all_tasks (bool), timeout.

    Returns:
        str: JSON with the raw 'tasks' output and the parsed build status.
    """
    args = ["--all"] if params.all_tasks else []
    return await _run_gradle(params.project_dir, ["tasks"], args, timeout=params.timeout)


@mcp.tool(name="as_gradle_assemble", annotations={
    "title": "Assemble (build) an APK", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False})
async def as_gradle_assemble(params: GradleAssembleInput) -> str:
    """Build APK(s) with assemble<Variant> and report the produced artefacts.

    Equivalent to Android Studio's "Build > Build APK(s)". After a successful
    build the response lists the produced .apk paths under build/outputs.

    Args:
        params (GradleAssembleInput): project_dir, variant ('Debug'/'Release'),
            optional module (':app'), timeout.

    Returns:
        str: JSON with gradle status, command, and 'outputs' {apk,aab,mapping}.
    """
    task = f"assemble{params.variant}"
    if params.module:
        task = f"{params.module.rstrip(':')}:{task}"
    return await _run_gradle(params.project_dir, [task], timeout=params.timeout, collect_outputs=True)


@mcp.tool(name="as_gradle_bundle", annotations={
    "title": "Build an Android App Bundle (.aab)", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False})
async def as_gradle_bundle(params: GradleAssembleInput) -> str:
    """Build an Android App Bundle (.aab) with bundle<Variant>.

    Args:
        params (GradleAssembleInput): project_dir, variant, optional module, timeout.

    Returns:
        str: JSON with gradle status and produced 'outputs' (aab paths).
    """
    task = f"bundle{params.variant}"
    if params.module:
        task = f"{params.module.rstrip(':')}:{task}"
    return await _run_gradle(params.project_dir, [task], timeout=params.timeout, collect_outputs=True)


@mcp.tool(name="as_gradle_test", annotations={
    "title": "Run Gradle tests", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False})
async def as_gradle_test(params: GradleTestInput) -> str:
    """Run unit tests (default) or instrumented connected tests.

    Args:
        params (GradleTestInput): project_dir, instrumented (bool), module, timeout.

    Returns:
        str: JSON with gradle status and parsed failures. HTML/XML test reports
        are written under <module>/build/reports/tests and build/outputs.
    """
    if params.instrumented:
        task = "connectedAndroidTest"
    else:
        task = "test"
    if params.module:
        task = f"{params.module.rstrip(':')}:{task}"
    res = await _run_gradle(params.project_dir, [task], timeout=params.timeout)
    # Parse JUnit XML reports into pass/fail counts + failing cases.
    import json
    from ..report_parsers import parse_junit_dir
    obj = json.loads(res)
    try:
        project = require_workspace(params.project_dir, must_exist=True)
        results = parse_junit_dir(project)
        obj["test_results"] = results
        if results.get("found"):
            t = results["totals"]
            obj["summary"] = (f"Tests: {results['passed']} passed, {t['failures']} failed, "
                              f"{t['errors']} errored, {t['skipped']} skipped.")
    except Exception as exc:
        obj["test_results"] = {"found": False, "error": str(exc)}
    return json.dumps(obj, indent=2, ensure_ascii=False)


@mcp.tool(name="as_gradle_lint", annotations={
    "title": "Run Android Lint", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_gradle_lint(params: GradleProjectInput) -> str:
    """Run Android Lint via Gradle and point to the generated reports.

    Args:
        params (GradleProjectInput): project_dir, timeout.

    Returns:
        str: JSON with gradle status. Reports: <module>/build/reports/lint-results-*.html|xml.
    """
    res = await _run_gradle(params.project_dir, ["lint"], timeout=params.timeout)
    # Parse the produced lint-results XML into structured findings.
    import json
    from ..report_parsers import find_and_parse_lint
    obj = json.loads(res)
    try:
        project = require_workspace(params.project_dir, must_exist=True)
        obj["lint_findings"] = find_and_parse_lint(project)
    except Exception as exc:
        obj["lint_findings"] = {"found": False, "error": str(exc)}
    # Lint returns non-zero when it finds issues; surface the findings either way.
    if obj.get("status") == "error" and obj["lint_findings"].get("found"):
        obj["summary"] = f"Lint completed with {obj['lint_findings'].get('total', 0)} issue(s)."
    return json.dumps(obj, indent=2, ensure_ascii=False)


@mcp.tool(name="as_gradle_clean", annotations={
    "title": "Gradle clean", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": True, "openWorldHint": False})
async def as_gradle_clean(params: GradleProjectInput) -> str:
    """Delete build outputs via 'gradle clean'. Only touches the build/ dir.

    Args:
        params (GradleProjectInput): project_dir, timeout.

    Returns:
        str: JSON with gradle status.
    """
    return await _run_gradle(params.project_dir, ["clean"], timeout=params.timeout)


@mcp.tool(name="as_gradle_dependencies", annotations={
    "title": "Show Gradle dependencies", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_gradle_dependencies(params: GradleDependenciesInput) -> str:
    """Print the dependency tree for a module.

    Args:
        params (GradleDependenciesInput): project_dir, module (':app'),
            optional configuration filter, timeout.

    Returns:
        str: JSON with the dependency tree in command.stdout.
    """
    task = f"{params.module.rstrip(':')}:dependencies" if params.module.startswith(":") else f"{params.module}:dependencies"
    args = ["--configuration", params.configuration] if params.configuration else []
    return await _run_gradle(params.project_dir, [task], args, timeout=params.timeout)


@mcp.tool(name="as_gradle_signing_report", annotations={
    "title": "Gradle signing report", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_gradle_signing_report(params: GradleProjectInput) -> str:
    """Show signing configs and certificate fingerprints (SHA1/SHA256).

    Useful for configuring API keys (Maps, Firebase). Secrets in the output are
    redacted by the server.

    Args:
        params (GradleProjectInput): project_dir, timeout.

    Returns:
        str: JSON with the signingReport output.
    """
    return await _run_gradle(params.project_dir, ["signingReport"], timeout=params.timeout)


@mcp.tool(name="as_gradle_run_task", annotations={
    "title": "Run arbitrary Gradle tasks", "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": False, "openWorldHint": False})
async def as_gradle_run_task(params: GradleRunTaskInput) -> str:
    """Run arbitrary Gradle tasks with optional extra args (power tool).

    Use the dedicated tools (assemble/test/lint/clean) when possible; this is the
    escape hatch for anything else (e.g. installDebug, uninstallAll, custom tasks).

    Args:
        params (GradleRunTaskInput): project_dir, tasks (list), args (list), timeout.

    Returns:
        str: JSON with gradle status, command output and detected build outputs.
    """
    return await _run_gradle(params.project_dir, params.tasks, params.args, timeout=params.timeout, collect_outputs=True)


@mcp.tool(name="as_gradle_stop", annotations={
    "title": "Stop Gradle daemons", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_gradle_stop(params: GradleProjectInput) -> str:
    """Stop all Gradle daemons for the project (frees memory, clears stuck state).

    Args:
        params (GradleProjectInput): project_dir, timeout.

    Returns:
        str: JSON with command output.
    """
    return await _run_gradle(params.project_dir, ["--stop"], timeout=min(params.timeout, 60))
