@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

REM ============================================================
REM   WoT 0.6.5 Emulator - Setup hosts entries (run once)
REM ============================================================

set "SERVER_HOST=63.185.68.216"
set "SERVER_IP="

REM ---- Check admin ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Need administrator rights to edit hosts file.
    echo [INFO] Restarting with UAC prompt...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

set "HOSTS=%WINDIR%\System32\drivers\etc\hosts"

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "$ErrorActionPreference='Stop'; [System.Net.Dns]::GetHostAddresses('%SERVER_HOST%') ^| Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork } ^| Select-Object -First 1 -ExpandProperty IPAddressToString" 2^>nul`) do set "SERVER_IP=%%I"

echo.
echo === WoT 0.6.5 Emulator Setup ===
echo.
echo Target server: %SERVER_HOST%
echo Resolved IP:   %SERVER_IP%
echo Hosts file:    %HOSTS%
echo.

if "%SERVER_HOST%"=="YOUR_SERVER_IP_OR_DOMAIN" (
    echo [ERROR] You forgot to edit setup.bat!
    echo [ERROR] Open setup.bat in a text editor and replace YOUR_SERVER_IP_OR_DOMAIN
    echo         with your actual server IP or domain ^(e.g., 123.45.67.89^).
    echo.
    pause
    exit /b 1
)

if not defined SERVER_IP (
    echo [ERROR] Unable to resolve %SERVER_HOST% through DNS.
    echo [ERROR] Check that the domain has an A record and try again.
    echo.
    pause
    exit /b 1
)

REM ---- Check if already configured ----
findstr /C:"# WoT Emulator" "%HOSTS%" >nul 2>&1
if %errorlevel% equ 0 (
    echo [=] Hosts already contain WoT Emulator entries.
    echo [=] If you want to change the target server, run uninstall.bat first.
    echo.
    pause
    exit /b 0
)

REM ---- Add entries ----
echo [+] Adding entries to hosts...
(
    echo.
    echo # WoT Emulator ^(do not edit manually^)
    echo %SERVER_IP% login-master.worldoftanks.com
    echo %SERVER_IP% game.worldoftanks.com
) >> "%HOSTS%"

REM ---- Flush DNS cache ----
ipconfig /flushdns >nul

echo.
echo [OK] Setup complete!
echo [OK] You can now run play.bat to start the game.
echo.
pause
exit /b 0
