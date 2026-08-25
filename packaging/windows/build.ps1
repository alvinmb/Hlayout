# Local Windows build: freezes HLayout with PyInstaller and, if Inno
# Setup is installed, wraps it into an installer .exe too.
#
# Run from the repo root in PowerShell:
#   .\packaging\windows\build.ps1
#   .\packaging\windows\build.ps1 -Version 0.2.0
#
# (CI runs the same two steps on windows-latest for every tagged
# release -- see .github/workflows/build-installers.yml -- this script
# is for building/testing a copy locally without pushing a tag.)

param(
    [string]$Version = "0.0.0-local"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepoRoot

Write-Host "==> Installing/upgrading build dependencies" -ForegroundColor Cyan
python -m pip install -r requirements.txt pyinstaller

Write-Host "==> Removing old build/dist" -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Freezing with PyInstaller (version $Version)" -ForegroundColor Cyan
$env:HLAYOUT_VERSION = $Version
python -m PyInstaller packaging\hlayout.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

Write-Host "==> Onedir build ready: dist\hlayout\hlayout.exe" -ForegroundColor Green

# Sanity check: fail loudly (instead of shipping a bloated build) if
# matplotlib or a Qt binding snuck back in -- see packaging/hlayout.spec
# for why that can happen (shapely.plotting pulls matplotlib, whose own
# PyInstaller hook then bundles whatever Qt binding is on this machine).
$stray = Get-ChildItem -Recurse "dist\hlayout" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "qt5|qt6|matplotlib" }
if ($stray) {
    Write-Warning "Found unexpected Qt/matplotlib files in the build -- packaging/hlayout.spec's excludes list may need updating:"
    $stray | ForEach-Object { Write-Warning "  $($_.FullName)" }
}

$isccCmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if ($isccCmd) {
    $isccPath = $isccCmd.Path
} else {
    $candidate = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $candidate) { $isccPath = $candidate } else { $isccPath = $null }
}

if ($isccPath) {
    Write-Host "==> Building installer with Inno Setup" -ForegroundColor Cyan
    & $isccPath "packaging\windows\installer.iss" "/DMyAppVersion=$Version"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed" }
    Write-Host "==> Installer ready: dist-installers\HLayout-$Version-windows-setup.exe" -ForegroundColor Green
} else {
    Write-Host "==> Inno Setup (ISCC.exe) not found -- skipping the installer step." -ForegroundColor Yellow
    Write-Host "    Install it from https://jrsoftware.org/isdl.php to also build a" -ForegroundColor Yellow
    Write-Host "    proper installer .exe, or just run dist\hlayout\hlayout.exe directly." -ForegroundColor Yellow
}
