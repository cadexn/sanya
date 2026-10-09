@echo off
rem Double-click to stop Sanya. Use the same account that started her: one account
rem cannot stop another's process without administrator rights.
title Stop Sanya
echo Stopping Sanya as %USERDOMAIN%\%USERNAME% ...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Stop-Sanya.ps1"
echo.
pause
