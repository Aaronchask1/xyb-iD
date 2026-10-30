# xyb · Identity Lab 4.0

Herramienta local para organizar y consultar nombres de usuario de Discord y enlaces de servidor. Nueva interfaz en español con tema oscuro/violeta, sesiones en vivo, historial, favoritos y generador de candidatos.

## Abrir

Haz doble clic en **Abrir xyb.bat** dentro de `D:\nuevo`. Se abrirá el panel en el navegador:

http://127.0.0.1:8765

También puedes iniciarlo desde PowerShell:

```powershell
cd D:\nuevo
.\.venv\Scripts\python.exe server.py
```

Si ya está abierto, el lanzador vuelve a abrir el panel existente. Para cerrar el servidor usa **Ajustes → Guardar y cerrar aplicación** o Ctrl+C en la terminal. Cerrar únicamente la pestaña deja la sesión ejecutándose.

La interfaz clásica continúa disponible mediante **Abrir xyb clasico.bat** o `checker.py`. Los archivos anteriores de `data` y `results` se conservan.

## Qué incluye

- **Usuarios:** normaliza `@nombre`, convierte a minúsculas, elimina duplicados y separa candidatos, nombres en uso, entradas inválidas y respuestas sin verificar.
- **Vanities:** acepta códigos y enlaces `discord.gg/...` o `discord.com/invite/...`; muestra la invitación activa, servidor y cantidades aproximadas de miembros cuando Discord las proporciona. Conserva mayúsculas en los códigos de invitación.
- **Sesiones:** progreso, solicitudes, pausa, reanudación, detención y reintento de pendientes. Una solicitud en curso puede terminar después de pulsar Pausar; la siguiente espera.
- **Recuperación:** cada resultado se guarda en SQLite. Las sesiones interrumpidas se pueden reanudar desde Historial sin repetir resultados ya guardados.
- **Generador local:** letras, números, combinaciones, prefijos, sufijos y patrones. `?` representa una letra y `#` un número; por ejemplo `??##`.
- **Favoritos:** separados entre usuarios y vanities, con copia al portapapeles.
- **Resultados:** búsqueda, filtros, páginas de 100 filas y exportación a CSV, JSON o TXT. Se exporta la sesión seleccionada con sus filtros actuales.
- **Listas:** pegar texto, importar TXT o cargar listas existentes del proyecto desde el selector.
- **Validación local:** comprueba formato sin conectar a Discord. No presenta esos resultados como disponibilidad.
- **Ajustes:** intervalo, timeout y uso opcional de la primera línea de `data/proxies.txt`. La interfaz nunca muestra las credenciales del proxy.

## Cómo interpretar los resultados

| Modo | Estado | Significado |
|---|---|---|
| Usuario | Candidato | El endpoint respondió explícitamente `taken=false`. Requiere confirmación al cambiar el nombre en Discord. |
| Usuario | En uso | El endpoint respondió explícitamente `taken=true`. |
| Vanity | Invitación activa | La invitación pública responde; puede ser una vanity o una invitación normal. |
| Vanity | Sin invitación | Discord devolvió `Unknown Invite`. **No confirma que la vanity pueda asignarse.** |
| Ambos | Sin verificar | Error de red, límite, rechazo o respuesta sin datos concluyentes. |
| Ambos | Inválido | El formato no supera la validación local; no se envía una solicitud. |
| Local | Formato válido | Solo validación de formato, sin consulta de disponibilidad. |

Los nombres antiguos `Nombre#1234` no son nombres únicos actuales. Los puntos al inicio o final de un nombre sí son válidos; dos puntos consecutivos no lo son, según [las reglas oficiales de nombres](https://support.discord.com/hc/en-us/articles/12620128861463-New-Usernames-Display-Names).

La consulta de usuarios conserva el endpoint no documentado que utilizaba el proyecto. Puede cambiar o dejar de responder; no constituye una reserva de nombre. Las vanities usan la [consulta oficial de invitaciones](https://docs.discord.com/developers/resources/invite#get-invite). La asignación definitiva se comprueba en los ajustes del servidor. [Discord explica las condiciones de los enlaces personalizados](https://support.discord.com/hc/en-us/articles/115001542132-Custom-Invite-Link).

## Conexión y datos

La interfaz nueva utiliza una solicitud a la vez y un intervalo configurable de 1 a 60 segundos. Respeta `retry_after`, `Retry-After` y los encabezados de agotamiento de cuota. Cambiar de proxy no evita la espera. Tras tres intentos sin respuesta concluyente, el resultado queda sin verificar.

- `results/lab.sqlite3`: sesiones, resultados y favoritos nuevos.
- `data/lab_settings.json`: ajustes de la interfaz nueva, sin tokens de usuario.
- `web/`: interfaz local; no necesita CDNs, fuentes remotas ni compilación con Node.
- `docs/RESEARCH-2026-10-29.md`: repositorios y documentación revisados.
- `docs/README-v3.md`: documentación previa conservada como referencia histórica.

No se importan automáticamente los antiguos resultados como conclusiones actuales. Puedes cargar las listas y consultarlas de nuevo. No se reescriben los históricos anteriores.

El servidor escucha solo en `127.0.0.1`; las escrituras exigen un token de la sesión local y se rechazan orígenes externos. Los resultados y credenciales locales se excluyen de Git.

## Instalación y pruebas

El entorno existente fue probado con Python 3.15, aiohttp 3.14.5 y rich 15.0.0 en Windows. No se ha sustituido tu entorno.

Para crear otro entorno:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Pruebas locales, sin consultar Discord ni enviar webhooks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Puerto alternativo o inicio sin abrir navegador:

```powershell
.\.venv\Scripts\python.exe server.py --port 8767 --no-browser
```

La suite cubre clasificación estricta, límites, generación acotada, persistencia, cancelación y reanudación, exportaciones, protección del servidor local y compatibilidad con el runner clásico.

## Créditos

Esta actualización extiende el proyecto xyb existente. La documentación previa atribuía el checker original a [cloudzik1337](https://guns.lol/cloudzik1337). Los repositorios consultados se usaron como referencias de características; no se instalaron ni ejecutaron sus programas.
