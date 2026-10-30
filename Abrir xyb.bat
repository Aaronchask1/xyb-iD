@echo off
setlocal
cd /d "%~dp0"
title xyb Identity Lab
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Iniciar xyb.ps1"
