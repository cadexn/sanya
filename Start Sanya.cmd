@echo off
rem Double-click to start Sanya in the background. This window says whether she came up.
rem She runs as whoever starts her, so use the account that should own her process.
rem Stop her with "Stop Sanya.cmd", from the same account.
title Start Sanya
echo Starting Sanya as %USERDOMAIN%\%USERNAME% ...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-Sanya.ps1"
echo.
pause
