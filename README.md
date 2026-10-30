# xyb · Identity Lab 4.0

Herramienta local para organizar y consultar nombres de usuario de Discord y enlaces de servidor. Nueva interfaz en español con tema oscuro/violeta, sesiones en vivo, historial, favoritos y generador de candidatos.

## Abrir

En Windows, ejecuta **Instalar xyb.bat** una vez y después haz doble clic en **Abrir xyb.bat** dentro de la carpeta del proyecto. Se abrirá el panel en el navegador:

http://127.0.0.1:8765

También puedes iniciarlo desde PowerShell:

```powershell
cd D:\nuevo
.\.venv\Scripts\python.exe server.py
```

Si ya está abierto, el lanzador vuelve a abrir el panel existente. Para cerrar el servidor usa **Ajustes → Guardar y cerrar aplicación** o Ctrl+C en la terminal. Cerrar únicamente la pestaña deja la sesión ejecutándose. El servidor es local: cada persona ejecuta su propia copia en su computadora.

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
- **Ajustes:** intervalo, timeout, proxy y webhook de Discord desde la web. Las credenciales guardadas no vuelven a mostrarse en el navegador.

## Proxy y webhook desde la web

En **Ajustes → Conexión**, introduce el proxy (por ejemplo `usuario:contraseña@host:puerto` o `host:puerto:usuario:contraseña`) y guarda la conexión. Activa **Usar mi proxy guardado** y guarda los ajustes para aplicarlo a las próximas sesiones. Solo se usa la primera línea de `data/proxies.txt`: al cambiarla o borrarla desde la interfaz se conserva una copia local del archivo anterior en `data/proxies-backup-*.txt`.

En la misma página puedes guardar una URL de webhook de Discord, enviar un mensaje de prueba y activar la notificación al completar una sesión. El mensaje contiene únicamente el tipo de sesión y el número de resultados. La configuración de notificación se toma al iniciar la sesión. Las credenciales se guardan localmente en `data/`, fuera de Git; quien tenga acceso a esos archivos puede leerlas.

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

La interfaz utiliza una solicitud de consulta a la vez y un intervalo configurable de 1 a 60 segundos. Respeta `retry_after`, `Retry-After` y los encabezados de agotamiento de cuota. Las esperas se conservan al reiniciar y se comparten de forma conservadora con los webhooks; cambiar de proxy no evita la espera. Si Discord rechaza el acceso con 401 o 403, la sesión se detiene con los resultados guardados. Tras tres intentos sin respuesta concluyente, el resultado queda sin verificar. Una notificación rechazada o bloqueada por una espera se muestra como error y no se reenvía automáticamente.

- `results/lab.sqlite3`: sesiones, resultados y favoritos nuevos.
- `data/lab_settings.json`: ajustes de la interfaz nueva, sin tokens de usuario.
- `web/`: interfaz local; no necesita CDNs, fuentes remotas ni compilación con Node.
- `docs/RESEARCH-2026-10-29.md`: repositorios y documentación revisados.
- `docs/RESEARCH-2026-10-29-LIMITS.md`: revisión de los límites de Discord.
- `docs/README-v3.md`: documentación previa conservada como referencia histórica.

No se importan automáticamente los antiguos resultados como conclusiones actuales. Puedes cargar las listas y consultarlas de nuevo. No se reescriben los históricos anteriores.

El servidor escucha solo en `127.0.0.1`; las escrituras exigen un token de la sesión local y se rechazan orígenes externos. Los resultados y credenciales locales se excluyen de Git.

## Instalación y pruebas

Necesitas Python 3.10 o superior y conexión a Internet durante la instalación. En Windows se recomienda Python 3.13, disponible en [python.org](https://www.python.org/downloads/). El entorno existente se probó también con Python 3.15, aiohttp 3.14.5 y rich 15.0.0. El repositorio es privado: quienes quieran descargarlo necesitan acceso concedido por su propietario.

En Windows, ejecuta **Instalar xyb.bat** o instala manualmente desde PowerShell abierto en la carpeta descargada:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

En macOS o Linux, desde la carpeta del proyecto:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
sh run.sh
```

Para la interfaz clásica en macOS o Linux, usa `.venv/bin/python checker.py`. Las instrucciones de macOS y Linux no se han probado en este equipo Windows.

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
