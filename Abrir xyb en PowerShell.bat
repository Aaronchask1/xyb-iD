@echo off
cd /d "%~dp0"
start "xyb Identity Lab" powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0Iniciar xyb.ps1"
