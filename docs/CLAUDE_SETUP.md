# Claude integration

This server speaks MCP over **stdio**, so it works with Claude Desktop, Claude
Code (CLI) and any MCP client.

The most robust launch command is the **venv Python** created by `uv sync`
(no dependency on `uv` being on PATH):

```
E:\Projekte\Android-Studio-MCP\.venv\Scripts\python.exe  -m android_studio_mcp
```

---

## A. Claude Desktop (Windows)

1. Open the config file (create it if missing):

   ```
   %APPDATA%\Claude\claude_desktop_config.json
   ```

2. Add the server (merge into any existing `mcpServers`):

   ```json
   {
     "mcpServers": {
       "android-studio": {
         "command": "E:\\Projekte\\Android-Studio-MCP\\.venv\\Scripts\\python.exe",
         "args": ["-m", "android_studio_mcp"],
         "env": {
           "ASMCP_WORKSPACE_ROOTS": "E:\\Projekte;C:\\Users\\Maurice"
         }
       }
     }
   }
   ```

3. **Fully quit and reopen Claude Desktop.** The server appears under the
   tools (plug) icon as `android-studio`.

> Alternative (uses `uv`, requires `uv` on PATH):
> ```json
> { "command": "uv",
>   "args": ["run", "--directory", "E:\\Projekte\\Android-Studio-MCP", "python", "-m", "android_studio_mcp"] }
> ```

A ready-to-copy file is provided: **`claude_desktop_config.example.json`**.

---

## B. Claude Code (CLI)

Easiest — register a user-scoped server:

```powershell
claude mcp add android-studio --scope user -- "E:\Projekte\Android-Studio-MCP\.venv\Scripts\python.exe" -m android_studio_mcp
```

Or add a project-local `.mcp.json`:

```json
{
  "mcpServers": {
    "android-studio": {
      "command": "E:\\Projekte\\Android-Studio-MCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "android_studio_mcp"]
    }
  }
}
```

Check it loaded:

```powershell
claude mcp list
```

---

## C. Verify Claude can see and use the tools

Ask Claude:

1. **Discovery** — "List the android-studio MCP tools" → you should see the
   `as_*` tools.
2. **Status** — "Use as_status to check my Android environment." → returns
   installed/running, SDK path, devices, AVDs.
3. **Scaffold** — "Create a new Compose app called Demo with id com.demo.app in
   E:\Projekte using as_project_create." (Claude must pass `confirm=true`.)
4. **Build** — "Build a debug APK of E:\Projekte\Demo with as_gradle_assemble and
   tell me where the APK is."
5. **Analyze** — "Run as_apk_info on that APK."

If a destructive call returns `confirmation_required`, that is expected — Claude
should re-issue it with `confirm=true`.

---

## D. (Optional) also wire the IDE's own MCP server

Recent Android Studio builds declare a `stdioMcpServer` command. If the *MCP
Server* plugin is installed (Settings → Plugins → Marketplace → "MCP Server"),
you can add the **live IDE** as a second MCP server for in-editor control:

```json
{
  "mcpServers": {
    "android-studio-ide": {
      "command": "C:\\Program Files\\Android\\Android Studio\\bin\\studio64.exe",
      "args": ["stdioMcpServer"]
    }
  }
}
```

This complements (does not replace) `android-studio`: the CLI/Gradle/ADB tools
here work without it. Use `as_ide_status` to check whether the plugin is present.
