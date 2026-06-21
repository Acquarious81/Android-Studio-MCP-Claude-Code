<div align="center">

# Android Studio MCP

**Let Claude operate Android Studio and the entire Android toolchain — not just talk about it.**

A professional [Model Context Protocol](https://modelcontextprotocol.io) server that turns Claude (Claude Code / Claude Desktop) into a real Android **operator**: build, test, lint, run on devices & emulators, analyse APKs, scaffold projects, inspect code headlessly, and drive the live IDE.

![Platform](https://img.shields.io/badge/platform-Windows-0078D6)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![FastMCP](https://img.shields.io/badge/MCP-FastMCP-7B68EE)
![Tools](https://img.shields.io/badge/tools-73-success)
![License](https://img.shields.io/badge/license-MIT-green)

</div>

---

## Why this exists

Android Studio is an **IntelliJ‑based IDE**. The reliable way to *operate* it programmatically is **not** to pixel‑click the GUI — it's to drive the same tools the IDE drives. This server is built **stable‑first**: the bulk of the power comes from the rock‑solid command‑line toolchain, with the live‑IDE bridge and UI automation as additional layers that degrade gracefully.

| Layer | Mechanism | Covers | Robustness |
|------|-----------|--------|-----------|
| **1. Build / SDK CLI** | `gradlew`, `adb`, `emulator`, `avdmanager`, `sdkmanager`, `apkanalyzer`, `apksigner`, `aapt2`, `bundletool` | build, test, lint, deps, signing, devices, emulators, APK/AAB analysis | ★★★★★ headless, deterministic |
| **2. Headless IDE CLI** | `studio64.exe inspect` / `format` / `diff`, open project/file | full code inspection reports, reformat, navigation | ★★★★☆ |
| **3. Live IDE** | built‑in HTTP server (`:63342`) + `stdioMcpServer` MCP bridge | open‑in‑IDE, IDE state, IDE actions — *while the IDE runs* | ★★★☆☆ |
| **4. UI fallback** | native Win32 focus + SendKeys + screenshot | any action via "Find Action", visual verification | ★★☆☆☆ |

Everything in layers 1–2 works **whether or not the IDE is open** and needs no plugins.

---

## Highlights

- 🟢 **One‑shot "Run" button** — `as_run_app`: build → install → launch → capture logs → **detect crashes** → screenshot, in a single call.
- 🔨 **Full Gradle control** — assemble APK/AAB, unit & instrumented tests, lint, dependencies, signing report — with **structured** findings (lint XML & JUnit XML parsed into file/line/severity).
- 🩺 **Self‑healing builds** — failing builds return a `remediation` list mapping the error to the exact next tool to call (license → accept, missing platform → install, no device → start emulator).
- 📱 **Devices & emulators** — list/create/start/stop AVDs, install/uninstall, **streaming logcat** you can poll, screenshots, screen recording, input injection.
- 🏗️ **Project scaffolding** — generate a real, buildable Views or Compose project (bundled Gradle wrapper, correct AGP/Kotlin/SDK levels).
- 🔍 **Headless code inspection** — run the IDE's offline "Inspect Code" engine and get structured problems back.
- 📦 **APK / AAB analysis** — package/version/permissions, method counts, sizes, signature verification, APK diff, AAB → installable APK set.
- 🔒 **Safety‑first** — workspace path allow‑list, confirmation gating for destructive actions, automatic backups, secret redaction, hardened device‑shell.
- 🧪 **Tested** — 73 tools, real APK built end‑to‑end, 15 parser unit tests, security‑gate regression, adversarial multi‑agent code review.

---

## Requirements

- **Windows** (the server is Windows‑targeted; paths and process control use Win32).
- **Android Studio** (tested against build `AI‑261`, 2026.1.x) — autodiscovered at `C:\Program Files\Android\Android Studio`.
- **Android SDK** with command‑line tools, platform‑tools, emulator and build‑tools — autodiscovered at `%LOCALAPPDATA%\Android\Sdk`.
- **Python ≥ 3.10** and **[uv](https://docs.astral.sh/uv/)**.

All paths are autodiscovered but can be overridden (see [Configuration](#configuration)).

---

## Install

```powershell
git clone https://github.com/MauricePutinas/Android-Studio-MCP-Claude-Code.git
cd Android-Studio-MCP-Claude-Code
uv sync

# sanity checks
uv run python -m android_studio_mcp --selfcheck     # resolved paths + tool count
uv run python -m android_studio_mcp --list-tools    # all 73 tools
uv run python scripts/smoke_test.py                 # 17/17 functional checks
uv run pytest tests -q                              # 15 parser unit tests
```

### Wire into Claude

**Claude Desktop** — `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "android-studio": {
      "command": "C:\\Users\\<you>\\AppData\\Local\\hermes\\bin\\uv.exe",
      "args": ["--directory", "C:\\path\\to\\Android-Studio-MCP-Claude-Code", "run", "python", "-m", "android_studio_mcp"]
    }
  }
}
```

**Claude Code (CLI):**

```powershell
claude mcp add android-studio --scope user -- uv --directory "C:\path\to\Android-Studio-MCP-Claude-Code" run python -m android_studio_mcp
```

**One‑click bundle:** build a `.mcpb` with `powershell -File scripts\build_mcpb.ps1` and install it via *Claude Desktop → Settings → Extensions → Install extension*. See [docs/CLAUDE_SETUP.md](docs/CLAUDE_SETUP.md) and [docs/PACKAGING.md](docs/PACKAGING.md).

> **Note:** the committed `manifest.json` / example configs are pre‑filled for the author's machine. Adjust the two absolute paths (project dir, `uv.exe`) to your setup.

---

## Tool catalogue (73 tools)

> All tools are prefixed `as_` and return JSON with a `status`
> (`ok` | `error` | `confirmation_required`) and a human `summary`.
> Destructive tools require `confirm=true`.

<details open>
<summary><b>Lifecycle &amp; navigation</b></summary>

`as_status` · `as_is_running` · `as_launch` · `as_close` · `as_restart` · `as_open_project` · `as_open_file` · `as_diff`
</details>

<details>
<summary><b>Run &amp; background processes</b></summary>

`as_run_app` (build→install→launch→logs→crash→screenshot) · `as_logcat_watch_start` / `as_logcat_watch_read` / `as_logcat_watch_stop` · `as_proc_list` / `as_proc_stop`
</details>

<details>
<summary><b>Gradle (build / test / lint)</b></summary>

`as_gradle_tasks` · `as_gradle_assemble` · `as_gradle_bundle` · `as_gradle_test` *(structured JUnit)* · `as_gradle_lint` *(structured findings)* · `as_gradle_clean` · `as_gradle_dependencies` · `as_gradle_signing_report` · `as_gradle_run_task` · `as_gradle_stop`
</details>

<details>
<summary><b>SDK manager</b></summary>

`as_sdk_list` · `as_sdk_install` · `as_sdk_accept_licenses`
</details>

<details>
<summary><b>Emulators / AVDs</b></summary>

`as_avd_list` · `as_avd_create` · `as_avd_delete` · `as_avd_start` · `as_avd_stop`
</details>

<details>
<summary><b>Devices (ADB)</b></summary>

`as_adb_devices` · `as_adb_install` · `as_adb_uninstall` · `as_adb_logcat` · `as_adb_logcat_clear` · `as_adb_shell` *(guarded)* · `as_adb_screenshot` · `as_adb_input` · `as_adb_start_app` · `as_adb_stop_app` · `as_adb_pull` · `as_adb_push`
</details>

<details>
<summary><b>Projects</b></summary>

`as_project_create` *(real Views/Compose scaffold)* · `as_project_info` *(incl. version catalog)* · `as_project_validate` · `as_project_backup` · `as_project_list_recent`
</details>

<details>
<summary><b>APK / AAB analysis</b></summary>

`as_apk_info` · `as_apk_methods` · `as_apk_filesize` · `as_apk_verify_signature` · `as_apk_compare` · `as_aab_build_apks` · `as_aab_install`
</details>

<details>
<summary><b>Headless IDE analysis</b></summary>

`as_inspect_code` *(offline "Inspect Code" → structured problems)* · `as_format_code` *(reformat in place, backups first)*
</details>

<details>
<summary><b>Extras</b></summary>

`as_retrace` *(de‑obfuscate R8/ProGuard stack traces)* · `as_screenrecord` *(record device screen → mp4)*
</details>

<details>
<summary><b>Live IDE bridge</b></summary>

`as_ide_status` · `as_ide_list_mcp_tools` · `as_ide_call_mcp_tool`
</details>

<details>
<summary><b>Native UI fallback</b></summary>

`as_ui_screenshot` · `as_ui_focus` · `as_ui_send_keys` · `as_ui_invoke_action` *(any IDE action via "Find Action")*
</details>

<details>
<summary><b>Reports / logs / safety</b></summary>

`as_report_health` · `as_logs_ide` · `as_logs_mcp` · `as_config` · `as_backup_file` · `as_restore_backup`
</details>

**Resources:** `asmcp://config` · `asmcp://idea-log` · `asmcp://projects/recent`
**Prompts:** `run_app` · `diagnose_build`

---

## Safety model

- **Path allow‑list** — write/delete operations must target a path inside a configured workspace root (default: `E:\Projekte` + home). Override with `ASMCP_WORKSPACE_ROOTS`.
- **Confirmation gating** — destructive tools (`as_close --force`, `as_adb_uninstall`, `as_avd_delete`, `as_format_code`, `as_sdk_install`, `as_restore_backup`, …) return `confirmation_required` unless called with `confirm=true`.
- **Backups first** — files are backed up before being reformatted/overwritten; projects can be zipped with `as_project_backup`.
- **Guarded device shell** — `as_adb_shell` runs recognised read‑only commands freely but requires `confirm=true` for anything containing shell operators (`;`, `|`, `&&`, `$()`, …) or destructive markers.
- **Secret redaction** — keystore/store/key passwords and tokens are masked in all captured output and logs.
- **stdio‑safe logging** — all logs go to stderr + a rotating file, never stdout.

---

## Configuration

Resolution order for every setting: **env var → `config.json` → autodiscovery → default**.

| Setting | Env var | Default |
|--------|---------|---------|
| Android Studio home | `ASMCP_STUDIO_HOME` / `STUDIO_HOME` | `C:\Program Files\Android\Android Studio` |
| Android SDK | `ASMCP_ANDROID_SDK` / `ANDROID_HOME` / `ANDROID_SDK_ROOT` | `%LOCALAPPDATA%\Android\Sdk` |
| Workspace roots (allow‑list) | `ASMCP_WORKSPACE_ROOTS` (`;`‑separated) | `E:\Projekte;%USERPROFILE%` |
| Built‑in server port | `ASMCP_BUILTIN_SERVER_PORT` | `63342` |
| bundletool jar | `ASMCP_BUNDLETOOL` | auto‑downloaded on first use |

See [`config.example.json`](config.example.json).

---

## Architecture

```
src/android_studio_mcp/
├── app.py            FastMCP instance
├── config.py         toolchain autodiscovery + settings
├── runner.py         safe subprocess execution (env, .bat, timeouts, redaction)
├── safety.py         path allow‑list, confirmation gating, backups
├── procregistry.py   background process registry (logcat watch, …)
├── parsers.py        Gradle / ADB / AVD / SDK / aapt output parsers
├── report_parsers.py lint XML, JUnit XML, version catalog
├── diagnostics.py    build‑failure → remediation rules
├── scaffold.py       project templates (Views / Compose)
├── ide_http.py       built‑in IDE server (:63342) client
├── mcp_bridge.py     stdioMcpServer bridge
├── winauto.py        native Win32 UI automation
└── tools/            73 @mcp.tool definitions, grouped by domain
```

---

## Testing

| Suite | What | Result |
|------|------|--------|
| `scripts/smoke_test.py` | env checks, gating, path allow‑list, scaffold round‑trip | 17/17 |
| `scripts/build_test.py` | real `gradlew assembleDebug` → signed APK → analysis | BUILD SUCCESSFUL |
| `scripts/security_test.py` | adb‑shell injection/chaining gate | 6/6 |
| `pytest tests` | pure parser functions | 15/15 |
| `evals/android_studio_eval.xml` | 10 read‑only eval Q&A (mcp‑builder format) | — |

A full capability/risk matrix is in [docs/QUALITY.md](docs/QUALITY.md).

---

## Limitations

- The official **live‑IDE MCP** (`stdioMcpServer`) needs the *MCP Server* plugin (`com.intellij.mcpServer`). On a stock Android Studio it may not be installed — `as_ide_status` tells you, and the CLI tools cover everything regardless.
- **Headless `inspect`** indexes the project first, so it can take minutes; don't run it while the same project is open in a running IDE (config‑dir contention).
- **UI automation** (SendKeys) is best‑effort and English‑keymap oriented; always verify via the returned screenshot.
- The server is **Windows‑only** by design.

---

## License

MIT © Maurice Putinas — see [LICENSE](LICENSE).

> Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk). Not affiliated with Google or JetBrains. "Android" and "Android Studio" are trademarks of Google LLC.
