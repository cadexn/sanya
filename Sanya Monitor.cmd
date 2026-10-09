@echo off
rem Double-click to open Sanya Monitor: a page in the browser that shows what she is doing.
rem It only reads what she records. Close this window to stop the monitor; Sanya keeps running.
title Sanya Monitor
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m monitor
if errorlevel 1 (
    echo.
    pause
)
