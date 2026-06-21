"""Environment discovery and configuration for android_studio_mcp.

This module locates Android Studio, the Android SDK, the bundled JBR runtime,
the Android command-line tools and (optionally) a Gradle launcher. Every value
can be overridden through environment variables or a JSON config file so the
server stays portable across machines.

Resolution order for every setting:
    1. Environment variable (ASMCP_* or the standard ANDROID_* vars)
    2. config.json (path from ASMCP_CONFIG, else <package>/config.json,
       else %LOCALAPPDATA%/android-studio-mcp/config.json)
    3. Auto-discovery on disk
    4. Documented default
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

DEFAULT_STUDIO_HOME = r"C:\Program Files\Android\Android Studio"
DEFAULT_BUILTIN_SERVER_PORT = 63342
ENV_PREFIX = "ASMCP_"


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))


def _roaming_appdata() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))


def _user_data_dir() -> Path:
    d = _local_appdata() / "android-studio-mcp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_config_file() -> dict:
    """Load the optional JSON config file, returning {} if absent/invalid."""
    candidates = []
    env_path = os.environ.get(f"{ENV_PREFIX}CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path(__file__).resolve().parent / "config.json")
    candidates.append(_local_appdata() / "android-studio-mcp" / "config.json")
    for c in candidates:
        try:
            if c.is_file():
                return json.loads(c.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


_FILE_CONFIG = _load_config_file()


def _setting(key: str, *env_names: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve a setting from env -> config.json -> default."""
    for name in env_names:
        val = os.environ.get(name)
        if val:
            return val
    if key in _FILE_CONFIG and _FILE_CONFIG[key]:
        return str(_FILE_CONFIG[key])
    return default


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def _discover_studio_home() -> Path:
    explicit = _setting("studio_home", f"{ENV_PREFIX}STUDIO_HOME", "STUDIO_HOME")
    if explicit and (Path(explicit) / "bin" / "studio64.exe").exists():
        return Path(explicit)
    candidates = [
        Path(DEFAULT_STUDIO_HOME),
        Path(r"C:\Program Files\Android\Android Studio Preview"),
        _local_appdata() / "Programs" / "Android Studio",
        Path(r"D:\Program Files\Android\Android Studio"),
    ]
    for c in candidates:
        if (c / "bin" / "studio64.exe").exists():
            return c
    # Fall back to the documented default even if not present, so error
    # messages remain meaningful.
    return Path(explicit) if explicit else Path(DEFAULT_STUDIO_HOME)


def _discover_sdk(studio_home: Path) -> Optional[Path]:
    explicit = _setting(
        "android_sdk",
        f"{ENV_PREFIX}ANDROID_SDK",
        "ANDROID_HOME",
        "ANDROID_SDK_ROOT",
    )
    if explicit and Path(explicit).exists():
        return Path(explicit)
    candidates = [
        _local_appdata() / "Android" / "Sdk",
        Path.home() / "AppData" / "Local" / "Android" / "Sdk",
        Path(r"C:\Android\Sdk"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _highest_versioned_child(parent: Path) -> Optional[Path]:
    """Return the child directory with the highest dotted-numeric name."""
    if not parent or not parent.is_dir():
        return None

    def key(p: Path):
        nums = re.findall(r"\d+", p.name)
        return [int(n) for n in nums] if nums else [0]

    children = [c for c in parent.iterdir() if c.is_dir()]
    if not children:
        return None
    return sorted(children, key=key)[-1]


def _discover_ide_data_dir(kind: str) -> Optional[Path]:
    """Locate the newest AndroidStudio<version> config/system/log dir.

    kind: 'config' -> %APPDATA%\\Google, 'system' -> %LOCALAPPDATA%\\Google
    """
    base = _roaming_appdata() / "Google" if kind == "config" else _local_appdata() / "Google"
    if not base.is_dir():
        return None
    studio_dirs = [c for c in base.iterdir() if c.is_dir() and c.name.lower().startswith("androidstudio")]
    if not studio_dirs:
        return None

    def key(p: Path):
        nums = re.findall(r"\d+", p.name)
        return [int(n) for n in nums] if nums else [0]

    return sorted(studio_dirs, key=key)[-1]


def _version_key(p: Path) -> list[int]:
    nums = re.findall(r"\d+", p.parent.parent.parent.name if p.parent else p.name)
    return [int(n) for n in nums] if nums else [0]


def _discover_gradle_launcher() -> Optional[Path]:
    """Find a usable `gradle(.bat)` launcher from the Gradle wrapper cache."""
    explicit = _setting("gradle_launcher", f"{ENV_PREFIX}GRADLE_LAUNCHER")
    if explicit and Path(explicit).exists():
        return Path(explicit)
    dists = Path.home() / ".gradle" / "wrapper" / "dists"
    if dists.is_dir():
        # dists/gradle-X.Y-bin/<hash>/gradle-X.Y/bin/gradle.bat — pick highest version
        matches = list(dists.glob("gradle-*/*/gradle-*/bin/gradle.bat"))
        if matches:
            return sorted(matches, key=_version_key)[-1]
    return None


def _bundled_wrapper_jar() -> Path:
    return Path(__file__).resolve().parent / "resources" / "gradle-wrapper.jar"


def _discover_wrapper_jar() -> Optional[Path]:
    """Locate a gradle-wrapper.jar for scaffolding new projects.

    Prefers an explicit override, then the jar bundled with this package. We do
    NOT scan the filesystem (that was both slow at startup and unreliable);
    project creation falls back to `gradle wrapper` generation when needed.
    """
    explicit = _setting("wrapper_jar", f"{ENV_PREFIX}WRAPPER_JAR")
    if explicit and Path(explicit).exists():
        return Path(explicit)
    bundled = _bundled_wrapper_jar()
    return bundled if bundled.exists() else None


# ---------------------------------------------------------------------------
# Workspace roots (path allow-list for write/delete operations)
# ---------------------------------------------------------------------------

def _parse_workspace_roots() -> list[str]:
    raw = _setting("workspace_roots", f"{ENV_PREFIX}WORKSPACE_ROOTS")
    roots: list[str] = []
    if raw:
        roots.extend([r.strip() for r in raw.split(os.pathsep) if r.strip()])
    if isinstance(_FILE_CONFIG.get("workspace_roots"), list):
        roots.extend([str(r) for r in _FILE_CONFIG["workspace_roots"]])
    if not roots:
        roots = [r"E:\Projekte", str(Path.home())]
    # de-duplicate preserving order
    seen, out = set(), []
    for r in roots:
        rl = r.lower()
        if rl not in seen:
            seen.add(rl)
            out.append(r)
    return out


WORKSPACE_ROOTS_RAW = _parse_workspace_roots()


# ---------------------------------------------------------------------------
# The resolved configuration object
# ---------------------------------------------------------------------------

@dataclass
class StudioConfig:
    studio_home: Path
    sdk: Optional[Path]
    jbr: Optional[Path]
    config_dir: Optional[Path]
    system_dir: Optional[Path]
    gradle_launcher: Optional[Path]
    wrapper_jar: Optional[Path]
    builtin_server_port: int
    workspace_roots: list[Path] = field(default_factory=list)

    # ---- derived tool paths -------------------------------------------------
    @property
    def studio_exe(self) -> Path:
        return self.studio_home / "bin" / "studio64.exe"

    @property
    def studio_bat(self) -> Path:
        return self.studio_home / "bin" / "studio.bat"

    @property
    def inspect_bat(self) -> Path:
        return self.studio_home / "bin" / "inspect.bat"

    @property
    def format_bat(self) -> Path:
        return self.studio_home / "bin" / "format.bat"

    @property
    def java_exe(self) -> Optional[Path]:
        if self.jbr and (self.jbr / "bin" / "java.exe").exists():
            return self.jbr / "bin" / "java.exe"
        return None

    @property
    def platform_tools(self) -> Optional[Path]:
        return self.sdk / "platform-tools" if self.sdk else None

    @property
    def adb(self) -> Optional[Path]:
        if self.sdk and (self.sdk / "platform-tools" / "adb.exe").exists():
            return self.sdk / "platform-tools" / "adb.exe"
        return None

    @property
    def emulator_dir(self) -> Optional[Path]:
        return self.sdk / "emulator" if self.sdk else None

    @property
    def emulator(self) -> Optional[Path]:
        if self.sdk and (self.sdk / "emulator" / "emulator.exe").exists():
            return self.sdk / "emulator" / "emulator.exe"
        return None

    @property
    def cmdline_bin(self) -> Optional[Path]:
        if self.sdk and (self.sdk / "cmdline-tools" / "latest" / "bin").exists():
            return self.sdk / "cmdline-tools" / "latest" / "bin"
        return None

    @property
    def sdkmanager(self) -> Optional[Path]:
        return (self.cmdline_bin / "sdkmanager.bat") if self.cmdline_bin else None

    @property
    def avdmanager(self) -> Optional[Path]:
        return (self.cmdline_bin / "avdmanager.bat") if self.cmdline_bin else None

    @property
    def lint(self) -> Optional[Path]:
        return (self.cmdline_bin / "lint.bat") if self.cmdline_bin else None

    @property
    def apkanalyzer(self) -> Optional[Path]:
        return (self.cmdline_bin / "apkanalyzer.bat") if self.cmdline_bin else None

    @property
    def retrace(self) -> Optional[Path]:
        return (self.cmdline_bin / "retrace.bat") if self.cmdline_bin else None

    @property
    def build_tools_dir(self) -> Optional[Path]:
        if not self.sdk:
            return None
        return _highest_versioned_child(self.sdk / "build-tools")

    def build_tool(self, name: str) -> Optional[Path]:
        """Return a build-tools executable/script (e.g. 'aapt2.exe', 'apksigner.bat')."""
        bt = self.build_tools_dir
        if bt and (bt / name).exists():
            return bt / name
        return None

    @property
    def idea_log(self) -> Optional[Path]:
        if self.system_dir and (self.system_dir / "log" / "idea.log").exists():
            return self.system_dir / "log" / "idea.log"
        return None

    @property
    def options_dir(self) -> Optional[Path]:
        return (self.config_dir / "options") if self.config_dir else None

    @property
    def log_dir(self) -> Path:
        return _user_data_dir() / "logs"

    @property
    def backup_dir(self) -> Path:
        d = _user_data_dir() / "backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def builtin_server_url(self) -> str:
        return f"http://127.0.0.1:{self.builtin_server_port}"

    def to_dict(self) -> dict:
        return {
            "studio_home": str(self.studio_home),
            "studio_exe": str(self.studio_exe),
            "studio_exe_exists": self.studio_exe.exists(),
            "sdk": str(self.sdk) if self.sdk else None,
            "jbr": str(self.jbr) if self.jbr else None,
            "java_exe": str(self.java_exe) if self.java_exe else None,
            "adb": str(self.adb) if self.adb else None,
            "emulator": str(self.emulator) if self.emulator else None,
            "sdkmanager": str(self.sdkmanager) if self.sdkmanager else None,
            "avdmanager": str(self.avdmanager) if self.avdmanager else None,
            "lint": str(self.lint) if self.lint else None,
            "apkanalyzer": str(self.apkanalyzer) if self.apkanalyzer else None,
            "build_tools_dir": str(self.build_tools_dir) if self.build_tools_dir else None,
            "config_dir": str(self.config_dir) if self.config_dir else None,
            "system_dir": str(self.system_dir) if self.system_dir else None,
            "idea_log": str(self.idea_log) if self.idea_log else None,
            "gradle_launcher": str(self.gradle_launcher) if self.gradle_launcher else None,
            "wrapper_jar": str(self.wrapper_jar) if self.wrapper_jar else None,
            "builtin_server_url": self.builtin_server_url,
            "workspace_roots": [str(r) for r in self.workspace_roots],
            "log_dir": str(self.log_dir),
            "backup_dir": str(self.backup_dir),
        }


def load_config() -> StudioConfig:
    studio_home = _discover_studio_home()
    sdk = _discover_sdk(studio_home)
    jbr = studio_home / "jbr" if (studio_home / "jbr" / "bin" / "java.exe").exists() else None
    port_raw = _setting("builtin_server_port", f"{ENV_PREFIX}BUILTIN_SERVER_PORT")
    try:
        port = int(port_raw) if port_raw else DEFAULT_BUILTIN_SERVER_PORT
    except ValueError:
        port = DEFAULT_BUILTIN_SERVER_PORT

    cfg = StudioConfig(
        studio_home=studio_home,
        sdk=sdk,
        jbr=jbr,
        config_dir=_discover_ide_data_dir("config"),
        system_dir=_discover_ide_data_dir("system"),
        gradle_launcher=_discover_gradle_launcher(),
        wrapper_jar=_discover_wrapper_jar(),
        builtin_server_port=port,
        workspace_roots=[Path(r) for r in WORKSPACE_ROOTS_RAW],
    )
    return cfg


# A single shared instance for the process.
CONFIG = load_config()
