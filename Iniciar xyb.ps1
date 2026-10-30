$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Host 'No se encuentra el entorno .venv. Consulta README.md para instalarlo.' -ForegroundColor Red
    Read-Host 'Pulsa Enter para cerrar'
    exit 1
}
& $pythonExe (Join-Path $PSScriptRoot 'server.py')

Write-Host ''
Read-Host 'Pulsa Enter para cerrar'
