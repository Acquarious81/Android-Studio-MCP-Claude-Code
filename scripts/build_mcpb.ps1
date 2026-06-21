# Builds android-studio-mcp.mcpb for Claude Desktop ("Erweiterung installieren").
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_mcpb.ps1
#
# The bundle launches the project's tested uv virtual environment
# (.venv\Scripts\python.exe -m android_studio_mcp), so it is ABI-safe and needs
# no dependency vendoring. Run `uv sync` first so the venv exists.

$ErrorActionPreference = "Stop"
$root  = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $root "dist\mcpb"
$dist  = Join-Path $root "dist"

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { throw "venv not found at $venvPy - run 'uv sync' first." }

if (-not (Test-Path $dist)) { New-Item -ItemType Directory -Force -Path $dist | Out-Null }
$mcpb = Join-Path $dist "android-studio-mcp.mcpb"

# Build the archive with Python's zipfile so entries use standard forward
# slashes and manifest.json sits at the archive root.
$py = @"
import zipfile, os
root = r'$root'
out = r'$mcpb'
if os.path.exists(out): os.remove(out)
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for it in ('manifest.json', 'README.md'):
        z.write(os.path.join(root, it), it)
    for dp, dn, fn in os.walk(os.path.join(root, 'src')):
        dn[:] = [d for d in dn if d != '__pycache__']
        for f in fn:
            full = os.path.join(dp, f)
            z.write(full, os.path.relpath(full, root).replace(os.sep, '/'))
print('entries', len(zipfile.ZipFile(out).namelist()))
"@
& $venvPy -c $py
Write-Host "Built -> $mcpb ($([math]::Round((Get-Item $mcpb).Length/1KB,1)) KB)"
