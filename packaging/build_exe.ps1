# Build LightGun.exe with PyInstaller, then smoke-test it.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1            # dist\LightGun\LightGun.exe
#   powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1 -OneFile   # dist\LightGun.exe
#
# Uses the repo's .venv (pip install -e ".[dev,build]" first).

param([switch]$OneFile)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv found. Run: py -m venv .venv; .venv\Scripts\pip install -e "".[dev,build]""" }

$work = Join-Path $PSScriptRoot "build"
New-Item -ItemType Directory -Force $work | Out-Null
& $py (Join-Path $PSScriptRoot "make_icon.py") (Join-Path $work "lightgun.ico")
if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

$env:LIGHTGUN_ONEFILE = if ($OneFile) { "1" } else { "0" }
& $py -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $root "dist") --workpath $work `
    (Join-Path $PSScriptRoot "lightgun.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = if ($OneFile) { Join-Path $root "dist\LightGun.exe" } else { Join-Path $root "dist\LightGun\LightGun.exe" }

# Smoke test: build the window (no camera) against a throwaway config, then exit.
$smoke = Join-Path $work "smoke"
Remove-Item -Recurse -Force $smoke -ErrorAction SilentlyContinue
$env:LIGHTGUN_CONFIG = Join-Path $smoke "config.toml"
$p = Start-Process -FilePath $exe -ArgumentList "--smoke" -Wait -PassThru
Remove-Item Env:\LIGHTGUN_CONFIG
$log = Join-Path $smoke "lightgun.log"
if ($p.ExitCode -ne 0 -or -not (Select-String -Path $log -Pattern "smoke ok" -Quiet)) {
    if (Test-Path $log) { Get-Content $log }
    throw "Smoke test failed (exit $($p.ExitCode))"
}
Get-Content $log | Select-Object -Last 1
Write-Host "Built $exe"
