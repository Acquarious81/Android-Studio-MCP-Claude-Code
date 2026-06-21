# Packaging as a `.mcpb` bundle

A `.mcpb` (MCP Bundle) is one-click-installable in Claude Desktop
(Settings → Erweiterungen → **Erweiterung installieren**) — same format as your
Unity/Premiere MCPs.

## Approach: launch the tested venv (ABI-safe)

This bundle does **not** vendor Python dependencies. Instead it launches the
project's own uv virtual environment:

```
E:\Projekte\Android-Studio-MCP\.venv\Scripts\python.exe  -m android_studio_mcp
```

That env was created by `uv sync` and is exactly what all tests ran against, so
there are no Python-version / compiled-wheel (pydantic-core) ABI mismatches.
**Trade-off:** the bundle is tied to this machine — keep the project and its
`.venv` where they are. (For a portable bundle on another machine, run
`uv sync` there and rebuild.)

## Build

```powershell
cd "E:\Projekte\Android-Studio-MCP"
uv sync                                   # ensure .venv exists
powershell -ExecutionPolicy Bypass -File scripts\build_mcpb.ps1
```

Output: **`dist\android-studio-mcp.mcpb`** (~127 KB). The archive contains
`manifest.json` at the root (standard forward-slash paths) plus the source.

## Install

1. Claude Desktop → Einstellungen → **Erweiterungen** → **Erweiterung installieren**
2. Choose `E:\Projekte\Android-Studio-MCP\dist\android-studio-mcp.mcpb`
3. The `android-studio` tools appear under the tools (plug) icon.

> Alternative: **Entpackte Erweiterung installieren** → select the project root
> folder `E:\Projekte\Android-Studio-MCP` (it contains `manifest.json`). Same
> behaviour, no zip needed.

## If the extension fails to start

The bundle relies on `.venv\Scripts\python.exe` existing. If you moved/deleted
the project or never ran `uv sync`, recreate it (`uv sync`) and reinstall, or use
the JSON config in `docs/CLAUDE_SETUP.md` (the always-works fallback).
