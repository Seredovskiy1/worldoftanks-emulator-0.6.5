@echo off
chcp 65001 > nul
setlocal

REM ============================================================
REM   WoT 0.6.5 Emulator - Game Launcher
REM ============================================================

set "GAME_DIR=%~dp0"
set "GAME_EXE=%GAME_DIR%WorldOfTanks.exe"
set "HOSTS=%WINDIR%\System32\drivers\etc\hosts"

REM ---- Verify hosts is configured ----
findstr /C:"# WoT Emulator" "%HOSTS%" >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] hosts is not configured for the WoT Emulator.
    echo [!] Please run setup.bat first ^(requires administrator rights^).
    echo.
    pause
    exit /b 1
)

REM ---- Verify game exe exists ----
if not exist "%GAME_EXE%" (
    echo [!] Cannot find WorldOfTanks.exe in:
    echo     %GAME_DIR%
    echo.
    echo [!] Place play.bat next to WorldOfTanks.exe inside the game folder.
    echo.
    pause
    exit /b 1
)

REM ---- Launch game ----
echo [*] Starting World of Tanks...
start "" "%GAME_EXE%"
exit /b 0
