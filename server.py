r"""Local XYB dashboard. Run: .venv\Scripts\python.exe server.py"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import secrets
import socket
import sqlite3
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import aiohttp
from aiohttp import web

from config import DATA_DIR, PROJECT_ROOT, RESULTS_DIR, atomic_write_text, load_lines
from lab import JobManager, Store, export_rows, generate_candidates, parse_candidates
from proxy import ProxyManager

VERSION = "4.0.0"
DEFAULTS = {"delay": 1.5, "timeout": 15, "use_proxy": False}
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
    if type(options["use_proxy"]) is not bool:
        raise ValueError("use_proxy debe ser verdadero o falso.")
    return options


class Service:
    def __init__(self, root, *, client_factory=None):
        self.root = root
        self.token = secrets.token_urlsafe(32)
        self.settings_path = root / "data" / "lab_settings.json"
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
        self.manager = JobManager(self.store, self.session, **kwargs)

    async def close(self):
        if self.manager:
            await self.manager.close()
        if self.session:
            await self.session.close()
        self.store.close()

    def proxy(self, options):
        if not options.get("use_proxy"):
            return None
        lines = load_lines(self.root / "data" / "proxies.txt")
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
                              "settings_error": s.settings_error, "inputs": inputs})


async def state(request):
    s = request.app[SERVICE]
    counts = [dict(row) for row in s.store.db.execute("SELECT mode,status,COUNT(*) AS count FROM results GROUP BY mode,status")]
    return web.json_response({"active": s.manager.live(), "jobs": s.store.jobs(), "counts": counts,
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
        web.post("/api/settings", settings), web.post("/api/favorites", favorites),
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
