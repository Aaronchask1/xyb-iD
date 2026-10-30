$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# Prefer the stable, tested Python 3.13 when available. Do not replace an
# existing environment: users may already have local data and dependencies.
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    if (-not (Get-Command py.exe -ErrorAction SilentlyContinue)) {
        Write-Host 'No se encuentra Python. Instala Python 3.13 desde python.org y vuelve a ejecutar este instalador.' -ForegroundColor Red
        exit 1
    }
    $pythonVersion = '-3'
    try {
        & py.exe -3.13 -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $pythonVersion = '-3.13' }
    } catch { }
    $pythonValid = $false
    try {
        & py.exe $pythonVersion -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        $pythonValid = $LASTEXITCODE -eq 0
    } catch { }
    if (-not $pythonValid) {
        Write-Host 'Se necesita Python 3.10 o superior. Instala Python 3.13 y vuelve a intentarlo.' -ForegroundColor Red
        exit 1
    }
    Write-Host 'Creando el entorno local de xyb...'
    & py.exe $pythonVersion -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonExe)) {
        Write-Host 'No se pudo crear el entorno. Revisa la instalacion de Python y los permisos de esta carpeta.' -ForegroundColor Red
        exit 1
    }
}

Write-Host 'Instalando dependencias desde requirements.txt...'
& $pythonExe -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) {
    Write-Host 'No se pudieron instalar las dependencias. Revisa tu conexion y vuelve a ejecutar el instalador.' -ForegroundColor Red
    exit 1
}
Write-Host 'Instalacion terminada. Abre la herramienta con Abrir xyb.bat.' -ForegroundColor Green
