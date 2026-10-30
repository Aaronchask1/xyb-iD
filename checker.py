#!/usr/bin/env python3
"""xyb Username Checker v3.1 – Discord username availability checker.

Async engine, circuit-breaker for rotating proxies.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from contextlib import AsyncExitStack, ExitStack

import aiohttp
from rich.live import Live
from rich.markup import escape

from config import (
    PROJECT_ROOT,
    DATA_DIR,
    LOGS_DIR,
    MAX_CONCURRENCY,
    RESULTS_DIR,
    AppSettings,
    Config,
    RunConfig,
    Stats,
    atomic_write_text,
    ensure_dir,
    ensure_file,
)
from proxy import ProxyManager
from engine import (
    Checker,
    CircuitBreaker,
    WebhookSender,
    set_debug,
    dbg,
)
from ui import (
    C,
    banner,
    console,
    final_summary,
    live_card,
)
from wizard import setup_wizard


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> AppSettings:
    parser = argparse.ArgumentParser(
        description="xyb Username Checker – Discord username availability checker",
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Enable debug output (request/response logs)",
    )
    parser.add_argument(
        "-n", "--no-wizard",
        action="store_true",
        help="Skip setup wizard – use saved config and last proxies/usernames",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"xyb Username Checker v{__import__('config').VERSION}",
    )
    args = parser.parse_args()
    return AppSettings(debug=args.debug, no_wizard=args.no_wizard)


# ---------------------------------------------------------------------------
# RPS calculator
# ---------------------------------------------------------------------------

async def _rps_calculator(stats: Stats, stop_event: asyncio.Event) -> None:
    """Update stats.rps and stats.checks_rps every second."""
    while not stop_event.is_set():
        prev_req = stats.requests
        prev_done = stats.works + stats.taken + stats.unverified
        await asyncio.sleep(1)
        await stats.set_rps(float(stats.requests - prev_req))
        await stats.set_checks_rps(float(stats.works + stats.taken + stats.unverified - prev_done))


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def _run_checker(cfg: RunConfig, settings: AppSettings) -> None:
    # Close files/session even if setup, rendering, or result persistence fails.
    with ExitStack() as files:
        async with AsyncExitStack() as resources:
            await _run_checker_body(cfg, settings, files, resources)


async def _run_checker_body(cfg, settings, files, resources) -> None:
    """Core async checker with live display."""

    _hits_path = RESULTS_DIR / "hits.txt"
    _available_path = RESULTS_DIR / "available.txt"
    _taken_path = RESULTS_DIR / "taken.txt"
    _unverified_path = RESULTS_DIR / "unverified.txt"
    _hits_path.parent.mkdir(parents=True, exist_ok=True)
    _hits_file = files.enter_context(_hits_path.open("a", encoding="utf-8"))
    _available_file = files.enter_context(_available_path.open("a", encoding="utf-8"))
    _taken_file = files.enter_context(_taken_path.open("a", encoding="utf-8"))
    _unverified_file = files.enter_context(_unverified_path.open("a", encoding="utf-8"))
    _results_lock = asyncio.Lock()
    # Names that were previously uncertain but receive a definitive answer in
    # this run.  They are removed from unverified.txt after all writers close.
    resolved_unverified: set[str] = set()

    pm = ProxyManager(cfg.proxies, remove_on_fail=cfg.remove_bad_proxies, scored=cfg.scraped)
    stats = Stats()
    start_time = time.time()
    proxyless = not cfg.proxies

    # Safety-cap concurrency (saved configs or --no-wizard could hold old values)
    if cfg.concurrency > MAX_CONCURRENCY:
        cfg.concurrency = MAX_CONCURRENCY

    # Bump file-descriptor limit (macOS defaults to 256)
    try:
        import resource as _resource
        _soft, _hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
        _resource.setrlimit(_resource.RLIMIT_NOFILE, (_hard if _hard > 1024 else 4096, _hard))
    except Exception:
        pass

    # Shared session — hard-cap connections to avoid OS/event-loop stalls
    connector = aiohttp.TCPConnector(
        limit=MAX_CONCURRENCY * 2,
        limit_per_host=0,
        force_close=False,
        ttl_dns_cache=300,
    )
    session_timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=5,
        sock_read=30,
    )
    session = await resources.enter_async_context(aiohttp.ClientSession(
        connector=connector,
        trust_env=False,
        timeout=session_timeout,
    ))

    # Circuit breaker for rotating proxies
    cb: CircuitBreaker | None = None
    paused = False
    if pm.is_single and not pm.is_proxyless:
        async def _on_circuit_open():
            nonlocal paused
            paused = True
            await stats.inc_circuit_open()
            await asyncio.sleep(2.0)
            paused = False
        cb = CircuitBreaker(threshold=10, window=2.0, cooldown=2.0, on_open=_on_circuit_open)

    http_timeout = 5 if proxyless else cfg.timeout
    checker = Checker(pm, timeout=http_timeout, scraped=cfg.scraped, circuit_breaker=cb, stats=stats)

    # Webhook sender
    webhook: WebhookSender | None = None
    if cfg.webhook_url and cfg.webhook_message:
        webhook = WebhookSender(
            cfg.webhook_url, cfg.webhook_message, session, start_time,
        )

    # Atomic index into the username list
    # Do not spend requests on usernames already confirmed in a previous run.
    # Keep the original list untouched so it remains a complete record of
    # generated candidates.
    previously_checked: set[str] = set()
    for confirmed_path in (_taken_path, _available_path, _hits_path):
        if confirmed_path.exists():
            previously_checked.update(
                line.strip().lower()
                for line in confirmed_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )

    # Attempt history is not proof of a conclusive result.
    pending_retry: set[str] = set()
    if _unverified_path.exists():
        pending_retry.update(
            line.strip().lower()
            for line in _unverified_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    names = [name for name in cfg.usernames if name.lower() not in previously_checked]
    total = len(names)
    _idx_lock = asyncio.Lock()
    _next_idx = 0

    async def _next_task() -> tuple[int, str] | None:
        nonlocal _next_idx
        async with _idx_lock:
            if _next_idx >= total:
                return None
            i = _next_idx
            _next_idx += 1
        return i, names[i]

    recent_hits: list[str] = []
    request_log: list[str] = []

    async def _mark_unverified(name: str) -> None:
        async with _results_lock:
            if name.lower() not in pending_retry:
                _unverified_file.write(f"{name}\n")
                _unverified_file.flush()
                pending_retry.add(name.lower())
        await stats.inc_unverified()

    # Worker
    async def _worker(worker_id: int) -> None:
        while True:
            t = await _next_task()
            if t is None:
                return
            idx, name = t
            recorded = False

            try:
                result, data, code = await checker.check(session, name)

                if result is True:
                    await stats.inc_works()
                    async with _results_lock:
                        _hits_file.write(f"{name}\n")
                        _hits_file.flush()
                        _available_file.write(f"{name}\n")
                        _available_file.flush()
                        resolved_unverified.add(name.lower())
                    recent_hits.append(name)
                    request_log.append(f"[{C.SUCCESS}]✓[/] {escape(name)}")
                    if webhook:
                        webhook.enqueue(name)

                elif result is False:
                    await stats.inc_taken()
                    async with _results_lock:
                        _taken_file.write(f"{name}\n")
                        _taken_file.flush()
                        resolved_unverified.add(name.lower())
                    request_log.append(f"[{C.DANGER}]✗[/] {escape(name)}")

                else:
                    await _mark_unverified(name)
                    request_log.append(f"[{C.WARNING}]?[/] {escape(name)}")
                recorded = True

                if proxyless:
                    await asyncio.sleep(cfg.timeout)

            except asyncio.CancelledError:
                if not recorded:
                    await _mark_unverified(name)
                raise
            except OSError:
                # A persistence failure must stop the run, not silently lose names.
                raise
            except Exception as exc:
                if not recorded:
                    await _mark_unverified(name)
                dbg(f"Worker failure: {type(exc).__name__}")
                request_log.append(f"[{C.DANGER}]!![/] {escape(name)}")
                continue
            finally:
                del recent_hits[:-6]
                del request_log[:-8]

    # Start background tasks
    stop_rps = asyncio.Event()
    rps_task = asyncio.create_task(_rps_calculator(stats, stop_rps))
    webhook_task = asyncio.create_task(webhook.run()) if webhook else None

    # Create worker tasks
    pending: set[asyncio.Task] = {
        asyncio.create_task(_worker(i)) for i in range(cfg.concurrency)
    }

    # Build live display function
    def _live_render():
        return live_card(
            done=stats.works + stats.taken + stats.unverified,
            total=total,
            works=stats.works,
            taken=stats.taken,
            unverified=stats.unverified,
            requests=stats.requests,
            ratelimited=stats.ratelimited,
            circuit_opens=stats.circuit_opens,
            rps=stats.rps,
            checks_rps=stats.checks_rps,
            elapsed=time.time() - start_time,
            proxy_alive=pm.alive_count,
            paused=paused,
            recent=recent_hits,
            feed=request_log,
        )

    console.print()
    interrupted = False
    try:
        with Live(_live_render(), refresh_per_second=4, console=console) as live:
            while pending:
                await asyncio.sleep(0.3)

                # Remove completed tasks
                completed = {w for w in pending if w.done()}
                for worker in completed:
                    worker.result()
                pending -= completed

                # Refill workers if any crashed
                while _next_idx < total and len(pending) < cfg.concurrency:
                    pending.add(asyncio.create_task(_worker(len(pending))))

                if not pending and _next_idx >= total:
                    live.update(_live_render())
                    break

                live.update(_live_render())
    except asyncio.CancelledError:
        interrupted = True
    finally:
        stop_rps.set()
        rps_task.cancel()
        for w in pending:
            w.cancel()
        if webhook_task:
            webhook_task.cancel()
        await asyncio.gather(rps_task, *pending,
                             *([webhook_task] if webhook_task else []), return_exceptions=True)

    # Final summary
    elapsed = time.time() - start_time
    snap = await stats.snapshot()

    _hits_file.close()
    _available_file.close()
    _taken_file.close()
    _unverified_file.close()

    # Keep only still-uncertain names.  This makes retries consumable: a name
    # that is confirmed taken/available does not remain queued forever.
    if resolved_unverified and _unverified_path.exists():
        still_unverified: list[str] = []
        seen_unverified: set[str] = set()
        for line in _unverified_path.read_text(encoding="utf-8").splitlines():
            candidate = line.strip()
            key = candidate.lower()
            if candidate and key not in resolved_unverified and key not in seen_unverified:
                still_unverified.append(candidate)
                seen_unverified.add(key)
        atomic_write_text(_unverified_path,
            "\n".join(still_unverified) + ("\n" if still_unverified else ""),
        )

    final_summary(
        requests=snap["requests"],
        works=snap["works"],
        taken=snap["taken"],
        unverified=snap["unverified"],
        interrupted=interrupted,
        remaining=max(0, total - snap["works"] - snap["taken"] - snap["unverified"]),
        ratelimited=snap["ratelimited"],
        circuit_opens=snap["circuit_opens"],
        elapsed=elapsed,
        peak_rps=snap["peak_rps"],
        best_streak=snap["best_streak"],
    )
    if interrupted:
        raise asyncio.CancelledError


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Application entry point."""
    settings = parse_args()

    set_debug(settings.debug)

    ensure_dir(DATA_DIR, LOGS_DIR, RESULTS_DIR)
    ensure_file(DATA_DIR / "config.json")
    ensure_file(DATA_DIR / "proxies.txt")
    ensure_file(DATA_DIR / "names_to_check.txt")
    ensure_file(LOGS_DIR / "error.txt")

    try:
        config = Config()
        if settings.no_wizard:
            # Quick start: load from config + saved files
            from config import load_lines
            console.print(banner())
            proxy_file = config.get("last_proxy_file", str(DATA_DIR / "proxies.txt"))
            names_file = config.get("last_names_file", str(DATA_DIR / "names_to_check.txt"))
            proxies = load_lines(proxy_file)
            usernames = load_lines(names_file)
            if not proxies:
                console.print(f"[{C.WARNING}]No proxies found – running proxyless.[/]")
            if not usernames:
                console.print(f"[{C.DANGER}]No usernames found. Run without --no-wizard first.[/]")
                sys.exit(1)
            run_config = RunConfig(
                proxies=proxies,
                remove_bad_proxies=config.get("remove_proxies", False),
                usernames=usernames,
                concurrency=config.get("concurrency", 50),
                timeout=config.get("timeout", 10),
                scraped=False,
                webhook_url=config.get("webhook"),
                webhook_message=config.get("webhook_message"),
            )
            console.print(f"[{C.MUTED}]Skipping wizard – {len(proxies)} proxies, {len(usernames)} usernames[/]")
        else:
            run_config = asyncio.run(setup_wizard(config, settings))

        asyncio.run(_run_checker(run_config, settings))
    except (EOFError, KeyboardInterrupt):
        console.print(f"\n[{C.WARNING}]Aborted.[/]")
        sys.exit(0)
    except (ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Could not read or save a local file. Check access and disk space."
        console.print(f"[{C.DANGER}]{message}[/]")
        sys.exit(1)


if __name__ == "__main__":
    main()
