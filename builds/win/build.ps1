# builds/win/build.ps1
# Produce a self-contained keybridgeBT win-receiver package.
#
# Usage (from repo root, in PowerShell):
#   .\builds\win\build.ps1
#
# Output:
#   builds\dist\keybridgebt-win-<version>.zip
#
# Requirements:
#   - Python 3.11+  (py / python on PATH)
#   - pip           (bundled with Python)
#   - Internet access (to download wheels into the venv)

#Requires -Version 5.1
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Paths and version
# ---------------------------------------------------------------------------
$RepoRoot  = (Resolve-Path "$PSScriptRoot\..\..").Path
$DistDir   = Join-Path $RepoRoot "builds\dist"
$VersionFile = Join-Path $RepoRoot "VERSION"

if (Test-Path $VersionFile) {
    $Version = (Get-Content $VersionFile -Raw).Trim()
} elseif ($env:VERSION) {
    $Version = $env:VERSION
} else {
    $Version = "0.1.0"
}

$PkgName = "keybridgebt-win-$Version"
$PkgDir  = Join-Path $DistDir $PkgName

Write-Host "=== keybridgeBT win-receiver build ===" -ForegroundColor Cyan
Write-Host "Version : $Version"
Write-Host "Output  : $DistDir\$PkgName.zip"
Write-Host ""

# ---------------------------------------------------------------------------
# Clean previous build
# ---------------------------------------------------------------------------
if (Test-Path $PkgDir) { Remove-Item $PkgDir -Recurse -Force }
New-Item -ItemType Directory -Path $PkgDir | Out-Null
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

# ---------------------------------------------------------------------------
# 1. Copy source
# ---------------------------------------------------------------------------
Write-Host "[1/5] Copying source files..."
$WinSrc = Join-Path $RepoRoot "win-receiver"
Copy-Item (Join-Path $WinSrc "keybridgebt_win") $PkgDir -Recurse
Copy-Item (Join-Path $WinSrc "config.yaml")      (Join-Path $PkgDir "config.yaml")
Copy-Item (Join-Path $WinSrc "requirements.txt") (Join-Path $PkgDir "requirements.txt")

# ---------------------------------------------------------------------------
# 2. Create isolated Python virtual environment
# ---------------------------------------------------------------------------
Write-Host "[2/5] Creating Python virtual environment..."

# Prefer 'py -3.11' launcher if available, fall back to python
$PyExe = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
& $PyExe -m venv (Join-Path $PkgDir ".venv")
if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }

$PipExe  = Join-Path $PkgDir ".venv\Scripts\pip.exe"
$PyVenv  = Join-Path $PkgDir ".venv\Scripts\python.exe"

# ---------------------------------------------------------------------------
# 3. Install dependencies
# ---------------------------------------------------------------------------
Write-Host "[3/5] Installing dependencies (this may take a moment)..."
& $PipExe install --quiet --upgrade pip
& $PipExe install --quiet -r (Join-Path $PkgDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# ---------------------------------------------------------------------------
# 4. Write launcher
# ---------------------------------------------------------------------------
Write-Host "[4/5] Writing launcher script..."
$RunBat = Join-Path $PkgDir "run.bat"
@"
@echo off
REM Launch keybridgeBT win-receiver using the bundled virtual environment.
REM Run from its own directory or double-click to start.
setlocal
set "DIR=%~dp0"
"%DIR%.venv\Scripts\python.exe" -m keybridgebt_win %*
endlocal
"@ | Set-Content $RunBat -Encoding ASCII

# PowerShell launcher (alternative)
$RunPs1 = Join-Path $PkgDir "run.ps1"
@"
# Launch keybridgeBT win-receiver using the bundled virtual environment.
`$Dir = Split-Path -Parent `$MyInvocation.MyCommand.Path
& "`$Dir\.venv\Scripts\python.exe" -m keybridgebt_win @args
"@ | Set-Content $RunPs1 -Encoding UTF8

# ---------------------------------------------------------------------------
# 5. Archive
# ---------------------------------------------------------------------------
Write-Host "[5/5] Creating zip archive..."
$ZipPath = Join-Path $DistDir "$PkgName.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path $PkgDir -DestinationPath $ZipPath

# Compute SHA-256 checksum
$Hash = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLower()

Write-Host ""
Write-Host "=== Build complete ===" -ForegroundColor Green
Write-Host "Archive : $ZipPath"
Write-Host "SHA-256 : $Hash"
Write-Host ""
Write-Host "To deploy:"
Write-Host "  Expand-Archive $PkgName.zip -DestinationPath C:\keybridgebt\"
Write-Host "  C:\keybridgebt\$PkgName\run.bat        # run directly"
