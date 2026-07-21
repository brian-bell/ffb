"""``ffb`` command line — the display end of the spine."""

from __future__ import annotations

import logging

import typer
from rich.console import Console
from rich.table import Table

from ffb import config, paths
from ffb.ingest import ensure_ingested
from ffb.rankings import ranked
from ffb.snapshot import SnapshotCache
from ffb.store import Store

app = typer.Typer(help="Fantasy football pipeline (walking skeleton).", no_args_is_help=True)
console = Console()


@app.callback()
def main() -> None:
    """Keep subcommand names (e.g. ``ffb rankings``) even with one command."""


@app.command()
def rankings(
    pos: str = typer.Option(None, "--pos", help="Filter by position (e.g. RB). Omit for all."),
    season: int = typer.Option(config.DEFAULT_SEASON, "--season", help="Projection season."),
    limit: int = typer.Option(30, "--limit", help="Max rows to show."),
    refresh: bool = typer.Option(False, "--refresh", help="Re-fetch from Sleeper (network)."),
) -> None:
    """Print PPR-ranked projections from Sleeper."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    store = Store(paths.db_path())
    store.init_schema()
    cache = SnapshotCache(paths.snapshot_dir())

    try:
        ensure_ingested(store, cache, season=season, refresh=refresh)
    except FileNotFoundError as exc:
        console.print(f"[red]No snapshot and offline:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    rows = ranked(store, season=season, position=pos, limit=limit)
    store.close()

    if not rows:
        scope = f"position {pos}" if pos else "any position"
        console.print(f"[yellow]No projections for {season} ({scope}).[/yellow]")
        raise typer.Exit(code=0)

    title = f"{season} rankings" + (f" — {pos.upper()}" if pos else "")
    table = Table(title=title)
    table.add_column("Rank", justify="right", style="cyan")
    table.add_column("Player")
    table.add_column("Pos", justify="center")
    table.add_column("Team", justify="center")
    table.add_column("Proj (PPR)", justify="right", style="green")

    for row in rows:
        table.add_row(
            str(row["rank"]),
            row["full_name"],
            row["position"],
            row["team"] or "—",
            f"{row['points']:.1f}",
        )
    console.print(table)


if __name__ == "__main__":
    app()
