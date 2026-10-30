#!/usr/bin/env python3
"""xyb Username Checker v3.1 – terminal UI."""

from __future__ import annotations

from rich.console import Console, Group
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from config import C, RESULTS_DIR, VERSION


console = Console()

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

def banner() -> Panel:
    """Main application header."""
    brand = Text(justify="center")
    brand.append("XYB", style=f"bold {C.PRIMARY}")
    brand.append("  USERNAME LAB", style="bold white")
    subtitle = Text(
        f"Interactive session console  •  v{VERSION}  •  results protected from duplicates",
        style=C.MUTED,
        justify="center",
    )
    return Panel(
        Group(brand, subtitle),
        title=f"[{C.PRIMARY}]CONTROL CENTER[/]",
        title_align="center",
        box=box.DOUBLE,
        border_style=C.BORDER,
        padding=(1, 3),
    )


def _unique_line_count(path) -> int:
    """Return a case-insensitive count without failing on a missing file."""
    if not path.exists():
        return 0
    return len({line.strip().lower() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()})


def history_summary() -> None:
    """Show saved result state before a new scan starts."""
    taken = _unique_line_count(RESULTS_DIR / "taken.txt")
    available = _unique_line_count(RESULTS_DIR / "available.txt")
    unverified = _unique_line_count(RESULTS_DIR / "unverified.txt")

    def metric(label: str, value: int, color: str, detail: str) -> Panel:
        body = Text(justify="center")
        body.append(f"{value:,}\n", style=f"bold {color}")
        body.append(label, style="white")
        body.append(f"\n{detail}", style=C.MUTED)
        return Panel(body, box=box.ROUNDED, border_style=color, padding=(0, 2))

    console.print(Columns([
        metric("TAKEN", taken, C.DANGER, "saved"),
        metric("AVAILABLE", available, C.SUCCESS, "saved"),
        metric("RETRY", unverified, C.WARNING, "unverified"),
    ], equal=True, expand=True))
    console.print(Text("  ✓ Confirmed names are excluded automatically on future scans.", style=C.MUTED))


# ---------------------------------------------------------------------------
# Progress indicator
# ---------------------------------------------------------------------------

def progress_steps(current: int, total: int = 4) -> str:
    """Render a step progress indicator: ● Proxies  ○ Usernames  ○ Speed  ○ Webhook"""
    steps = ["Proxies", "Usernames", "Speed", "Webhook"]
    parts: list[str] = []
    for i, label in enumerate(steps):
        if i < current:
            parts.append(f"[{C.SUCCESS}]✓[/] {label}")
        elif i == current:
            parts.append(f"[{C.PRIMARY}]●[/] {label}")
        else:
            parts.append(f"[{C.MUTED}]○[/] {label}")
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Section header
# ---------------------------------------------------------------------------

def section(title: str) -> None:
    """Print a clean section header."""
    console.print()
    console.print(Text(title, style=f"bold {C.PRIMARY}"))
    console.print("─" * 40, style=C.MUTED)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def card(title: str | None, *lines: str, border: str = C.PRIMARY) -> None:
    """Print a rounded-card panel."""
    body = "\n".join(lines) if lines else ""
    console.print(Panel(
        body,
        title=title,
        title_align="left",
        box=box.ROUNDED,
        border_style=border,
        padding=(1, 2),
    ))


def info_card(*lines: str) -> None:
    """Print an informational card (muted border)."""
    card(None, *lines, border=C.MUTED)


def warn_card(*lines: str) -> None:
    """Print a warning card."""
    card(None, *lines, border=C.WARNING)


# ---------------------------------------------------------------------------
# Summary table (config)
# ---------------------------------------------------------------------------

def config_summary(
    proxy_count: int,
    scraped: bool,
    remove_bad: bool,
    username_count: int,
    concurrency: int,
    timeout: int,
    webhook: bool,
) -> None:
    """Print a clean configuration summary."""
    console.print()
    t = Table(box=box.ROUNDED, border_style=C.BORDER, show_header=False, padding=(0, 2))
    t.add_column(style=C.MUTED, width=16)
    t.add_column(style="white")

    tag = f" [{C.MUTED}](free)[/]" if scraped else ""
    t.add_row("Proxies", f"[{C.PRIMARY}]{proxy_count}[/] loaded{tag}")
    if proxy_count > 1 and not scraped:
        t.add_row("Auto-remove", "Yes" if remove_bad else "No")
    t.add_row("Usernames", str(username_count))
    t.add_row("Workers", str(concurrency))
    t.add_row("Timeout", f"{timeout}s")
    t.add_row("Webhook", f"[{C.SUCCESS}]on[/]" if webhook else f"[{C.MUTED}]off[/]")
    console.print(Panel(t, title="Summary", title_align="left", box=box.ROUNDED, border_style=C.PRIMARY))


# ---------------------------------------------------------------------------
# Live checker card builder
# ---------------------------------------------------------------------------

def live_card(
    done: int,
    total: int,
    works: int,
    taken: int,
    requests: int,
    ratelimited: int,
    circuit_opens: int,
    rps: float,
    elapsed: float,
    proxy_alive: int,
    checks_rps: float = 0.0,
    paused: bool = False,
    recent: list[str] | None = None,
    feed: list[str] | None = None,
    unverified: int = 0,
) -> Panel:
    """Build the live display panel for the checker runner."""

    pct = done / max(total, 1) * 100

    inner = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    inner.add_column(style=C.MUTED, width=14, no_wrap=True)
    inner.add_column(style="white")
    inner.add_column(style=C.MUTED, width=14, no_wrap=True)
    inner.add_column(style="white")

    inner.add_row(
        "Available", f"[{C.SUCCESS}]{works}[/]",
        "Taken", f"[{C.DANGER}]{taken}[/]",
    )
    inner.add_row("Unverified", f"[{C.WARNING}]{unverified}[/]", "Processed", f"{done}/{total}")
    inner.add_row(
        "Req/s", f"[{C.PRIMARY}]{rps:.0f}[/]",
        "Requests", str(requests),
    )

    # Rate-limited row
    if ratelimited > 0:
        inner.add_row(
            f"[{C.WARNING}]Rate limited[/]", f"[{C.WARNING}]{ratelimited}[/]",
            "Elapsed", f"{elapsed:.0f}s",
        )
    else:
        inner.add_row(
            "Progress", f"{done}/{total} ({pct:.0f}%)",
            "Elapsed", f"{elapsed:.0f}s",
        )

    # Circuit breaker / paused
    if circuit_opens > 0 or paused:
        inner.add_row(
            f"[{C.WARNING}]Circuit breaks[/]", f"[{C.WARNING}]{circuit_opens}[/]",
            "Proxies", f"{proxy_alive} alive",
        )
    else:
        inner.add_row(
            "Proxies", f"{proxy_alive} alive",
            "Workers", "active",
        )

    content = Table(box=None, show_header=False, padding=(0, 0), expand=True)
    state = "PAUSED" if paused else "SCANNING"
    state_color = C.WARNING if paused else C.SUCCESS
    status_line = Text()
    status_line.append("● ", style=state_color)
    status_line.append(state, style=f"bold {state_color}")
    status_line.append("  •  live result stream  •  Ctrl+C stops safely", style=C.MUTED)
    content.add_row(status_line)
    content.add_row(inner)

    # Visual progress and a useful ETA based on completed username checks.
    width = 30
    filled = min(width, max(0, round(width * pct / 100)))
    bar = Text("Progress  ", style=C.MUTED)
    bar.append("█" * filled, style=C.PRIMARY)
    bar.append("░" * (width - filled), style=C.BORDER)
    if checks_rps > 0 and done < total:
        eta = f"  ETA {(total - done) / checks_rps:.0f}s"
    elif done >= total:
        eta = "  complete"
    else:
        eta = "  estimating…"
    bar.append(eta, style=C.MUTED)
    content.add_row(bar)

    # Status banner for heavy rate-limiting
    if ratelimited > 0 and (done == 0 or ratelimited > done * 2):
        content.add_section()
        content.add_row(Text(
            f"⚠ Discord is rate-limiting — {ratelimited} requests blocked. Workers are alive, just waiting.",
            style=C.WARNING,
        ))

    if paused:
        content.add_section()
        content.add_row(Text(
            f"⏸ Circuit breaker active — all workers paused briefly to protect the proxy.",
            style=C.WARNING,
        ))

    # Recent hits
    if recent:
        content.add_section()
        recent_str = "  ".join(f"[{C.SUCCESS}]{n}[/]" for n in recent[-6:])
        content.add_row(Text("Recent  ", style=C.MUTED) + Text.from_markup(recent_str))

    # Live feed (only last 8 entries)
    if feed:
        content.add_section()
        feed_str = "  ".join(feed[-8:])
        content.add_row(Text.from_markup(feed_str))

    status = f"rate-limited" if (ratelimited > 0 and done == 0) else f"{done:,}/{total:,} ({pct:.0f}%)"
    return Panel(
        content,
        title=f"[{C.PRIMARY}]XYB LIVE[/]  {status}  [dim]{elapsed:.0f}s[/]",
        title_align="left",
        box=box.DOUBLE,
        border_style=state_color,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

def final_summary(
    requests: int,
    works: int,
    taken: int,
    ratelimited: int,
    circuit_opens: int,
    elapsed: float,
    peak_rps: float = 0.0,
    best_streak: int = 0,
    unverified: int = 0,
    interrupted: bool = False,
    remaining: int = 0,
) -> None:
    """Print the final results card."""
    console.print()

    avg_rps = requests / max(elapsed, 0.1)

    t = Table(box=box.ROUNDED, border_style=C.BORDER, show_header=False, padding=(0, 2))
    t.add_column(style=C.MUTED, width=16)
    t.add_column(style="white")
    t.add_row("Available", f"[{C.SUCCESS} bold]{works}[/]")
    t.add_row("Taken", f"[{C.DANGER} bold]{taken}[/]")
    t.add_row("Unverified", f"[{C.WARNING}]{unverified}[/]")
    if remaining:
        t.add_row("Not processed", str(remaining))
    t.add_row("Requests", str(requests))
    if ratelimited > 0:
        t.add_row("Rate limited", f"[{C.WARNING}]{ratelimited}[/]")
    if circuit_opens > 0:
        t.add_row("Circuit breaks", str(circuit_opens))
    t.add_row("Elapsed", f"{elapsed:.0f}s")
    t.add_row("Avg req/s", f"{avg_rps:.1f}")
    if peak_rps > 0:
        t.add_row("Peak req/s", f"[{C.PRIMARY}]{peak_rps:.0f}[/]")
    if best_streak > 1:
        t.add_row("Best streak", f"[{C.SUCCESS}]{best_streak}[/] hits")
    if works > 0:
        t.add_row("Saved to", "results/hits.txt")

    console.print(Panel(
        t,
        title=f"[{C.WARNING if interrupted else C.SUCCESS}]{'Stopped' if interrupted else 'Done'}[/] · {elapsed:.0f}s",
        title_align="left",
        box=box.ROUNDED,
        border_style=C.SUCCESS,
    ))


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def ok(msg: str) -> None:
    console.print(f"  [{C.SUCCESS}]✓[/] {msg}")

def fail(msg: str) -> None:
    console.print(f"  [{C.DANGER}]✗[/] {msg}")

def info(msg: str) -> None:
    console.print(f"  [{C.MUTED}]ℹ[/] {msg}")
