r"""Local XYB dashboard. Run: .venv\Scripts\python.exe server.py"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import secrets
import socket
import sqlite3
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import aiohttp
from aiohttp import web

from config import DATA_DIR, PROJECT_ROOT, RESULTS_DIR, atomic_write_text, load_lines
from lab import JobManager, Store, export_rows, generate_candidates, parse_candidates
from proxy import ProxyManager
from engine import retry_delay

VERSION = "4.0.0"
DEFAULTS = {"delay": 1.5, "timeout": 15, "use_proxy": False, "webhook_enabled": False}
INPUT_FILES = {
    "names": DATA_DIR / "names_to_check.txt",
    "combinations": DATA_DIR / "custom_combinations.txt",
    "remaining": DATA_DIR / "remaining_3char_to_check.txt",
    "pending": RESULTS_DIR / "unverified.txt",
    "legacy_candidates": RESULTS_DIR / "available.txt",
    "vanities": DATA_DIR / "vanities.txt",
}
SERVICE = web.AppKey("service", object)


def validate_options(data):
    options = {key: data.get(key, default) for key, default in DEFAULTS.items()}
    for key, low, high in (("delay", 1, 60), ("timeout", 3, 60)):
        value = options[key]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key}: introduce un número entre {low} y {high} segundos.")
    for key in ("use_proxy", "webhook_enabled"):
        if type(options[key]) is not bool:
            raise ValueError(f"{key} debe ser verdadero o falso.")
    return options


class Service:
    def __init__(self, root, *, client_factory=None):
        self.root = root
        self.token = secrets.token_urlsafe(32)
        self.settings_path = root / "data" / "lab_settings.json"
        self.proxy_path = root / "data" / "proxies.txt"
        self.webhook_path = root / "data" / "lab_webhook.txt"
        self.webhook_status = ""
        self.webhook_next_request = 0.0
        self.webhook_lock = asyncio.Lock()
        self.settings = dict(DEFAULTS)
        self.settings_error = ""
        if self.settings_path.exists():
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict):
                    raise ValueError()
                self.settings = validate_options(data)
            except (ValueError, UnicodeError, OSError):
                self.settings_error = "No se pudieron leer los ajustes. Se usan los valores iniciales; el archivo se conserva."
        self.store = Store(root / "results" / "lab.sqlite3")
        self.session = self.manager = None
        self.client_factory = client_factory

    async def start(self):
        self.session = aiohttp.ClientSession(trust_env=False, headers={"User-Agent": "XYB-Lab/4.0 (local desktop tool)"})
        kwargs = {"client_factory": self.client_factory} if self.client_factory else {}
        self.manager = JobManager(self.store, self.session, on_complete=self.notify_complete, **kwargs)

    async def close(self):
        if self.manager:
            await self.manager.close()
        if self.session:
            await self.session.close()
        self.store.close()

    def proxy(self, options):
        if not options.get("use_proxy"):
            return None
        lines = load_lines(self.proxy_path)
        if not lines:
            raise ValueError("No hay proxy configurado en data/proxies.txt.")
        value = ProxyManager._format(lines[0])
        try:
            url = urlparse(value)
            if url.scheme not in {"http", "https"} or not url.hostname or not url.port or any(c.isspace() for c in value):
                raise ValueError()
        except ValueError:
            raise ValueError("La primera línea de data/proxies.txt no tiene un formato de proxy válido.")
        return value

    def connections(self):
        return {"proxy_configured": bool(load_lines(self.proxy_path)),
                "webhook_configured": bool(load_lines(self.webhook_path))}

    async def send_webhook(self, content):
        lines = load_lines(self.webhook_path)
        if not lines:
            raise ValueError("No hay webhook configurado.")
        webhook = validate_webhook(lines[0])
        async with self.webhook_lock:
            if time.monotonic() < self.webhook_next_request or time.time() < self.store.cooldown_deadline():
                raise ValueError("Discord limitó el webhook. Espera antes de volver a intentarlo.")
            try:
                async with self.session.post(webhook, json={"content": content, "allowed_mentions": {"parse": []}},
                                             timeout=aiohttp.ClientTimeout(total=10), allow_redirects=False) as response:
                    if response.status == 429:
                        try:
                            data = await response.json()
                        except (ValueError, aiohttp.ContentTypeError):
                            data = {}
                        delay = max(1.0, retry_delay(data, response.headers))
                        self.webhook_next_request = time.monotonic() + delay
                        self.manager.defer_for(delay)
                        raise ValueError("Discord limitó el webhook. Espera antes de volver a intentarlo.")
                    if response.headers.get("X-RateLimit-Remaining") == "0":
                        delay = retry_delay({}, response.headers)
                        self.webhook_next_request = time.monotonic() + delay
                        self.manager.defer_for(delay)
                    if response.status not in (200, 204):
                        raise ValueError("Discord rechazó el webhook. Revisa la URL y vuelve a intentarlo.")
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                raise ValueError("No se pudo conectar con el webhook de Discord.") from None

    async def notify_complete(self, job_id):
        job = self.store.get_job(job_id)
        if not job["options"].get("webhook_enabled", False):
            return
        try:
            await self.send_webhook(f"XYB: sesión de {'usuarios' if job['mode'] == 'username' else 'vanities'} completada. Resultados: {job['completed']} / {job['total']}.")
            self.webhook_status = "Notificación enviada."
        except (ValueError, OSError):
            self.webhook_status = "No se pudo enviar la notificación. Revisa la configuración del webhook."


def validate_webhook(value):
    if not isinstance(value, str) or not re.fullmatch(r"https://(?:discord\.com|discordapp\.com)/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]+", value):
        raise ValueError("Introduce una URL de webhook válida de Discord.")
    return value


@web.middleware
async def boundary(request, handler):
    service = request.app[SERVICE]
    # Reject remote origins and DNS rebinding. No wildcard CORS on localhost.
    try:
        host = urlparse("http://" + request.host).hostname
    except ValueError:
        host = None
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise web.HTTPForbidden(text="Local access only")
    origin = request.headers.get("Origin")
    if origin and origin != f"http://{request.host}":
        raise web.HTTPForbidden(text="Origin not allowed")
    if request.method not in {"GET", "HEAD"}:
        if not secrets.compare_digest(request.headers.get("X-XYB-Token", ""), service.token):
            return web.json_response({"error": "Recarga la interfaz para recuperar la sesión local."}, status=403)
    try:
        response = await handler(request)
    except (ValueError, TypeError, KeyError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Datos de solicitud inválidos."
        response = web.json_response({"error": message}, status=400)
    except (OSError, sqlite3.Error):
        response = web.json_response({"error": "No se pudo acceder a un archivo local. Revisa los permisos y el espacio libre."}, status=500)
    response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                             "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                             "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"})
    return response


async def payload(request):
    if request.content_type != "application/json":
        raise ValueError("Se esperaba JSON.")
    value = await request.json()
    if not isinstance(value, dict):
        raise ValueError("Se esperaba un objeto JSON.")
    return value


async def index(request):
    return web.FileResponse(PROJECT_ROOT / "web" / "index.html")


async def bootstrap(request):
    s = request.app[SERVICE]
    inputs = [{"id": key, "name": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")}
              for key, path in INPUT_FILES.items() if (s.root / path.relative_to(PROJECT_ROOT)).is_file()]
    return web.json_response({"app": "xyb-lab", "version": VERSION, "token": s.token, "settings": s.settings,
                              "settings_error": s.settings_error, "inputs": inputs, "connections": s.connections()})


async def state(request):
    s = request.app[SERVICE]
    counts = [dict(row) for row in s.store.db.execute("SELECT mode,status,COUNT(*) AS count FROM results GROUP BY mode,status")]
    return web.json_response({"active": s.manager.live(), "jobs": s.store.jobs(), "counts": counts, "webhook_status": s.webhook_status,
                              "favorites": s.store.favorites()})


async def create_job(request):
    s = request.app[SERVICE]
    body = await payload(request)
    mode = body.get("mode", "username")
    names, duplicates = parse_candidates(body.get("candidates", ""), mode)
    options = validate_options({**s.settings, **body.get("options", {})})
    offline = body.get("offline", False)
    if type(offline) is not bool:
        raise ValueError("El modo local debe ser verdadero o falso.")
    options["offline"] = offline
    job_id = s.manager.start(mode, names, options, s.proxy(options) if not offline else None)
    s.webhook_status = ""
    return web.json_response({"id": job_id, "duplicates": duplicates}, status=201)


async def job_action(request):
    s = request.app[SERVICE]
    body = await payload(request)
    job_id = request.match_info["id"]
    job = s.store.get_job(job_id)
    action = body.get("action")
    if action == "retry":
        rows = s.store.results(job_id, status="unverified", limit=50_000)["items"]
        names = [r["candidate"] for r in rows]
        done = {row[0] for row in s.store.db.execute("SELECT candidate FROM results WHERE job_id=?", (job_id,))}
        names.extend(n for n in job["candidates"] if n not in done)
        new_id = s.manager.start(job["mode"], names, job["options"], s.proxy(job["options"]) if not job["options"].get("offline") else None)
        return web.json_response({"id": new_id})
    proxy = s.proxy(job["options"]) if action == "resume" and not job["options"].get("offline") else None
    await s.manager.action(job_id, action, proxy)
    return web.json_response({"ok": True})


async def results(request):
    s = request.app[SERVICE]
    offset = max(0, int(request.query.get("offset", 0)))
    if offset > 2**63 - 1:
        raise ValueError("La posición de página es demasiado grande.")
    data = s.store.results(request.query.get("job"), status=request.query.get("status", ""),
                           search=request.query.get("search", "")[:256], offset=offset, limit=100)
    return web.json_response(data)


async def export(request):
    s = request.app[SERVICE]
    job_id = request.query.get("job")
    if not job_id:
        raise ValueError("Selecciona una sesión para exportar.")
    s.store.get_job(job_id)
    rows = s.store.results(job_id, status=request.query.get("status", ""),
                           search=request.query.get("search", "")[:256], limit=50_000)["items"]
    fmt = request.query.get("format", "csv")
    content, mime = export_rows(rows, fmt)
    return web.Response(text=content, content_type=mime, charset="utf-8",
                        headers={"Content-Disposition": f'attachment; filename="xyb-{job_id}.{fmt}"'})


async def generate(request):
    names = generate_candidates(await payload(request))
    return web.json_response({"candidates": names, "count": len(names)})


async def input_file(request):
    s = request.app[SERVICE]
    key = request.match_info["key"]
    if key not in INPUT_FILES:
        raise ValueError("Archivo no disponible.")
    path = s.root / INPUT_FILES[key].relative_to(PROJECT_ROOT)
    if path.exists() and path.stat().st_size > 4_000_000:
        raise ValueError("El archivo supera los 4 MB. Divide la lista antes de importarla.")
    return web.json_response({"text": "\n".join(load_lines(path))})


async def settings(request):
    s = request.app[SERVICE]
    updated = validate_options(await payload(request))
    atomic_write_text(s.settings_path, json.dumps(updated, indent=2))
    s.settings = updated
    s.settings_error = ""
    return web.json_response(updated)


async def connections(request):
    s = request.app[SERVICE]
    body = await payload(request)
    if any(key not in {"proxy", "webhook", "clear_proxy", "clear_webhook"} for key in body):
        raise ValueError("Opción de conexión desconocida.")
    for key in ("clear_proxy", "clear_webhook"):
        if key in body and type(body[key]) is not bool:
            raise ValueError("Opción de eliminación inválida.")
    proxy = body.get("proxy", "")
    webhook = body.get("webhook", "")
    if not isinstance(proxy, str) or not isinstance(webhook, str):
        raise ValueError("La conexión debe ser texto.")
    proxy, webhook = proxy.strip(), webhook.strip()
    if proxy and body.get("clear_proxy") or webhook and body.get("clear_webhook"):
        raise ValueError("No se puede guardar y eliminar el mismo dato a la vez.")
    if proxy:
        value = ProxyManager._format(proxy)
        try:
            parsed = urlparse(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port
                    or parsed.path or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
                raise ValueError()
        except ValueError:
            raise ValueError("Formato de proxy inválido.") from None
    if webhook:
        validate_webhook(webhook)
    if proxy or body.get("clear_proxy"):
        if s.proxy_path.exists():
            backup = s.proxy_path.with_name(f"proxies-backup-{secrets.token_hex(8)}.txt")
            atomic_write_text(backup, s.proxy_path.read_text(encoding="utf-8-sig"))
        atomic_write_text(s.proxy_path, proxy + "\n" if proxy else "")
    if webhook or body.get("clear_webhook"):
        atomic_write_text(s.webhook_path, webhook + "\n" if webhook else "")
    return web.json_response(s.connections())


async def webhook_test(request):
    await payload(request)
    await request.app[SERVICE].send_webhook("XYB: conexión de webhook configurada correctamente.")
    return web.json_response({"ok": True})


async def favorites(request):
    s = request.app[SERVICE]
    body = await payload(request)
    if type(body.get("enabled")) is not bool:
        raise ValueError("Estado de favorito inválido.")
    s.store.favorite(body.get("mode"), body.get("candidate"), body["enabled"])
    return web.json_response({"ok": True})


async def shutdown(request):
    await request.app[SERVICE].manager.close()
    def exit_server():
        raise web.GracefulExit
    asyncio.get_running_loop().call_later(0.3, exit_server)
    return web.json_response({"ok": True})


def create_app(root=PROJECT_ROOT, *, client_factory=None):
    app = web.Application(middlewares=[boundary], client_max_size=4 * 1024 * 1024)
    app[SERVICE] = Service(Path(root), client_factory=client_factory)
    async def lifecycle(app):
        service = app[SERVICE]
        try:
            await service.start()
            yield
        finally:
            await service.close()
    app.cleanup_ctx.append(lifecycle)
    app.add_routes([
        web.get("/", index), web.get("/api/bootstrap", bootstrap), web.get("/api/state", state),
        web.post("/api/jobs", create_job), web.post("/api/jobs/{id}/action", job_action),
        web.get("/api/results", results), web.get("/api/export", export),
        web.post("/api/generate", generate), web.get("/api/input/{key}", input_file),
        web.post("/api/settings", settings), web.post("/api/connections", connections),
        web.post("/api/webhook/test", webhook_test), web.post("/api/favorites", favorites),
        web.post("/api/shutdown", shutdown),
        web.static("/assets", PROJECT_ROOT / "web", show_index=False),
    ])
    return app


def main():
    parser = argparse.ArgumentParser(description="XYB Lab · Usuarios y vanities")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("El puerto debe estar entre 1024 y 65535.")
    url = f"http://127.0.0.1:{args.port}"
    # Detect another local instance before opening its database.
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            try:
                with urlopen(url + "/api/bootstrap", timeout=2) as response:
                    existing = json.load(response).get("app") == "xyb-lab"
            except Exception:
                existing = False
            if existing:
                print(f"XYB ya está abierto: {url}")
                if not args.no_browser:
                    webbrowser.open(url)
            else:
                print(f"El puerto {args.port} está ocupado por otra aplicación. Elige otro con --port.")
            return
    print(f"XYB Lab {VERSION} · {url}\nCtrl+C para cerrar el servidor local.")
    try:
        app = create_app()
    except ValueError as exc:
        print(str(exc))
        return
    if not args.no_browser:
        async def open_browser(app):
            asyncio.get_running_loop().call_later(0.8, webbrowser.open, url)
        app.on_startup.append(open_browser)
    # Selector avoids AcceptEx/WinError 10014 on this Windows Python 3.15 build.
    loop = asyncio.SelectorEventLoop() if sys.platform == "win32" else None
    web.run_app(app, host="127.0.0.1", port=args.port, access_log=None, print=None, loop=loop)


if __name__ == "__main__":
    main()
