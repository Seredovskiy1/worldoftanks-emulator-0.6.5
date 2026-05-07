# build_player_distribution.ps1
# Збирає zip-архів для роздачі гравцям:
#   - копіює клієнт WoT
#   - додає launcher (setup.bat, play.bat, uninstall.bat, README.md)
#   - підставляє SERVER_HOST у скрипти
#   - пакує в zip
#
# Usage:
#   .\build_player_distribution.ps1 -GamePath "C:\Path\To\World_of_Tanks" -ServerHost "myserver.example.com"
#
# Optional:
#   -OutputZip    шлях до результуючого .zip (за замовчуванням: WoT_PrivateServer.zip)
#   -OutputDir    куди розпакувати перед zipуванням (за замовчуванням: dist/)

param(
    [Parameter(Mandatory = $true)]
    [string]$GamePath,

    [Parameter(Mandatory = $true)]
    [string]$ServerHost,

    [string]$OutputZip = "WoT_PrivateServer.zip",
    [string]$OutputDir = "dist"
)

$ErrorActionPreference = "Stop"

$ScriptRoot = $PSScriptRoot
$LauncherDir = Join-Path $ScriptRoot "launcher"

# --- Validate inputs ---
if (-not (Test-Path $GamePath)) {
    Write-Error "Game path not found: $GamePath"
    exit 1
}

$gameExe = Join-Path $GamePath "WorldOfTanks.exe"
if (-not (Test-Path $gameExe)) {
    Write-Error "WorldOfTanks.exe not found in: $GamePath"
    exit 1
}

if (-not (Test-Path $LauncherDir)) {
    Write-Error "Launcher directory not found: $LauncherDir"
    exit 1
}

# --- Prepare output dir ---
$DistRoot = Join-Path $ScriptRoot $OutputDir
$DistGameDir = Join-Path $DistRoot "World_of_Tanks"

if (Test-Path $DistRoot) {
    Write-Host "[*] Cleaning existing $DistRoot..." -ForegroundColor Yellow
    Remove-Item -Path $DistRoot -Recurse -Force
}

New-Item -Path $DistGameDir -ItemType Directory -Force | Out-Null

# --- Copy game files ---
Write-Host "[*] Copying client from $GamePath..." -ForegroundColor Cyan
Copy-Item -Path "$GamePath\*" -Destination $DistGameDir -Recurse -Force

# --- Copy launcher files ---
Write-Host "[*] Copying launcher files..." -ForegroundColor Cyan
$launcherFiles = @("setup.bat", "play.bat", "uninstall.bat", "README.md")
foreach ($f in $launcherFiles) {
    Copy-Item -Path (Join-Path $LauncherDir $f) -Destination $DistGameDir -Force
}

# --- Patch SERVER_HOST in setup.bat and uninstall.bat ---
Write-Host "[*] Setting SERVER_HOST=$ServerHost..." -ForegroundColor Cyan
foreach ($bat in @("setup.bat", "uninstall.bat")) {
    $batPath = Join-Path $DistGameDir $bat
    if (Test-Path $batPath) {
        $content = Get-Content $batPath -Raw
        $content = $content -replace 'YOUR_SERVER_IP_OR_DOMAIN', [regex]::Escape($ServerHost).Replace('\','')
        Set-Content -Path $batPath -Value $content -Encoding ASCII
    }
}

# --- Verify ---
$setupBat = Get-Content (Join-Path $DistGameDir "setup.bat") -Raw
if ($setupBat -match 'YOUR_SERVER_IP_OR_DOMAIN') {
    Write-Error "Failed to patch SERVER_HOST in setup.bat"
    exit 1
}

# --- Zip ---
$ZipPath = Join-Path $ScriptRoot $OutputZip
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

Write-Host "[*] Creating archive $OutputZip..." -ForegroundColor Cyan
Compress-Archive -Path $DistGameDir -DestinationPath $ZipPath -CompressionLevel Optimal

# --- Done ---
$ZipSize = (Get-Item $ZipPath).Length / 1MB
Write-Host ""
Write-Host "[OK] Distribution ready!" -ForegroundColor Green
Write-Host "     Archive: $ZipPath ($('{0:N1}' -f $ZipSize) MB)" -ForegroundColor Green
Write-Host "     Server:  $ServerHost" -ForegroundColor Green
Write-Host ""
Write-Host "Send the .zip to players. They unpack it, run setup.bat once, then play.bat to play." -ForegroundColor White
