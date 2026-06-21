# Installation

## 1. Prerequisites (already present on this machine)

- Windows 11
- Android Studio (`C:\Program Files\Android\Android Studio`)
- Android SDK (`%LOCALAPPDATA%\Android\Sdk`) with: platform-tools, emulator,
  cmdline-tools/latest, build-tools
- Python ≥ 3.10 and **uv**

Verify:

```powershell
uv --version
python --version
```

## 2. Set up the server

```powershell
cd "E:\Projekte\Android-Studio-MCP"
uv sync
```

This creates `.venv` and installs the package + dependencies (`mcp`, `httpx`,
`pydantic`).

## 3. Verify

```powershell
# Resolved paths + tool count
uv run python -m android_studio_mcp --selfcheck

# List all tools
uv run python -m android_studio_mcp --list-tools

# Full functional smoke test (read-only + scaffold round-trip)
uv run python scripts/smoke_test.py

# Parser unit tests + security-gate regression
uv run pytest tests -q
uv run python scripts/security_test.py

# Optional: prove a real Gradle build (downloads AGP on first run)
uv run python scripts/build_test.py
```

Expected: `--list-tools` prints **73 tools**; `smoke_test.py` ends with
`RESULT: 17 passed, 0 failed`; `pytest` shows `15 passed`.

To build the one-click `.mcpb` bundle, see **docs/PACKAGING.md**.

## 4. (Optional) configuration

All paths are auto-discovered. To override, either set environment variables
(`ASMCP_STUDIO_HOME`, `ASMCP_ANDROID_SDK`, `ASMCP_WORKSPACE_ROOTS`, …) or copy
`config.example.json` to `config.json` next to the package and edit it.

The **workspace allow-list** controls where write/delete tools may operate.
Default: `E:\Projekte` and your home directory. Add more roots with:

```powershell
$env:ASMCP_WORKSPACE_ROOTS = "E:\Projekte;D:\Apps"
```

(or set it permanently via the Claude MCP config `env` block — see
`docs/CLAUDE_SETUP.md`).

## 5. Wire into Claude

See **docs/CLAUDE_SETUP.md**.

## Troubleshooting

| Symptom | Fix |
|--------|-----|
| `Android Studio not found` | Set `ASMCP_STUDIO_HOME` to the install dir |
| `adb/sdkmanager not found` | Set `ASMCP_ANDROID_SDK` to your SDK dir; install "SDK Command-line Tools (latest)" |
| `No Gradle wrapper found` | Open the project once in Studio (generates the wrapper) or run `gradle wrapper` |
| `Refusing to operate ... outside allowed workspace roots` | Add the path to `ASMCP_WORKSPACE_ROOTS` |
| Live-IDE tools unavailable | Start Studio (`as_launch`); for MCP-bridge tools install the *MCP Server* plugin |
