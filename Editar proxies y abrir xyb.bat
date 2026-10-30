@echo off
setlocal
title xyb - Proxies y Username Checker
cd /d "%~dp0"

echo ===============================================
echo   xyb Username Checker
echo ===============================================
echo.
echo Se abrira Bloc de notas para editar las proxies.
echo Pega una por linea, guarda con Ctrl+S y cierralo.
echo.
pause
start "" /wait notepad.exe "%~dp0data\proxies.txt"

echo.
echo Archivo guardado. Iniciando xyb en PowerShell...
echo.
powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0Iniciar xyb.ps1"
