"""Output parsers for the various Android CLI tools.

Kept separate from the tool modules so the parsing logic is reusable and
independently testable.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Gradle
# ---------------------------------------------------------------------------

def parse_gradle_result(stdout: str, stderr: str) -> dict:
    """Extract build status, failing tasks and the FAILURE block from Gradle output."""
    combined = (stdout or "") + "\n" + (stderr or "")
    has_success = "BUILD SUCCESSFUL" in combined
    has_failed = "BUILD FAILED" in combined
    if has_success and has_failed:
        # Both present (e.g. stderr diagnostics): trust whichever came last.
        success = combined.rfind("BUILD SUCCESSFUL") > combined.rfind("BUILD FAILED")
        failed = not success
    else:
        success, failed = has_success, has_failed

    failing_tasks = re.findall(r"^> Task (\S+) FAILED", combined, re.MULTILINE)

    failure_block = None
    m = re.search(r"\nFAILURE:.*?(?=\nBUILD FAILED|\n\* Get more help|\Z)", combined, re.DOTALL)
    if m:
        failure_block = m.group(0).strip()[:4000]

    # Compiler-style errors: "e: file:line:col message" (Kotlin) and "error:" (javac)
    errors = re.findall(r"^e: .*$", combined, re.MULTILINE)[:50]
    errors += re.findall(r"^.*: error: .*$", combined, re.MULTILINE)[:50]

    duration = None
    dm = re.search(r"BUILD (?:SUCCESSFUL|FAILED) in (.+)$", combined, re.MULTILINE)
    if dm:
        duration = dm.group(1).strip()

    return {
        "build_successful": success,
        "build_failed": failed,
        "duration": duration,
        "failing_tasks": failing_tasks,
        "failure_block": failure_block,
        "compiler_errors": errors[:50],
    }


def find_build_outputs(project_dir: Path) -> dict:
    """Scan a project for produced APK/AAB/mapping artefacts."""
    outputs = {"apk": [], "aab": [], "mapping": []}
    if not project_dir.is_dir():
        return outputs
    try:
        # Match build/outputs at any module depth (root and sub-modules).
        for p in project_dir.glob("**/build/outputs/**/*"):
            if not p.is_file():
                continue
            sfx = p.suffix.lower()
            if sfx == ".apk":
                outputs["apk"].append(str(p))
            elif sfx == ".aab":
                outputs["aab"].append(str(p))
            elif p.name == "mapping.txt":
                outputs["mapping"].append(str(p))
    except Exception:
        pass
    for k in outputs:
        outputs[k] = sorted(set(outputs[k]))
    return outputs


# ---------------------------------------------------------------------------
# ADB
# ---------------------------------------------------------------------------

def parse_adb_devices(stdout: str) -> list[dict]:
    """Parse ``adb devices -l`` output into structured device records."""
    devices = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        meta = {}
        for tok in parts[2:]:
            if ":" in tok:
                k, v = tok.split(":", 1)
                meta[k] = v
        devices.append({
            "serial": serial,
            "state": state,
            "model": meta.get("model"),
            "device": meta.get("device"),
            "product": meta.get("product"),
            "transport_id": meta.get("transport_id"),
            "is_emulator": serial.startswith("emulator-"),
        })
    return devices


# ---------------------------------------------------------------------------
# AVD
# ---------------------------------------------------------------------------

def parse_avdmanager_list(stdout: str) -> list[dict]:
    """Parse ``avdmanager list avd`` output."""
    avds = []
    current = {}
    for line in (stdout or "").splitlines():
        s = line.strip()
        if s.startswith("Name:"):
            if current:
                avds.append(current)
            current = {"name": s.split(":", 1)[1].strip()}
        elif s.startswith("Device:"):
            current["device"] = s.split(":", 1)[1].strip()
        elif s.startswith("Path:"):
            current["path"] = s.split(":", 1)[1].strip()
        elif s.startswith("Target:"):
            current["target"] = s.split(":", 1)[1].strip()
        elif s.startswith("Based on:"):
            current["based_on"] = s.split(":", 1)[1].strip()
    if current:
        avds.append(current)
    return avds


# ---------------------------------------------------------------------------
# sdkmanager
# ---------------------------------------------------------------------------

def parse_sdkmanager_list(stdout: str) -> dict:
    """Parse ``sdkmanager --list`` into installed / available package tables."""
    installed, available = [], []
    section = None
    for line in (stdout or "").splitlines():
        low = line.strip().lower()
        if low.startswith("installed packages"):
            section = "installed"
            continue
        if low.startswith("available packages") or low.startswith("available updates"):
            section = "available"
            continue
        if not line.strip() or set(line.strip()) <= {"-"} or line.strip().startswith("Path"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2 and section:
            rec = {"path": parts[0], "version": parts[1] if len(parts) > 1 else None,
                   "description": parts[2] if len(parts) > 2 else None}
            (installed if section == "installed" else available).append(rec)
    return {"installed": installed, "available": available}


# ---------------------------------------------------------------------------
# aapt2 badging / apkanalyzer
# ---------------------------------------------------------------------------

def parse_aapt_badging(stdout: str) -> dict:
    """Parse ``aapt2 dump badging`` output."""
    info: dict = {"permissions": [], "features": [], "launchable_activity": None}
    for line in (stdout or "").splitlines():
        m = re.match(r"package: name='([^']+)' versionCode='([^']*)' versionName='([^']*)'", line)
        if m:
            info["package"] = m.group(1)
            info["version_code"] = m.group(2)
            info["version_name"] = m.group(3)
        # build-tools differ: older print "sdkVersion:'24'", newer "minSdkVersion:'24'"
        m = re.match(r"(?:min)?[Ss]dkVersion:'(\d+)'", line)
        if m and not line.startswith("target"):
            info["min_sdk"] = m.group(1)
        m = re.match(r"targetSdkVersion:'(\d+)'", line)
        if m:
            info["target_sdk"] = m.group(1)
        m = re.match(r"application-label:'([^']*)'", line)
        if m:
            info["label"] = m.group(1)
        m = re.match(r"uses-permission: name='([^']+)'", line)
        if m:
            info["permissions"].append(m.group(1))
        m = re.match(r"launchable-activity: name='([^']+)'", line)
        if m:
            info["launchable_activity"] = m.group(1)
    return info


# ---------------------------------------------------------------------------
# IntelliJ headless inspection results (XML)
# ---------------------------------------------------------------------------

def parse_inspection_results(out_dir: Path, max_problems: int = 200) -> dict:
    """Parse the XML files produced by ``studio64.exe inspect`` into problems."""
    problems = []
    by_severity: dict[str, int] = {}
    by_inspection: dict[str, int] = {}
    files = sorted(out_dir.glob("*.xml")) if out_dir.is_dir() else []
    for xml_file in files:
        if xml_file.name == ".descriptions.xml":
            continue
        try:
            tree = ET.parse(xml_file)
        except Exception:
            continue
        for prob in tree.getroot().findall("problem"):
            file_el = prob.find("file")
            line_el = prob.find("line")
            sev_el = prob.find("problem_class")
            desc_el = prob.find("description")
            severity = sev_el.get("severity") if sev_el is not None else "UNKNOWN"
            inspection = (sev_el.text if sev_el is not None else None) or xml_file.stem
            rec = {
                "inspection": inspection,
                "severity": severity,
                "file": (file_el.text if file_el is not None else None),
                "line": (line_el.text if line_el is not None else None),
                "description": (desc_el.text if desc_el is not None else None),
            }
            by_severity[severity] = by_severity.get(severity, 0) + 1
            by_inspection[inspection] = by_inspection.get(inspection, 0) + 1
            if len(problems) < max_problems:
                problems.append(rec)
    return {
        "total_problems": sum(by_severity.values()),
        "by_severity": by_severity,
        "by_inspection": dict(sorted(by_inspection.items(), key=lambda kv: -kv[1])),
        "problems": problems,
        "report_dir": str(out_dir),
        "result_files": [str(f) for f in files],
    }


# ---------------------------------------------------------------------------
# Gradle build files (lightweight project metadata)
# ---------------------------------------------------------------------------

def extract_gradle_value(text: str, key: str) -> Optional[str]:
    """Extract a value like ``applicationId "x"`` / ``minSdk = 24`` from Gradle DSL."""
    patterns = [
        rf'{key}\s*=\s*"([^"]+)"',
        rf'{key}\s+"([^"]+)"',
        rf"{key}\s*=\s*'([^']+)'",
        rf"{key}\s*=\s*(\d+)",
        rf"{key}\s+(\d+)",
        rf"{key}\.set\(\s*\"([^\"]+)\"\s*\)",
        # Function-call DSL: minSdk(34), applicationId("x"), namespace('x')
        rf"{key}\s*\(\s*(\d+)\s*\)",
        rf'{key}\s*\(\s*"([^"]+)"\s*\)',
        rf"{key}\s*\(\s*'([^']+)'\s*\)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None
