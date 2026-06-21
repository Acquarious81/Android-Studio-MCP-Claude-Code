# Qualitätsprüfung & Fähigkeiten-Matrix

Kritische Bewertung: **Was kann Claude über diesen MCP wirklich?**

## Steuerungsmethoden (stabilster Weg zuerst)

1. **CLI/Build-Toolchain** (Gradle, ADB, emulator, sdkmanager, apkanalyzer, apksigner) — vollständig headless, deterministisch, unabhängig davon ob die IDE läuft. **Das Rückgrat.**
2. **Headless-IDE-CLI** (`studio64.exe inspect/format/diff`, Projekt/Datei öffnen) — echte Code-Analyse & Formatierung ohne GUI.
3. **Live-IDE** (eingebauter HTTP-Server `:63342` + `stdioMcpServer`-Brücke) — nur wenn IDE läuft; MCP-Brücke braucht das *MCP-Server*-Plugin.
4. **Native UI-Automation** (Win32-Fokus + SendKeys + Screenshot) — Notnagel für alles ohne CLI-Pfad.

## Fähigkeiten-Matrix

| Funktion | Status | Methode | Risiko | Bemerkung |
|----------|--------|---------|--------|-----------|
| Programmstatus / Umgebung lesen | ✅ Vollständig | CLI + Prozess + HTTP | – | `as_status`, `as_config`, `as_report_health` |
| Studio starten / öffnen | ✅ Vollständig | `studio64.exe` | – | `as_launch`, `as_open_project`, `as_open_file` |
| Studio schließen | ⚠️ Mit Bestätigung | taskkill | Datenverlust bei `force` | `as_close` (confirm), graceful default |
| Studio neu starten | ⚠️ Mit Bestätigung | taskkill + launch | wie Schließen | `as_restart` |
| Neues Projekt erstellen | ✅ Vollständig | Scaffolding (Views/Compose) | schreibt Dateien (confirm) | `as_project_create` – echtes, baubares Projekt |
| Projekt öffnen | ✅ Vollständig | `studio64.exe <dir>` | – | `as_open_project` |
| Projekt-Metadaten lesen | ✅ Vollständig | Datei-Parser | – | `as_project_info`, `as_project_validate` |
| Projekt sichern (Backup) | ✅ Vollständig | ZIP | – | `as_project_backup`, `as_backup_file` |
| APK bauen | ✅ Vollständig | `gradlew assemble<V>` | – | `as_gradle_assemble` (+ Output-Pfade) |
| AAB bauen | ✅ Vollständig | `gradlew bundle<V>` | – | `as_gradle_bundle` |
| Tests ausführen | ✅ Vollständig | `gradlew test / connectedAndroidTest` | – | `as_gradle_test` |
| Lint ausführen | ✅ Vollständig | `gradlew lint` | – | `as_gradle_lint` (+ Report-Pfade) |
| Abhängigkeiten anzeigen | ✅ Vollständig | `gradlew dependencies` | – | `as_gradle_dependencies` |
| Signing-Report / Fingerprints | ✅ Vollständig | `gradlew signingReport` | Secrets redacted | `as_gradle_signing_report` |
| Beliebige Gradle-Tasks | ✅ Vollständig | `gradlew <tasks>` | je nach Task (confirm-Flag) | `as_gradle_run_task` |
| Code-Inspektion (Fehler erkennen) | ✅ Vollständig* | `studio64.exe inspect` | – | `as_inspect_code`; *langsam (Indexierung), nicht parallel zur offenen IDE |
| Code formatieren | ⚠️ Mit Bestätigung | `studio64.exe format` | ändert Dateien (Backup zuvor) | `as_format_code` |
| SDK-Pakete auflisten | ✅ Vollständig | `sdkmanager --list` | – | `as_sdk_list` |
| SDK-Pakete installieren | ⚠️ Mit Bestätigung | `sdkmanager` | Download, Lizenz-Accept | `as_sdk_install`, `as_sdk_accept_licenses` |
| AVDs auflisten | ✅ Vollständig | `emulator -list-avds` / `avdmanager` | – | `as_avd_list` |
| AVD erstellen | ⚠️ Mit Bestätigung | `avdmanager create` | braucht System-Image | `as_avd_create` |
| AVD löschen | ⚠️ Mit Bestätigung | `avdmanager delete` | unwiderruflich | `as_avd_delete` |
| Emulator starten/stoppen | ✅ Vollständig | `emulator`, `adb emu kill` | – | `as_avd_start` (wait_boot), `as_avd_stop` |
| Geräte auflisten | ✅ Vollständig | `adb devices -l` | – | `as_adb_devices` |
| APK installieren | ✅ Vollständig | `adb install` | überschreibt App | `as_adb_install` |
| App deinstallieren | ⚠️ Mit Bestätigung | `adb uninstall` | löscht App + Daten | `as_adb_uninstall` |
| Logcat lesen | ✅ Vollständig | `adb logcat -d` | – | `as_adb_logcat` (Filter, grep) |
| Gerät-Shell | ⚠️ Teilw./Bestätigung | `adb shell` | gefährliche Befehle gated | `as_adb_shell` (Allowlist) |
| Geräte-Screenshot | ✅ Vollständig | `adb screencap` | – | `as_adb_screenshot` |
| Geräte-Input (tap/swipe/text) | ✅ Vollständig | `adb input` | – | `as_adb_input` |
| App starten/stoppen | ✅ Vollständig | `am start / force-stop` | – | `as_adb_start_app`, `as_adb_stop_app` |
| Dateien push/pull | ✅ Vollständig | `adb push/pull` | Pfad-Allowlist | `as_adb_pull`, `as_adb_push` |
| APK analysieren | ✅ Vollständig | `aapt2`, `apkanalyzer`, `apksigner` | – | `as_apk_info/methods/filesize/verify/compare` |
| Logs lesen (IDE) | ✅ Vollständig | Datei-Tail | – | `as_logs_ide`, `as_logs_mcp` |
| Bericht erstellen | ✅ Vollständig | Aggregation | – | `as_report_health` |
| Live-IDE-Status | ✅ Vollständig | HTTP `:63342` + Prozess | – | `as_ide_status` |
| Datei in laufender IDE öffnen | ✅ Vollständig | Built-in Server / CLI | – | `as_open_file` |
| Live-IDE MCP-Tools nutzen | 🔶 Später/Plugin | `stdioMcpServer`-Brücke | braucht *MCP-Server*-Plugin | `as_ide_list_mcp_tools`, `as_ide_call_mcp_tool` |
| Beliebige IDE-Aktion (Find Action) | 🔶 Best-Effort | Win32 SendKeys | UI-abhängig, Screenshot-Verifikation | `as_ui_invoke_action`, `as_ui_send_keys` |
| Screenshot (IDE/Desktop) | ✅ Vollständig | .NET/Win32 | – | `as_ui_screenshot`, `as_ui_focus` |
| Backup/Restore | ✅ / ⚠️ | Copy/ZIP | Restore = confirm | `as_backup_file`, `as_restore_backup` |

Legende: ✅ Vollständig · ⚠️ Gefährlich, nur mit Bestätigung · 🔶 Teilweise/Best-Effort/Plugin-abhängig

## Ehrliche Grenzen

- **Live-IDE-MCP** (`stdioMcpServer`) ist im aktuellen Build *deklariert*, aber das `com.intellij.mcpServer`-Plugin ist **nicht installiert** → `as_ide_call_mcp_tool` meldet das klar und verweist auf die CLI-Tools. (Plugin nachrüstbar via Marketplace.)
- **`inspect`** indexiert das Projekt zuerst → kann Minuten dauern; nicht parallel zur offenen IDE mit gleichem Projekt laufen lassen (Config-Dir-Konflikt).
- **UI-Automation** (SendKeys) ist die unzuverlässigste Schicht (Tastatur-Layout/Fokus-abhängig) → immer per Screenshot verifizieren; nur Notnagel.
- **Windows-only** (bewusst).

## Ausbaustufe 2 (neue Operator-Features)

| Funktion | Status | Tool(s) |
|----------|--------|---------|
| „Run"-Button (build→install→launch→logs→Crash→Screenshot) | ✅ | `as_run_app` (Build live verifiziert) |
| Logcat-Streaming (Watch + Offset-Polling) | ✅ | `as_logcat_watch_start/read/stop`, `as_proc_list/stop` |
| Lint-Report → strukturierte Findings | ✅ live (8 Warnings geparst) | `as_gradle_lint` |
| Test-Report → pass/fail + Failing-Cases | ✅ (JUnit-XML) | `as_gradle_test` |
| Build-Fehler-Remediation (Fehler→Fix-Tool) | ✅ | in `_run_gradle`/`diagnostics.py` |
| Version-Catalog (`libs.versions.toml`) | ✅ | `as_project_info` |
| Stacktrace de-obfuskieren | ✅ | `as_retrace` |
| Bildschirm-Aufnahme | ✅ | `as_screenrecord` |
| AAB → APK-Set + Install (bundletool, Auto-Download) | ✅ | `as_aab_build_apks`, `as_aab_install` |
| MCP-Resources + Prompts | ✅ | `asmcp://config|idea-log|projects/recent`, Prompts `run_app`/`diagnose_build` |
| `.mcpb`-Paket | ✅ vorbereitet | `manifest.json` + `scripts/build_mcpb.ps1` (docs/PACKAGING.md) |

## Teststatus

- `--list-tools` → **73 Tools** registriert (+ 3 Resources, 2 Prompts).
- `scripts/smoke_test.py` → **17/17 PASS** (Status, Geräte, AVDs, SDK, Live-IDE-Status, Logs, Bestätigungs-Gating, Pfad-Allowlist-Ablehnung, Scaffold + Validate + Info + Backup).
- `scripts/build_test.py` → echter `gradlew assembleDebug` des erzeugten Projekts → **BUILD SUCCESSFUL** → signierte `app-debug.apk` → `as_apk_info` (com.example.smoke, minSdk 24, target 34) + Signatur verifiziert.
- `scripts/security_test.py` → **6/6 PASS** (adb-shell-Gate blockt `;`, `|`, `&&`, `$()`, unbekannte Befehle; Read-only-Befehle passieren).
- `pytest tests` → **15/15 PASS** (Parser: gradle/adb/avd/sdk/aapt/lint-XML/JUnit-XML/version-catalog/diagnostics).
- `scripts/feature_test.py` → `as_run_app` baut APK (build-step ok), `as_gradle_lint` liefert 8 strukturierte Findings live, Watch-/Device-Guards korrekt.
- `evals/android_studio_eval.xml` → 10 verifizierbare Read-only-Eval-Fragen (mcp-builder-Format).
- Headless `as_inspect_code` live ausgeführt (studio.bat inspect) → Reports geparst.
- **Adversarielles Multi-Agent-Code-Review** (24 Agenten, 4 Dimensionen): 14 bestätigte Findings → 12 gefixt (u.a. adb-shell-Injection-Härtung, Pfad-Traversal in inspect/restore, FD-Leak, MCP-Brücken-Cleanup, Parser-Robustheit, gebündelter Gradle-Wrapper statt langsamem FS-Scan), 2 begründet abgelehnt.

> Hinweis: uv zeigt evtl. „Failed to hardlink files" — rein kosmetisch (Cache auf C:, Projekt auf E:); via `UV_LINK_MODE=copy` abstellbar.
