"""Project tools: scaffold a new project, read metadata, validate, backup, recent."""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .. import scaffold
from ..app import mcp
from ..config import CONFIG
from ..formatting import err, ok, needs_confirmation
from ..logging_setup import LOG
from ..parsers import extract_gradle_value
from ..runner import run
from ..safety import SafetyError, backup_dir_zip, require_workspace, resolve_existing


class CreateProjectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    parent_dir: str = Field(..., description="Directory in which to create the project folder (inside a workspace root).", min_length=1)
    name: str = Field(..., description="Project/app name, e.g. 'My App'.", min_length=1, max_length=80)
    application_id: str = Field(..., description="Android applicationId / namespace, e.g. 'com.example.myapp'.", pattern=r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")
    template: str = Field(default="views", description="'views' (classic XML UI) or 'compose' (Jetpack Compose).", pattern=r"^(views|compose)$")
    min_sdk: int = Field(default=24, description="minSdkVersion.", ge=21, le=35)
    compile_sdk: int = Field(default=34, description="compileSdk/targetSdk. Must be installed.", ge=29, le=36)
    open_in_studio: bool = Field(default=False, description="Open the new project in Android Studio after creating it.")
    confirm: bool = Field(default=False, description="Must be true to write files to disk.")


class ProjectPathInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_dir: str = Field(..., description="Android project root.", min_length=1)


class RecentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    limit: int = Field(default=20, ge=1, le=100, description="Max recent projects to return.")


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

def _safe_folder_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "App"


async def _materialize_wrapper(project: Path) -> dict:
    """Provide gradle-wrapper.jar via copy or 'gradle wrapper' generation."""
    target_jar = project / "gradle" / "wrapper" / "gradle-wrapper.jar"
    if CONFIG.wrapper_jar and Path(CONFIG.wrapper_jar).exists():
        target_jar.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CONFIG.wrapper_jar, target_jar)
        return {"wrapper": "copied", "source": str(CONFIG.wrapper_jar)}
    if CONFIG.gradle_launcher and Path(CONFIG.gradle_launcher).exists():
        r = await run([str(CONFIG.gradle_launcher), "wrapper", "--gradle-version",
                       scaffold.GRADLE_VERSION], cwd=str(project), timeout=180)
        if target_jar.exists():
            return {"wrapper": "generated", "ok": True}
        return {"wrapper": "generate_failed", "detail": r.to_dict()}
    return {"wrapper": "missing",
            "detail": "No gradle-wrapper.jar source found. Open the project once in "
                      "Android Studio (it will create the wrapper), or run 'gradle wrapper'."}


@mcp.tool(name="as_project_create", annotations={
    "title": "Create a new Android project", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False})
async def as_project_create(params: CreateProjectInput) -> str:
    """Scaffold a new, buildable Android project (Views or Compose template).

    Generates settings/build Gradle files, a Gradle wrapper, local.properties
    (pointing at the detected SDK), manifest, MainActivity and resources, then
    optionally opens it in Android Studio.

    Args:
        params (CreateProjectInput): parent_dir, name, application_id, template,
            min_sdk, compile_sdk, open_in_studio, confirm.

    Returns:
        str: JSON with project_dir, files_written, wrapper status, and a hint to
        build via as_gradle_assemble. Or confirmation_required.
    """
    try:
        parent = require_workspace(params.parent_dir, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    folder = _safe_folder_name(params.name)
    project = parent / folder
    if not params.confirm:
        return needs_confirmation("create a new Android project", {
            "project_dir": str(project), "template": params.template,
            "application_id": params.application_id})
    if project.exists() and any(project.iterdir()):
        return err(f"Target already exists and is not empty: {project}")

    compose = params.template == "compose"
    pkg = params.application_id
    pkg_path = Path(*pkg.split("."))
    main_activity = (scaffold.MAIN_ACTIVITY_COMPOSE if compose else scaffold.MAIN_ACTIVITY_VIEWS).format(pkg=pkg)
    app_gradle = (scaffold.app_build_gradle_compose if compose else scaffold.app_build_gradle_views)(
        pkg, params.min_sdk, params.compile_sdk)

    sdk_dir = str(CONFIG.sdk).replace("\\", "\\\\") if CONFIG.sdk else ""

    files: dict[str, str] = {
        "settings.gradle.kts": scaffold.settings_gradle(params.name),
        "build.gradle.kts": scaffold.root_build_gradle(compose),
        "gradle.properties": scaffold.gradle_properties(),
        "local.properties": f"sdk.dir={sdk_dir}\n",
        ".gitignore": scaffold.GITIGNORE,
        "gradlew.bat": scaffold.GRADLEW_BAT,
        "gradlew": scaffold.GRADLEW_SH,
        "gradle/wrapper/gradle-wrapper.properties": scaffold.wrapper_properties(),
        "app/build.gradle.kts": app_gradle,
        "app/proguard-rules.pro": scaffold.PROGUARD,
        "app/src/main/AndroidManifest.xml": scaffold.MANIFEST,
        f"app/src/main/java/{pkg_path.as_posix()}/MainActivity.kt": main_activity,
        "app/src/main/res/values/strings.xml": scaffold.strings_xml(params.name),
    }
    if not compose:
        files["app/src/main/res/layout/activity_main.xml"] = scaffold.LAYOUT_MAIN
        files["app/src/main/res/values/themes.xml"] = scaffold.THEMES_VIEWS

    written = []
    try:
        for rel, content in files.items():
            dest = project / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            written.append(rel)
    except Exception as exc:
        return err(f"Failed while writing project files: {exc}", written=written)

    wrapper_status = await _materialize_wrapper(project)
    LOG.info("Scaffolded project at %s (%d files)", project, len(written))

    result = {
        "project_dir": str(project),
        "application_id": pkg,
        "template": params.template,
        "files_written": written,
        "wrapper": wrapper_status,
        "next": "Build with as_gradle_assemble(project_dir, variant='Debug').",
    }
    if params.open_in_studio and CONFIG.studio_exe.exists():
        from ..runner import spawn_detached
        result["studio_pid"] = spawn_detached([str(CONFIG.studio_exe), str(project)])
    return ok(f"Created {params.template} project at {project}.", **result)


# ---------------------------------------------------------------------------
# info / validate
# ---------------------------------------------------------------------------

def _parse_manifest(manifest: Path) -> dict:
    info = {"permissions": [], "activities": [], "package": None}
    try:
        tree = ET.parse(manifest)
        root = tree.getroot()
        ns = "{http://schemas.android.com/apk/res/android}"
        info["package"] = root.get("package")
        for perm in root.findall("uses-permission"):
            info["permissions"].append(perm.get(f"{ns}name"))
        app = root.find("application")
        if app is not None:
            for act in app.findall("activity"):
                info["activities"].append(act.get(f"{ns}name"))
    except Exception as exc:
        info["error"] = str(exc)
    return info


@mcp.tool(name="as_project_info", annotations={
    "title": "Read project metadata", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_project_info(params: ProjectPathInput) -> str:
    """Extract metadata from an Android project without building it.

    Reads settings/build Gradle files and the app manifest to report the
    modules, applicationId, version, SDK levels, plugins and permissions.

    Args:
        params (ProjectPathInput): project_dir.

    Returns:
        str: JSON with modules, application_id, version_code/name,
        min_sdk/target_sdk/compile_sdk, manifest (permissions, activities),
        and detected gradle files.
    """
    try:
        project = resolve_existing(params.project_dir, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))

    settings = None
    for n in ("settings.gradle.kts", "settings.gradle"):
        if (project / n).exists():
            settings = project / n
            break
    if not settings:
        return err(f"No settings.gradle(.kts) found in {project}; not an Android/Gradle root.")

    settings_text = settings.read_text(encoding="utf-8", errors="replace")
    import re
    modules = re.findall(r'include\(([^)]*)\)', settings_text)
    modules = re.findall(r'["\']([:\w-]+)["\']', " ".join(modules))

    app_gradle = None
    for cand in (project / "app" / "build.gradle.kts", project / "app" / "build.gradle"):
        if cand.exists():
            app_gradle = cand
            break
    meta: dict = {"modules": modules, "settings_file": str(settings)}
    if app_gradle:
        text = app_gradle.read_text(encoding="utf-8", errors="replace")
        meta.update({
            "app_build_file": str(app_gradle),
            "application_id": extract_gradle_value(text, "applicationId") or extract_gradle_value(text, "namespace"),
            "version_code": extract_gradle_value(text, "versionCode"),
            "version_name": extract_gradle_value(text, "versionName"),
            "min_sdk": extract_gradle_value(text, "minSdk"),
            "target_sdk": extract_gradle_value(text, "targetSdk"),
            "compile_sdk": extract_gradle_value(text, "compileSdk"),
            "uses_compose": "compose = true" in text or "compose-bom" in text,
        })
    manifest = project / "app" / "src" / "main" / "AndroidManifest.xml"
    if manifest.exists():
        meta["manifest"] = _parse_manifest(manifest)

    catalog = project / "gradle" / "libs.versions.toml"
    if catalog.exists():
        from ..report_parsers import parse_version_catalog
        meta["version_catalog"] = parse_version_catalog(catalog)

    meta["has_wrapper"] = (project / "gradlew.bat").exists()
    return ok(f"Project metadata for {project.name}.", project_dir=str(project), **meta)


@mcp.tool(name="as_project_validate", annotations={
    "title": "Validate project structure", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_project_validate(params: ProjectPathInput) -> str:
    """Check that a project has the expected structure and report problems.

    Args:
        params (ProjectPathInput): project_dir.

    Returns:
        str: JSON with 'valid' (bool), 'problems' (list) and 'checks' (dict).
    """
    try:
        project = resolve_existing(params.project_dir, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    checks = {
        "settings.gradle(.kts)": (project / "settings.gradle.kts").exists() or (project / "settings.gradle").exists(),
        "gradlew.bat": (project / "gradlew.bat").exists(),
        "gradle-wrapper.jar": (project / "gradle" / "wrapper" / "gradle-wrapper.jar").exists(),
        "local.properties": (project / "local.properties").exists(),
        "app module": (project / "app").is_dir(),
        "AndroidManifest.xml": (project / "app" / "src" / "main" / "AndroidManifest.xml").exists(),
    }
    problems = [f"Missing: {k}" for k, v in checks.items() if not v]
    # sdk.dir sanity
    lp = project / "local.properties"
    if lp.exists():
        if "sdk.dir" not in lp.read_text(encoding="utf-8", errors="replace"):
            problems.append("local.properties has no sdk.dir entry.")
    valid = not problems
    return (ok if valid else err)(
        "Project structure looks valid." if valid else f"{len(problems)} problem(s) found.",
        valid=valid, problems=problems, checks=checks, project_dir=str(project))


@mcp.tool(name="as_project_backup", annotations={
    "title": "Backup a project", "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False})
async def as_project_backup(params: ProjectPathInput) -> str:
    """Create a timestamped .zip backup of a project (excluding build artefacts).

    Args:
        params (ProjectPathInput): project_dir.

    Returns:
        str: JSON with the backup zip path.
    """
    try:
        project = require_workspace(params.project_dir, must_exist=True)
    except SafetyError as exc:
        return err(str(exc))
    try:
        dest = backup_dir_zip(project, label="project")
    except Exception as exc:
        return err(f"Backup failed: {exc}")
    return ok(f"Backed up {project.name}.", backup=dest, project_dir=str(project))


@mcp.tool(name="as_project_list_recent", annotations={
    "title": "List recent projects", "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False})
async def as_project_list_recent(params: RecentInput) -> str:
    """List recently opened projects from Android Studio's config.

    Args:
        params (RecentInput): limit.

    Returns:
        str: JSON with 'recent' (list of project paths). Empty if none recorded.
    """
    recents: list[str] = []
    opts = CONFIG.options_dir
    rp = (opts / "recentProjects.xml") if opts else None
    if rp and rp.exists():
        try:
            tree = ET.parse(rp)
            for entry in tree.getroot().iter("entry"):
                key = entry.get("key")
                if key:
                    recents.append(key.replace("$USER_HOME$", str(Path.home())))
        except Exception as exc:
            return err(f"Failed to parse recentProjects.xml: {exc}")
    return ok(f"{len(recents)} recent project(s).", recent=recents[: params.limit],
              source=str(rp) if rp else None)
