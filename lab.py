"""XYB Lab: typed results, local history and controlled Discord lookups."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import random
import re
import sqlite3
import string
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import aiohttp

from config import ENDPOINT, is_valid_username
from engine import classify_response, retry_delay

MODES = {"username", "vanity"}
STATUSES = {"candidate", "taken", "active", "not_found", "invalid", "unverified", "valid"}
MAX_ITEMS = 50_000


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(value: str, mode: str) -> str:
    value = value.strip().lstrip("\ufeff")
    if mode == "username":
        return value.removeprefix("@").lower()
    if value.lower().startswith(("https://", "http://", "discord.gg/", "discord.com/", "www.discord.gg/")):
        parsed = urlparse(value if "://" in value else "https://" + value)
        host = (parsed.hostname or "").lower()
        parts = parsed.path.strip("/").split("/")
        if host in {"discord.gg", "www.discord.gg"} and len(parts) == 1:
            return parts[0]
        if host in {"discord.com", "www.discord.com", "discordapp.com"} and len(parts) == 2 and parts[0] == "invite":
            return parts[1]
        return value
    # Invite codes are case-sensitive. Do not lowercase copied links.
    return value


def valid_candidate(value: str, mode: str) -> bool:
    if mode == "username":
        return is_valid_username(value)
    # Public invite codes and vanity candidates; assignment rules may differ.
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value))


def parse_candidates(raw, mode: str) -> tuple[list[str], int]:
    if mode not in MODES:
        raise ValueError("Modo desconocido.")
    if isinstance(raw, str):
        entries = re.split(r"[,;\s]+", raw.strip())
    elif isinstance(raw, list) and all(isinstance(n, str) for n in raw):
        entries = raw
    else:
        raise ValueError("Introduce una lista de nombres o enlaces.")
    if len(entries) > MAX_ITEMS * 2:
        raise ValueError(f"Importa como máximo {MAX_ITEMS:,} candidatos por sesión.")
    values, seen, duplicates = [], set(), 0
    for entry in entries:
        value = normalize(entry, mode)
        if not value:
            continue
        if len(value) > 256:
            raise ValueError("Una entrada supera los 256 caracteres.")
        if value in seen:
            duplicates += 1
        else:
            seen.add(value)
            values.append(value)
    if len(values) > MAX_ITEMS:
        raise ValueError(f"Máximo {MAX_ITEMS:,} candidatos por sesión.")
    return values, duplicates


def generate_candidates(options: dict) -> list[str]:
    mode = options.get("mode", "username")
    if mode not in MODES:
        raise ValueError("Modo desconocido.")
    count, length = options.get("count", 100), options.get("length", 4)
    if type(count) is not int or type(length) is not int:
        raise ValueError("Cantidad y longitud deben ser números enteros.")
    if not 1 <= count <= 10_000 or not 2 <= length <= 32:
        raise ValueError("Cantidad: 1–10.000. Longitud: 2–32.")
    alphabets = {"letters": string.ascii_lowercase, "digits": string.digits,
                 "mixed": string.ascii_lowercase + string.digits}
    alphabet = alphabets.get(options.get("alphabet", "mixed"))
    if alphabet is None:
        raise ValueError("Alfabeto desconocido.")
    prefix, suffix = str(options.get("prefix", "")).lower(), str(options.get("suffix", "")).lower()
    pattern = str(options.get("pattern", "")).lower()
    if len(prefix) + len(suffix) > 30 or len(pattern) > 32:
        raise ValueError("Prefijo, sufijo o patrón demasiado largos.")
    if pattern:
        pools = [string.ascii_lowercase if c == "?" else string.digits if c == "#" else c for c in pattern]
    else:
        pools = [alphabet] * length
    # Check feasibility before sampling so impossible patterns cannot hang.
    sample = prefix + "".join(p[0] for p in pools) + suffix
    if not valid_candidate(sample, mode) or (mode == "vanity" and not re.fullmatch(r"[a-z0-9-]{1,25}", sample)):
        raise ValueError("El patrón o la longitud final no forman candidatos válidos.")
    capacity = math.prod(len(p) for p in pools)
    count = min(count, capacity)
    if capacity <= 100_000:
        indices = random.sample(range(capacity), count)
        values = []
        for index in indices:
            chars = []
            for pool in reversed(pools):
                index, digit = divmod(index, len(pool))
                chars.append(pool[digit])
            values.append(prefix + "".join(reversed(chars)) + suffix)
        return values
    values = set()
    for _ in range(count * 30):
        values.add(prefix + "".join(random.choice(p) for p in pools) + suffix)
        if len(values) == count:
            break
    return sorted(values)


@dataclass
class Result:
    candidate: str
    mode: str
    status: str
    detail: str
    http_status: int | None = None
    guild_name: str = ""
    guild_id: str = ""
    members: int | None = None
    online: int | None = None
    latency_ms: int = 0
    checked_at: str = ""

    def __post_init__(self):
        self.checked_at = self.checked_at or utcnow()


def interpret(mode: str, name: str, status: int, data) -> Result:
    if mode == "username":
        response = classify_response(status, data)
        if response is True:
            return Result(name, mode, "candidate", "La respuesta indica taken=false. Confirma el cambio en Discord.", status)
        if response is False:
            return Result(name, mode, "taken", "La respuesta indica taken=true.", status)
        return Result(name, mode, "unverified", "Respuesta sin disponibilidad concluyente.", status)
    if status == 200 and isinstance(data, dict) and isinstance(data.get("code"), str):
        if data["code"] != name:
            return Result(name, mode, "unverified", "La respuesta contiene un código distinto.", status)
        guild = data.get("guild") if isinstance(data.get("guild"), dict) else {}
        def count(value):
            return value if type(value) is int and 0 <= value <= 2**63-1 else None
        return Result(name, mode, "active", "Invitación pública activa; puede ser una invitación normal o una vanity.", status,
                      str(guild.get("name", ""))[:100], str(guild.get("id", ""))[:32],
                      count(data.get("approximate_member_count")), count(data.get("approximate_presence_count")))
    if status == 404 and isinstance(data, dict) and data.get("code") == 10006:
        return Result(name, mode, "not_found", "Sin invitación pública. Esto NO confirma que la vanity pueda asignarse.", status)
    return Result(name, mode, "unverified", "No se pudo confirmar la invitación.", status)


class DiscordClient:
    """One shared pace and cooldown; no identity rotation on rate limits."""
    def __init__(self, session, *, delay=1.5, timeout=15, proxy=None, on_wait=None):
        self.session, self.delay, self.timeout = session, delay, timeout
        self.proxy, self.on_wait = proxy, on_wait
        self.next_request = 0.0
        self.requests = self.rate_limits = 0
        self.wait_until = 0.0

    async def check(self, mode, name):
        if not valid_candidate(name, mode):
            return Result(name, mode, "invalid", "Formato inválido; no se ha enviado ninguna solicitud.")
        started = time.monotonic()
        result = Result(name, mode, "unverified", "No se obtuvo una respuesta válida.")
        for attempt in range(3):
            wait = max(0, self.next_request - time.monotonic())
            if wait:
                await asyncio.sleep(wait)
            self.wait_until = 0
            if self.on_wait:
                self.on_wait(0)
            self.requests += 1
            self.next_request = time.monotonic() + self.delay
            method = "POST" if mode == "username" else "GET"
            url = ENDPOINT if mode == "username" else "https://discord.com/api/v10/invites/" + quote(name, safe="")
            options = {"timeout": aiohttp.ClientTimeout(total=self.timeout), "proxy": self.proxy}
            options.update({"json": {"username": name}} if mode == "username" else {"params": {"with_counts": "true"}})
            try:
                async with self.session.request(method, url, **options) as response:
                    try:
                        data = await response.json()
                    except (ValueError, aiohttp.ContentTypeError):
                        data = None
                    if response.status == 429:
                        self.rate_limits += 1
                        delay = retry_delay(data, response.headers)
                        self.next_request = max(self.next_request, time.monotonic() + delay)
                        self.wait_until = time.time() + delay
                        if self.on_wait:
                            self.on_wait(delay)
                        result = Result(name, mode, "unverified", "Discord limitó las solicitudes; pendiente de reintento.", 429)
                        continue
                    if response.headers.get("X-RateLimit-Remaining") == "0":
                        delay = retry_delay({}, {"Retry-After": response.headers.get("X-RateLimit-Reset-After")})
                        self.next_request = max(self.next_request, time.monotonic() + delay)
                    result = interpret(mode, name, response.status, data)
                    if response.status < 500:
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                result = Result(name, mode, "unverified", "Error de conexión o tiempo de espera agotado.")
            self.next_request = max(self.next_request, time.monotonic() + 2 ** attempt)
        result.latency_ms = round((time.monotonic() - started) * 1000)
        return result


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file_lock = path.with_suffix(path.suffix + ".lock").open("a+b")
        if self._file_lock.seek(0, 2) == 0:
            self._file_lock.write(b"0")
            self._file_lock.flush()
        self._file_lock.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._file_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._file_lock.close()
            raise ValueError("Otro panel XYB ya está usando este historial. Cierra la otra instancia primero.")
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, mode TEXT NOT NULL, state TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                candidates TEXT NOT NULL, options TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, candidate TEXT NOT NULL,
                mode TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
                http_status INTEGER, guild_name TEXT, guild_id TEXT, members INTEGER,
                online INTEGER, latency_ms INTEGER, checked_at TEXT NOT NULL,
                UNIQUE(job_id, candidate));
            CREATE INDEX IF NOT EXISTS results_job ON results(job_id, id);
            CREATE TABLE IF NOT EXISTS favorites (
                mode TEXT NOT NULL, candidate TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(mode, candidate));
        """)
        self.db.execute("UPDATE jobs SET state='interrupted' WHERE state IN ('running','paused','waiting')")
        self.db.commit()

    def close(self):
        self.db.close()
        if not self._file_lock.closed:
            self._file_lock.close()

    def create(self, mode, candidates, options):
        job_id, now = uuid.uuid4().hex[:12], utcnow()
        self.db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)",
                        (job_id, mode, "running", now, now, json.dumps(candidates), json.dumps(options), ""))
        self.db.commit()
        return job_id

    def state(self, job_id, state, error=""):
        self.db.execute("UPDATE jobs SET state=?, updated_at=?, error=? WHERE id=?", (state, utcnow(), error, job_id))
        self.db.commit()

    def save(self, job_id, result):
        data = {"job_id": job_id, **asdict(result)}
        cols = ",".join(data)
        self.db.execute(f"INSERT OR REPLACE INTO results ({cols}) VALUES ({','.join('?' for _ in data)})", tuple(data.values()))
        self.db.execute("UPDATE jobs SET updated_at=? WHERE id=?", (utcnow(), job_id))
        self.db.commit()

    def get_job(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("Sesión no encontrada.")
        job = dict(row)
        job["candidates"] = json.loads(job["candidates"])
        job["options"] = json.loads(job["options"])
        job["total"] = len(job["candidates"])
        job["counts"] = dict(self.db.execute("SELECT status,COUNT(*) FROM results WHERE job_id=? GROUP BY status", (job_id,)))
        job["completed"] = sum(job["counts"].values())
        return job

    def jobs(self):
        return [{k: v for k, v in self.get_job(row[0]).items() if k != "candidates"}
                for row in self.db.execute("SELECT id FROM jobs ORDER BY rowid DESC LIMIT 100")]

    def results(self, job_id=None, *, status="", search="", offset=0, limit=100):
        clauses, args = [], []
        if job_id:
            clauses.append("job_id=?")
            args.append(job_id)
        if status:
            if status not in STATUSES:
                raise ValueError("Filtro desconocido.")
            clauses.append("status=?")
            args.append(status)
        if search:
            clauses.append("instr(lower(candidate),lower(?))>0")
            args.append(search)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = self.db.execute("SELECT COUNT(*) FROM results" + where, args).fetchone()[0]
        rows = self.db.execute("SELECT * FROM results" + where + " ORDER BY id DESC LIMIT ? OFFSET ?", [*args, limit, offset])
        return {"total": total, "items": [dict(r) for r in rows]}

    def favorites(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM favorites ORDER BY created_at DESC")]

    def favorite(self, mode, candidate, enabled):
        if mode not in MODES or not valid_candidate(candidate, mode):
            raise ValueError("Candidato no válido.")
        if enabled:
            self.db.execute("INSERT OR IGNORE INTO favorites VALUES (?,?,?)", (mode, candidate, utcnow()))
        else:
            self.db.execute("DELETE FROM favorites WHERE mode=? AND candidate=?", (mode, candidate))
        self.db.commit()


def export_rows(rows, fmt):
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2), "application/json"
    if fmt == "txt":
        return "\n".join(row["candidate"] for row in rows) + ("\n" if rows else ""), "text/plain"
    if fmt != "csv":
        raise ValueError("Formato de exportación desconocido.")
    output = io.StringIO(newline="")
    fields = ["candidate", "mode", "status", "detail", "http_status", "guild_name", "guild_id", "members", "online", "checked_at"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # Names and server titles must not become spreadsheet formulas.
        safe = {k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for k, v in row.items()}
        writer.writerow(safe)
    return "\ufeff" + output.getvalue(), "text/csv"


class JobManager:
    def __init__(self, store, session, *, client_factory=DiscordClient):
        self.store, self.session, self.client_factory = store, session, client_factory
        self.task = None
        self.active_id = None
        self.gate = asyncio.Event()
        self.gate.set()
        self.client = None
        self.started = 0
        self.saved_on_start = 0
        self.cooldown_until = 0.0
        self.next_request = 0.0
        self.finished = None

    def busy(self):
        return self.task is not None and not self.task.done()

    def start(self, mode, candidates, options, proxy=None):
        if self.busy():
            raise ValueError("Ya hay una sesión activa. Deténla o espera a que termine.")
        if not candidates:
            raise ValueError("Añade al menos un candidato.")
        job_id = self.store.create(mode, candidates, options)
        self._launch(job_id, proxy)
        return job_id

    def _launch(self, job_id, proxy=None):
        self.active_id = job_id
        self.gate.set()
        self.started = time.monotonic()
        self.saved_on_start = self.store.get_job(job_id)["completed"]
        self.cooldown_until = 0
        self.finished = None
        self.task = asyncio.create_task(self._run(job_id, proxy))

    async def _run(self, job_id, proxy):
        job = self.store.get_job(job_id)
        opts = job["options"]
        done = {row[0] for row in self.store.db.execute("SELECT candidate FROM results WHERE job_id=?", (job_id,))}
        def on_wait(delay):
            self.cooldown_until = time.time() + delay if delay else 0
        self.client = self.client_factory(self.session, delay=opts["delay"], timeout=opts["timeout"], proxy=proxy, on_wait=on_wait)
        self.client.next_request = max(self.next_request, getattr(self.client, "next_request", 0))
        try:
            for name in job["candidates"]:
                if name in done:
                    continue
                await self.gate.wait()
                if opts.get("offline"):
                    ok = valid_candidate(name, job["mode"])
                    result = Result(name, job["mode"], "valid" if ok else "invalid",
                                    "Formato válido. Disponibilidad no consultada." if ok else "Formato inválido.")
                    await asyncio.sleep(0)
                else:
                    result = await self.client.check(job["mode"], name)
                self.store.save(job_id, result)
            self.store.state(job_id, "completed")
        except asyncio.CancelledError:
            self.store.state(job_id, "stopped")
            raise
        except Exception:
            self.store.state(job_id, "failed", "La sesión falló. Los resultados ya guardados se conservan; puedes reanudarla.")
        finally:
            self.next_request = max(self.next_request, getattr(self.client, "next_request", 0))
            self.finished = time.monotonic()

    async def action(self, job_id, action, proxy=None):
        job = self.store.get_job(job_id)
        if action == "resume" and not self.busy():
            if job["completed"] >= job["total"]:
                raise ValueError("Esta sesión ya terminó. Usa Reintentar pendientes o una nueva consulta.")
            self.store.state(job_id, "running")
            self._launch(job_id, proxy)
            return
        if job_id != self.active_id or not self.busy():
            raise ValueError("La sesión no está activa.")
        if action == "pause":
            self.gate.clear()
            self.store.state(job_id, "paused")
        elif action == "resume":
            self.gate.set()
            self.store.state(job_id, "running")
        elif action == "stop":
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        else:
            raise ValueError("Acción desconocida.")

    def live(self):
        if not self.active_id:
            return None
        job = self.store.get_job(self.active_id)
        job.pop("candidates")
        elapsed = max(0.01, (self.finished or time.monotonic()) - self.started)
        job.update(requests=self.client.requests if self.client else 0,
                   rate_limits=self.client.rate_limits if self.client else 0,
                   cooldown=max(0, math.ceil(self.cooldown_until - time.time())),
                   elapsed=round(elapsed), speed=round((job["completed"] - self.saved_on_start) / elapsed, 2))
        return job

    async def close(self):
        if self.busy():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
