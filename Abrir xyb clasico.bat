@echo off
setlocal
cd /d "%~dp0"
title xyb - Interfaz clasica
"%~dp0.venv\Scripts\python.exe" "%~dp0checker.py"
pause
