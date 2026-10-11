@echo off
rem Double-click for the first-run setup: makes the Python environment (.venv) if it is missing,
rem then asks for the token, the servers, the model server, the memory service and the keys,
rem checks each one, and writes .env and config\sanya.yaml. Run it again any time to change them.
title Sanya setup
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Making the Python environment ...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-env.ps1"
    if errorlevel 1 (
        echo.
        echo The environment could not be made. Install Python 3.11 or newer, or uv, and try again.
        pause
        exit /b 1
    )
    echo.
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
echo.
pause
