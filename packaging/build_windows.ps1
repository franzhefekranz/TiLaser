# Baut unter Windows: EXE (PyInstaller), Installer (Inno Setup) und portable ZIP.
# Aufruf aus dem Repository-Wurzelordner:
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# Voraussetzungen: Python 3.10+, Inno Setup 6 (iscc.exe im PATH oder Standardpfad)
param(
    [switch]$SkipInstall,     # pip-Installation ueberspringen
    [switch]$SkipInstaller    # kein Inno-Setup-Installer
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Version = (python -c "import tilaser; print(tilaser.__version__)").Trim()
Write-Host "== TiLaser $Version ==" -ForegroundColor Cyan

if (-not $SkipInstall) {
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "pip install fehlgeschlagen" }
}

# 1) Tests
python tests\test_core.py
if ($LASTEXITCODE -ne 0) { throw "Tests fehlgeschlagen" }

# 2) PyInstaller (Ordner-Build: startet schneller und wird seltener von Virenscannern beanstandet)
Remove-Item -Recurse -Force build, dist\TiLaser -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --clean packaging\tilaser.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller fehlgeschlagen" }

$Release = Join-Path $Root "dist\release"
New-Item -ItemType Directory -Force $Release | Out-Null

# 3) Portable ZIP: portable.txt sorgt dafuer, dass Einstellungen im Ordner "daten" neben der EXE landen
$Portable = Join-Path $Root "dist\TiLaser-portable"
Remove-Item -Recurse -Force $Portable -ErrorAction SilentlyContinue
Copy-Item -Recurse dist\TiLaser $Portable
Copy-Item README.md, LICENSE $Portable
Copy-Item -Recurse beispiele (Join-Path $Portable "beispiele")
Set-Content -Path (Join-Path $Portable "portable.txt") -Encoding UTF8 -Value @"
TiLaser portable
Solange diese Datei existiert, speichert TiLaser Einstellungen, Maschinenprofil und Presets
im Unterordner 'daten' neben TiLaser.exe. Den ganzen Ordner z. B. auf einen USB-Stick kopieren.
"@
$Zip = Join-Path $Release "TiLaser-$Version-portable-win64.zip"
Remove-Item $Zip -ErrorAction SilentlyContinue
Compress-Archive -Path $Portable -DestinationPath $Zip
Write-Host "Portable: $Zip" -ForegroundColor Green

# 4) Inno Setup
if (-not $SkipInstaller) {
    $Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $Iscc) {
        foreach ($p in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                         "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
            if (Test-Path $p) { $Iscc = $p; break }
        }
    }
    if (-not $Iscc) { throw "Inno Setup (ISCC.exe) nicht gefunden - https://jrsoftware.org/isdl.php" }
    & $Iscc "/DMyAppVersion=$Version" packaging\windows\tilaser.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup fehlgeschlagen" }
    Write-Host "Installer: $Release\TiLaser-$Version-Setup.exe" -ForegroundColor Green
}
