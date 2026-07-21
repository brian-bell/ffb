"""``ffb`` command line — the display end of the spine."""

from __future__ import annotations

import logging

import typer
from rich.console import Console
from rich.table import Table

from ffb import config, paths
from ffb.consensus import consensus_rows
from ffb.ingest import ensure_crosswalk, ensure_espn_ingested, ensure_ingested
from ffb.snapshot import SnapshotCache
from ffb.store import Store

app = typer.Typer(help="Fantasy football pipeline (walking skeleton).", no_args_is_help=True)
console = Console()

# Per-source columns shown by --sources, in display order.
_SOURCE_COLUMNS = ("sleeper", "espn")


@app.callback()
def main() -> None:
    """Keep subcommand names (e.g. ``ffb rankings``) even with one command."""


@app.command()
def rankings(
    pos: str = typer.Option(None, "--pos", help="Filter by position (e.g. RB). Omit for all."),
    season: int = typer.Option(config.DEFAULT_SEASON, "--season", help="Projection season."),
    limit: int = typer.Option(30, "--limit", help="Max rows to show."),
    sources: bool = typer.Option(
        False, "--sources", help="Add ESPN and show per-source + consensus columns."
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Re-fetch sources (network)."),
) -> None:
    """Print consensus-ranked projections (Sleeper, plus ESPN with --sources)."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    store = Store(paths.db_path())
    store.init_schema()
    cache = SnapshotCache(paths.snapshot_dir())

    # Crosswalk is best-effort: without it players simply stay unmatched. When it
    # arrives after a source was first ingested, ingest self-heals — it re-resolves
    # any stranded rows offline (see Store.has_stale_resolution).
    try:
        ensure_crosswalk(store, cache, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - degrade, don't crash the ranking
        console.print(f"[yellow]Crosswalk unavailable ({exc}); players may be unmatched.[/yellow]")

    try:
        ensure_ingested(store, cache, season=season, refresh=refresh)
    except FileNotFoundError as exc:
        console.print(f"[red]No Sleeper snapshot and offline:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    active_sources = ["sleeper"]
    if sources:
        try:
            ensure_espn_ingested(store, cache, season=season, refresh=refresh)
            active_sources.append("espn")
        except Exception as exc:  # noqa: BLE001 - fall back to Sleeper only
            console.print(f"[yellow]ESPN unavailable ({exc}); showing Sleeper only.[/yellow]")

    # Consensus uses only the sources that actually succeeded this run, so a
    # failed ESPN fetch can't leave stale ESPN rows silently feeding the average,
    # and the same command stays deterministic regardless of what a prior run
    # persisted.
    rows = consensus_rows(store, season=season, position=pos, sources=active_sources)
    store.close()

    if not rows:
        scope = f"position {pos}" if pos else "any position"
        console.print(f"[yellow]No projections for {season} ({scope}).[/yellow]")
        raise typer.Exit(code=0)

    rows = rows[:limit]
    _render(rows, season=season, pos=pos, sources=sources)
    _report_unmatched(rows)


def _render(rows: list[dict], *, season: int, pos: str | None, sources: bool) -> None:
    title = f"{season} rankings" + (f" — {pos.upper()}" if pos else "")
    table = Table(title=title)
    table.add_column("Rank", justify="right", style="cyan")
    table.add_column("Player")
    table.add_column("Pos", justify="center")
    table.add_column("Team", justify="center")
    if sources:
        for source in _SOURCE_COLUMNS:
            table.add_column(source.capitalize(), justify="right")
        table.add_column("Consensus", justify="right", style="green")
        table.add_column("n", justify="right", style="dim")
    else:
        table.add_column("Proj (PPR)", justify="right", style="green")

    for row in rows:
        cells = [str(row["rank"]), row["full_name"], row["position"], row["team"] or "—"]
        if sources:
            for source in _SOURCE_COLUMNS:
                pts = row["source_points"].get(source)
                cells.append(f"{pts:.1f}" if pts is not None else "—")
            cells.append(f"{row['consensus']:.1f}")
            cells.append(str(row["n"]))
        else:
            cells.append(f"{row['consensus']:.1f}")
        table.add_row(*cells)
    console.print(table)


def _report_unmatched(rows: list[dict]) -> None:
    """Surface crosswalk misses among the shown players (never silently drop)."""
    misses = [r for r in rows if not r["matched"]]
    if not misses:
        return
    names = ", ".join(r["full_name"] for r in misses[:8])
    more = "…" if len(misses) > 8 else ""
    console.print(
        f"[yellow]⚠ {len(misses)} shown player(s) unmatched to crosswalk "
        f"(source-only): {names}{more}[/yellow]"
    )


if __name__ == "__main__":
    app()
