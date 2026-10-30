#!/bin/sh
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    printf '%s\n' 'No se encuentra .venv. Consulta README.md para instalar la herramienta.' >&2
    exit 1
fi
exec .venv/bin/python server.py "$@"
