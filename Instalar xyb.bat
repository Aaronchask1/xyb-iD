@echo off
setlocal
cd /d "%~dp0"
title Instalar xyb Identity Lab
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Instalar xyb.ps1"
echo.
pause
