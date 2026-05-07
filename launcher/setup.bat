@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

REM ============================================================
REM   WoT 0.6.5 Emulator - Setup hosts entries (run once)
REM ============================================================

set "SERVER_HOST=YOUR_SERVER_IP_OR_DOMAIN"

REM ---- Check admin ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Need administrator rights to edit hosts file.
    echo [INFO] Restarting with UAC prompt...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

set "HOSTS=%WINDIR%\System32\drivers\etc\hosts"

echo.
echo === WoT 0.6.5 Emulator Setup ===
echo.
echo Target server: %SERVER_HOST%
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
    echo %SERVER_HOST% login-master.worldoftanks.com
    echo %SERVER_HOST% game.worldoftanks.com
) >> "%HOSTS%"

REM ---- Flush DNS cache ----
ipconfig /flushdns >nul

echo.
echo [OK] Setup complete!
echo [OK] You can now run play.bat to start the game.
echo.
pause
exit /b 0
