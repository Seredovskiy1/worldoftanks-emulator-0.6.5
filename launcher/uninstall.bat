@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

REM ============================================================
REM   WoT 0.6.5 Emulator - Remove hosts entries
REM ============================================================

REM ---- Check admin ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Need administrator rights to edit hosts file.
    echo [INFO] Restarting with UAC prompt...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

set "HOSTS=%WINDIR%\System32\drivers\etc\hosts"
set "HOSTS_BAK=%HOSTS%.wotemu.bak"
set "HOSTS_TMP=%HOSTS%.wotemu.tmp"

echo.
echo === WoT 0.6.5 Emulator Uninstall ===
echo.

findstr /C:"# WoT Emulator" "%HOSTS%" >nul 2>&1
if %errorlevel% neq 0 (
    echo [=] No WoT Emulator entries found in hosts.
    echo.
    pause
    exit /b 0
)

REM ---- Backup ----
copy /Y "%HOSTS%" "%HOSTS_BAK%" >nul

REM ---- Remove block: from "# WoT Emulator" line up to and including next 2 lines ----
powershell -NoProfile -Command ^
    "$lines = Get-Content -LiteralPath '%HOSTS%';" ^
    "$out = New-Object System.Collections.Generic.List[string];" ^
    "$skip = 0;" ^
    "foreach ($l in $lines) {" ^
    "  if ($skip -gt 0) { $skip--; continue; }" ^
    "  if ($l -match '^\s*#\s*WoT Emulator') { $skip = 2; continue; }" ^
    "  $out.Add($l);" ^
    "}" ^
    "[IO.File]::WriteAllLines('%HOSTS_TMP%', $out, [Text.Encoding]::ASCII)"

if not exist "%HOSTS_TMP%" (
    echo [ERROR] Failed to process hosts file. No changes made.
    echo [ERROR] A backup is at: %HOSTS_BAK%
    pause
    exit /b 1
)

move /Y "%HOSTS_TMP%" "%HOSTS%" >nul

REM ---- Flush DNS cache ----
ipconfig /flushdns >nul

echo [OK] WoT Emulator entries removed from hosts.
echo [OK] Backup saved to: %HOSTS_BAK%
echo.
pause
exit /b 0
