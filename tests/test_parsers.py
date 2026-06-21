"""Unit tests for the pure parsing functions (no external tools needed)."""

from __future__ import annotations

from pathlib import Path

from android_studio_mcp.parsers import (
    extract_gradle_value, parse_aapt_badging, parse_adb_devices,
    parse_avdmanager_list, parse_gradle_result, parse_sdkmanager_list,
)
from android_studio_mcp.report_parsers import (
    parse_junit_dir, parse_lint_xml, parse_version_catalog,
)
from android_studio_mcp.diagnostics import diagnose


# --- parse_gradle_result ---------------------------------------------------

def test_gradle_success():
    r = parse_gradle_result("> Task :app:assembleDebug\nBUILD SUCCESSFUL in 7s\n", "")
    assert r["build_successful"] and not r["build_failed"]
    assert r["duration"] == "7s"


def test_gradle_failure_and_failing_tasks():
    out = "> Task :app:compileDebug FAILED\nFAILURE: Build failed with an exception.\nBUILD FAILED in 3s\n"
    r = parse_gradle_result(out, "")
    assert r["build_failed"] and not r["build_successful"]
    assert ":app:compileDebug" in r["failing_tasks"]
    assert "FAILURE:" in (r["failure_block"] or "")


def test_gradle_both_strings_uses_last():
    # stale SUCCESSFUL earlier, real FAILED later -> should report failed
    out = "BUILD SUCCESSFUL in 1s\n...later...\nBUILD FAILED in 2s\n"
    r = parse_gradle_result(out, "")
    assert r["build_failed"] and not r["build_successful"]


# --- parse_adb_devices -----------------------------------------------------

def test_adb_devices():
    out = ("List of devices attached\n"
           "emulator-5554       device product:sdk_gphone model:Pixel_7 device:emu transport_id:1\n"
           "* daemon line ignored *\n")
    devs = parse_adb_devices(out)
    assert len(devs) == 1
    assert devs[0]["serial"] == "emulator-5554"
    assert devs[0]["is_emulator"] and devs[0]["model"] == "Pixel_7"


# --- parse_avdmanager_list -------------------------------------------------

def test_avd_list():
    out = "Name: Pixel_7_API_34\n  Device: pixel_7\n  Path: /x\n  Target: Google APIs\n"
    avds = parse_avdmanager_list(out)
    assert avds[0]["name"] == "Pixel_7_API_34" and avds[0]["device"] == "pixel_7"


# --- parse_sdkmanager_list -------------------------------------------------

def test_sdkmanager_list():
    out = ("Installed packages:\n"
           "  Path | Version | Description | Location\n"
           "  platform-tools | 37.0.0 | Android SDK Platform-Tools |\n"
           "Available Packages:\n"
           "  platforms;android-35 | 1 | Android SDK Platform 35 |\n")
    parsed = parse_sdkmanager_list(out)
    assert any(p["path"] == "platform-tools" for p in parsed["installed"])
    assert any(p["path"] == "platforms;android-35" for p in parsed["available"])


# --- parse_aapt_badging (the minSdk regression) ----------------------------

def test_aapt_badging_new_format():
    out = ("package: name='com.example.smoke' versionCode='1' versionName='1.0'\n"
           "minSdkVersion:'24'\n"
           "targetSdkVersion:'34'\n"
           "application-label:'SmokeApp'\n"
           "uses-permission: name='android.permission.INTERNET'\n"
           "launchable-activity: name='com.example.smoke.MainActivity'\n")
    info = parse_aapt_badging(out)
    assert info["package"] == "com.example.smoke"
    assert info["min_sdk"] == "24" and info["target_sdk"] == "34"
    assert info["label"] == "SmokeApp"
    assert "android.permission.INTERNET" in info["permissions"]


def test_aapt_badging_old_format():
    out = "package: name='x' versionCode='1' versionName='1.0'\nsdkVersion:'21'\ntargetSdkVersion:'30'\n"
    info = parse_aapt_badging(out)
    assert info["min_sdk"] == "21" and info["target_sdk"] == "30"


# --- extract_gradle_value --------------------------------------------------

def test_extract_gradle_value_forms():
    assert extract_gradle_value('applicationId = "com.x.y"', "applicationId") == "com.x.y"
    assert extract_gradle_value("minSdk = 24", "minSdk") == "24"
    assert extract_gradle_value("minSdk(28)", "minSdk") == "28"           # function-call DSL
    assert extract_gradle_value('namespace("com.a.b")', "namespace") == "com.a.b"


# --- lint xml --------------------------------------------------------------

def test_parse_lint_xml(tmp_path: Path):
    xml = tmp_path / "lint-results.xml"
    xml.write_text(
        '<issues format="6">'
        '<issue id="HardcodedText" severity="Warning" message="Hardcoded string" category="Internationalization">'
        '<location file="A.kt" line="10"/></issue>'
        '<issue id="UnusedResources" severity="Warning" message="Unused"><location file="B.xml" line="2"/></issue>'
        '</issues>', encoding="utf-8")
    r = parse_lint_xml(xml)
    assert r["total"] == 2
    assert r["by_severity"]["Warning"] == 2
    assert r["issues"][0]["id"] == "HardcodedText" and r["issues"][0]["line"] == "10"


# --- junit xml -------------------------------------------------------------

def test_parse_junit_dir(tmp_path: Path):
    d = tmp_path / "app" / "build" / "test-results" / "testDebug"
    d.mkdir(parents=True)
    (d / "TEST-com.example.SampleTest.xml").write_text(
        '<testsuite name="com.example.SampleTest" tests="3" failures="1" errors="0" skipped="0">'
        '<testcase classname="com.example.SampleTest" name="passes"/>'
        '<testcase classname="com.example.SampleTest" name="fails">'
        '<failure type="AssertionError" message="expected true"/></testcase>'
        '</testsuite>', encoding="utf-8")
    r = parse_junit_dir(tmp_path)
    assert r["found"] and r["totals"]["tests"] == 3 and r["totals"]["failures"] == 1
    assert r["passed"] == 2
    assert r["failed_cases"][0]["test"] == "fails"


# --- version catalog -------------------------------------------------------

def test_parse_version_catalog(tmp_path: Path):
    toml = tmp_path / "libs.versions.toml"
    toml.write_text(
        '[versions]\nkotlin = "2.0.21"\nagp = "8.7.3"\n'
        '[libraries]\ncore-ktx = { module = "androidx.core:core-ktx", version = "1.13.1" }\n'
        '[plugins]\nandroid-application = { id = "com.android.application", version.ref = "agp" }\n',
        encoding="utf-8")
    r = parse_version_catalog(toml)
    assert r["found"] and r["versions"]["kotlin"] == "2.0.21"
    assert "core-ktx" in r["libraries"]


# --- diagnostics -----------------------------------------------------------

def test_diagnose_license():
    out = "You have not accepted the license agreements of the following SDK components"
    sugg = diagnose(out)
    assert any(s["suggested_tool"] == "as_sdk_accept_licenses" for s in sugg)


def test_diagnose_sdk_missing():
    out = "Failed to find target with hash string 'android-35'"
    sugg = diagnose(out)
    assert any(s["suggested_tool"] == "as_sdk_install" for s in sugg)


def test_diagnose_no_match():
    assert diagnose("BUILD SUCCESSFUL in 1s") == []
