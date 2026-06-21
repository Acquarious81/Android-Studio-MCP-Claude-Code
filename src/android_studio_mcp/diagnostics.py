"""Map common Gradle/Android build failures to concrete remediation steps.

Each rule matches a signature in the build output and returns a human hint plus
the MCP tool the agent should call next. Attached to failing build responses so
Claude can self-correct instead of guessing.
"""

from __future__ import annotations

import re

# (regex, problem, suggested_tool, hint)
_RULES = [
    (r"SDK location not found|sdk\.dir|ANDROID_HOME is not set",
     "Android SDK location not configured for this project.",
     "as_config",
     "Ensure local.properties has 'sdk.dir=...' or ANDROID_HOME is set. as_project_create writes this automatically; check as_config for the detected SDK path."),
    (r"You have not accepted the license agreements|SDK license|not accepted",
     "An SDK package license has not been accepted.",
     "as_sdk_accept_licenses",
     "Call as_sdk_accept_licenses with confirm=true."),
    (r"Failed to find target with hash string|compileSdkVersion .* not found|package .* not installed|Could not find .*platforms;android",
     "A required SDK platform/build-tools package is missing.",
     "as_sdk_install",
     "Install the missing 'platforms;android-XX' / 'build-tools;XX' via as_sdk_install."),
    (r"Could not determine java version|Unsupported Java|requires Java|invalid source release|bigger Java|JAVA_HOME",
     "JDK/Java version mismatch for Gradle or AGP.",
     "as_status",
     "The bundled JBR (Java 21) is used as JAVA_HOME automatically; if a project pins a different JDK, align it in gradle.properties (org.gradle.java.home)."),
    (r"Could not resolve|Could not download|Connection (timed out|refused)|Unable to resolve dependency|dependency was not found",
     "A dependency could not be resolved (network or wrong coordinate).",
     "as_gradle_dependencies",
     "Check connectivity and the dependency coordinate; as_gradle_dependencies shows the resolved tree."),
    (r"No connected devices|no devices/emulators found|INSTALL_FAILED|device .* not found",
     "No device/emulator available for an install/instrumented task.",
     "as_avd_start",
     "Start an emulator with as_avd_start (wait_boot=true) or connect a device, then retry."),
    (r"Gradle wrapper|gradle-wrapper\.jar.*not found|Could not find or load main class org\.gradle\.wrapper",
     "Gradle wrapper is missing or broken.",
     "as_project_validate",
     "Re-create the wrapper (open once in Studio or 'gradle wrapper'); as_project_create now bundles it."),
    (r"OutOfMemoryError|GC overhead|Java heap space|Metaspace",
     "The Gradle/daemon JVM ran out of memory.",
     "as_gradle_stop",
     "Increase org.gradle.jvmargs (e.g. -Xmx4g) in gradle.properties and as_gradle_stop to restart daemons."),
    (r"Configuration cache problems|configuration cache",
     "Configuration cache problems.",
     "as_gradle_run_task",
     "Re-run with args ['--no-configuration-cache'] to bypass, then fix the reported tasks."),
]


def diagnose(output: str) -> list[dict]:
    """Return remediation suggestions matching the given build output."""
    text = output or ""
    out = []
    seen = set()
    for pattern, problem, tool, hint in _RULES:
        if re.search(pattern, text, re.IGNORECASE) and problem not in seen:
            seen.add(problem)
            out.append({"problem": problem, "suggested_tool": tool, "hint": hint})
    return out
