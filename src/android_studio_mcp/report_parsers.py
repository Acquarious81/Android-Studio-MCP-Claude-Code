"""Parsers that turn Android tool reports into structured findings.

Covers Android Lint XML, JUnit/Gradle test XML, and Gradle version catalogs.
Kept separate from parsers.py so report parsing is independently testable.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Android Lint (lint-results*.xml)
# ---------------------------------------------------------------------------

def parse_lint_xml(xml_path: Path, max_issues: int = 200) -> dict:
    """Parse a single lint-results XML file into structured issues."""
    issues = []
    by_severity: dict[str, int] = {}
    by_id: dict[str, int] = {}
    try:
        root = ET.parse(xml_path).getroot()
    except Exception as exc:
        return {"error": f"could not parse {xml_path}: {exc}", "issues": [],
                "by_severity": {}, "total": 0}
    for issue in root.findall("issue"):
        sev = issue.get("severity", "Unknown")
        iid = issue.get("id", "Unknown")
        loc = issue.find("location")
        rec = {
            "id": iid,
            "severity": sev,
            "message": issue.get("message"),
            "category": issue.get("category"),
            "file": loc.get("file") if loc is not None else None,
            "line": loc.get("line") if loc is not None else None,
        }
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_id[iid] = by_id.get(iid, 0) + 1
        if len(issues) < max_issues:
            issues.append(rec)
    return {
        "total": sum(by_severity.values()),
        "by_severity": by_severity,
        "by_id": dict(sorted(by_id.items(), key=lambda kv: -kv[1])),
        "issues": issues,
        "source": str(xml_path),
    }


def find_and_parse_lint(project_dir: Path, max_issues: int = 200) -> dict:
    """Find the newest lint-results XML under a project and parse it."""
    candidates = sorted(project_dir.glob("**/build/reports/lint-results*.xml"))
    candidates += sorted(project_dir.glob("**/build/intermediates/lint*/**/lint-results*.xml"))
    xmls = [c for c in candidates if c.is_file()]
    if not xmls:
        return {"found": False, "total": 0, "issues": [],
                "note": "No lint-results*.xml found; run as_gradle_lint first."}
    parsed = parse_lint_xml(xmls[-1], max_issues)
    parsed["found"] = True
    parsed["all_reports"] = [str(x) for x in xmls]
    return parsed


# ---------------------------------------------------------------------------
# JUnit / Gradle test results (TEST-*.xml)
# ---------------------------------------------------------------------------

def parse_junit_dir(project_dir: Path, max_failures: int = 100) -> dict:
    """Aggregate all JUnit XML test reports under a project."""
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    failed_cases = []
    suites = []
    xmls = sorted(project_dir.glob("**/build/**/TEST-*.xml"))
    xmls = [x for x in xmls if x.is_file()]
    for xml in xmls:
        try:
            root = ET.parse(xml).getroot()
        except Exception:
            continue
        # root may be <testsuite> or <testsuites>
        testsuites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        for ts in testsuites:
            t = int(ts.get("tests", 0) or 0)
            f = int(ts.get("failures", 0) or 0)
            e = int(ts.get("errors", 0) or 0)
            s = int(ts.get("skipped", 0) or 0)
            totals["tests"] += t
            totals["failures"] += f
            totals["errors"] += e
            totals["skipped"] += s
            suites.append({"name": ts.get("name"), "tests": t, "failures": f, "errors": e, "skipped": s})
            for case in ts.findall("testcase"):
                fail = case.find("failure")
                erro = case.find("error")
                if fail is not None or erro is not None:
                    node = fail if fail is not None else erro
                    if len(failed_cases) < max_failures:
                        failed_cases.append({
                            "class": case.get("classname"),
                            "test": case.get("name"),
                            "type": node.get("type") if node is not None else None,
                            "message": (node.get("message") if node is not None else None),
                        })
    return {
        "found": bool(xmls),
        "totals": totals,
        "passed": totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"],
        "failed_cases": failed_cases,
        "suites": suites,
        "reports": [str(x) for x in xmls],
    }


# ---------------------------------------------------------------------------
# Gradle version catalog (gradle/libs.versions.toml)
# ---------------------------------------------------------------------------

def parse_version_catalog(toml_path: Path) -> dict:
    """Parse a Gradle version catalog. Uses tomllib when available, else a
    lightweight line parser (no external dependency)."""
    if not toml_path.is_file():
        return {"found": False}
    text = toml_path.read_text(encoding="utf-8", errors="replace")
    data: Optional[dict] = None
    try:
        import tomllib  # Python 3.11+
        data = tomllib.loads(text)
    except Exception:
        data = None
    if data is not None:
        return {
            "found": True,
            "versions": data.get("versions", {}),
            "libraries": list(data.get("libraries", {}).keys()),
            "plugins": list(data.get("plugins", {}).keys()),
            "source": str(toml_path),
        }
    # Fallback: parse [versions] table keys=values only.
    versions, section = {}, None
    import re
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
            continue
        if section == "versions":
            m = re.match(r'([\w.-]+)\s*=\s*"([^"]+)"', s)
            if m:
                versions[m.group(1)] = m.group(2)
    return {"found": True, "versions": versions, "libraries": [], "plugins": [],
            "source": str(toml_path), "parser": "fallback"}
